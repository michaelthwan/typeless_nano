from __future__ import annotations

import ctypes
import logging
import queue
import threading
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32ts

from dictation.events import AppEvent, EventKind
from dictation.platform_common import RightAltToggleState

LOGGER = logging.getLogger("dictation.hotkey")

VK_RMENU = 0xA5
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_LOGOFF = 0x6
PBT_APMSUSPEND = 0x0004
LRESULT = ctypes.c_ssize_t
HOOK_CALLBACK = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_USER32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,
    HOOK_CALLBACK,
    wintypes.HINSTANCE,
    wintypes.DWORD,
)
_USER32.SetWindowsHookExW.restype = wintypes.HHOOK
_USER32.CallNextHookEx.argtypes = (
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
_USER32.CallNextHookEx.restype = LRESULT
_USER32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
_USER32.UnhookWindowsHookEx.restype = wintypes.BOOL


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class WindowsHotkeyHook:
    def __init__(self, events: queue.Queue[AppEvent]) -> None:
        self.events = events
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook: int | None = None
        self._window: int | None = None
        self._toggle = RightAltToggleState()
        self._toggle_lock = threading.Lock()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        # Keep the C callback alive for the full lifetime of the hook.
        self._callback_ref = HOOK_CALLBACK(self._keyboard_callback)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="keyboard-hook",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("keyboard_hook_start_timeout")
        if self._startup_error:
            raise RuntimeError("keyboard_hook_start_failed") from self._startup_error

    def stop(self) -> None:
        window = self._window
        if window:
            try:
                win32gui.PostMessage(window, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        elif self._thread_id:
            try:
                win32api.PostThreadMessage(
                    self._thread_id, win32con.WM_QUIT, 0, 0
                )
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

    def _message_loop(self) -> None:
        self._thread_id = win32api.GetCurrentThreadId()
        class_name = f"TypelessNanoHookWindow_{id(self)}"
        try:
            window_class = win32gui.WNDCLASS()
            window_class.lpfnWndProc = self._window_proc
            window_class.hInstance = win32api.GetModuleHandle(None)
            window_class.lpszClassName = class_name
            atom = win32gui.RegisterClass(window_class)
            self._window = win32gui.CreateWindow(
                atom,
                class_name,
                0,
                0,
                0,
                0,
                0,
                # A hidden top-level window receives power broadcasts; a
                # message-only HWND_MESSAGE window does not.
                0,
                0,
                window_class.hInstance,
                None,
            )
            try:
                win32ts.WTSRegisterSessionNotification(
                    self._window, win32ts.NOTIFY_FOR_THIS_SESSION
                )
            except Exception:
                LOGGER.warning("session_notification_unavailable")

            self._hook = _USER32.SetWindowsHookExW(
                win32con.WH_KEYBOARD_LL,
                self._callback_ref,
                win32api.GetModuleHandle(None),
                0,
            )
            if not self._hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            win32gui.PumpMessages()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            LOGGER.error("keyboard_hook_failed error=%s", type(exc).__name__)
        finally:
            if self._hook:
                try:
                    _USER32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
                self._hook = None
            if self._window:
                try:
                    win32ts.WTSUnRegisterSessionNotification(self._window)
                except Exception:
                    pass
                try:
                    win32gui.DestroyWindow(self._window)
                except Exception:
                    pass
                self._window = None
            try:
                win32gui.UnregisterClass(class_name, win32api.GetModuleHandle(None))
            except Exception:
                pass

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return _USER32.CallNextHookEx(
                self._hook or 0, n_code, w_param, l_param
            )

        data = ctypes.cast(
            l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
        ).contents
        if data.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
            return _USER32.CallNextHookEx(
                self._hook or 0, n_code, w_param, l_param
            )

        if data.vkCode != VK_RMENU:
            return _USER32.CallNextHookEx(
                self._hook or 0, n_code, w_param, l_param
            )

        is_down = w_param in (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN)
        is_up = w_param in (win32con.WM_KEYUP, win32con.WM_SYSKEYUP)

        if is_down:
            with self._toggle_lock:
                action = self._toggle.key_down()
            if action is not None:
                self.events.put_nowait(
                    AppEvent(
                        action,
                        source="right_alt",
                        foreground_window=(
                            win32gui.GetForegroundWindow()
                            if action is EventKind.START_RECORDING
                            else None
                        ),
                    )
                )
            return 1

        if is_up:
            with self._toggle_lock:
                self._toggle.key_up()
            return 1

        return _USER32.CallNextHookEx(
            self._hook or 0, n_code, w_param, l_param
        )

    def _window_proc(
        self, hwnd: int, message: int, w_param: int, l_param: int
    ) -> int:
        if message == win32con.WM_POWERBROADCAST and w_param == PBT_APMSUSPEND:
            self.events.put_nowait(
                AppEvent(EventKind.CANCEL_RECORDING, source="system_suspend")
            )
            self.reset_pressed()
            return 1
        if message == WM_WTSSESSION_CHANGE and w_param in (
            WTS_SESSION_LOCK,
            WTS_SESSION_LOGOFF,
        ):
            self.events.put_nowait(
                AppEvent(EventKind.CANCEL_RECORDING, source="session_change")
            )
            self.reset_pressed()
            return 0
        if message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, w_param, l_param)
