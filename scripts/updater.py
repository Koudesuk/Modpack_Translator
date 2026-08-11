#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
UPDATE_DIR = RUNTIME_DIR / "updates"
UPDATE_STATE = RUNTIME_DIR / "update_state.json"
BACKUP_DIR = RUNTIME_DIR / "update_backup"
FINALIZE_SCRIPT = RUNTIME_DIR / "finalize_update"

GITHUB_REPO = "Koudesuk/Modpack_Translator"
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
ASSET_PREFIX = "Modpack_Translator"

PRESERVE_TOP_LEVEL = {
    ".git",
    ".runtime",
    ".venv",
    "venv",
    "env",
    "outputs",
    "Failed Items",
    "mods_bak",
    "quests_bak",
    "data_bak",
    "data",
    "logs",
}
PRESERVE_FILES = {".env"}


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    release_url: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    sha256: str | None = None


def normalize_version(value: str) -> str:
    value = value.strip()
    return value[1:] if value.lower().startswith("v") else value


def version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in normalize_version(value).split("."):
        number = ""
        for char in chunk:
            if not char.isdigit():
                break
            number += char
        parts.append(int(number or "0"))
    return tuple(parts)


def is_newer_version(latest: str, current: str) -> bool:
    left = version_tuple(latest)
    right = version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _current_version_for_agent() -> str:
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from modpack_translator.version import __version__

        return __version__
    except Exception:
        return "unknown"


def _request_json(url: str, timeout: float = 8.0) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_text(url: str, timeout: float = 8.0) -> str:
    request = Request(
        url,
        headers={"User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _select_asset(release: dict) -> tuple[dict, str | None]:
    tag = str(release.get("tag_name") or "")
    normalized = normalize_version(tag)
    assets = release.get("assets") or []
    zip_assets = [
        asset
        for asset in assets
        if str(asset.get("name", "")).lower().endswith(".zip")
        and str(asset.get("name", "")).startswith(f"{ASSET_PREFIX}-v{normalized}")
    ]
    if not zip_assets:
        zip_assets = [
            asset
            for asset in assets
            if str(asset.get("name", "")).lower().endswith(".zip")
            and str(asset.get("name", "")).startswith(ASSET_PREFIX)
        ]
    if not zip_assets:
        raise RuntimeError(
            f"No release asset named {ASSET_PREFIX}-v{normalized}.zip was found."
        )

    zip_asset = zip_assets[0]
    sha_asset = next(
        (asset for asset in assets if asset.get("name") == f"{zip_asset['name']}.sha256"),
        None,
    )
    sha256: str | None = None
    if sha_asset:
        sha_text = _download_text(str(sha_asset["browser_download_url"]))
        sha256 = sha_text.strip().split()[0].lower()
    elif isinstance(zip_asset.get("digest"), str) and zip_asset["digest"].startswith("sha256:"):
        sha256 = zip_asset["digest"].split(":", 1)[1].lower()
    return zip_asset, sha256


def check_for_update(current_version: str) -> UpdateInfo | None:
    try:
        release = _request_json(RELEASE_API_URL)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    if release.get("draft") or release.get("prerelease"):
        return None

    tag_name = str(release.get("tag_name") or "")
    if not tag_name or not is_newer_version(tag_name, current_version):
        return None

    try:
        asset, sha256 = _select_asset(release)
    except Exception:
        return None

    return UpdateInfo(
        version=normalize_version(tag_name),
        tag_name=tag_name,
        release_url=str(release.get("html_url") or RELEASES_URL),
        notes=str(release.get("body") or ""),
        asset_name=str(asset["name"]),
        asset_url=str(asset["browser_download_url"]),
        asset_size=int(asset.get("size") or 0),
        sha256=sha256,
    )


def download_update(info: UpdateInfo) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATE_DIR / info.asset_name
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()

    request = Request(
        info.asset_url,
        headers={"User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}"},
    )
    with urlopen(request, timeout=30) as response, tmp.open("wb") as fh:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            fh.write(chunk)

    actual = digest.hexdigest()
    if info.sha256 and actual.lower() != info.sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Update checksum mismatch: expected {info.sha256}, got {actual}."
        )

    tmp.replace(dest)
    UPDATE_STATE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_STATE.write_text(
        json.dumps(
            {
                "version": info.version,
                "tag_name": info.tag_name,
                "asset_name": info.asset_name,
                "asset_path": str(dest),
                "sha256": actual,
                "downloaded_at": int(time.time()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def launch_apply_update(zip_path: Path, restart: bool = True) -> subprocess.Popen:
    args = [sys.executable, str(Path(__file__).resolve()), "apply", str(zip_path)]
    if restart:
        args.append("--restart")
    return subprocess.Popen(
        args,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=(os.name != "nt"),
    )


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        path = Path(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe path in update archive: {info.filename}")
        members.append(info)
    return members


def _archive_root(members: list[zipfile.ZipInfo]) -> str:
    roots = {
        Path(info.filename).parts[0]
        for info in members
        if Path(info.filename).parts and not info.filename.endswith("/")
    }
    return next(iter(roots)) if len(roots) == 1 else ""


def _copy_tree_contents(src_root: Path, dest_root: Path) -> None:
    for src in src_root.iterdir():
        name = src.name
        if name in PRESERVE_TOP_LEVEL or name in PRESERVE_FILES:
            continue
        dest = dest_root / name
        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)


def _write_finalize_script(restart: bool) -> Path:
    current_pid = os.getpid()
    if os.name == "nt":
        script = FINALIZE_SCRIPT.with_suffix(".ps1")
        script.write_text(
            f"""
$ErrorActionPreference = "Continue"
while (Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 300
}}
Set-Location -LiteralPath {str(PROJECT_ROOT)!r}
if (Test-Path -LiteralPath ".venv") {{
    Remove-Item -LiteralPath ".venv" -Recurse -Force -ErrorAction SilentlyContinue
}}
Remove-Item -LiteralPath ".runtime\\backend.json" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".runtime\\llama-server.log" -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath ".runtime\\llama_cpp_amd") {{
    Remove-Item -LiteralPath ".runtime\\llama_cpp_amd" -Recurse -Force -ErrorAction SilentlyContinue
}}
if (Test-Path -LiteralPath ".runtime\\downloads") {{
    Remove-Item -LiteralPath ".runtime\\downloads" -Recurse -Force -ErrorAction SilentlyContinue
}}
& ".\\setup_windows.bat"
if ($LASTEXITCODE -eq 0 -and ${str(bool(restart)).lower()}) {{
    Start-Process -FilePath "uv" -ArgumentList @("run", "python", "main.py") -WorkingDirectory {str(PROJECT_ROOT)!r} -WindowStyle Hidden
}}
""".lstrip(),
            encoding="utf-8",
        )
        return script

    script = FINALIZE_SCRIPT.with_suffix(".sh")
    script.write_text(
        f"""#!/usr/bin/env sh
while kill -0 {current_pid} 2>/dev/null; do
  sleep 0.3
done
cd {str(PROJECT_ROOT)!r} || exit 1
rm -rf .venv
rm -f .runtime/backend.json .runtime/llama-server.log
rm -rf .runtime/llama_cpp_amd .runtime/downloads
chmod +x ./setup_unix.sh 2>/dev/null || true
./setup_unix.sh
status=$?
if [ "$status" -eq 0 ] && [ "{'1' if restart else '0'}" = "1" ]; then
  nohup uv run python main.py >/dev/null 2>&1 &
fi
exit "$status"
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


def _launch_finalize_script(restart: bool) -> None:
    script = _write_finalize_script(restart)
    if os.name == "nt":
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        subprocess.Popen(
            ["sh", str(script)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def apply_update(zip_path: Path, restart: bool, refresh_environment: bool = True) -> None:
    zip_path = zip_path.resolve()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    apply_dir = UPDATE_DIR / "apply"
    if apply_dir.exists():
        shutil.rmtree(apply_dir)
    apply_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        members = _safe_members(zf)
        zf.extractall(apply_dir, members=members)
    root = _archive_root(members)
    src_root = apply_dir / root if root else apply_dir

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_root = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    for item in PROJECT_ROOT.iterdir():
        if item.name in PRESERVE_TOP_LEVEL or item.name in PRESERVE_FILES:
            continue
        target = backup_root / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, target)

    _copy_tree_contents(src_root, PROJECT_ROOT)

    UPDATE_STATE.write_text(
        json.dumps(
            {
                "applied_at": int(time.time()),
                "zip_path": str(zip_path),
                "backup_path": str(backup_root),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if refresh_environment:
        _launch_finalize_script(restart)
    elif restart:
        subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "main.py")],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )


def _cmd_check(args: argparse.Namespace) -> int:
    info = check_for_update(args.current_version)
    if info is None:
        return 1
    print(json.dumps(info.__dict__, ensure_ascii=False, indent=2))
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    info = check_for_update(args.current_version)
    if info is None:
        return 1
    print(download_update(info))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    apply_update(
        Path(args.zip_path),
        restart=args.restart,
        refresh_environment=not args.keep_environment,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Modpack Translator updater")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Check GitHub Releases for an update")
    check.add_argument("--current-version", default=_current_version_for_agent())
    check.set_defaults(func=_cmd_check)

    download = sub.add_parser("download", help="Download the latest update")
    download.add_argument("--current-version", default=_current_version_for_agent())
    download.set_defaults(func=_cmd_download)

    apply = sub.add_parser("apply", help="Apply a downloaded update zip")
    apply.add_argument("zip_path")
    apply.add_argument("--restart", action="store_true")
    apply.add_argument("--keep-environment", action="store_true")
    apply.set_defaults(func=_cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
