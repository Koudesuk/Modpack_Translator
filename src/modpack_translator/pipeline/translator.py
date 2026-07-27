from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from modpack_translator.config import ModelConfig

_PROJECT_ROOT = Path(__file__).parents[3]
_RUNTIME_BACKEND = _PROJECT_ROOT / ".runtime" / "backend.json"
_SERVER_LOG = _PROJECT_ROOT / ".runtime" / "llama-server.log"
_READY_POLL_SECONDS = 1.0
_READY_REQUEST_TIMEOUT = 2.0


class _WindowsJob:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self._handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen) -> None:
        ok = self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle))
        if not ok:
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _resolve_local(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _resolve_base_gguf(cfg: ModelConfig) -> Path:
    if cfg.base_gguf_path:
        p = _resolve_local(cfg.base_gguf_path)
        if not p.exists():
            raise FileNotFoundError(f"base_gguf_path not found: {p}")
        return p

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise RuntimeError(
            "huggingface-hub is required to auto-download the base model.\n"
            "Run: uv add huggingface-hub\n"
            "Or set base_gguf_path in configs/model.yaml to a local GGUF file."
        )

    print(f"Base model not cached locally. Downloading {cfg.base_hf_filename} "
          f"from {cfg.base_hf_repo} (~5 GB, one-time)...")
    path = hf_hub_download(repo_id=cfg.base_hf_repo, filename=cfg.base_hf_filename)
    return Path(path)


def _resolve_lora_gguf(cfg: ModelConfig) -> Path:
    p = _resolve_local(cfg.lora_gguf_path)
    if not p.exists():
        raise FileNotFoundError(
            f"LoRA adapter not found: {p}\n"
            f"Expected at: {_PROJECT_ROOT / 'adapter'}/"
        )
    return p


def _load_runtime_backend() -> dict:
    if not _RUNTIME_BACKEND.exists():
        return {}
    try:
        return json.loads(_RUNTIME_BACKEND.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid backend config: {_RUNTIME_BACKEND}: {exc}") from exc


def _normalize_base_url(url: str) -> str:
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def _as_command(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        command = [str(part) for part in value]
    else:
        command = shlex.split(value, posix=os.name != "nt")

    # Older setup output passed --lora_scale to llama_cpp.server. The Python
    # server does not accept that flag in current releases, so sanitize stale
    # .runtime/backend.json files instead of making users rerun setup.
    #
    # Also normalize memory locking off. llama-cpp-python enables use_mlock by
    # default on platforms that support it, which makes some Windows machines
    # fail VirtualLock during model load. This app favors reliable startup over
    # pinning the whole model in RAM.
    cleaned: list[str] = []
    index = 0
    while index < len(command):
        part = command[index]
        if part == "--lora_scale":
            if index + 1 < len(command) and not command[index + 1].startswith("-"):
                index += 1
            index += 1
            continue
        if part == "--mlock":
            index += 1
            continue
        if part == "--use_mlock":
            cleaned.extend(["--use_mlock", "false"])
            if index + 1 < len(command) and not command[index + 1].startswith("-"):
                index += 1
            index += 1
            continue
        if part.startswith("--use_mlock="):
            cleaned.append("--use_mlock=false")
            index += 1
            continue
        cleaned.append(part)
        index += 1
    return cleaned


def _server_status(base_url: str, timeout: float = _READY_REQUEST_TIMEOUT) -> str:
    for path in ("/health", "/v1/health"):
        request = Request(f"{base_url}{path}")
        try:
            with urlopen(request, timeout=timeout) as response:
                if response.status == 200:
                    return "ready"
                if response.status == 503:
                    return "loading"
        except HTTPError as exc:
            if exc.code == 503:
                return "loading"
        except (OSError, URLError):
            pass

    request = Request(f"{base_url}/v1/models")
    try:
        with urlopen(request, timeout=timeout) as response:
            return "ready" if 200 <= response.status < 500 else "unreachable"
    except HTTPError as exc:
        if exc.code == 503:
            return "loading"
        if exc.code in (401, 403):
            return "ready"
        return "unreachable"
    except (OSError, URLError):
        return "unreachable"


def _server_ready(base_url: str, timeout: float = 3.0) -> bool:
    return _server_status(base_url, timeout=timeout) == "ready"


def _server_log_tail(max_chars: int = 4000) -> str:
    if not _SERVER_LOG.exists():
        return ""
    try:
        return _SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-max_chars:].strip()
    except OSError:
        return ""


def _backend_help_from_log(detail: str) -> str:
    lowered = detail.lower()
    if "0xc000001d" in lowered or "-1073741795" in lowered:
        return (
            "\n\nLikely cause: the llama-cpp-python CPU wheel is compiled with CPU "
            "instructions your processor does not have (AVX-512). Consumer Intel CPUs "
            "since Alder Lake — i5-14400F, i7-13700K and so on — have no AVX-512, so "
            "the model load is killed with 0xc000001d.\n"
            "Fix: re-run setup so the CPU backend is reinstalled. Current versions "
            "install llama.cpp's official prebuilt server instead, which picks a CPU "
            "kernel matching your processor at runtime.\n"
            "  Windows: setup_windows.bat --backend cpu\n"
            "  Linux/macOS: ./setup_unix.sh --backend cpu"
        )
    if "failed to virtuallock" in lowered or "failed to mlock" in lowered:
        return (
            "\n\nLikely cause: the backend tried to lock the model into RAM. "
            "That is fragile on Windows and on low-memory machines. "
            "Re-run setup so .runtime/backend.json is regenerated with memory "
            "locking disabled."
        )
    if "llama.dll" in lowered or "could not find module" in lowered:
        return (
            "\n\nLikely cause: the installed llama-cpp-python backend is broken "
            "or one of llama.dll's dependencies is missing.\n"
            "Fix on Windows:\n"
            "1. Install Microsoft Visual C++ Redistributable 2015-2022 x64.\n"
            "2. Install/update the NVIDIA driver and CUDA 12.x runtime if using CUDA.\n"
            "3. Re-run setup_windows.bat --backend cuda.\n"
            "4. If GPU setup still fails and slow translation is acceptable, run "
            "setup_windows.bat --backend cpu."
        )
    return ""


class GGUFTranslator:
    def __init__(self, cfg: ModelConfig, system_prompt: str) -> None:
        from openai import OpenAI

        runtime = _load_runtime_backend()
        server_url = (
            os.getenv("MODPACK_TRANSLATOR_SERVER_URL")
            or os.getenv("LLAMA_SERVER_URL")
            or runtime.get("server_url")
            or cfg.server_url
        )
        api_key = (
            os.getenv("MODPACK_TRANSLATOR_SERVER_API_KEY")
            or os.getenv("LLAMA_SERVER_API_KEY")
            or runtime.get("server_api_key")
            or cfg.server_api_key
        )
        self._model = (
            os.getenv("MODPACK_TRANSLATOR_SERVER_MODEL")
            or os.getenv("LLAMA_SERVER_MODEL")
            or runtime.get("server_model")
            or cfg.server_model
        )
        self._base_url = _normalize_base_url(server_url)
        self._server_process: subprocess.Popen | None = None
        self._server_job: _WindowsJob | None = None

        if not _server_ready(self._base_url) and cfg.auto_start_server:
            command = _as_command(runtime.get("server_command") or cfg.server_start_command)
            if command:
                self._launch_server(command, cfg.server_ready_timeout)

        status = _server_status(self._base_url)
        if status != "ready":
            detail = _server_log_tail()
            suffix = f"\n\nLast llama-server log:\n{detail}{_backend_help_from_log(detail)}" if detail else ""
            if status == "loading":
                suffix = (
                    f"\n\nThe server is still loading after {cfg.server_ready_timeout} seconds. "
                    "On slow disks, CPU-only systems, or low-memory Windows machines, "
                    "increase model.server_ready_timeout in configs/model.yaml."
                    f"{suffix}"
                )
            raise RuntimeError(
                "Local model server is not reachable. Run setup_windows.bat or "
                "setup_unix.sh first, or start llama-server manually and set "
                f"LLAMA_SERVER_URL.{suffix}"
            )

        self._client = OpenAI(base_url=f"{self._base_url}/v1", api_key=api_key)
        self._system_prompt = system_prompt
        # 用語庫（可選）。由呼叫端在建構後指定，translate() 會把命中的詞條附在
        # system prompt 最後——靜態前綴不變，prompt 快取才留得住。
        self.glossary = None
        self._cfg = cfg

    def _launch_server(self, command: list[str], ready_timeout: int) -> int | None:
        """啟動模型服務並等它就緒。回傳行程退出碼；仍在執行（或已就緒）則回 None。"""
        # 重試前先收掉上一輪的 job 物件，否則 handle 會漏。
        if self._server_job is not None:
            self._server_job.close()
            self._server_job = None

        _SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with _SERVER_LOG.open("ab") as log:
            self._server_process = subprocess.Popen(
                command,
                cwd=_PROJECT_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
        if os.name == "nt":
            try:
                self._server_job = _WindowsJob()
                self._server_job.assign(self._server_process)
            except Exception:
                if self._server_job is not None:
                    self._server_job.close()
                    self._server_job = None

        deadline = time.monotonic() + max(1, ready_timeout)
        while time.monotonic() < deadline:
            if _server_ready(self._base_url):
                return None
            if self._server_process.poll() is not None:
                return self._server_process.returncode
            time.sleep(_READY_POLL_SECONDS)
        return None

    def close(self) -> None:
        if self._server_process is None:
            if self._server_job is not None:
                self._server_job.close()
                self._server_job = None
            return
        if self._server_process.poll() is not None:
            self._server_process = None
            if self._server_job is not None:
                self._server_job.close()
                self._server_job = None
            return

        if os.name == "nt":
            if self._server_job is not None:
                self._server_job.close()
                self._server_job = None
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(self._server_process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        else:
            try:
                os.killpg(self._server_process.pid, signal.SIGTERM)
            except OSError:
                self._server_process.send_signal(signal.SIGTERM)

        try:
            self._server_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                self._server_process.kill()
            else:
                try:
                    os.killpg(self._server_process.pid, signal.SIGKILL)
                except OSError:
                    self._server_process.kill()
            self._server_process.wait(timeout=5)
        finally:
            self._server_process = None
            if self._server_job is not None:
                self._server_job.close()
                self._server_job = None

    def __enter__(self) -> "GGUFTranslator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _glossary_block(self, text: str) -> str:
        if self.glossary is None:
            return ""
        return self.glossary.prompt_block(text)

    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        """
        翻譯單條字串，使用串流模式逐 token 生成。
        cancel_check 若回傳 True，立即中止並回傳空字串（使後處理驗證失敗，安全回退至原文）。
        """
        chunks: list[str] = []
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt + self._glossary_block(text)},
                {"role": "user",   "content": text},
            ],
            max_tokens=self._cfg.max_tokens,
            temperature=self._cfg.temperature,
            stream=True,
            extra_body={"repeat_penalty": self._cfg.repeat_penalty},
        )
        for chunk in stream:
            if cancel_check is not None and cancel_check():
                return ""   # 強制後處理失敗 → 安全回退至原文
            delta = chunk.choices[0].delta
            if delta.content:
                chunks.append(delta.content)
        return "".join(chunks).strip()
