# Typeless Nano

*English | [繁體中文](README.zh-TW.md)*

A local English dictation MVP for Windows. Press Right Alt once to start recording and again
to stop. A local Qwen3-ASR or Whisper model transcribes the audio, and the result is typed
into the window that was active when you started.

While recording, a floating capsule appears at the bottom centre of the current screen without
stealing focus: `×` on the left cancels, a waveform in the middle shows that recording is
active, and `✓` on the right finishes the recording. After stopping, the capsule shrinks to a
`Thinking` state and stays until transcription and text injection are complete.

Entering recording plays a soft rising two-note cue, leaving recording plays the same pair
falling, and cancelling plays a single low note -- the Discord join/leave feel. The cues are
sine tones synthesised at startup and played through Python's built-in `winsound`, so they add
no extra dependency and ship no third-party audio file. Microphone blocks captured while the
start cue is playing are discarded before the real recording begins.

PyWin32 312 does not wrap the three low-level hook functions directly, so `hotkey.py` calls the
native `SetWindowsHookExW`, `CallNextHookEx` and `UnhookWindowsHookEx` through `ctypes`.
PyWin32 still handles the Windows message loop, the foreground window and session notifications.

## Quick start

Minimal path from nothing to a running app. Python 3.12 on Windows.

```powershell
python -m venv .venv
.venv\Scriptsctivate
pip install -r requirements.txt
```

Download the Whisper model. The `hf` CLI ships with `huggingface_hub`, which pip already
installed as a dependency of `transformers`:

```powershell
hf download openai/whisper-small.en --local-dir models\whisper-small.en
```

If that machine has no access to the Hub, download the files by hand from
<https://huggingface.co/openai/whisper-small.en/tree/main> and put them in
`models\whisper-small.en`. You need every file except the `.gitattributes` and the `.msgpack`
/ `.h5` / `.ot` weights, which are for other frameworks:

```text
config.json  generation_config.json  merges.txt  model.safetensors
normalizer.json  preprocessor_config.json  special_tokens_map.json
tokenizer.json  tokenizer_config.json  vocab.json  added_tokens.json
```

Run it:

```powershell
python app.py
```

The app itself never downloads anything -- it runs with `HF_HUB_OFFLINE=1` and loads only from
the local folder. The download step above is the one and only time the Hub is contacted.

Conda instead of venv, if you prefer (see [Environment](#environment) for the full version):

```powershell
conda create -n typeless_nano python=3.12 pip -y
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run --no-capture-output -n typeless_nano python app.py
```

## Safety and privacy

- The runtime forces `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
- All models are loaded with `local_files_only=True`.
- Audio exists only in RAM; the program never writes audio or transcripts to disk.
- Logs contain only state, latency and error codes -- never transcript text.
- After ASR finishes, the foreground window is checked again; if it changed, nothing is typed.
  The transcript is not discarded: it stays in a floating panel (see below).
- If `SendInput` fails, the text is copied to the clipboard by default. Disable this with
  `--no-clipboard-fallback`.

## When there is nowhere to type

If the transcript cannot be inserted -- the original window is gone, or the foreground window
changed while ASR was running -- the text is no longer lost. A floating panel appears at the
bottom of the screen holding the transcript, with a `Copy` button that copies it and closes the
panel, and an `×` that dismisses it without touching the clipboard. The panel does not take
focus and has no timeout, so it waits until you deal with it.

A long transcript is visually clipped in the panel, but `Copy` always yields the whole text.
The panel is in-memory only and is lost if you quit the app with `Ctrl+C` while it is open.

If a dictation produced no words at all -- silence, a recording too short to use, or an ASR
result with no text -- a small dark `No speech detected` notice appears instead and fades after
about 1.7 seconds. It needs no interaction and there is nothing to recover.

## Environment

```powershell
conda create -n typeless_nano python=3.12 pip -y
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run -n typeless_nano python scripts/dependency_gate.py
```

`requirements.txt` contains only the four company-approved direct dependencies. NumPy is
installed through the dependency graph of `sounddevice`, `torch` or `transformers`, and the
program uses it directly to handle in-memory audio. `dependency_gate.py` reports the actual
NumPy version along with the transitive requirements declared by Transformers, so they can be
checked against the company allowlist.

## Local model layout

Model checkpoints must be approved first, then placed in one of these local folders:

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

The program never downloads models. `--model auto` prefers Qwen; it falls back to Whisper only
when the Qwen folder does not exist. You can also point at any other local path:

```powershell
conda run --no-capture-output -n typeless_nano python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en
```

To pin and verify checkpoint checksums:

```powershell
conda run -n typeless_nano python scripts/model_sha256.py D:\ApprovedModels\whisper-small.en --write
conda run --no-capture-output -n typeless_nano python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en --verify-checksums
```

## Run

List the microphones first:

```powershell
conda run -n typeless_nano python app.py --list-microphones
```

Start the app:

```powershell
conda run --no-capture-output -n typeless_nano python app.py
```

`--no-capture-output` matters: without it, `conda run` buffers the console output of
long-running programs, which makes the app look like it never started.

Or, inside an already-activated Conda environment:

```powershell
python app.py
```

Usage:

1. Wait for `Typeless Nano is ready.`
2. Press Right Alt once; the recording waveform capsule appears.
3. Speak English.
4. Press Right Alt again, or click `✓`; the capsule switches to `Thinking`.
5. Keep the original window in the foreground and wait for the text to be typed.
6. Click `×` to discard the current recording; press `Ctrl+C` to stop the whole program.

The program intercepts Right Alt only. F1, Left Alt and every other key are passed through
unchanged to other tools and to the active application.

Common options:

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

The tests do not require a model checkpoint:

```powershell
conda run -n typeless_nano python -m unittest discover -s tests -v
conda run -n typeless_nano python -m pip check
```

Once a Whisper model is in place, you can use the built-in Windows SAPI voice to generate
temporary English speech and run a fully local ASR smoke test. The temporary WAV is deleted in
a `finally` block, and the output contains only latency and word count -- never the transcript:

```powershell
conda run -n typeless_nano python -m scripts.smoke_transcription --model whisper
```

## Personal-PC smoke test

Verify in this order:

1. `scripts/dependency_gate.py` reports all four direct dependency versions as an exact match.
2. `app.py --list-microphones` shows the expected device.
3. Test press-once-to-start, press-again-to-stop with Right Alt in Notepad first.
4. Confirm that starting a recording plays the rising cue and stopping plays the falling cue.
5. A recording shorter than 250 ms, or one that stays silent, should produce only
   `utterance_rejected`.
6. Switching to another window immediately after recording should produce
   `injection_skipped reason=foreground_window_changed`, with no text typed.
7. A recording longer than 60 seconds is stopped automatically by the watchdog.
8. Test several times each in Outlook, Teams, Chrome and VS Code.

Some elevated programs do not accept `SendInput` from a lower-privilege process. When that
happens, run Typeless Nano at the same integrity level as the target program; do not leave the
whole tool running as Administrator permanently.
