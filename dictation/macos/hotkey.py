from __future__ import annotations

import logging
import os
import queue
import threading

from dictation.events import AppEvent, EventKind
from dictation.macos import quartz
from dictation.platform_common import RightAltToggleState

LOGGER = logging.getLogger("dictation.hotkey")

# Right Option is the macOS counterpart of Right Alt. Modifier keys arrive only
# as kCGEventFlagsChanged; the device-dependent NX_DEVICERALTKEYMASK bit tells
# whether the right-hand Option key is now held.
KVK_RIGHT_OPTION = 61
NX_DEVICERALTKEYMASK = 0x00000040


def right_option_transition(keycode: int, flags: int) -> bool | None:
    """True for Right Option down, False for up, None for any other key."""
    if keycode != KVK_RIGHT_OPTION:
        return None
    return bool(flags & NX_DEVICERALTKEYMASK)


class MacHotkeyTap:
    """Session event tap on its own CFRunLoop thread; suppresses Right Option only.

    Needs Accessibility and Input Monitoring permission for the terminal (or
    Python) that runs the app, otherwise CGEventTapCreate returns NULL.
    """

    def __init__(self, events: queue.Queue[AppEvent]) -> None:
        self.events = events
        self._thread: threading.Thread | None = None
        self._run_loop: int | None = None
        self._tap: int | None = None
        self._toggle = RightAltToggleState()
        self._toggle_lock = threading.Lock()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._own_pid = os.getpid()
        # Keep the C callback alive for the full lifetime of the tap.
        self._callback_ref = quartz.EVENT_TAP_CALLBACK(self._tap_callback)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run, name="keyboard-hook", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("keyboard_hook_start_timeout")
        if self._startup_error:
            raise RuntimeError("keyboard_hook_start_failed") from self._startup_error

    def stop(self) -> None:
        run_loop = self._run_loop
        if run_loop:
            try:
                quartz.load().CFRunLoopStop(run_loop)
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self.reset_pressed()

    def reset_pressed(self) -> None:
        with self._toggle_lock:
            self._toggle.reset()

    def set_recording(self, active: bool) -> None:
        with self._toggle_lock:
            self._toggle.set_recording(active)

    def _run(self) -> None:
        q = quartz.load()
        source = None
        try:
            if not q.AXIsProcessTrusted():
                LOGGER.warning("accessibility_not_trusted")
            self._tap = q.CGEventTapCreate(
                quartz.CG_SESSION_EVENT_TAP,
                quartz.CG_HEAD_INSERT_EVENT_TAP,
                quartz.CG_EVENT_TAP_OPTION_DEFAULT,
                1 << quartz.CG_EVENT_FLAGS_CHANGED,
                self._callback_ref,
                None,
            )
            if not self._tap:
                raise PermissionError("event_tap_denied")
            source = q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._run_loop = q.CFRunLoopGetCurrent()
            q.CFRunLoopAddSource(self._run_loop, source, q.kCFRunLoopCommonModes)
            q.CGEventTapEnable(self._tap, True)
            self._ready.set()
            q.CFRunLoopRun()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            LOGGER.error("keyboard_hook_failed error=%s", type(exc).__name__)
        finally:
            if self._tap:
                try:
                    q.CGEventTapEnable(self._tap, False)
                    q.CFMachPortInvalidate(self._tap)
                    q.CFRelease(self._tap)
                except Exception:
                    pass
                self._tap = None
            if source:
                try:
                    q.CFRelease(source)
                except Exception:
                    pass
            self._run_loop = None

    def _tap_callback(
        self, proxy: int, event_type: int, event: int, refcon: int
    ) -> int | None:
        del proxy, refcon
        try:
            q = quartz.load()
            if event_type in (
                quartz.CG_EVENT_TAP_DISABLED_BY_TIMEOUT,
                quartz.CG_EVENT_TAP_DISABLED_BY_USER_INPUT,
            ):
                # macOS disables a tap whose callback was too slow; re-arm it.
                # A Right Option key-up may have been missed meanwhile, so
                # release the held flag (the recording state is kept).
                with self._toggle_lock:
                    self._toggle.key_up()
                if self._tap:
                    q.CGEventTapEnable(self._tap, True)
                LOGGER.warning("event_tap_reenabled")
                return event
            if event_type != quartz.CG_EVENT_FLAGS_CHANGED:
                return event
            if (
                q.CGEventGetIntegerValueField(
                    event, quartz.CG_EVENT_SOURCE_UNIX_PROCESS_ID
                )
                == self._own_pid
            ):
                return event
            pressed = right_option_transition(
                q.CGEventGetIntegerValueField(
                    event, quartz.CG_KEYBOARD_EVENT_KEYCODE
                ),
                q.CGEventGetFlags(event),
            )
            if pressed is None:
                return event
            if pressed:
                with self._toggle_lock:
                    action = self._toggle.key_down()
                if action is not None:
                    self.events.put_nowait(
                        AppEvent(
                            action,
                            source="right_option",
                            foreground_window=(
                                quartz.frontmost_pid()
                                if action is EventKind.START_RECORDING
                                else None
                            ),
                        )
                    )
            else:
                with self._toggle_lock:
                    self._toggle.key_up()
            # Returning NULL swallows Right Option so apps never see it.
            return None
        except Exception as exc:
            LOGGER.error("event_tap_callback_failed error=%s", type(exc).__name__)
            return event
