# Typeless Nano

*[English](README.md) | 繁體中文*

Windows 本機英文語音輸入 MVP。按一下 Right Alt 開始錄音，再按一下停止，
以本機 Qwen3-ASR 或 Whisper 轉錄，最後將結果輸入原本的 active window。

錄音時會在目前螢幕底部中央顯示一個不搶焦點的浮動膠囊：左側 `×` 取消，
中央 waveform 顯示錄音中，右側 `✓` 可完成錄音。停止後會縮成
`Thinking` 狀態，直到轉錄與輸入完成才消失。

進入錄音時會播放柔和的上升雙音，離開錄音時播放同一組下降雙音，取消則播放
單一低音，接近 Discord 的 join／leave 感覺。提示音是啟動時合成的 sine tone，
以 Python 內建 `winsound` 播放，不需要額外 dependency，也不附帶任何第三方
音檔。開始提示音期間捕捉到的 microphone blocks 會在正式錄音前清除。

PyWin32 312 沒有直接包裝 low-level hook 的三個 functions，因此
`hotkey.py` 以 `ctypes` 呼叫原生 `SetWindowsHookExW`、`CallNextHookEx`
及 `UnhookWindowsHookEx`；PyWin32 仍負責 Windows message loop、
foreground window 與 session notification。

## Quick start

從零到可執行的最短路徑。Windows 上的 Python 3.12。

```powershell
python -m venv .venv
.venv\Scriptsctivate
pip install -r requirements.txt
```

下載 Whisper model。`hf` CLI 隨 `huggingface_hub` 一起安裝，而它已經是
`transformers` 的 dependency，pip 上一步就裝好了：

```powershell
hf download openai/whisper-small.en --local-dir models\whisper-small.en
```

若該機器無法連上 Hub，請從
<https://huggingface.co/openai/whisper-small.en/tree/main> 手動下載檔案並放進
`models\whisper-small.en`。除了 `.gitattributes` 以及給其他 framework 用的
`.msgpack`／`.h5`／`.ot` weights 以外，其餘檔案都需要：

```text
config.json  generation_config.json  merges.txt  model.safetensors
normalizer.json  preprocessor_config.json  special_tokens_map.json
tokenizer.json  tokenizer_config.json  vocab.json  added_tokens.json
```

執行：

```powershell
python app.py
```

程式本身不會下載任何東西——它以 `HF_HUB_OFFLINE=1` 執行，只從本機資料夾載入。
上面的下載步驟是唯一一次連到 Hub。

若偏好 Conda 而非 venv（完整說明見 [Environment](#environment)）：

```powershell
conda create -n typeless_nano python=3.12 pip -y
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run --no-capture-output -n typeless_nano python app.py
```

## Safety and privacy

- Runtime 強制設定 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。
- 所有模型皆以 `local_files_only=True` 載入。
- Audio 只存在 RAM；程式不寫入 audio 或 transcript。
- Logs 只包含狀態、latency 與 error code，不包含 transcript。
- ASR 完成後會再次核對 foreground window；若視窗已改變便不輸入，
  但 transcript 不會被丟棄，而是保留在浮動視窗（見下文）。
- `SendInput` 失敗時預設將文字複製到 clipboard，可用
  `--no-clipboard-fallback` 關閉。

## 沒有地方可以輸入時

若 transcript 無法輸入——原本的視窗已消失，或 ASR 期間 foreground window 改變了
——文字不會再遺失。螢幕下方會出現一個浮動視窗顯示 transcript，按 `Copy` 會複製
並關閉視窗，按 `×` 則直接關閉且不動 clipboard。此視窗不搶焦點，也沒有 timeout，
會一直等你處理。

過長的 transcript 在視窗中會被截斷顯示，但 `Copy` 一定會複製完整文字。
視窗只存在記憶體中；若在開啟狀態下以 `Ctrl+C` 結束程式，內容會遺失。

若一次錄音完全沒有產生文字——靜音、錄音太短，或 ASR 結果為空——則會改為顯示
一個小小的深色 `No speech detected` 提示，約 1.7 秒後自動消失。不需要任何操作，
也沒有東西需要救回。

## Environment

```powershell
conda create -n typeless_nano python=3.12 pip -y
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run -n typeless_nano python scripts/dependency_gate.py
```

`requirements.txt` 只有四個公司批准的 direct dependencies。NumPy 由
`sounddevice`、`torch` 或 `transformers` 的 dependency graph 安裝，程式會直接使用
它處理 in-memory audio。`dependency_gate.py` 會顯示實際 NumPy 版本及
Transformers 宣告的 transitive requirements，供公司 allowlist 檢查。

## Local model layout

模型 checkpoint 必須先經批准，然後放進以下其中一個本機 folder：

```text
models/
  qwen3-asr-0.6b-hf/
    config.json
    model.safetensors
    ...
  whisper-small.en/
    config.json
    model.safetensors
    ...
```

程式不會下載模型。`--model auto` 會優先使用 Qwen；若 Qwen folder 不存在，
才使用 Whisper。亦可指定其他本機路徑：

```powershell
conda run --no-capture-output -n typeless_nano python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en
```

如要固定及驗證 checkpoint checksum：

```powershell
conda run -n typeless_nano python scripts/model_sha256.py D:\ApprovedModels\whisper-small.en --write
conda run --no-capture-output -n typeless_nano python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en --verify-checksums
```

## Run

先列出 microphone：

```powershell
conda run -n typeless_nano python app.py --list-microphones
```

啟動：

```powershell
conda run --no-capture-output -n typeless_nano python app.py
```

`--no-capture-output` 很重要：沒有它時，`conda run` 會暫存長時間程式的
console output，看起來像程式沒有啟動。

或在已啟用的 Conda environment：

```powershell
python app.py
```

操作：

1. 等待 `Typeless Nano is ready.`
2. 按一下 Right Alt；錄音 waveform 膠囊出現。
3. 說英文。
4. 再按一下 Right Alt，或點 `✓`；膠囊會切換成 `Thinking`。
5. 保持原本視窗在 foreground，等待文字輸入。
6. 點 `×` 可放棄目前錄音；按 `Ctrl+C` 可停止整個程式。

程式只攔截 Right Alt。F1、Left Alt 與其他 keys 都會原樣傳給其他工具及
active application。

常用參數：

```text
--model auto|qwen|whisper
--device auto|cpu|cuda
--microphone DEVICE_NAME_OR_INDEX
--mute-cues
--no-clipboard-fallback
--new-paragraph
--verify-checksums
```

## Tests

測試不需要 model checkpoint：

```powershell
conda run -n typeless_nano python -m unittest discover -s tests -v
conda run -n typeless_nano python -m pip check
```

如已放好 Whisper model，可用 Windows 內建 SAPI 產生臨時英文語音，做一次
完全本機 ASR smoke test。臨時 WAV 會在 `finally` 中刪除，輸出只包含
latency 與 word count，不會列出 transcript：

```powershell
conda run -n typeless_nano python -m scripts.smoke_transcription --model whisper
```

## Personal-PC smoke test

建議依序驗證：

1. `scripts/dependency_gate.py` 顯示四個 direct dependency 版本完全相符。
2. `app.py --list-microphones` 可看到預期裝置。
3. 先在 Notepad 測試 Right Alt 的「按一下開始、再按一下停止」。
4. 確認開始錄音為上升提示音，停止錄音為下降提示音。
5. 錄音少於 250 ms 或保持靜音時，應只看到 `utterance_rejected`。
6. 錄音後立即切換到另一個視窗，應看到
   `injection_skipped reason=foreground_window_changed`，且不輸入文字。
7. 錄音超過 60 秒會由 watchdog 自動停止。
8. 在 Outlook、Teams、Chrome、VS Code 各測試多次。

某些提高權限執行的程式不接受低權限 process 的 `SendInput`。遇到這種情況，
請讓 Typeless Nano 與目標程式使用相同 integrity level；不要把整個工具長期以
Administrator 執行。
