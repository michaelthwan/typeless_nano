from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
import tkinter as tk
from collections import deque
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto

import win32api
import win32con
import win32gui

from dictation.events import AppEvent, EventKind
from dictation.inject import copy_to_clipboard

RECORDING_WIDTH = 132
RECORDING_HEIGHT = 44
THINKING_WIDTH = 102
THINKING_HEIGHT = 36
BOTTOM_MARGIN = 72

# Recording waveform: a scrolling level history. Each bar is one mic level
# sample; a new sample enters at the right every LEVEL_SAMPLE_SECONDS and the
# older ones shift left. Silence sits at BAR_MIN_HEIGHT.
BAR_COUNT = 12
BAR_MIN_HEIGHT = 4.0
BAR_MAX_EXTRA = 15.0
LEVEL_SAMPLE_SECONDS = 0.1

# Rescue panel: shown when a transcript could not be typed anywhere, so the
# text is never silently lost. It persists until the user copies or dismisses
# it -- deliberately no timeout, since a timeout would lose the text again.
RESCUE_WIDTH = 428
RESCUE_PAD = 16
RESCUE_HEADER_HEIGHT = 26
RESCUE_BUTTON_ROW = 46
RESCUE_MIN_TEXT_HEIGHT = 20
RESCUE_MAX_TEXT_HEIGHT = 190
RESCUE_COPY_WIDTH = 92
RESCUE_COPY_HEIGHT = 30
RESCUE_CLOSE_HIT = 34

# Transient notice: shown when a dictation produced no words at all, so the
# capsule never disappears in silence leaving the user unsure what happened.
NOTICE_HEIGHT = 30
NOTICE_PAD_X = 16
NOTICE_SECONDS = 1.7
NOTICE_EMPTY_TEXT = "No speech detected"

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
    NOTICE = auto()


class OverlayCommand(Enum):
    SHOW_RECORDING = auto()
    SHOW_PROCESSING = auto()
    HIDE = auto()
    SHOW_NOTICE = auto()
    SHOW_RESCUE = auto()
    HIDE_RESCUE = auto()
    CLOSE = auto()


@dataclass(frozen=True, slots=True)
class QueuedOverlayCommand:
    command: OverlayCommand
    target_window: int | None = None
    text: str = ""


class RecordingOverlay:
    """A non-activating Windows recording capsule hosted on its own UI thread."""

    def __init__(
        self,
        events: queue.Queue[AppEvent],
        level_source: Callable[[], float] | None = None,
    ) -> None:
        self.events = events
        # Polled on the UI thread each frame; returns live mic loudness 0..1.
        self._level_source = level_source
        self._levels = LevelHistory(BAR_COUNT)
        self._next_sample_at = 0.0
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
        self._rescue_root: tk.Toplevel | None = None
        self._rescue_canvas: tk.Canvas | None = None
        self._rescue_hwnd: int | None = None
        # Full transcript; what the panel draws may be visually clipped, but
        # the clipboard always receives this whole string.
        self._rescue_text = ""
        self._rescue_height = 0
        self._notice_text = ""
        self._notice_size = (0, 0)
        self._notice_until = 0.0

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

    def show_notice(
        self, text: str = NOTICE_EMPTY_TEXT, target_window: int | None = None
    ) -> None:
        """Flash a small notice that disappears on its own."""
        self._commands.put(
            QueuedOverlayCommand(OverlayCommand.SHOW_NOTICE, target_window, text)
        )

    def show_rescue(self, text: str, target_window: int | None = None) -> None:
        """Keep an un-typed transcript on screen instead of dropping it."""
        self._commands.put(
            QueuedOverlayCommand(
                OverlayCommand.SHOW_RESCUE, target_window, text
            )
        )

    def hide_rescue(self) -> None:
        self._commands.put(QueuedOverlayCommand(OverlayCommand.HIDE_RESCUE))

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
            self._apply_non_activating_style(self._hwnd)
            self._build_rescue_window(root)
            self._draw_frame()
            self._ready.set()
            root.after(16, self._tick)
            root.mainloop()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            # Drop every Tk reference on the UI thread. A widget surviving
            # past interpreter teardown is collected on the main thread and
            # Tcl aborts with "async handler deleted by the wrong thread".
            self._root = None
            self._canvas = None
            self._hwnd = None
            self._rescue_root = None
            self._rescue_canvas = None
            self._rescue_hwnd = None

    def _apply_non_activating_style(self, hwnd: int | None) -> None:
        if not hwnd:
            return
        style = _USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _USER32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )

    def _build_rescue_window(self, root: tk.Tk) -> None:
        panel = tk.Toplevel(root)
        panel.withdraw()
        panel.overrideredirect(True)
        panel.configure(background=SHELL)
        panel.attributes("-topmost", True)
        canvas = tk.Canvas(
            panel,
            width=RESCUE_WIDTH,
            height=RESCUE_MIN_TEXT_HEIGHT,
            background=SHELL,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack()
        canvas.bind("<Button-1>", self._on_rescue_click)
        canvas.bind("<Motion>", self._on_rescue_motion)
        panel.update_idletasks()
        tk_child = int(panel.winfo_id())
        self._rescue_hwnd = int(win32gui.GetParent(tk_child) or tk_child)
        self._apply_non_activating_style(self._rescue_hwnd)
        self._rescue_root = panel
        self._rescue_canvas = canvas

    def _tick(self) -> None:
        root = self._root
        if root is None:
            return
        self._drain_commands()
        if (
            self._state is OverlayState.NOTICE
            and time.monotonic() >= self._notice_until
        ):
            self._hide()
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
                self._levels.clear()
                self._next_sample_at = 0.0
                self._target_window = queued.target_window
                self._show(queued.target_window)
            elif queued.command is OverlayCommand.SHOW_PROCESSING:
                if self._state is not OverlayState.HIDDEN:
                    self._state = OverlayState.PROCESSING
                    self._animation_started_at = time.monotonic()
                    self._show(self._target_window)
            elif queued.command is OverlayCommand.HIDE:
                self._hide()
            elif queued.command is OverlayCommand.SHOW_NOTICE:
                self._show_notice(queued.text, queued.target_window)
            elif queued.command is OverlayCommand.SHOW_RESCUE:
                self._show_rescue(queued.text, queued.target_window)
            elif queued.command is OverlayCommand.HIDE_RESCUE:
                self._hide_rescue()
            elif queued.command is OverlayCommand.CLOSE:
                self._hide()
                self._hide_rescue()
                if self._rescue_root is not None:
                    self._rescue_root.destroy()
                    self._rescue_root = None
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
        if self._state is OverlayState.NOTICE:
            self._draw_notice(canvas)
            return
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

    def _sample_level(self) -> None:
        now = time.monotonic()
        if now < self._next_sample_at:
            return
        self._next_sample_at = now + LEVEL_SAMPLE_SECONDS
        level = 0.0
        if self._level_source is not None:
            try:
                level = self._level_source()
            except Exception:
                level = 0.0
        self._levels.push(level)

    def _draw_waveform(self, canvas: tk.Canvas) -> None:
        elapsed = time.monotonic() - self._animation_started_at
        center_y = RECORDING_HEIGHT / 2
        if self._state is OverlayState.RECORDING:
            self._sample_level()
        levels = self._levels.values()
        for index in range(BAR_COUNT):
            x = 47 + (index * 3.6)
            if self._state is OverlayState.RECORDING:
                height = BAR_MIN_HEIGHT + (BAR_MAX_EXTRA * levels[index])
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
        if self._state is OverlayState.NOTICE:
            return self._notice_size
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

    def _show_notice(self, text: str, target_window: int | None) -> None:
        canvas = self._canvas
        if canvas is None or not text:
            return
        probe = canvas.create_text(
            0, 0, text=text, font=("Segoe UI", 9), anchor=tk.NW
        )
        bounds = canvas.bbox(probe)
        canvas.delete(probe)
        text_width = (bounds[2] - bounds[0]) if bounds else 120
        self._notice_text = text
        self._notice_size = (int(text_width + (NOTICE_PAD_X * 2)), NOTICE_HEIGHT)
        self._state = OverlayState.NOTICE
        self._notice_until = time.monotonic() + NOTICE_SECONDS
        self._show(target_window)
        self._draw_frame()

    def _draw_notice(self, canvas: tk.Canvas) -> None:
        width, height = self._notice_size
        _rounded_rectangle(
            canvas, 1, 1, width - 1, height - 1, radius=14, fill=BORDER
        )
        _rounded_rectangle(
            canvas, 2, 2, width - 2, height - 2, radius=13, fill=SHELL
        )
        canvas.create_text(
            width / 2,
            height / 2,
            text=self._notice_text,
            fill=ICON_MUTED,
            font=("Segoe UI", 9),
            anchor=tk.CENTER,
        )

    def _show_rescue(self, text: str, target_window: int | None) -> None:
        panel = self._rescue_root
        canvas = self._rescue_canvas
        if panel is None or canvas is None or not text:
            return
        self._rescue_text = text
        height = self._measure_rescue(canvas, text)
        self._rescue_height = height

        left, _top, right, bottom = monitor_work_area(target_window)
        x = left + ((right - left - RESCUE_WIDTH) // 2)
        y = bottom - height - BOTTOM_MARGIN
        canvas.configure(width=RESCUE_WIDTH, height=height)
        panel.geometry(f"{RESCUE_WIDTH}x{height}+{x}+{y}")
        panel.update_idletasks()
        self._draw_rescue()
        if self._rescue_hwnd:
            win32gui.SetWindowPos(
                self._rescue_hwnd,
                win32con.HWND_TOPMOST,
                x,
                y,
                RESCUE_WIDTH,
                height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )

    def _hide_rescue(self) -> None:
        self._rescue_text = ""
        if self._rescue_hwnd:
            win32gui.ShowWindow(self._rescue_hwnd, win32con.SW_HIDE)

    def _measure_rescue(self, canvas: tk.Canvas, text: str) -> int:
        """Size the panel to the transcript, clamped so it cannot fill the screen."""
        probe = canvas.create_text(
            0,
            0,
            text=text,
            width=RESCUE_WIDTH - (RESCUE_PAD * 2),
            font=("Segoe UI", 10),
            anchor=tk.NW,
        )
        bounds = canvas.bbox(probe)
        canvas.delete(probe)
        text_height = (bounds[3] - bounds[1]) if bounds else RESCUE_MIN_TEXT_HEIGHT
        text_height = max(
            RESCUE_MIN_TEXT_HEIGHT, min(RESCUE_MAX_TEXT_HEIGHT, text_height)
        )
        return int(
            RESCUE_PAD
            + RESCUE_HEADER_HEIGHT
            + text_height
            + RESCUE_BUTTON_ROW
            + RESCUE_PAD
        )

    def _draw_rescue(self) -> None:
        canvas = self._rescue_canvas
        if canvas is None:
            return
        height = self._rescue_height
        canvas.delete("all")
        _rounded_rectangle(
            canvas, 1, 1, RESCUE_WIDTH - 1, height - 1, radius=15, fill=BORDER
        )
        _rounded_rectangle(
            canvas, 2, 2, RESCUE_WIDTH - 2, height - 2, radius=14, fill=SHELL
        )

        canvas.create_text(
            RESCUE_PAD,
            RESCUE_PAD,
            text="Nowhere to type - text kept here",
            fill=ICON_MUTED,
            font=("Segoe UI", 9),
            anchor=tk.NW,
        )

        close_x = RESCUE_WIDTH - RESCUE_PAD - 7
        close_y = RESCUE_PAD + 6
        for x1, y1, x2, y2 in (
            (close_x - 5, close_y - 5, close_x + 5, close_y + 5),
            (close_x + 5, close_y - 5, close_x - 5, close_y + 5),
        ):
            canvas.create_line(
                x1, y1, x2, y2, fill=ICON_MUTED, width=2, capstyle=tk.ROUND
            )

        text_top = RESCUE_PAD + RESCUE_HEADER_HEIGHT
        text_area = height - RESCUE_PAD - RESCUE_BUTTON_ROW - text_top
        canvas.create_text(
            RESCUE_PAD,
            text_top,
            text=self._rescue_text,
            fill=ICON,
            width=RESCUE_WIDTH - (RESCUE_PAD * 2),
            font=("Segoe UI", 10),
            anchor=tk.NW,
        )
        # A long transcript is visually clipped; the clipboard still gets all
        # of it, so say so rather than letting the user think it was cut.
        if text_area >= RESCUE_MAX_TEXT_HEIGHT:
            canvas.create_rectangle(
                2,
                text_top + text_area - 16,
                RESCUE_WIDTH - 2,
                text_top + text_area,
                fill=SHELL,
                outline="",
            )
            canvas.create_text(
                RESCUE_PAD,
                text_top + text_area - 14,
                text="... copy to get the full text",
                fill=ICON_MUTED,
                font=("Segoe UI", 8),
                anchor=tk.NW,
            )

        button_left = RESCUE_WIDTH - RESCUE_PAD - RESCUE_COPY_WIDTH
        button_top = height - RESCUE_PAD - RESCUE_COPY_HEIGHT
        _rounded_rectangle(
            canvas,
            button_left,
            button_top,
            button_left + RESCUE_COPY_WIDTH,
            button_top + RESCUE_COPY_HEIGHT,
            radius=8,
            fill=ICON,
        )
        canvas.create_text(
            button_left + (RESCUE_COPY_WIDTH / 2),
            button_top + (RESCUE_COPY_HEIGHT / 2),
            text="Copy",
            fill="#111317",
            font=("Segoe UI", 10, "bold"),
            anchor=tk.CENTER,
        )

    def _rescue_hit(self, x: float, y: float) -> str:
        height = self._rescue_height
        if x >= RESCUE_WIDTH - RESCUE_CLOSE_HIT and y <= RESCUE_CLOSE_HIT:
            return "close"
        button_left = RESCUE_WIDTH - RESCUE_PAD - RESCUE_COPY_WIDTH
        button_top = height - RESCUE_PAD - RESCUE_COPY_HEIGHT
        if x >= button_left and y >= button_top:
            return "copy"
        return ""

    def _on_rescue_click(self, event: tk.Event) -> None:
        if not self._rescue_text:
            return
        action = self._rescue_hit(event.x, event.y)
        if action == "copy":
            copy_to_clipboard(self._rescue_text)
            self._hide_rescue()
        elif action == "close":
            self._hide_rescue()

    def _on_rescue_motion(self, event: tk.Event) -> None:
        if self._rescue_canvas is None:
            return
        hit = self._rescue_hit(event.x, event.y)
        self._rescue_canvas.configure(cursor="hand2" if hit else "")

    def _on_motion(self, event: tk.Event) -> None:
        if self._canvas is None:
            return
        interactive = (
            self._state is OverlayState.RECORDING
            and (event.x <= 42 or event.x >= 90)
        )
        self._canvas.configure(cursor="hand2" if interactive else "")


class LevelHistory:
    """Fixed-length mic level history; oldest first, newest last (rightmost bar)."""

    def __init__(self, size: int) -> None:
        self._values: deque[float] = deque([0.0] * size, maxlen=size)

    def push(self, level: float) -> None:
        self._values.append(min(1.0, max(0.0, level)))

    def clear(self) -> None:
        for _ in range(len(self._values)):
            self._values.append(0.0)

    def values(self) -> tuple[float, ...]:
        return tuple(self._values)


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
