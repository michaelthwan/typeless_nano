from __future__ import annotations

from dataclasses import dataclass

from dictation.events import EventKind

# Platform-neutral pieces shared by the Windows and macOS hotkey and injector
# implementations. Nothing here may import a platform API.


@dataclass(frozen=True, slots=True)
class InjectionResult:
    injected: bool
    copied_to_clipboard: bool = False
    reason: str = ""


class RightAltToggleState:
    def __init__(self) -> None:
        self.physically_down = False
        self.recording = False

    def key_down(self) -> EventKind | None:
        if self.physically_down:
            return None
        self.physically_down = True
        self.recording = not self.recording
        return (
            EventKind.START_RECORDING
            if self.recording
            else EventKind.STOP_RECORDING
        )

    def key_up(self) -> None:
        self.physically_down = False

    def set_recording(self, active: bool) -> None:
        self.recording = active

    def reset(self) -> None:
        self.physically_down = False
        self.recording = False
