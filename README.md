# Minecraft Modpack Translator v1.5.3

**Language / 語言：** English | [繁體中文](README_zh.md)

[![Ko-fi](https://img.shields.io/badge/Support%20me%20on-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/koudesuk)

---

A tool that automatically translates Minecraft modpacks from English (`en_us`) to Traditional Chinese (`zh_tw`) — language files, quest books, and in-game guidebooks — using a fine-tuned GGUF model with LoRA adaptation. Supports both a graphical user interface and a command-line interface.

---

## What's New in v1.5.3

| Fix | Description |
|---|---|
| **Failed Items on Windows** | Failed-item reports now use short, unique filenames. Reports work even when the app or modpack is stored under a deeply nested folder, while the full source location remains inside each report |
| **Safer release packaging** | Release builds now verify the committed dependency lock and test the exact requested tag before publishing |

---

## What's New in v1.5.2

| Fix | Description |
|---|---|
| **Origins / Apoli powers** | `data/<ns>/powers` and `data/<ns>/origins` are now scanned. Origins lets `name` and `description` be written as literal text instead of lang keys, and those strings were invisible to every previous version — power panels stayed fully English with nothing in the failed-items list to show for it. Condition, action and modifier subtrees are never touched: the `name` in there is an identifier such as a damage type, and translating it breaks the power without any error |
| **Single words are no longer dropped** | The old rule treated "no whitespace" as "identifier", so quest titles like `Bookshelves`, `Carrots` and `Cooking` were silently discarded — visible in game, absent from the failed-items list. Identifier detection now requires an actual separator, and everything else goes through the same classifier the lang files use |
| **Unreadable files leave a trace** | Trailing commas are accepted everywhere now (the game's GSON reader tolerates them; Python's `json` does not). Files that are genuinely broken get a line in `outputs/run.log` naming the file and the parse error, instead of being skipped in silence |
| **No invented line breaks** | Line breaks the model adds on its own are removed when the source is a single line. Cached entries are repaired in place on reuse |
| **Dropped-clause detection** | A translation that omits an entire clause used to pass every structural check. Output that is drastically shorter than a multi-clause source is now rejected, kept in English and listed for manual correction. Measured against 144,580 shipped en→zh pairs, the false-positive rate is 0.022% |
| **Case-insensitive glossary lookup** | Glossary hints were matched case-sensitively, so a source writing `saturation value` never received the `Saturation` entry and the model was left guessing. Matching is now case-insensitive for prompt hints (substitution stays case-sensitive so identifiers are never damaged) |

---

## What's New in v1.5.0

| Feature | Description |
|---|---|
| **Glossary** | Ships 1,945 official Traditional Chinese Minecraft terms. When the whole source string matches a term, the translation is taken directly without invoking the model; for longer sentences, matching terms are appended to the prompt; leftover English terms are substituted afterwards. The GUI's "自訂用語…" button lets you add or override terms |
| **GuideME guides** | In-game guide pages (`.md`) from AE2 (press G), Powah, and similar mods are now translated. JSX component tags and links are preserved verbatim |
| **Citadel guidebooks** | Guidebook text (`.txt`) from Alex's Mobs / Alex's Caves is now translated. Chinese has no spaces for the renderer to break on, so the output is wrapped following the convention of each mod's own official translation, keeping text inside the page |
| **Resource packs / shader packs** | `resourcepacks/` and `shaderpacks/` are now scanned. These packs add or override GUI text with keys that do not exist in any mod JAR, so without scanning them those strings stay English forever |
| **Manual correction of failed items** | After translation, strings the model could not handle are listed for manual entry and written straight back into the modpack. Corrections are remembered and never overwritten on later runs; the "失敗項目…" button reopens the dialog as long as the app is still open and the modpack folder has not changed |
| **Run log** | `outputs/run.log` records every translation result and every rejection reason for a single run, with no line limit. Attach this file when reporting an issue |
| **Format-argument validation** | A translation that uses one more `%s` than the source makes the game throw when it reads that string. Such output is now rejected and retranslated, including pre-existing translations shipped with the mod |
| **Duplicate JAR entry handling** | A few mod JARs contain duplicate entries for the same path. Rewrites now de-duplicate them (last entry wins) instead of producing a broken JAR |
| **New CPU backend** | Switched to llama.cpp's official prebuilt binary, fixing the `0xc000001d` crash on some CPUs |
| **Antivirus false-positive fix** | The launcher no longer starts the app through `cmd.exe` and now carries full version and publisher metadata |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| [Git](https://git-scm.com/downloads) | any | Required to clone the repo |
| [Git LFS](https://git-lfs.com) | any | **Required** — the LoRA adapter (~66 MB) is stored via LFS |
| [uv](https://docs.astral.sh/uv/) | latest | Installs and manages this project's Python runtime |
| GPU (optional) | NVIDIA CUDA or supported AMD ROCm | Strongly recommended; CPU works but is very slow |
| [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) | 12.4 or newer | **Required for NVIDIA CUDA backend**; Game Ready/Studio Driver alone is not enough. cuDNN is not required |
| Free disk space | ~6 GB | ~66 MB for adapter (LFS) + ~5 GB for base model (auto-download) |

---

## Installation

### Step 1 — Install uv

`uv` is a fast Python package manager. Install it once on your machine:

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2 — Install Git LFS

The LoRA adapter is stored in Git LFS. Install it before cloning:

**Windows:** Download the installer from [git-lfs.com](https://git-lfs.com), or:
```powershell
winget install GitHub.GitLFS
```

**macOS:**
```bash
brew install git-lfs
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install git-lfs
```

Then enable it once for your user account:
```bash
git lfs install
```

### Step 3 — Clone the repository

```bash
git clone <repository-url>
cd Modpack_Translator
```

Git LFS will automatically download the adapter during clone. Verify it downloaded correctly — the file should be **~66 MB**, not a few hundred bytes:

```bash
# Should print ~66 MB
ls -lh adapter/minecraft_translator_gemma4_e4b_lora.gguf   # macOS/Linux
dir adapter\minecraft_translator_gemma4_e4b_lora.gguf       # Windows

# If the file is tiny (a pointer file), run:
git lfs pull
```

### NVIDIA GPU users — Install CUDA Toolkit

If you want to use the CUDA backend, install **CUDA Toolkit 12.4 or newer** before running setup:

```text
https://developer.nvidia.com/cuda-downloads
```

The NVIDIA Game Ready/Studio Driver provides the driver library, but this project's CUDA `llama-cpp-python` wheel also needs CUDA runtime/cuBLAS libraries such as `cudart64_12.dll` and `cublas64_12.dll` on Windows. The setup script checks for these libraries and prints a clear error if they are missing.

cuDNN is **not** required.

### Step 4 — Run the backend setup

The setup script installs uv-managed CPython 3.12, creates `.venv/`, detects your hardware, installs the matching local inference backend, downloads the base model, and writes `.runtime/backend.json`. Users do not need to install Python separately.

**Windows:**
```bat
setup_windows.bat
```

After setup, Windows builds a versioned launcher such as `模組包翻譯器v1.5.3.exe` in the project folder. Double-click it to start the app without opening a terminal. If the launcher is missing, run setup again or build it manually:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_launcher.ps1
```

**macOS / Linux:**
```bash
./setup_unix.sh
```

Hardware selection is automatic:

| Hardware | Backend |
|---|---|
| NVIDIA | CUDA `llama-cpp-python[server]` wheel |
| AMD Windows/Linux | AMD prebuilt `llama.cpp` / `llama-server` binary |
| CPU only | llama.cpp's official prebuilt `llama-server` binary (Windows/Linux) |

As of v1.5.0 the CPU backend uses llama.cpp's official binary instead of the `llama-cpp-python[server]` CPU wheel. That wheel ships a single `ggml-cpu.dll` build which executes unsupported instructions on some CPUs and crashes (`0xc000001d`); the official binary ships 14 instruction-set variants and picks one at startup based on the actual CPU.

Close the app before re-running setup. On Windows, a running local model server can lock `.dll` files and prevent backend replacement.

---

## Backend Setup Overrides

Auto-detection should be enough for normal users. To force a backend:

**Windows:**
```bat
setup_windows.bat --backend cuda
setup_windows.bat --backend amd
setup_windows.bat --backend cpu
```

**macOS / Linux:**
```bash
./setup_unix.sh --backend cuda
./setup_unix.sh --backend amd
./setup_unix.sh --backend cpu
```

The application talks to the model through an OpenAI-compatible local HTTP API. You can also start your own compatible server and set `LLAMA_SERVER_URL`, for example `http://127.0.0.1:8080/v1`.

If you change the base model, LoRA adapter, context size, GPU layer count, or backend type in `configs/model.yaml`, run the setup script again so `.runtime/backend.json` is regenerated.

---

## Configuration Files

### `configs/model.yaml`

```yaml
model:
  base_gguf_path: ""                              # Leave blank to auto-download
  base_hf_repo: "unsloth/gemma-4-E4B-it-GGUF"
  base_hf_filename: "gemma-4-E4B-it-Q4_K_M.gguf"
  lora_gguf_path: "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
  lora_scale: 1.0
  n_gpu_layers: -1     # -1 = all GPU, 0 = CPU only
  n_ctx: 2048
  max_tokens: 512
  temperature: 0.05
  repeat_penalty: 1.1
  verbose: false
  server_url: "http://127.0.0.1:8080/v1"
  server_api_key: "llama.cpp"
  server_model: "local-model"
  auto_start_server: true
  server_ready_timeout: 600
```

### `configs/paths.yaml`

```yaml
paths:
  output_root: "outputs"
  resource_pack_dir: "outputs/resource_packs"
  translation_cache: "outputs/translation_cache.json"
```

### `configs/languages/zh_tw.yaml`

Contains the language code, display name, and system prompt for the translation model. Do not modify unless you are adding support for a different target language.

---

## GUI Usage

Launch the graphical interface:

```bash
uv run python main.py
```

On Windows, users can also double-click the versioned launcher EXE, such as `模組包翻譯器v1.5.3.exe`. It checks that setup has been run, launches `uv run python main.py` in the background, and writes launcher errors to `.runtime/launcher.log`.

On startup, the app checks the latest GitHub Release in the background. If a newer release package is available, it shows an update dialog; if there is no update, it shows nothing. Auto-update downloads the release ZIP, verifies its SHA256 file when present, applies the new source files, removes the old `.venv` and stale local backend runtime files, runs setup again, and then restarts the app.

**Step-by-step workflow:**

1. **Modpack Folder** — Click "瀏覽…" to select your modpack instance directory (the folder containing `mods/`, `config/`, etc.).
2. **Model Settings** — The normal setup flow already configured the local model server. Only change these fields if you also regenerate the backend setup.
3. **Options** — Check "翻譯模組 (.jar)" and/or "翻譯任務書". Set retry count (default: 3).
4. **Scan** — Click "🔍 掃描模組包". The result panel shows the number of targets and sample strings.
5. **Translate** — Click "▶ 開始翻譯". The progress bar shows percentage, speed, elapsed time, and ETA.
6. **Done** — When complete, the progress bar turns green and the button shows "✓ 完成".

**Other buttons:**

| Button | Purpose |
|---|---|
| **自訂用語…** (Custom terms) | Pin a fixed translation for an English term. Custom terms take priority over the built-in official glossary, so they can override official names. Leaving a translation blank disables that term. Stored in `outputs/custom_glossary.json`, which auto-update never clears |
| **失敗項目…** (Failed items) | Reopen the manual correction dialog. It pops up automatically once when translation finishes with failures; this button reopens it as long as the app is still open and the modpack folder has not changed |
| **執行紀錄** (Run log) | Open `outputs/run.log`. Attach this file when reporting an issue |

**Original files are always backed up:**
- Mod JARs → `mods_bak/`
- Quest configs → `quests_bak/`
- Resource packs → `resourcepacks_bak/`
- Shader packs → `shaderpacks_bak/`
- Data pack files edited in place → `data_bak/`

**Failed items** (strings that could not be translated after all retries) are written to `Failed Items/<mod_name>.txt` for review. If no items fail, this folder is not created. When translation finishes with failures, the app lists every one of them so you can fill in translations by hand; clicking apply writes them straight back into the modpack. Manual corrections are stored in `outputs/manual_translations.json` and take priority on later runs, so the model never overwrites them.

> Manual correction is GUI-only; the CLI does not read `outputs/manual_translations.json`. Custom glossary terms (`outputs/custom_glossary.json`) apply to both interfaces.

---

## CLI Usage

```bash
uv run python scripts/translate_modpack.py --modpack <path> [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--modpack PATH` | (required) | Path to the modpack instance folder |
| `--language FILE` | `configs/languages/zh_tw.yaml` | Language config file |
| `--model-config FILE` | `configs/model.yaml` | Model config file |
| `--paths-config FILE` | `configs/paths.yaml` | Paths config file |
| `--dry-run` | false | Scan only, no translation |
| `--skip-mods` | false | Skip mod JAR scanning |
| `--skip-quests` | false | Skip quest config scanning |
| `--max-steps N` | -1 (all) | Limit to first N targets (for testing) |
| `--retry N` | 0 | Retry count per string when postprocessor rejects output |

### Examples

```bash
# Dry run to preview what will be translated
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# Full translation with 3 retries per failed string
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# Translate quest files only
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
```

The CLI shares the same run log as the GUI: every run clears and rewrites `outputs/run.log`.

---

## Supported File Formats

| Format Key | Extension | Description |
|---|---|---|
| `json_lang` | `.json` | Standard mod language file (`assets/<mod>/lang/en_us.json`); resource pack overrides use the same format |
| `legacy_lang` | `.lang` | Pre-1.13 mod language file (`en_us.lang`); shader pack `shaders/lang/` uses the same format |
| `patchouli_json` | `.json` | Patchouli guidebook pages |
| `guideme_md` | `.md` | GuideME in-game guide pages (AE2, Powah, etc.) |
| `citadel_txt` | `.txt` | Citadel guidebook pages (Alex's Mobs, Alex's Caves, etc.) |
| `ftbq_snbt` | `.snbt` | FTB Quests language files |
| `ftbq_inline_snbt` | `.snbt` | FTB Quests direct text fields in quest files |
| `heracles_snbt` | `.snbt` | Heracles (Odyssey Quests) language files |
| `heracles_inline_snbt` | `.snbt` | Heracles inline text fields |
| `bq_lang` | `.lang` | Better Questing language format (1.12) |
| `kubejs_json` | `.json` | KubeJS script translation files |
| `apoli_power` | `.json` | Origins/Apoli power and origin definitions (`data/<ns>/powers`, `data/<ns>/origins`) whose `name`/`description` are written as literal text instead of lang keys. Condition, action and modifier subtrees are never touched — the `name` in there is an identifier such as a damage type, and translating it breaks the power silently |

### Scan Scope

| Location | Handling |
|---|---|
| `mods/*.jar` | Translations injected back into the JAR; originals backed up to `mods_bak/` |
| `config/`, `kubejs/` | Written in place; originals backed up to `quests_bak/` |
| `datapacks/`, `config/openloader/`, `global_packs/` | Power definitions in folder-based data packs are written in place; originals backed up to `data_bak/`. ZIP data packs are not handled |
| `resourcepacks/` | ZIP packs are injected back into the ZIP, folder packs are written in place; originals backed up to `resourcepacks_bak/` |
| `shaderpacks/` | `shaders/lang/` of folder-based packs is written in place; originals backed up to `shaderpacks_bak/`. ZIP shader packs are not handled |

---

## Supported Minecraft Versions

1.16.2, 1.16.5, 1.17, 1.17.1, 1.18, 1.18.2, 1.19, 1.19.2, 1.19.4, 1.20, 1.20.1, 1.20.2, 1.20.4, 1.20.6, 1.21, 1.21.1, 1.21.3, 1.21.4, 1.21.5

---

## Output Structure

```
<modpack-folder>/
├── mods/                ← translated JARs (in-place)
├── mods_bak/            ← original JAR backups
├── config/              ← translated quest configs (in-place)
├── quests_bak/          ← original quest config backups
├── resourcepacks/       ← translated resource packs (in-place)
├── resourcepacks_bak/   ← original resource pack backups
├── shaderpacks/         ← translated shader packs (in-place)
└── shaderpacks_bak/     ← original shader pack backups

<project-root>/
├── outputs/
│   ├── translation_cache.json     ← reused on subsequent runs
│   ├── manual_translations.json   ← manual corrections, applied first on later runs
│   ├── custom_glossary.json       ← custom terms (written by the GUI's "自訂用語…")
│   └── run.log                    ← full log of this run, cleared when the app starts
└── Failed Items/
    ├── modname__json_lang.txt   ← strings that failed after all retries
    └── ...
```

Nothing under `outputs/` is ever overwritten by auto-update.

---

## FAQ

**Q: How do ZIP users update the app?**
- Open the app. If a newer GitHub Release exists, click **Auto update** in the update dialog.
- The updater preserves user outputs and backups, but rebuilds `.venv` and the local backend setup to avoid dependency conflicts.
- Release ZIPs are generated by GitHub Actions from tags such as `v1.5.3`.

**Q: Windows Defender flags the launcher as malware and blocks it.**
- This is a false positive. The launcher is a small, unsigned executable, and antivirus machine-learning models sometimes flag such files (v1.4.1 was misidentified as `Trojan:Win32/Suschil!rfn`).
- v1.5.0 addresses the causes: the launcher no longer starts the app through `cmd.exe`, and it carries full version and publisher metadata.
- If it is still blocked: verify the download against the SHA-256 published on the Release page, then go to Windows Security → Virus & threat protection → Protection history and choose "Allow". Reporting the file to [Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission) helps get the false positive corrected.
- You can skip the launcher entirely and run `uv run python main.py` instead — the result is identical.

**Q: Startup fails with `OSError: [WinError -1073741795] Windows Error 0xc000001d`.**
- This came from the old CPU backend: the `llama-cpp-python` CPU wheel is a single build that executes unsupported instructions on some CPUs and crashes.
- As of v1.5.0 the CPU backend uses llama.cpp's official prebuilt binary, which ships 14 instruction-set variants and selects one automatically at startup.
- If you upgraded from an older version, re-run `setup_windows.bat` or `./setup_unix.sh` so the backend is replaced.

**Q: What should I attach when reporting a problem?**
- `outputs/run.log` — click the "執行紀錄" button in the GUI to open it.
- It records every translation result, every rejected translation with its reason, and full exception tracebacks for a single run, with no line limit.
- It is cleared each time the app starts, so it only ever contains the most recent run.

**Q: I don't like how a particular term was translated. Can I change it?**
- Click "自訂用語…" in the GUI and add an English term with your preferred translation. Custom terms take priority over the built-in official glossary, so they override official names.
- Leaving the translation blank disables that term and lets the model translate it freely.
- Settings are stored in `outputs/custom_glossary.json`, which auto-update never clears.

**Q: Scan finds 0 translatable files.**
- Make sure you selected the correct folder. It should be the instance root containing `mods/` or `config/`.
- If the modpack was already translated, all strings will be skipped.
- Check that at least one translation option is checked.

**Q: Local model server fails to start.**
- Re-run `setup_windows.bat` or `./setup_unix.sh`.
- Close the app before re-running setup. A running server can lock backend files on Windows.
- For NVIDIA CUDA backend, install CUDA Toolkit 12.4 or newer. cuDNN is not required.
- If the log only shows tensor loading or a `VirtualLock`/`mlock` warning, the model is usually still loading or an old backend command enabled memory locking. Re-run setup; generated Python backends disable memory locking by default.
- Check `.runtime/llama-server.log` for the real server error.

**Q: Model files are missing.**
- Verify the LoRA adapter path in the GUI or `configs/model.yaml`.
- If the base model download fails, download it manually from HuggingFace, set `base_gguf_path` in `configs/model.yaml`, then run setup again.

**Q: GPU is not being used / translation is slow.**
- Run setup again and check the selected backend in `.runtime/backend.json`.
- Make sure `n_gpu_layers` is set to `-1` in `configs/model.yaml` before running setup.
- AMD acceleration uses AMD's prebuilt `llama.cpp` binaries on supported Windows/Linux systems.

**Q: Some strings fall back to English.**
- This happens when the model output fails validation: a missing placeholder (e.g., `{0}` dropped from the translation), a higher format-argument count than the source (an extra `%s` makes the game throw when it reads that string), missing structural markup, collapsed line breaks, or no Chinese at all in the output.
- Increase the retry count in the GUI or with `--retry N` on the CLI.
- Every rejection reason is written to `outputs/run.log`; failed items are additionally collected in `Failed Items/`.
- The GUI lists failed items after translation so you can fill them in by hand, writing the results straight back into the modpack.

**Q: Where is the translated output?**
- **Mod JARs**: Translations are injected directly into the mod `.jar` files. Original JARs are backed up to `mods_bak/`.
- **Quest configs**: A new language file (e.g., `zh_tw.json`) is written next to the English source. Originals are backed up to `quests_bak/`.
- **Translation cache**: Stored at `outputs/translation_cache.json` for reuse on subsequent runs.
