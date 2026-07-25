from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class EventKind(Enum):
    START_RECORDING = auto()
    STOP_RECORDING = auto()
    CANCEL_RECORDING = auto()
    ASR_FINISHED = auto()
    SHUTDOWN = auto()


@dataclass(frozen=True, slots=True)
class AppEvent:
    kind: EventKind
    source: str = ""
    foreground_window: int | None = None
    error_code: str | None = None

