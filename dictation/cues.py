from __future__ import annotations

import io
import math
import struct
import wave
import winsound
from collections.abc import Sequence

# A Discord-style pair of soft two-note cues: a rising "join" on enter and a
# falling "leave" on exit. These are synthesised here rather than shipped as
# audio assets, so the app gains no new dependency and no third-party sound
# file.
#
# Design notes:
# * winsound.Beep can only emit a square wave, which is what made the original
#   cues piercing. A sine with a shaped envelope is what removes the harshness.
# * The cues sit in the human vocal register (roughly 150-450 Hz fundamental)
#   rather than above it, so they read as part of speaking rather than as an
#   alarm over it.
# * Low tones are perceived as quieter than high tones at equal amplitude, so
#   each note gets a loudness compensation before the mix is normalised.
#   Without it a rising cue sounds like it also gets louder.
# * Small laptop speakers roll off sharply below ~180 Hz, so the register is
#   kept above that or the cue vanishes on the machines that need it most.

SAMPLE_RATE = 44_100
NOTE_SECONDS = 0.185
NOTE_GAP_SECONDS = 0.080
ATTACK_SECONDS = 0.007
RELEASE_SECONDS = 0.014
DECAY_TAU = 0.070
PEAK_AMPLITUDE = 0.34

# Perceived loudness falls off toward the bass; compensate gently so the two
# notes of a cue sound level. Exponent is deliberately mild -- full A-weighting
# overcorrects and makes the low note boom on good speakers.
LOUDNESS_REFERENCE_HZ = 440.0
LOUDNESS_EXPONENT = 0.32
LOUDNESS_MAX_GAIN = 1.8

# (harmonic multiple, gain, decay multiplier). Upper partials decay faster,
# which is what gives a struck-mallet body instead of a flat test tone.
PARTIALS: tuple[tuple[float, float, float], ...] = (
    (1.0, 1.00, 1.00),
    (2.0, 0.22, 0.55),
    (3.0, 0.07, 0.35),
)

# (frequency_hz, start_offset_seconds)
Note = tuple[float, float]

# Vocal register, an octave below the first version. A3 -> E4 is a rising
# fifth; both notes sit inside the range of a speaking voice.
A3 = 220.00
E4 = 329.63
D3 = 146.83

JOIN_NOTES: tuple[Note, ...] = ((A3, 0.0), (E4, NOTE_GAP_SECONDS))
LEAVE_NOTES: tuple[Note, ...] = ((E4, 0.0), (A3, NOTE_GAP_SECONDS))
CANCEL_NOTES: tuple[Note, ...] = ((D3 * 2, 0.0),)


def _loudness_gain(frequency: float) -> float:
    """Boost lower notes so a cue's two notes sound equally loud."""
    gain = (LOUDNESS_REFERENCE_HZ / frequency) ** LOUDNESS_EXPONENT
    return min(gain, LOUDNESS_MAX_GAIN)


def render_cue_wav(
    notes: Sequence[Note],
    *,
    note_seconds: float = NOTE_SECONDS,
    peak_amplitude: float = PEAK_AMPLITUDE,
) -> bytes:
    """Render notes to an in-memory 16-bit mono WAV."""
    total_seconds = max(start for _, start in notes) + note_seconds
    total_samples = int(total_seconds * SAMPLE_RATE)
    buffer = [0.0] * total_samples

    for frequency, start in notes:
        offset = int(start * SAMPLE_RATE)
        gain = _loudness_gain(frequency)
        for index in range(int(note_seconds * SAMPLE_RATE)):
            position = offset + index
            if position >= total_samples:
                break
            seconds = index / SAMPLE_RATE
            attack = min(1.0, seconds / ATTACK_SECONDS)
            # Fade the tail to true silence: cutting a note while amplitude
            # remains produces a click.
            release = min(1.0, (note_seconds - seconds) / RELEASE_SECONDS)
            value = 0.0
            for multiple, partial_gain, decay_scale in PARTIALS:
                value += (
                    math.sin(2.0 * math.pi * frequency * multiple * seconds)
                    * partial_gain
                    * math.exp(-seconds / (DECAY_TAU * decay_scale))
                )
            buffer[position] += attack * release * gain * value

    peak = max((abs(sample) for sample in buffer), default=0.0)
    scale = (peak_amplitude / peak) if peak else 0.0
    frames = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, sample * scale)) * 32767))
        for sample in buffer
    )

    stream = io.BytesIO()
    with wave.open(stream, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(frames)
    return stream.getvalue()


class SoundCuePlayer:
    """Short synchronous cues; never called from the keyboard hook thread.

    Playback stays synchronous because the controller discards the microphone
    blocks captured while the start cue plays; an asynchronous cue would leak
    into the recording.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._cache: dict[int, bytes] = {}

    def play_start(self) -> None:
        self._play(JOIN_NOTES)

    def play_stop(self) -> None:
        self._play(LEAVE_NOTES)

    def play_cancel(self) -> None:
        self._play(CANCEL_NOTES)

    def _wav_for(self, notes: Sequence[Note]) -> bytes:
        key = id(notes)
        cached = self._cache.get(key)
        if cached is None:
            cached = render_cue_wav(notes)
            self._cache[key] = cached
        return cached

    def _play(self, notes: Sequence[Note]) -> None:
        if not self.enabled:
            return
        try:
            winsound.PlaySound(self._wav_for(notes), winsound.SND_MEMORY)
        except (OSError, RuntimeError):
            # PlaySound can be unavailable on some remote or audio-less
            # Windows sessions. Keep dictation functional.
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except (OSError, RuntimeError):
                pass
