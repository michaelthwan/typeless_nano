# Local Dictation App — Final Engineering Handoff

## 1. Product goal

Build a Windows local English speech-input tool for personal use, with a Typeless-like interaction model:

```text
Press Right Alt once
→ start recording
→ show recording waveform floating window
→ press Right Alt again
→ stop recording
→ show Thinking floating window
→ local ASR
→ type English at the current cursor position
```

Primary environment:

- Windows company PC
- Primarily recognizes English
- Fully local inference
- No cloud API
- No complex UI — only a recording/Thinking floating capsule
- Runs directly with Python
- Other personal tools use F1, so this program may only intercept Right Alt

## 2. Final scope

Version 1 includes:

- Right Alt toggle-to-record
- Does not intercept F1, Left Alt, or other keys
- Recording waveform floating capsule
- Thinking floating capsule shown during ASR
- A rising cue plays on entering recording, a falling cue on leaving recording
- Microphone recording
- Qwen3-ASR / Whisper local transcription
- Automatic input into the active application
- Basic error handling
- `Ctrl+C` stops the program
- Fully offline mode

Version 1 excludes:

- System tray
- Complex GUI (other than the recording/Thinking floating capsule)
- EXE packaging
- True streaming transcription
- Voice Activity Detection
- Transcript history
- Audio history
- Local LLM polishing
- Cloud API
- Telemetry

## 3. Company-approved direct dependencies

Use only:

```text
sounddevice==0.5.5
pywin32==312
torch==2.9.0
transformers==5.14.1
```

Purpose:

| Library | Purpose |
|---|---|
| `sounddevice` | Record 16 kHz mono audio from the microphone |
| `pywin32` | Listen for Right Alt, get the active window, inject text |
| `torch` | Run the ASR model on CPU/GPU |
| `transformers` | Load, preprocess, and decode with Qwen3-ASR/Whisper |

Explicitly not used:

```text
pynput
qwen-asr
faster-whisper
pystray
Pillow
PyInstaller
platformdirs
vllm
flash-attn
```

The following appear on the allowlist but must not be used:

```text
pytorch==1.0.2
pytorch-transformers==1.1.0
```

The modern, correct packages are:

```text
torch
transformers
```

Still to confirm:

- Whether `numpy` is an approved transitive dependency of the company
- Whether `transformers`' other transitive dependencies can be installed from the company package index
- Whether storing the model checkpoint on the company PC is approved

## 4. Application architecture

```text
pywin32 keyboard hook
        ↓
Right Alt toggle events
        ↓
sounddevice audio capture
        ↓
in-memory audio buffer
        ↓
transformers audio processor
        ↓
torch ASR inference
        ↓
decoded English transcript
        ↓
basic deterministic cleanup
        ↓
pywin32 SendInput
        ↓
active application
```

Suggested internal structure:

```text
app.py
dictation/
  controller.py
  hotkey.py
  audio.py
  asr/
    base.py
    qwen.py
    whisper.py
  inject.py
  cleanup.py
  config.py
tests/
  test_cleanup.py
  test_state_machine.py
  test_model_selection.py
```

Even though there is only one process, it should still be split into:

- Main/controller thread
- Keyboard-hook thread
- Audio callback/worker
- ASR worker

## 5. Right Alt design

Do not use `RegisterHotKey`, because Right Alt is a standalone modifier and requires key-down/key-up handling.

Use:

```text
SetWindowsHookEx(WH_KEYBOARD_LL)
VK_RMENU = 0xA5
```

Handle:

```text
WM_KEYDOWN
WM_KEYUP
WM_SYSKEYDOWN
WM_SYSKEYUP
```

Behavior:

```text
First Right Alt down
├── suppress event
├── save the foreground window handle
├── put START_RECORDING onto the queue
├── show the waveform floating window and play the rising cue
└── return from the hook callback immediately

Right Alt up
├── suppress event
├── only clears the physical-down state
└── does not stop recording

Second Right Alt down
├── suppress event
├── put STOP_RECORDING onto the queue
├── show the Thinking floating window and play the falling cue
└── return from the hook callback immediately
```

Important rules:

- The hook callback must not perform recording or model inference
- Ignore auto-repeat key-down
- Ignore keyboard events injected by the program itself
- Ignore auto-repeat key-down from the same key press
- A second, independent Right Alt key-down stops recording
- Automatically stop after more than 60 seconds
- A watchdog clears the recording state if key-up is missed
- Must unhook when the program exits
- Cancel recording on Windows lock/sleep
- Intercepting Right Alt must not trigger the application menu
- F1, Left Alt, and other keys must be passed through to other applications
- The recording waveform floating window must not steal foreground focus
- After recording stops, show the Thinking floating window; hide it only after ASR completes

If Right Alt has problems due to `AltGr` or the corporate environment, the program should stop rather than intercept F1.

## 6. Audio design

Defaults:

```text
sample rate: 16000 Hz
channels: 1
dtype: float32
storage: RAM only
maximum utterance: 60 seconds
minimum utterance: 250 ms
```

Flow:

```text
First Right Alt down
→ open sounddevice.InputStream
→ callback puts audio blocks onto the queue
→ the audio worker assembles the utterance

Second Right Alt down
→ close the stream
→ concatenate the audio buffer
→ check length and volume
→ feed into ASR
```

Version 1 does not use VAD. Right Alt already explicitly delimits the utterance boundary.

Must reject:

- Recordings that are too short
- Recordings that are almost entirely silent
- Empty audio buffers

This reduces the risk of the model hallucinating during silence.

## 7. ASR model strategy

### Candidate A: Qwen3-ASR-0.6B-hf

Model:

```text
Qwen/Qwen3-ASR-0.6B-hf
```

Do not install `qwen-asr`; use directly:

```text
AutoProcessor
AutoModelForMultimodalLM
```

Qwen3-ASR has had native support since Transformers 5.13, so the company-approved 5.14.1 meets the version requirement. It also supports a free-form vocabulary/context prompt. [Qwen3-ASR official model page](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf)

Recommended fixed settings:

```text
language="English"
do_sample=False
local_files_only=True
```

Vocabulary prompt:

```text
Vocabulary: Databricks, PySpark, XGBoost, SHAP, MLflow,
precision-recall curve, false positive rate.
```

Advantages:

- Stronger English accuracy
- More convenient technical-term prompting
- Newer-generation open-source ASR
- Can be run directly through the company-approved Transformers

Risks:

- The model is newer
- Windows integration must be tested empirically
- Larger than Whisper small.en
- CPU latency may be higher

### Candidate B: Whisper small.en

Model:

```text
openai/whisper-small.en
```

Use directly:

```text
AutoProcessor
AutoModelForSpeechSeq2Seq
```

Whisper small.en is an English-only model with about 244M parameters; it is mature technology with many years of Transformers support. [Whisper small.en official model page](https://huggingface.co/openai/whisper-small.en)

Advantages:

- Smaller
- More mature
- CPU-friendly
- Lower Windows deployment risk
- Does not require `faster-whisper`

Disadvantages:

- Reported English accuracy is usually lower than Qwen3-ASR
- Hotword/context control is less direct than Qwen's
- Officially acknowledged to sometimes produce text that was not actually spoken

### Selection decision

No single model is locked in ahead of time.

```text
Quality-first candidate:
Qwen3-ASR-0.6B-hf

Reliability baseline:
Whisper small.en
```

If Qwen:

- Can be loaded on the company PC
- Has acceptable latency after the second Right Alt press
- Has better technical-term accuracy than Whisper
- Is stable under continuous use

then use Qwen as the production model.

Otherwise, use Whisper small.en.

## 8. Model benchmark

Build a test set of 50–100 of your own sentences, including:

- General English
- Longer sentences
- Short sentences
- Numbers and percentages
- Background noise
- Canadian/Hong Kong English accents
- Data science technical terms

Example sentences:

```text
We trained the XGBoost model using PySpark.

The false positive rate decreased from twelve percent to eight percent.

Log the experiment in MLflow and compare the precision-recall curves.

The Databricks pipeline runs every weekday at seven thirty.
```

Compare:

- WER: lower is better
- Technical-term recall
- Number accuracy
- Negation accuracy
- Finalization latency
- RAM/VRAM
- Model load time
- Hallucination frequency

Do not compare WER directly across different model cards; use the same batch of recordings and the same normalization scheme.

## 9. Text cleanup and insertion

Version 1 performs only safe, deterministic cleanup:

- `strip()`
- Collapse repeated spaces
- Remove abnormal control characters
- Preserve the model's punctuation and capitalization
- Optionally convert `new paragraph` into a line break

Does not do:

- Automatic rewriting
- Automatic summarization
- LLM grammar correction
- Arbitrary removal of filler words
- Changing numbers, negations, or proper nouns

Text injection:

1. Save the foreground window handle on the first Right Alt down.
2. Re-check the foreground window after ASR completes.
3. If the active window has changed, do not auto-inject.
4. Only use Win32 `SendInput` if the window is the same.
5. If injection fails, the result can be written to the clipboard as a fallback.

This avoids text being typed into the wrong window (e.g. Teams, Outlook, or others) during transcription.

## 10. Offline and privacy requirements

Models are placed into a local folder in advance via a company-approved channel.

At runtime:

```text
local_files_only=True
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

Privacy defaults:

- Do not save audio
- Do not save transcripts
- Do not log the content of user input
- Do not send telemetry
- Do not download models at runtime
- Logs only record latency, state transitions, and error codes
- The model folder uses a fixed checksum

## 11. Implementation phases

### Phase 0 — Dependency gate

- Install the four direct dependencies from the company package index
- Confirm `numpy` and Transformers' transitive dependencies
- Confirm Torch CPU/CUDA status
- Confirm model checkpoint approval

### Phase 1 — Right Alt spike

- Build the pywin32 low-level hook
- Verify Right Alt toggle start/stop
- Verify event suppression
- Verify F1 and other keys are unaffected
- Verify the waveform/Thinking floating window does not steal focus
- Test in Outlook, Teams, Chrome, VS Code

### Phase 2 — Audio spike

- sounddevice microphone enumeration
- 16 kHz mono capture
- RAM buffer
- Minimum length/volume checks

### Phase 3 — Model benchmark

- Load Qwen and Whisper separately
- Compare using the same recording set
- Select the production model
- Keep the other backend as a fallback

### Phase 4 — End-to-end MVP

```text
Right Alt
→ record
→ transcribe
→ validate active window
→ SendInput
```

### Phase 5 — Hardening

- Timeout
- Missed key-up watchdog
- Model initialization errors
- GPU out-of-memory fallback
- Window-change protection
- Offline network verification
- Clean shutdown

## 12. Definition of Done

- 100 consecutive Right Alt tests with no missed key-up
- No permanent Alt-pressed state is left behind
- F1, Left Alt, and other keys are not intercepted
- The first Right Alt shows the recording floating window, the second shows the Thinking floating window
- The start/stop recording cues must be easy to distinguish, and the cues must not enter the ASR audio buffer
- Audio is stored only in RAM
- No network requests at runtime
- Logs contain no transcript content
- Text is never injected into the wrong window
- At least one of Qwen/Whisper runs stably
- Technical vocabulary recall is acceptable
- A 5–10 second English utterance completes within a reasonable time
- No direct Python dependency outside the company allowlist

## Copyable Handoff Prompt

```text
Build a minimal Windows local English dictation app in this repository.

Requirements:
- Reply to me in Traditional Chinese; use English titles.
- Inspect the repository before editing.
- Use Python 3.12.
- No GUI, tray icon, packaging, web server, cloud API, or telemetry.
- Run directly with `python app.py`.
- Toggle-to-record key is Right Alt; do not intercept F1, Left Alt, or any other key.
- Right Alt must be implemented with pywin32 using a Windows low-level keyboard hook:
  WH_KEYBOARD_LL, VK_RMENU 0xA5, key-down starts recording, key-up stops recording.
- Suppress Right Alt events while the app is enabled.
- Keep the hook callback lightweight; send events to a worker queue.
- Capture 16 kHz mono microphone audio with sounddevice and keep it in RAM.
- First Right Alt press starts recording; second Right Alt press stops and transcribes.
- Show a non-activating waveform capsule while recording.
- Show a compact Thinking capsule until transcription and injection finish.
- Play a rising cue when recording starts and a falling cue when it stops.
- Clear microphone blocks captured during the start cue before transcription.
- Primary ASR candidate: Qwen/Qwen3-ASR-0.6B-hf loaded directly through Transformers.
- Baseline/fallback: openai/whisper-small.en loaded directly through Transformers.
- Do not use qwen-asr, faster-whisper, pynput, vllm, flash-attn, pystray, Pillow, PyInstaller, or platformdirs.
- Company-approved direct dependency versions:
  sounddevice==0.5.5
  pywin32==312
  torch==2.9.0
  transformers==5.14.1
- Confirm numpy and all transitive dependencies before implementation.
- Models must load from local directories with local_files_only=True.
- Runtime must remain offline.
- Do not persist audio or transcripts.
- Save the foreground window on key-down and verify it is still active before injecting text.
- Use Win32 SendInput for text insertion.
- First implement and verify the dependency, hotkey, audio, and model spikes before completing the end-to-end app.
- Benchmark Qwen and Whisper on the same local English recordings; compare WER, technical-term recall, latency, RAM/VRAM, and hallucinations.
- WER is lower-is-better.
- Preserve existing repository changes and provide tests appropriate to the implementation.
```
