from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto

import win32api
import win32con
import win32gui

from dictation.events import AppEvent, EventKind

RECORDING_WIDTH = 132
RECORDING_HEIGHT = 44
THINKING_WIDTH = 102
THINKING_HEIGHT = 36
BOTTOM_MARGIN = 72

TRANSPARENT = "#ff00ff"
SHELL = "#090a0c"
BORDER = "#4a4e54"
BUTTON = "#282b30"
ICON = "#eef0f2"
ICON_MUTED = "#bfc3c8"

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_USER32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
_USER32.GetWindowLongW.restype = wintypes.LONG
_USER32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
_USER32.SetWindowLongW.restype = wintypes.LONG


class OverlayState(Enum):
    HIDDEN = auto()
    RECORDING = auto()
    PROCESSING = auto()


class OverlayCommand(Enum):
    SHOW_RECORDING = auto()
    SHOW_PROCESSING = auto()
    HIDE = auto()
    CLOSE = auto()


@dataclass(frozen=True, slots=True)
class QueuedOverlayCommand:
    command: OverlayCommand
    target_window: int | None = None


class RecordingOverlay:
    """A non-activating Windows recording capsule hosted on its own UI thread."""

    def __init__(self, events: queue.Queue[AppEvent]) -> None:
        self.events = events
        self._commands: queue.Queue[QueuedOverlayCommand] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._hwnd: int | None = None
        self._state = OverlayState.HIDDEN
        self._animation_started_at = time.monotonic()
        self._target_window: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._ui_thread,
            name="recording-overlay",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("overlay_start_timeout")
        if self._startup_error:
            raise RuntimeError("overlay_start_failed") from self._startup_error

    def show_recording(self, target_window: int | None) -> None:
        self._commands.put(
            QueuedOverlayCommand(OverlayCommand.SHOW_RECORDING, target_window)
        )

    def show_processing(self) -> None:
        self._commands.put(QueuedOverlayCommand(OverlayCommand.SHOW_PROCESSING))

    def hide(self) -> None:
        self._commands.put(QueuedOverlayCommand(OverlayCommand.HIDE))

    def close(self) -> None:
        if not self._thread:
            return
        self._commands.put(QueuedOverlayCommand(OverlayCommand.CLOSE))
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _ui_thread(self) -> None:
        try:
            root = tk.Tk()
            self._root = root
            root.withdraw()
            root.overrideredirect(True)
            root.configure(background=TRANSPARENT)
            root.attributes("-topmost", True)
            root.attributes("-transparentcolor", TRANSPARENT)

            canvas = tk.Canvas(
                root,
                width=RECORDING_WIDTH,
                height=RECORDING_HEIGHT,
                background=TRANSPARENT,
                highlightthickness=0,
                borderwidth=0,
            )
            canvas.pack()
            canvas.bind("<Button-1>", self._on_click)
            canvas.bind("<Motion>", self._on_motion)
            self._canvas = canvas

            root.update_idletasks()
            tk_child = int(root.winfo_id())
            # Tkinter exposes a TkChild HWND. ShowWindow/SetWindowPos must
            # target its native top-level parent or the drawn canvas remains
            # hidden when the Tk root was withdrawn.
            self._hwnd = int(win32gui.GetParent(tk_child) or tk_child)
            self._apply_non_activating_style()
            self._draw_frame()
            self._ready.set()
            root.after(16, self._tick)
            root.mainloop()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            self._root = None
            self._canvas = None
            self._hwnd = None

    def _apply_non_activating_style(self) -> None:
        if not self._hwnd:
            return
        style = _USER32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
        _USER32.SetWindowLongW(
            self._hwnd,
            GWL_EXSTYLE,
            style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )

    def _tick(self) -> None:
        root = self._root
        if root is None:
            return
        self._drain_commands()
        if self._state is not OverlayState.HIDDEN:
            self._draw_frame()
        if self._root is not None:
            root.after(32, self._tick)

    def _drain_commands(self) -> None:
        while True:
            try:
                queued = self._commands.get_nowait()
            except queue.Empty:
                return

            if queued.command is OverlayCommand.SHOW_RECORDING:
                self._state = OverlayState.RECORDING
                self._animation_started_at = time.monotonic()
                self._target_window = queued.target_window
                self._show(queued.target_window)
            elif queued.command is OverlayCommand.SHOW_PROCESSING:
                if self._state is not OverlayState.HIDDEN:
                    self._state = OverlayState.PROCESSING
                    self._animation_started_at = time.monotonic()
                    self._show(self._target_window)
            elif queued.command is OverlayCommand.HIDE:
                self._hide()
            elif queued.command is OverlayCommand.CLOSE:
                self._hide()
                if self._root is not None:
                    self._root.quit()
                    self._root.destroy()
                return

    def _show(self, target_window: int | None) -> None:
        if not self._root or not self._hwnd:
            return
        width, height = self._window_size()
        left, top, right, bottom = monitor_work_area(target_window)
        x = left + ((right - left - width) // 2)
        y = bottom - height - BOTTOM_MARGIN
        self._canvas.configure(width=width, height=height)
        self._root.geometry(f"{width}x{height}+{x}+{y}")
        self._root.update_idletasks()
        win32gui.SetWindowPos(
            self._hwnd,
            win32con.HWND_TOPMOST,
            x,
            y,
            width,
            height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _hide(self) -> None:
        self._state = OverlayState.HIDDEN
        if self._hwnd:
            win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)

    def _draw_frame(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.delete("all")
        if self._state is OverlayState.PROCESSING:
            self._draw_thinking(canvas)
            return

        _rounded_rectangle(canvas, 1, 1, 131, 43, radius=21, fill=BORDER)
        _rounded_rectangle(canvas, 2, 2, 130, 42, radius=20, fill=SHELL)

        canvas.create_oval(8, 8, 36, 36, fill=BUTTON, outline="#3a3e44", width=1)
        canvas.create_line(
            18,
            17,
            27,
            26,
            fill=ICON_MUTED,
            width=2,
            capstyle=tk.ROUND,
        )
        canvas.create_line(
            27,
            17,
            18,
            26,
            fill=ICON_MUTED,
            width=2,
            capstyle=tk.ROUND,
        )

        canvas.create_oval(96, 8, 124, 36, fill=ICON, outline="#ffffff", width=1)
        canvas.create_line(
            103,
            22,
            108,
            27,
            117,
            17,
            fill="#111317",
            width=2,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
        )
        self._draw_waveform(canvas)

    def _draw_waveform(self, canvas: tk.Canvas) -> None:
        elapsed = time.monotonic() - self._animation_started_at
        center_y = RECORDING_HEIGHT / 2
        bar_count = 12
        for index in range(bar_count):
            x = 47 + (index * 3.6)
            if self._state is OverlayState.RECORDING:
                phase = (elapsed * 8.0) + (index * 0.83)
                envelope = 0.55 + (0.45 * math.sin(index * 1.31) ** 2)
                height = 4.0 + abs(math.sin(phase)) * 13.0 * envelope
                color = ICON
            else:
                phase = (elapsed * 3.2) - (index * 0.48)
                height = 4.0 + ((math.sin(phase) + 1.0) / 2.0) * 8.0
                color = ICON_MUTED
            canvas.create_line(
                x,
                center_y - (height / 2),
                x,
                center_y + (height / 2),
                fill=color,
                width=2,
                capstyle=tk.ROUND,
            )

    def _draw_thinking(self, canvas: tk.Canvas) -> None:
        elapsed = time.monotonic() - self._animation_started_at
        _rounded_rectangle(
            canvas,
            1,
            1,
            THINKING_WIDTH - 1,
            THINKING_HEIGHT - 1,
            radius=17,
            fill=BORDER,
        )
        _rounded_rectangle(
            canvas,
            2,
            2,
            THINKING_WIDTH - 2,
            THINKING_HEIGHT - 2,
            radius=16,
            fill=SHELL,
        )
        canvas.create_text(
            43,
            THINKING_HEIGHT / 2,
            text="Thinking",
            fill=ICON_MUTED,
            font=("Segoe UI", 10),
            anchor=tk.CENTER,
        )
        active_dot = int(elapsed * 3.5) % 3
        for index in range(3):
            x = 76 + (index * 7)
            radius = 2.1 if index == active_dot else 1.5
            color = ICON if index == active_dot else "#666b72"
            canvas.create_oval(
                x - radius,
                (THINKING_HEIGHT / 2) - radius,
                x + radius,
                (THINKING_HEIGHT / 2) + radius,
                fill=color,
                outline="",
            )

    def _window_size(self) -> tuple[int, int]:
        if self._state is OverlayState.PROCESSING:
            return THINKING_WIDTH, THINKING_HEIGHT
        return RECORDING_WIDTH, RECORDING_HEIGHT

    def _on_click(self, event: tk.Event) -> None:
        if self._state is not OverlayState.RECORDING:
            return
        if event.x <= 42:
            self._hide()
            self.events.put(
                AppEvent(EventKind.CANCEL_RECORDING, source="overlay_cancel")
            )
        elif event.x >= 90:
            self._state = OverlayState.PROCESSING
            self._animation_started_at = time.monotonic()
            self.events.put(
                AppEvent(EventKind.STOP_RECORDING, source="overlay_done")
            )

    def _on_motion(self, event: tk.Event) -> None:
        if self._canvas is None:
            return
        interactive = (
            self._state is OverlayState.RECORDING
            and (event.x <= 42 or event.x >= 90)
        )
        self._canvas.configure(cursor="hand2" if interactive else "")


def monitor_work_area(target_window: int | None) -> tuple[int, int, int, int]:
    try:
        if target_window and win32gui.IsWindow(target_window):
            monitor = win32api.MonitorFromWindow(
                target_window, win32con.MONITOR_DEFAULTTONEAREST
            )
        else:
            monitor = win32api.MonitorFromPoint(
                (0, 0), win32con.MONITOR_DEFAULTTOPRIMARY
            )
        work = win32api.GetMonitorInfo(monitor)["Work"]
        return tuple(int(value) for value in work)
    except Exception:
        return (
            0,
            0,
            win32api.GetSystemMetrics(win32con.SM_CXSCREEN),
            win32api.GetSystemMetrics(win32con.SM_CYSCREEN),
        )


def _rounded_rectangle(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    radius: float,
    fill: str,
) -> int:
    points = (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )
    return canvas.create_polygon(
        points,
        smooth=True,
        splinesteps=24,
        fill=fill,
        outline="",
    )
