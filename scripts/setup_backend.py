#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
BACKEND_JSON = RUNTIME_DIR / "backend.json"

LLAMA_CPP_PYTHON_VERSION = "0.3.23"
LLAMA_CPP_SERVER_DEPS = [
    "fastapi>=0.100.0",
    "uvicorn>=0.22.0",
    "pydantic-settings>=2.0.1",
    "sse-starlette>=1.6.1",
    "starlette-context>=0.3.6,<0.4",
    "PyYAML>=5.1",
]
CUDA_WIN_WHEEL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-win_amd64.whl"
)
CUDA_LINUX_WHEEL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-linux_x86_64.whl"
)
CPU_WHEEL_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"

# CPU 後端改用 llama.cpp 官方預編譯 binary，不用 llama-cpp-python 的 CPU wheel。
#
# 原因：那個 wheel 是「單一 ggml-cpu.dll、編譯期決定指令集」的建置，裡面含有 AVX-512
# 指令。Intel 從 Alder Lake 起的消費級 CPU（i5-14400F、i7-13700K…）全都沒有 AVX-512，
# 一載入模型就被系統以非法指令砍掉，錯誤碼 0xc000001d。
#
# 官方 binary 則附 14 個 ggml-cpu-*.dll 變體（sandybridge / haswell / alderlake /
# skylakex / zen4 …），啟動時依實際 CPU 挑一個載入，所以哪顆 CPU 都跑得起來。
LLAMA_CPP_BUILD = "b10142"
CPU_WIN_ZIP = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_BUILD}/"
    f"llama-{LLAMA_CPP_BUILD}-bin-win-cpu-x64.zip"
)
CPU_LINUX_ZIP = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_BUILD}/"
    f"llama-{LLAMA_CPP_BUILD}-bin-ubuntu-x64.zip"
)
CUDA_RUNTIME_LIBS = {
    "windows": ("cudart64_12.dll", "cublas64_12.dll"),
    "linux": ("libcudart.so.12", "libcublas.so.12"),
}
CUDA_DRIVER_LIBS = {
    "windows": ("nvcuda.dll",),
    "linux": ("libcuda.so.1",),
}

AMD_WIN_ZIP = (
    "https://repo.radeon.com/rocm/llama.cpp/windows/rocm-rel-7.2.1/"
    "llama-b8407-windows-rocm-7.2.1-gfx110X-gfx115X-gfx120X-x64.zip"
)
AMD_LINUX_ZIP = (
    "https://repo.radeon.com/rocm/llama.cpp/linux/rocm-rel-7.2.1/"
    "llama-b8407-ubuntu-24.04-rocm-7.2.1-gfx110X-gfx115X-gfx120X-x64.zip"
)


class CudaDependencyError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化本機 llama.cpp 模型後端。")
    parser.add_argument(
        "--backend",
        choices=("auto", "cuda", "amd", "cpu"),
        default="auto",
        help="要安裝的後端。預設為自動偵測。",
    )
    parser.add_argument(
        "--skip-model-download",
        action="store_true",
        help="初始化時不要下載基礎 GGUF 模型。",
    )
    return parser.parse_args()


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def run_allow_fail(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)


def run_capture(cmd: list[str]) -> str:
    print("+ " + " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    if completed.stderr:
        print(completed.stderr, end="")
    return completed.stdout.strip()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def command_succeeds(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def command_output(cmd: list[str]) -> str:
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return completed.stdout
    except OSError:
        return ""


def setup_command(backend: str) -> str:
    if platform.system().lower() == "windows":
        return f"setup_windows.bat --backend {backend}"
    return f"./setup_unix.sh --backend {backend}"


def load_model_config() -> dict:
    text = (PROJECT_ROOT / "configs" / "model.yaml").read_text(encoding="utf-8")
    in_model = False
    config: dict[str, object] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw_line.startswith(" ") and line.rstrip(":") == "model":
            in_model = True
            continue
        if not in_model:
            continue
        if raw_line and not raw_line.startswith(" "):
            break
        if ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            config[key] = value[1:-1]
        elif value.lower() in ("true", "false"):
            config[key] = value.lower() == "true"
        else:
            try:
                config[key] = int(value)
            except ValueError:
                try:
                    config[key] = float(value)
                except ValueError:
                    config[key] = value
    return config


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def detect_nvidia() -> bool:
    return command_exists("nvidia-smi") and command_succeeds(["nvidia-smi"])


def detect_amd() -> bool:
    system = platform.system().lower()
    if system == "windows":
        output = command_output(["wmic", "path", "win32_VideoController", "get", "name"])
    elif system == "linux":
        output = command_output(["lspci"])
        if not output:
            output = command_output(["rocminfo"])
    else:
        return False
    output = output.lower()
    return any(token in output for token in ("amd", "radeon", "advanced micro devices"))


def select_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if detect_nvidia():
        return "cuda"
    if detect_amd():
        return "amd"
    return "cpu"


def library_search_dirs() -> list[Path]:
    """系統載入器可能放置 CUDA/NVIDIA DLL 的目錄。

    Python 3.8 起 ctypes 用裸檔名載入 DLL 時改用
    LOAD_LIBRARY_SEARCH_DEFAULT_DIRS，不再搜尋 PATH。CUDA Toolkit 把
    cudart/cuBLAS 安裝在加進 PATH 的 bin 目錄，因此必須明確列出這些目錄，
    否則就算 CUDA 正確安裝也會誤判為缺少函式庫。
    """
    dirs: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry:
            dirs.append(Path(entry))
    for key, value in os.environ.items():
        if key.upper().startswith("CUDA_PATH") and value:
            dirs.append(Path(value) / "bin")
    if platform.system().lower() == "windows":
        toolkit = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / "NVIDIA GPU Computing Toolkit" / "CUDA"
        if toolkit.is_dir():
            for child in toolkit.iterdir():
                dirs.append(child / "bin")
    return dirs


def find_library_path(name: str) -> Path | None:
    for directory in library_search_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def can_load_library(name: str) -> bool:
    import ctypes

    loader = ctypes.WinDLL if platform.system().lower() == "windows" else ctypes.CDLL
    # 先用完整路徑載入：找得到檔案就用它（完整路徑會以該 DLL 所在目錄解析其
    # 相依函式庫）；找不到才退回裸檔名，沿用系統預設搜尋（System32、ld.so）。
    full_path = find_library_path(name)
    target = str(full_path) if full_path else name
    try:
        loader(target)
        return True
    except OSError:
        return False


def explain_missing_cuda_toolkit(missing_driver: list[str], missing_runtime: list[str]) -> str:
    missing_lines = "\n".join(f"  - {name}" for name in [*missing_driver, *missing_runtime])
    cuda_setup_cmd = setup_command("cuda")
    cpu_setup_cmd = setup_command("cpu")
    driver_note = ""
    if missing_driver:
        driver_note = (
            "\nNVIDIA driver library is missing. Install or repair the NVIDIA Game Ready/"
            "Studio driver first.\n"
        )
    return f"""

CUDA 後端缺少必要的 NVIDIA/CUDA 動態函式庫。

缺少：
{missing_lines}
{driver_note}
NVIDIA CUDA 後端需要：
  1. 支援 CUDA 12.x 的 NVIDIA 顯示卡驅動。
  2. CUDA Toolkit 12.4 或更新版本提供的 CUDA runtime/cuBLAS DLL/so。

請安裝 CUDA Toolkit 12.4 或更新版本後重新執行：
  {cuda_setup_cmd}

Linux 使用者請安裝發行版對應的 CUDA Toolkit 12.4+ 套件，或確保
libcudart.so.12 與 libcublas.so.12 在系統動態連結器搜尋路徑中。

cuDNN 不需要安裝。llama.cpp CUDA 後端不使用 cuDNN。

若你接受較慢速度，請改用 CPU 後端：
  {cpu_setup_cmd}
"""


def validate_cuda_runtime_libraries() -> None:
    system = platform.system().lower()
    driver_libs = CUDA_DRIVER_LIBS.get(system, ())
    runtime_libs = CUDA_RUNTIME_LIBS.get(system, ())
    if not runtime_libs:
        return

    missing_driver = [name for name in driver_libs if not can_load_library(name)]
    missing_runtime = [name for name in runtime_libs if not can_load_library(name)]
    if missing_driver or missing_runtime:
        raise CudaDependencyError(explain_missing_cuda_toolkit(missing_driver, missing_runtime))
    print("CUDA runtime dependency check OK.")


def install_cuda_backend() -> None:
    system = platform.system().lower()
    if system == "windows":
        wheel = CUDA_WIN_WHEEL
    elif system == "linux":
        wheel = CUDA_LINUX_WHEEL
    else:
        raise RuntimeError("CUDA wheel is only configured for Windows and Linux.")
    validate_cuda_runtime_libraries()
    uninstall_llama_cpp_python()
    run(["uv", "pip", "install", *LLAMA_CPP_SERVER_DEPS])
    run([
        "uv",
        "pip",
        "install",
        "--reinstall",
        "--no-cache",
        f"llama-cpp-python @ {wheel}",
    ])


def install_cpu_backend() -> Path:
    """下載 llama.cpp 官方 CPU binary，回傳 llama-server 的路徑。

    不再安裝 llama-cpp-python 的 CPU wheel——那個建置在沒有 AVX-512 的 CPU 上
    必定崩潰（見檔頭 CPU_WIN_ZIP 的說明）。
    """
    system = platform.system().lower()
    if system == "windows":
        url = CPU_WIN_ZIP
    elif system == "linux":
        url = CPU_LINUX_ZIP
    else:
        raise RuntimeError("CPU 預編譯 llama.cpp binary 目前只支援 Windows/Linux。")

    archive = RUNTIME_DIR / "downloads" / Path(urlparse(url).path).name
    extract_dir = RUNTIME_DIR / "llama_cpp_cpu"
    download(url, archive)

    if not extract_dir.exists() or not list(extract_dir.rglob("llama-server*")):
        print(f"正在解壓縮：{archive}")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
    return find_llama_server(extract_dir)


def uninstall_llama_cpp_python() -> None:
    run_allow_fail(["uv", "pip", "uninstall", "llama-cpp-python"])


def validate_python_backend() -> None:
    code = (
        "import importlib.util; "
        "import llama_cpp; "
        "import llama_cpp.server; "
        "spec = importlib.util.find_spec('llama_cpp.lib'); "
        "print('llama-cpp-python import OK')"
    )
    run([sys.executable, "-c", code])


def explain_cuda_failure(exc: BaseException) -> str:
    return f"""

CUDA 後端安裝後驗證失敗。

程式不會把這種狀態當成 GPU 安裝成功。
若改用 CPU 後端，翻譯仍可執行，但速度會慢非常多。

偵測到的錯誤：
  {exc}

Windows 常見修復方式：
  1. 安裝或修復 Microsoft Visual C++ Redistributable 2015-2022 x64：
     https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist
  2. 安裝或更新支援 CUDA 12.x 的 NVIDIA 驅動。
  3. 若缺少 CUDA runtime/cuBLAS DLL，請安裝 CUDA Toolkit 12.4 或更新版本。
  4. 清除損壞的環境後重新安裝 CUDA 後端：
       rmdir /s /q .venv
       rmdir /s /q .runtime
       setup_windows.bat --backend cuda
  5. 若這台機器沒有 NVIDIA GPU，或你接受較慢速度，請明確使用 CPU：
       setup_windows.bat --backend cpu

說明：llama-cpp-python 的 CUDA wheel 需要相容的 CUDA/Python/系統 DLL 環境。
cuDNN 不需要安裝。
"""


def confirm_cpu_fallback() -> bool:
    if not sys.stdin.isatty():
        print("偵測到非互動式安裝；拒絕靜默改用 CPU 後端。")
        return False
    answer = input("是否改安裝 CPU 後端？速度會慢非常多。 [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"使用已下載的快取檔案：{dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"正在下載：{url}")
    urllib.request.urlretrieve(url, dest)


def find_llama_server(root: Path) -> Path:
    exe_name = "llama-server.exe" if platform.system().lower() == "windows" else "llama-server"
    matches = sorted(root.rglob(exe_name))
    if not matches:
        raise FileNotFoundError(f"在 {root} 找不到 {exe_name}")
    server = matches[0]
    if platform.system().lower() != "windows":
        server.chmod(server.stat().st_mode | 0o111)
    return server


def install_amd_backend() -> Path:
    system = platform.system().lower()
    if system == "windows":
        url = AMD_WIN_ZIP
    elif system == "linux":
        url = AMD_LINUX_ZIP
    else:
        raise RuntimeError("AMD 預編譯 llama.cpp binary 目前只支援 Windows/Linux。")

    archive = RUNTIME_DIR / "downloads" / Path(urlparse(url).path).name
    extract_dir = RUNTIME_DIR / "llama_cpp_amd"
    download(url, archive)

    if not extract_dir.exists() or not list(extract_dir.rglob("llama-server*")):
        print(f"正在解壓縮：{archive}")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
    return find_llama_server(extract_dir)


def resolve_base_model(cfg: dict, skip_download: bool) -> Path:
    if cfg.get("base_gguf_path"):
        path = resolve_project_path(cfg["base_gguf_path"])
        if not path.exists():
            raise FileNotFoundError(f"找不到 base_gguf_path：{path}")
        return path
    if skip_download:
        return Path(cfg["base_hf_filename"])
    print(
        f"正在從 {cfg['base_hf_repo']} 下載基礎模型 {cfg['base_hf_filename']} "
        "（檔案較大，首次執行才需要）..."
    )
    code = (
        "from huggingface_hub import hf_hub_download; "
        f"print(hf_hub_download(repo_id={cfg['base_hf_repo']!r}, "
        f"filename={cfg['base_hf_filename']!r}))"
    )
    try:
        return Path(run_capture([sys.executable, "-c", code]).splitlines()[-1])
    except subprocess.CalledProcessError:
        print("偵測到 HuggingFace/PyYAML 匯入失敗，正在修復套件...")
        run(["uv", "pip", "install", "--force-reinstall", "pyyaml>=6.0", "huggingface-hub>=0.23.0"])
        return Path(run_capture([sys.executable, "-c", code]).splitlines()[-1])


def resolve_lora(cfg: dict) -> Path:
    path = resolve_project_path(cfg["lora_gguf_path"])
    if not path.exists():
        raise FileNotFoundError(f"找不到 LoRA 適配器：{path}")
    return path


def server_host_port(server_url: str) -> tuple[str, int]:
    parsed = urlparse(server_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 8080


def ensure_server_port_free(cfg: dict) -> None:
    host, port = server_host_port(cfg["server_url"])
    try:
        with socket.create_connection((host, port), timeout=1):
            raise RuntimeError(
                f"{host}:{port} 已經有程式在使用。請先關閉翻譯器或殘留的 llama-server，"
                "再重新執行初始化腳本。"
            )
    except OSError:
        return


def python_server_command(cfg: dict, base_model: Path, lora: Path, gpu_layers: int) -> list[str]:
    host, port = server_host_port(cfg["server_url"])
    return [
        sys.executable,
        "-m",
        "llama_cpp.server",
        "--model",
        str(base_model),
        "--lora_path",
        str(lora),
        "--n_gpu_layers",
        str(gpu_layers),
        "--n_ctx",
        str(cfg["n_ctx"]),
        "--use_mlock",
        "false",
        "--host",
        host,
        "--port",
        str(port),
    ]


def binary_server_command(
    server: Path,
    cfg: dict,
    base_model: Path,
    lora: Path,
    gpu_layers: int | None = None,
) -> list[str]:
    """組出 llama-server binary 的啟動指令。

    `gpu_layers` 傳 0 代表純 CPU；留空則沿用設定檔（GPU 後端用）。CPU 後端一併把
    flash attention 交給 auto——CPU 上強制開啟只會多一個失敗點。
    """
    host, port = server_host_port(cfg["server_url"])
    if gpu_layers is None:
        gpu_layers = int(cfg["n_gpu_layers"])
    layers = "99" if gpu_layers < 0 else str(gpu_layers)
    command = [
        str(server),
        "-m",
        str(base_model),
        "-c",
        str(cfg["n_ctx"]),
        "-ngl",
        layers,
        "-fa",
        "auto" if layers == "0" else "on",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if float(cfg["lora_scale"]) == 1.0:
        command.extend(["--lora", str(lora)])
    else:
        command.extend(["--lora-scaled", str(lora), str(cfg["lora_scale"])])
    return command


def write_backend_config(backend: str, command: list[str], cfg: dict, base_model: Path) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": backend,
        "server_url": cfg["server_url"],
        "server_api_key": cfg["server_api_key"],
        "server_model": Path(base_model).name,
        "server_command": command,
        "created_at": int(time.time()),
    }
    BACKEND_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"已寫入後端設定：{BACKEND_JSON}")


def main() -> None:
    args = parse_args()
    cfg = load_model_config()
    ensure_server_port_free(cfg)
    requested_backend = args.backend
    backend = select_backend(args.backend)
    print(f"選擇的後端：{backend}")

    base_model = resolve_base_model(cfg, args.skip_model_download)
    lora = resolve_lora(cfg)

    if backend == "cuda":
        try:
            install_cuda_backend()
            validate_python_backend()
            command = python_server_command(cfg, base_model, lora, int(cfg["n_gpu_layers"]))
        except CudaDependencyError as exc:
            print(exc)
            if requested_backend == "cuda" or not confirm_cpu_fallback():
                raise SystemExit(
                    "GPU 後端安裝失敗。請先修復上方 CUDA 安裝問題；"
                    f"若接受較慢的 CPU 翻譯，請重新執行 {setup_command('cpu')}。"
                ) from exc
            print("使用者已同意改用 CPU 後端。正在安裝 CPU backend...")
            server = install_cpu_backend()
            backend = "cpu"
            command = binary_server_command(server, cfg, base_model, lora, gpu_layers=0)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            print(explain_cuda_failure(exc))
            if requested_backend == "cuda" or not confirm_cpu_fallback():
                raise SystemExit(
                    "GPU 後端安裝失敗。請先修復上方 CUDA 安裝問題；"
                    f"若接受較慢的 CPU 翻譯，請重新執行 {setup_command('cpu')}。"
                ) from exc
            print("使用者已同意改用 CPU 後端。正在安裝 CPU backend...")
            server = install_cpu_backend()
            backend = "cpu"
            command = binary_server_command(server, cfg, base_model, lora, gpu_layers=0)
    elif backend == "amd":
        server = install_amd_backend()
        command = binary_server_command(server, cfg, base_model, lora)
    else:
        server = install_cpu_backend()
        command = binary_server_command(server, cfg, base_model, lora, gpu_layers=0)

    write_backend_config(backend, command, cfg, base_model)
    print("後端初始化完成。正常啟動程式即可，程式會自動啟動本機模型服務。")


if __name__ == "__main__":
    main()
