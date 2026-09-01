# Minecraft Modpack Translator v1.5.4

**Language / 語言：** English | [繁體中文](README_zh.md)

[![Downloads](https://img.shields.io/github/downloads/Koudesuk/Modpack_Translator/total?label=downloads&color=brightgreen)](https://github.com/Koudesuk/Modpack_Translator/releases)
[![Ko-fi](https://img.shields.io/badge/Support%20me%20on-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/koudesuk)

---

A tool that automatically translates Minecraft modpacks from English (`en_us`) to Traditional Chinese (`zh_tw`) — language files, quest books, and in-game guidebooks — using a fine-tuned GGUF model with LoRA adaptation. Supports both a graphical user interface and a command-line interface.

---

## What's New in v1.5.4

| Feature | Description |
|---|---|
| **Batch translation of failed items** | The failed-items dialog can now export the whole list to a JSON file and read the result back. The rules an online model has to follow (keep placeholders, keep the line count, use official Minecraft terminology, leave untranslatable entries empty) are written into the file itself, so any online model — GPT, Claude, Grok, Gemini — only has to fill in the `zh_tw` field of each entry. Typing a few hundred corrections by hand is no longer the only option |
| **Imported translations are checked, not trusted** | An import only fills the table; nothing reaches the modpack until you press "套用". Imported rows are tinted, and rows that fail the same validation the translator itself uses — dropped placeholders, collapsed line breaks, extra format arguments — get a warning colour, carry the reason in a tooltip, and can be isolated with "只顯示需確認的項目". Online models like to merge a multi-line string into one line, and that breaks the layout of quest and config panels |
| **Forgiving import matching** | Entries are matched back by `id`, then source + key, then key, then the English text, so a reordered or partially returned file still lands on the right rows. A bare array or a plain `{key: translation}` map is accepted too. Anything that matches nothing is reported instead of being silently dropped |

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

After setup, Windows builds a versioned launcher such as `模組包翻譯器v1.5.4.exe` in the project folder. Double-click it to start the app without opening a terminal. If the launcher is missing, run setup again or build it manually:

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

On Windows, users can also double-click the versioned launcher EXE, such as `模組包翻譯器v1.5.4.exe`. It checks that setup has been run, launches `uv run python main.py` in the background, and writes launcher errors to `.runtime/launcher.log`.

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
| **自訂用語…** (Custom terms) | Pin a fixed translation for an English term — see [Glossary and custom terms](#glossary-and-custom-terms) |
| **失敗項目…** (Failed items) | Reopen the manual correction dialog — see [Failed items and manual correction](#failed-items-and-manual-correction) |
| **執行紀錄** (Run log) | Open `outputs/run.log` — see [Run log](#run-log) |

### Glossary and custom terms

The app ships **1,945 official Traditional Chinese Minecraft terms**, used at three points so the result stays consistent:

- When the whole source string matches a term, the translation is taken directly without invoking the model. This is the single biggest time saving, and it makes every vanilla name exactly right.
- For longer sentences, matching terms are appended to the prompt so the model keeps the official wording.
- Any English term the model leaves behind is substituted afterwards.

Term matching for prompt hints is case-insensitive, so a source writing `saturation value` still receives the `Saturation` entry; the final substitution stays case-sensitive so identifiers are never damaged.

"自訂用語…" pins your own translation for an English term. Priority is **custom > mod name > official glossary**, so a custom entry can override an official name; leaving a translation blank disables that term and lets the model translate it freely. Custom terms are stored in `outputs/custom_glossary.json`, which auto-update never clears, and they apply to the CLI as well.

### Failed items and manual correction

Strings that could not be translated after all retries are written to `Failed Items/`, grouped by why they are hard (`natural_text`, `markup_or_book_text`, `short_fragments`, `copy_or_skip_noise`). Report filenames are short and unique, so reports work even when the app or the modpack sits in a deeply nested folder; the full source target is named inside each report. If no items fail, the folder is not created.

When translation finishes with failures, the app lists every one of them so you can fill in translations by hand; clicking "套用" writes them straight back into the modpack. Corrections are stored in `outputs/manual_translations.json` and take priority on later runs, so the model never overwrites them. The "失敗項目…" button reopens the dialog as long as the app is still open and the modpack folder has not changed.

For long lists, use the export/import buttons at the top right of the dialog instead of typing:

1. **匯出失敗項目** writes the whole list to JSON (default folder: `Failed Items/`). The file carries the rules the model has to follow: keep `id`, `source`, `key` and `en_us` untouched; keep placeholders such as `%s`, `%1$s`, `{0}`, `\n`, `§a`, `$(...)`, `[text](link)` and `<tag>` intact; keep the same number of lines; use official Minecraft Traditional Chinese names; leave `zh_tw` empty for anything that should not be translated.
2. Hand the file to any online model (GPT, Claude, Grok, Gemini …) and ask it to fill in `zh_tw` only.
3. **匯入翻譯完成之失敗項目** reads the result back into the table. Nothing is written to the modpack at this point — review the rows, edit them directly in the table where needed, then press "套用".

**Original files are always backed up:**
- Mod JARs → `mods_bak/`
- Quest configs → `quests_bak/`
- Resource packs → `resourcepacks_bak/`
- Shader packs → `shaderpacks_bak/`
- Data pack files edited in place → `data_bak/`

> Manual correction is GUI-only; the CLI does not read `outputs/manual_translations.json`. Custom glossary terms (`outputs/custom_glossary.json`) apply to both interfaces.

### Run log

`outputs/run.log` records every translation result and every rejection reason for a single run, with no line limit. Files that are genuinely broken are named there together with the parse error instead of being skipped in silence. The log is cleared each time the app starts, so it always describes the most recent run only. Attach this file when reporting an issue.

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

The CLI runs the same pipeline as the GUI:

- The official glossary and `outputs/custom_glossary.json` are applied exactly the same way.
- Output validation is identical — placeholders, format-argument count, structural markup, line breaks, dropped clauses. Rejected strings are retried up to `--retry N` times, then fall back to English and are collected in `Failed Items/`.
- The run log is shared: every run clears and rewrites `outputs/run.log`.
- Manual correction is GUI-only. The CLI neither shows the correction dialog nor reads `outputs/manual_translations.json`, so run the GUI once if you want to fix the failed items.

---

## Supported File Formats

| Format Key | Extension | Description |
|---|---|---|
| `json_lang` | `.json` | Standard mod language file (`assets/<mod>/lang/en_us.json`); resource pack overrides use the same format |
| `legacy_lang` | `.lang` | Pre-1.13 mod language file (`en_us.lang`); shader pack `shaders/lang/` uses the same format |
| `patchouli_json` | `.json` | Patchouli guidebook pages |
| `guideme_md` | `.md` | GuideME in-game guide pages (AE2 — press G in game — Powah, etc.). JSX component tags and links are preserved verbatim |
| `citadel_txt` | `.txt` | Citadel guidebook pages (Alex's Mobs, Alex's Caves, etc.). Chinese has no spaces for the renderer to break on, so the output is wrapped following the convention of each mod's own official translation, keeping text inside the page |
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
| `resourcepacks/` | ZIP packs are injected back into the ZIP, folder packs are written in place; originals backed up to `resourcepacks_bak/`. These packs add or override GUI text with keys that exist in no mod JAR, so without scanning them those strings stay English forever |
| `shaderpacks/` | `shaders/lang/` of folder-based packs is written in place; originals backed up to `shaderpacks_bak/`. ZIP shader packs are not handled |

Two things that used to produce broken output are handled while rewriting:

- A few mod JARs contain duplicate entries for the same path. Rewrites de-duplicate them (last entry wins) instead of producing a JAR the game cannot read.
- Trailing commas are accepted in JSON everywhere — the game's GSON reader tolerates them, Python's `json` does not. Files that are genuinely broken get a line in `outputs/run.log` naming the file and the parse error.

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
└── Failed Items/                  ← strings that failed after all retries; rewritten every run
    ├── natural_text/              ← ordinary sentences — the ones worth correcting by hand
    ├── markup_or_book_text/       ← guidebook pages and markup-heavy strings
    ├── short_fragments/           ← very short strings and bare format arguments
    └── copy_or_skip_noise/        ← identifiers and strings that need no translation
```

Nothing under `outputs/` is ever overwritten by auto-update.

---

## FAQ

**Q: How do ZIP users update the app?**
- Open the app. If a newer GitHub Release exists, click **Auto update** in the update dialog.
- The updater preserves user outputs and backups, but rebuilds `.venv` and the local backend setup to avoid dependency conflicts.
- Release ZIPs are generated by GitHub Actions from tags such as `v1.5.4`, and only from tags on this repository's `main` branch.

**Q: Windows Defender flags the launcher as malware and blocks it.**
- This is a false positive. The launcher is a small, unsigned executable, and antivirus machine-learning models sometimes flag such files (v1.4.1 was misidentified as `Trojan:Win32/Suschil!rfn`).
- v1.5.0 addresses the causes: the launcher no longer starts the app through `cmd.exe`, and it carries full version and publisher metadata.
- If it is still blocked: verify the download against the `.zip.sha256` file attached to the Release page, then go to Windows Security → Virus & threat protection → Protection history and choose "Allow". Reporting the file to [Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission) helps get the false positive corrected.
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
- This happens when the model output fails validation:
  - a missing placeholder (e.g. `{0}` dropped from the translation);
  - a higher format-argument count than the source — an extra `%s` makes the game throw when it reads that string, so this is rejected even for translations the mod shipped itself;
  - missing structural markup, or collapsed line breaks;
  - a dropped clause: output drastically shorter than a multi-clause source is rejected instead of passing every structural check. Measured against 144,580 shipped en→zh pairs, the false-positive rate is 0.022%;
  - no Chinese at all in the output.
- Line breaks the model adds on its own are removed rather than rejected when the source is a single line; cached entries are repaired in place on reuse.
- Increase the retry count in the GUI or with `--retry N` on the CLI.
- Every rejection reason is written to `outputs/run.log`; failed items are additionally collected in `Failed Items/`.
- The GUI lists failed items after translation so you can fill them in by hand, or export them for an online model and import the result back.

**Q: Where is the translated output?**
- **Mod JARs**: Translations are injected directly into the mod `.jar` files. Original JARs are backed up to `mods_bak/`.
- **Quest configs**: A new language file (e.g., `zh_tw.json`) is written next to the English source. Originals are backed up to `quests_bak/`.
- **Translation cache**: Stored at `outputs/translation_cache.json` for reuse on subsequent runs.
