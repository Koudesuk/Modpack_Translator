# Minecraft模組包翻譯器 v1.5.4

**Language / 語言：** [English](README.md) | 繁體中文

[![下載次數](https://img.shields.io/github/downloads/Koudesuk/Modpack_Translator/total?label=%E4%B8%8B%E8%BC%89%E6%AC%A1%E6%95%B8&color=brightgreen)](https://github.com/Koudesuk/Modpack_Translator/releases)
[![Ko-fi](https://img.shields.io/badge/贊助我-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/koudesuk)

---

自動將 Minecraft 模組包從英文（`en_us`）翻譯為繁體中文（`zh_tw`）的工具，涵蓋語言檔、任務書與遊戲內指南書，底層使用 GGUF 格式的微調模型搭配 LoRA 適配器。提供圖形化介面（GUI）與命令列介面（CLI）。

---

## v1.5.4 更新內容

| 項目 | 說明 |
|---|---|
| **失敗項目批次翻譯** | 失敗項目視窗可以把整份清單匯出成 JSON，翻好再讀回來。線上大模型必須遵守的規則（保留佔位符、保留行數、沿用官方譯名、不該翻的留空）已經寫在檔案裡，GPT、Claude、Grok、Gemini 都只要填每一筆的 `zh_tw` 欄位就好。幾百條失敗項目不再只能一條一條打字 |
| **匯入的譯文要驗過才算數** | 匯入只把譯文填進表格，沒按「套用」之前模組包一個位元組都不會動。匯入的列會標底色；沒通過程式自己那套檢查的（佔位符掉了、換行被壓成一行、格式引數變多）另外標警示色，滑鼠移過去看得到原因，也可以勾「只顯示需確認的項目」單獨檢視。線上模型最愛把多行併成一行，那種東西套下去任務書與設定面板整個爆版 |
| **匯入比對容錯** | 條目依 `id` → 來源＋鍵 → 鍵 → 英文原文由嚴到寬對回列號，順序被打亂或只翻了一部分也對得回去；裸陣列、模型偷懶回的 `{鍵: 譯文}` 對照表同樣收。完全對不上的條目會回報，不會無聲吃掉 |

---

## 系統需求

| 需求 | 版本 | 說明 |
|---|---|---|
| [Git](https://git-scm.com/downloads) | 任意版本 | clone 倉庫所需 |
| [Git LFS](https://git-lfs.com) | 任意版本 | **必須安裝** — LoRA 適配器（約 66 MB）透過 LFS 儲存 |
| [uv](https://docs.astral.sh/uv/) | 最新版 | 安裝並管理本專案使用的 Python runtime |
| GPU（可選） | NVIDIA CUDA 或支援的 AMD ROCm | 強烈建議；純 CPU 可用但速度非常慢 |
| [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) | 12.4 或更新版本 | **NVIDIA CUDA 後端必須安裝**；只有 Game Ready/Studio Driver 不夠。cuDNN 不需要 |
| 可用磁碟空間 | 約 6 GB | 適配器 ~66 MB（LFS）＋基礎模型 ~5 GB（自動下載） |

---

## 安裝步驟

### 第一步 — 安裝 uv

`uv` 是本專案使用的 Python 套件管理器，在您的電腦上安裝一次即可：

**Windows（PowerShell）：**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux：**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 第二步 — 安裝 Git LFS

LoRA 適配器透過 Git LFS 儲存，**clone 前必須先安裝 Git LFS**：

**Windows：** 從 [git-lfs.com](https://git-lfs.com) 下載安裝程式，或執行：
```powershell
winget install GitHub.GitLFS
```

**macOS：**
```bash
brew install git-lfs
```

**Linux（Ubuntu/Debian）：**
```bash
sudo apt install git-lfs
```

安裝完成後，為您的帳號啟用一次：
```bash
git lfs install
```

### 第三步 — Clone 倉庫

```bash
git clone <repository-url>
cd Modpack_Translator
```

Git LFS 會在 clone 時自動下載適配器。請確認檔案大小約為 **66 MB**（若只有幾百位元組，代表只下載到指標檔）：

```bash
# macOS/Linux
ls -lh adapter/minecraft_translator_gemma4_e4b_lora.gguf

# Windows
dir adapter\minecraft_translator_gemma4_e4b_lora.gguf

# 若檔案太小（指標檔），請執行：
git lfs pull
```

### NVIDIA GPU 使用者 — 安裝 CUDA Toolkit

如果要使用 CUDA 後端，請在執行初始化前先安裝 **CUDA Toolkit 12.4 或更新版本**：

```text
https://developer.nvidia.com/cuda-downloads
```

NVIDIA Game Ready/Studio Driver 只提供驅動程式函式庫；本專案使用的 CUDA `llama-cpp-python` wheel 還需要 CUDA runtime/cuBLAS 函式庫，例如 Windows 上的 `cudart64_12.dll` 與 `cublas64_12.dll`。初始化腳本會檢查這些函式庫，缺少時會印出明確錯誤訊息。

cuDNN **不需要**安裝。

### 第四步 — 執行後端初始化

初始化腳本會安裝 uv 管理的 CPython 3.12、建立 `.venv/`、偵測硬體、安裝對應的本機推理後端、下載基礎模型，並寫入 `.runtime/backend.json`。使用者不需要另外安裝 Python。

**Windows：**
```bat
setup_windows.bat
```

初始化完成後，Windows 會在專案資料夾建立版本化 launcher，例如 `模組包翻譯器v1.5.4.exe`。之後直接雙擊它即可啟動程式，不需要開終端機手動輸入命令。若 launcher 遺失，請重新執行 setup，或手動建立：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_launcher.ps1
```

**macOS / Linux：**
```bash
./setup_unix.sh
```

硬體會自動選擇：

| 硬體 | 後端 |
|---|---|
| NVIDIA | CUDA `llama-cpp-python[server]` wheel |
| AMD Windows/Linux | AMD 預先編譯的 `llama.cpp` / `llama-server` binary |
| 僅 CPU | llama.cpp 官方預編譯的 `llama-server` binary（Windows/Linux） |

CPU 後端在 v1.5.0 從 `llama-cpp-python[server]` 的 CPU wheel 換成 llama.cpp 官方 binary。該 wheel 的 `ggml-cpu.dll` 是單一建置，在部分 CPU 上會執行到不支援的指令而崩潰（`0xc000001d`）；官方 binary 內含 14 種指令集變體，啟動時依實際 CPU 挑選。

重新執行初始化前請先關閉翻譯器。Windows 上正在執行的本機模型服務會鎖住 `.dll` 檔案，導致後端替換失敗。

---

## 後端初始化覆寫

一般使用者用自動偵測即可。若要強制指定後端：

**Windows：**
```bat
setup_windows.bat --backend cuda
setup_windows.bat --backend amd
setup_windows.bat --backend cpu
```

**macOS / Linux：**
```bash
./setup_unix.sh --backend cuda
./setup_unix.sh --backend amd
./setup_unix.sh --backend cpu
```

程式會透過 OpenAI-compatible 本機 HTTP API 呼叫模型。若您自行啟動相容 server，也可以設定 `LLAMA_SERVER_URL`，例如 `http://127.0.0.1:8080/v1`。

若修改了 `configs/model.yaml` 中的基礎模型、LoRA、context size、GPU 層數或後端類型，請重新執行初始化腳本，讓 `.runtime/backend.json` 重新產生。

---

## 設定檔說明

### `configs/model.yaml`

```yaml
model:
  base_gguf_path: ""                              # 留空自動下載
  base_hf_repo: "unsloth/gemma-4-E4B-it-GGUF"
  base_hf_filename: "gemma-4-E4B-it-Q4_K_M.gguf"
  lora_gguf_path: "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
  lora_scale: 1.0
  n_gpu_layers: -1     # -1 = 全部卸載至 GPU，0 = 僅 CPU
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

包含語言代碼、顯示名稱及翻譯模型的系統提示詞。除非要新增其他目標語言，否則請勿修改此檔案。

---

## GUI 使用方法

啟動圖形化介面：

```bash
uv run python main.py
```

Windows 使用者也可以直接雙擊版本化 launcher，例如 `模組包翻譯器v1.5.4.exe`；它會先檢查是否已完成 setup，再在背景執行 `uv run python main.py`，launcher 錯誤會寫到 `.runtime/launcher.log`。

啟動時，程式會在背景檢查最新 GitHub Release。有新版 release package 時才顯示更新視窗；沒有更新時不顯示任何訊息。自動更新會下載 release ZIP，若有 SHA256 檔會先驗證，接著套用新版原始碼、移除舊 `.venv` 與過期的本機後端 runtime 檔案、重新執行 setup，完成後再啟動新版程式。

**操作步驟：**

1. **模組包資料夾** — 點擊「瀏覽…」選擇模組包實例目錄（包含 `mods/`、`config/` 的資料夾）。
2. **模型設定** — 一般安裝流程已由初始化腳本設定本機模型服務。只有在重新產生後端設定時才需要修改這些欄位。
3. **選項** — 勾選「翻譯模組 (.jar)」或「翻譯任務書」，並設定重試次數（預設 3）。
4. **掃描** — 點擊「🔍 掃描模組包」，掃描結果面板顯示目標數量與樣本字串。
5. **翻譯** — 點擊「▶ 開始翻譯」，進度條顯示百分比、速度、已用時間及預計剩餘時間。
6. **完成** — 翻譯完成後，進度條變綠，按鈕顯示「✓ 完成」。

**其他按鈕：**

| 按鈕 | 用途 |
|---|---|
| **自訂用語…** | 指定英文詞的固定譯法，詳見[用語庫與自訂用語](#用語庫與自訂用語) |
| **失敗項目…** | 重開手動補譯視窗，詳見[失敗項目與手動補譯](#失敗項目與手動補譯) |
| **執行紀錄** | 開啟 `outputs/run.log`，詳見[執行紀錄](#執行紀錄) |

### 用語庫與自訂用語

程式內建 **1,945 條 Minecraft 官方繁中譯名**，同時餵給三個環節，譯名才會前後一致：

- 原文整串就是某個詞條時直接取官方譯名，不經模型。這是本地模型最大的一筆省時，也保證原版名稱百分之百正確。
- 翻長句時把命中的詞條附進 prompt，讓模型沿用官方用語。
- 模型仍留英文原詞的，事後強制替換。

prompt 注入的比對不分大小寫，所以原文寫 `saturation value` 也命中得到 `Saturation` 詞條；事後替換維持大小寫敏感，以免動壞程式識別字。

「自訂用語…」用來指定自己要的譯法，優先序為 **自訂 > 模組名 > 官方用語**，可以直接覆蓋官方譯名；譯名留空代表停用該詞條，讓模型自由翻譯。設定存在 `outputs/custom_glossary.json`，自動更新不會清掉，CLI 也一樣會套用。

### 失敗項目與手動補譯

重試後仍無法翻譯的字串會寫入 `Failed Items/`，並依「難在哪裡」分成 `natural_text`、`markup_or_book_text`、`short_fragments`、`copy_or_skip_noise` 四類。報告檔名短且不重複，程式或模組包放在很深的資料夾裡也能正常輸出，完整來源位置寫在報告內容裡。若無失敗項目，此資料夾不會被建立。

翻譯結束時若有失敗項目，程式會列出所有字串讓您逐條手動補譯，按「套用」即直接寫回模組包；補上的譯文存於 `outputs/manual_translations.json`，下次翻譯會優先沿用、不會被模型蓋掉。只要沒關程式、也沒換模組包資料夾，就能用「失敗項目…」按鈕再開。

條目太多時別硬打，用視窗右上角的匯出／匯入：

1. **匯出失敗項目** 把整份清單存成 JSON（預設資料夾 `Failed Items/`）。檔案裡附了模型必須遵守的規則：`id`、`source`、`key`、`en_us` 原樣保留；`%s`、`%1$s`、`{0}`、`\n`、`§a`、`$(...)`、`[文字](連結)`、`<tag>` 這類佔位符與控制碼照原樣保留；原文幾行、譯文就幾行；專有名詞沿用 Minecraft 官方繁中譯名；不該翻的把 `zh_tw` 留空。
2. 把整個檔案交給任一線上大模型（GPT、Claude、Grok、Gemini 等），請它只填 `zh_tw` 欄位。
3. **匯入翻譯完成之失敗項目** 把結果讀回表格。此時模組包還沒被動到——檢查過（必要時直接在表格裡改）之後，按「套用」才會寫回去。

**原始檔案備份位置：**
- 模組 JAR → `mods_bak/`
- 任務設定 → `quests_bak/`
- 資源包 → `resourcepacks_bak/`
- 光影包 → `shaderpacks_bak/`
- 就地改寫的資料包檔案 → `data_bak/`

> 手動補譯是 GUI 專屬功能；CLI 不會讀取 `outputs/manual_translations.json`。自訂用語（`outputs/custom_glossary.json`）則兩種介面都會套用。

### 執行紀錄

`outputs/run.log` 完整記錄單次執行的每一條翻譯結果與每一次拒絕原因，不設行數上限。真正寫壞、讀不進來的檔案也會在這裡留一行，標明檔案與解析錯誤位置，不會無聲跳過。每次開啟程式時清空，所以裡面永遠只有最近一次執行的內容。回報問題時請附上這個檔案。

---

## CLI 使用方法

```bash
uv run python scripts/translate_modpack.py --modpack <路徑> [選項]
```

### 參數說明

| 參數 | 預設值 | 說明 |
|---|---|---|
| `--modpack PATH` | （必填） | 模組包實例資料夾路徑 |
| `--language FILE` | `configs/languages/zh_tw.yaml` | 語言設定檔 |
| `--model-config FILE` | `configs/model.yaml` | 模型設定檔 |
| `--paths-config FILE` | `configs/paths.yaml` | 路徑設定檔 |
| `--dry-run` | false | 僅掃描，不執行翻譯 |
| `--skip-mods` | false | 略過模組 JAR 掃描 |
| `--skip-quests` | false | 略過任務設定掃描 |
| `--max-steps N` | -1（全部） | 限制翻譯前 N 個目標（測試用） |
| `--retry N` | 0 | 後處理驗證失敗時每個字串的重試次數 |

### 使用範例

```bash
# 預覽將要翻譯的內容（不實際翻譯）
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# 完整翻譯，失敗時最多重試 3 次
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# 僅翻譯任務書
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
```

CLI 跑的是與 GUI 完全相同的流程：

- 官方用語庫與 `outputs/custom_glossary.json` 的套用方式一模一樣。
- 輸出驗證也一樣：佔位符、格式引數數量、結構標記、換行、漏譯子句。被拒絕的字串最多重試 `--retry N` 次，仍失敗就回退原文並收進 `Failed Items/`。
- 執行紀錄共用：每次執行會清空並重寫 `outputs/run.log`。
- 手動補譯是 GUI 專屬：CLI 不會跳出補譯視窗，也不會讀 `outputs/manual_translations.json`。要補譯請開 GUI 跑一次（翻好的會走快取，很快）。

---

## 支援的檔案格式

| 格式代碼 | 副檔名 | 說明 |
|---|---|---|
| `json_lang` | `.json` | 標準模組語言檔（`assets/<mod>/lang/en_us.json`），資源包內的覆蓋檔同格式 |
| `legacy_lang` | `.lang` | 1.13 以前的舊式語言檔（`en_us.lang`），光影包的 `shaders/lang/` 同格式 |
| `patchouli_json` | `.json` | Patchouli 導覽書頁面 |
| `guideme_md` | `.md` | GuideME 遊戲內指南頁面（AE2 遊戲內按 G、Powah 等），JSX 元件標籤與連結原樣保留 |
| `citadel_txt` | `.txt` | Citadel 圖鑑書頁面（Alex's Mobs、Alex's Caves 等）。中文沒有空格無法自動斷行，程式會依模組官方譯本的慣例自行折行，避免文字衝出書頁 |
| `ftbq_snbt` | `.snbt` | FTB Quests 語言檔 |
| `ftbq_inline_snbt` | `.snbt` | FTB Quests 任務檔中的直接文字欄位 |
| `heracles_snbt` | `.snbt` | Heracles（Odyssey Quests）語言檔 |
| `heracles_inline_snbt` | `.snbt` | Heracles 直接文字欄位 |
| `bq_lang` | `.lang` | Better Questing 語言格式（1.12） |
| `kubejs_json` | `.json` | KubeJS 腳本翻譯檔 |
| `apoli_power` | `.json` | Origins／Apoli 能力與起源定義（`data/<ns>/powers`、`data/<ns>/origins`）中直接寫成字面文字的 `name`／`description`。條件、動作、修飾符子樹一律不碰——那裡的 `name` 是傷害類型之類的 ID，翻了能力會安靜失效 |

### 掃描範圍

| 位置 | 處理方式 |
|---|---|
| `mods/*.jar` | 譯文注入回 jar，原檔備份至 `mods_bak/` |
| `config/`、`kubejs/` | 就地寫入，原檔備份至 `quests_bak/` |
| `datapacks/`、`config/openloader/`、`global_packs/` | 資料夾型資料包的能力定義就地寫入，原檔備份至 `data_bak/`。zip 型資料包不處理 |
| `resourcepacks/` | zip 包注入回 zip、資料夾包就地寫入，原檔備份至 `resourcepacks_bak/`。這些包會覆蓋或新增 GUI 文字，那些鍵在模組 jar 裡並不存在，不掃就永遠是英文 |
| `shaderpacks/` | 就地寫入資料夾型光影包的 `shaders/lang/`，原檔備份至 `shaderpacks_bak/`。zip 光影包目前不處理 |

改寫時另外處理兩件以前會產出壞檔的事：

- 少數模組 jar 內含同名重複檔案，改寫時以最後一筆為準去重，不再產出遊戲讀不動的 jar。
- JSON 的尾逗號一律容忍（遊戲的 GSON 讀得動，Python 的 `json` 不收）。真正寫壞的檔案會在 `outputs/run.log` 留一行，標明檔案與解析錯誤位置。

---

## 支援的 Minecraft 版本

1.16.2、1.16.5、1.17、1.17.1、1.18、1.18.2、1.19、1.19.2、1.19.4、1.20、1.20.1、1.20.2、1.20.4、1.20.6、1.21、1.21.1、1.21.3、1.21.4、1.21.5

---

## 輸出結構

```
<模組包資料夾>/
├── mods/                ← 翻譯後的 JAR（原位修改）
├── mods_bak/            ← 原始 JAR 備份
├── config/              ← 翻譯後的任務設定（原位修改）
├── quests_bak/          ← 原始任務設定備份
├── resourcepacks/       ← 翻譯後的資源包（原位修改）
├── resourcepacks_bak/   ← 原始資源包備份
├── shaderpacks/         ← 翻譯後的光影包（原位修改）
└── shaderpacks_bak/     ← 原始光影包備份

<專案根目錄>/
├── outputs/
│   ├── translation_cache.json     ← 翻譯快取，再次執行時重複使用
│   ├── manual_translations.json   ← 手動補譯的譯文，下次翻譯優先沿用
│   ├── custom_glossary.json       ← 自訂用語（由 GUI「自訂用語…」寫入）
│   └── run.log                    ← 本次執行的完整紀錄，開啟程式時清空
└── Failed Items/                  ← 重試後仍失敗的字串，每次翻譯重寫
    ├── natural_text/              ← 一般句子，最值得手動補譯的一類
    ├── markup_or_book_text/       ← 指南書頁面與標記密集的字串
    ├── short_fragments/           ← 極短字串與只有格式引數的內容
    └── copy_or_skip_noise/        ← 識別字與本來就不必翻譯的字串
```

`outputs/` 底下的檔案都不會被自動更新覆蓋。

---

## 常見問題

**Q：ZIP 使用者要怎麼更新？**
- 開啟程式即可。如果 GitHub Release 有新版，更新視窗會出現，按 **自動更新**。
- updater 會保留使用者輸出與備份，但會重建 `.venv` 和本機後端設定，避免依賴衝突。
- Release ZIP 由 GitHub Actions 根據 `v1.5.4` 這類 tag 自動產生，且只接受本倉庫 `main` 分支上的 tag。

**Q：Windows Defender 說 launcher 是病毒，擋著不讓執行。**
- 這是誤判。launcher 是一支未經數位簽章的小型執行檔，防毒軟體的機器學習模型有時會把這類檔案標記為威脅（v1.4.1 曾被誤判為 `Trojan:Win32/Suschil!rfn`）。
- v1.5.0 已針對誤判成因調整：launcher 不再透過 `cmd.exe` 啟動程式，並加上完整的版本與發行者中繼資料。
- 若仍被攔截：先用 Release 頁面附的 `.zip.sha256` 確認檔案沒被竄改，再到 Windows 安全性 → 病毒與威脅防護 → 保護歷程記錄選擇「允許」。也歡迎回報給 [Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission) 以便修正誤判。
- 不想用 launcher 的話，直接執行 `uv run python main.py` 效果完全相同。

**Q：啟動時出現 `OSError: [WinError -1073741795] Windows Error 0xc000001d`。**
- 這是舊版 CPU 後端的問題：`llama-cpp-python` 的 CPU wheel 是單一建置，在部分 CPU 上會執行到不支援的指令而崩潰。
- v1.5.0 起 CPU 後端改用 llama.cpp 官方預編譯 binary，內含 14 種指令集變體、啟動時自動挑選。
- 從舊版升上來的使用者請重新執行 `setup_windows.bat` 或 `./setup_unix.sh`，讓後端換成新的。

**Q：我要回報問題，該附上什麼？**
- `outputs/run.log`，GUI 裡點「執行紀錄」按鈕即可開啟。
- 該檔案記錄單次執行的每一條翻譯結果、每一次被拒絕的譯文與原因、以及完整的例外堆疊，沒有行數上限。
- 每次開啟程式時會清空，所以裡面永遠只有最近一次執行的內容。

**Q：某個詞的譯名我不滿意，怎麼改？**
- GUI 點「自訂用語…」，加上「英文原詞 → 繁中譯名」即可。自訂用語優先序高於內建的官方用語，可以直接覆蓋官方譯名。
- 譯名留空代表停用該詞條，讓模型自由翻譯。
- 設定存在 `outputs/custom_glossary.json`，自動更新不會清掉。

**Q：掃描找不到任何可翻譯的檔案。**
- 確認選擇的是正確的資料夾，應包含 `mods/` 或 `config/` 子資料夾。
- 若模組包已完全翻譯，所有字串都會被略過。
- 確認至少勾選了一個翻譯選項。

**Q：本機模型服務啟動失敗。**
- 重新執行 `setup_windows.bat` 或 `./setup_unix.sh`。
- 重新初始化前請先關閉翻譯器。Windows 上正在執行的 server 可能鎖住後端檔案。
- NVIDIA CUDA 後端需要 CUDA Toolkit 12.4 或更新版本。cuDNN 不需要。
- 如果 log 只顯示 tensor loading 或 `VirtualLock`/`mlock` warning，通常是模型仍在載入，或舊的後端命令啟用了 memory locking。請重新執行 setup；新產生的 Python 後端預設會關閉 memory locking。
- 查看 `.runtime/llama-server.log`，裡面會有真正的 server 錯誤。

**Q：模型檔案遺失。**
- 確認 LoRA 適配器路徑正確（GUI 設定或 `configs/model.yaml`）。
- 若基礎模型下載失敗，可手動從 HuggingFace 下載，在 `configs/model.yaml` 填入 `base_gguf_path` 後重新執行初始化。

**Q：GPU 沒有被使用 / 翻譯速度很慢。**
- 重新執行初始化，並檢查 `.runtime/backend.json` 內選到的後端。
- 初始化前確認 `configs/model.yaml` 的 `n_gpu_layers` 設為 `-1`（全部層卸載至 GPU）。
- AMD 加速使用 AMD 官方預編譯的 `llama.cpp` binary，支援範圍以 Windows/Linux 為主。

**Q：部分字串回退為英文。**
- 這發生在模型輸出未通過驗證時：
  - 佔位符遺失（例如翻譯後少了 `{0}`）；
  - 格式引數變多（譯文多用了一個 `%s`，遊戲讀到會直接丟例外），模組原本就帶錯的既有譯文一樣會被擋下重寫；
  - 結構標記遺失，或換行被壓縮；
  - 漏譯：多子句原文若譯出的內容量明顯不足，會被判定整個子句被吃掉而退回原文。以 144,580 條模組出貨的既有中英對照實測，誤判率 0.022%；
  - 譯文裡根本沒有中文。
- 原文是單行時，模型自己加的換行會直接移除而不是判失敗；命中舊快取時也就地修正。
- 在 GUI 中增加重試次數，或在 CLI 使用 `--retry N` 參數。
- 每一次拒絕的原因都寫在 `outputs/run.log`，失敗項目另外整理於 `Failed Items/`。
- GUI 會在翻譯結束後把失敗項目列出來讓您手動補譯，也可以匯出給線上大模型翻好再匯入。

**Q：翻譯結果輸出在哪裡？**
- **模組 JAR**：翻譯結果直接注入模組 `.jar` 檔案，原始 JAR 備份至 `mods_bak/`。
- **任務設定**：在英文源檔旁邊產生新的語言檔（如 `zh_tw.json`），原始檔備份至 `quests_bak/`。
- **翻譯快取**：儲存於 `outputs/translation_cache.json`，再次執行時自動重複使用。
