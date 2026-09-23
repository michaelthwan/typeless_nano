from __future__ import annotations

import ctypes
import ctypes.util
from functools import cache
from types import SimpleNamespace

# Hand-written ctypes bindings for the few CoreGraphics / CoreFoundation calls
# the macOS backend needs. PyObjC is not on the dependency allowlist, so these
# replace it. Frameworks load lazily in load(), which keeps this module (and the
# pure helpers that import it) importable on Windows for the test suite.

CG_SESSION_EVENT_TAP = 1
CG_HID_EVENT_TAP = 0
CG_HEAD_INSERT_EVENT_TAP = 0
CG_EVENT_TAP_OPTION_DEFAULT = 0
CG_EVENT_FLAGS_CHANGED = 12
CG_EVENT_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
CG_EVENT_TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF
CG_KEYBOARD_EVENT_KEYCODE = 9
CG_EVENT_SOURCE_UNIX_PROCESS_ID = 41
CG_WINDOW_LIST_ON_SCREEN_ONLY = 1
CG_WINDOW_LIST_EXCLUDE_DESKTOP = 16
CF_STRING_ENCODING_UTF8 = 0x08000100
CF_NUMBER_SINT64 = 4
CF_NUMBER_FLOAT64 = 6

EVENT_TAP_CALLBACK = ctypes.CFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_void_p,
)

_APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"


def _bind(lib: ctypes.CDLL, name: str, restype: object, *argtypes: object) -> object:
    function = getattr(lib, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


@cache
def load() -> SimpleNamespace:
    cg = ctypes.CDLL(_APP_SERVICES)
    cf = ctypes.CDLL(_CORE_FOUNDATION)
    carbon = ctypes.CDLL(_CARBON)
    vp = ctypes.c_void_p
    return SimpleNamespace(
        AXIsProcessTrusted=_bind(cg, "AXIsProcessTrusted", ctypes.c_bool),
        CGEventTapCreate=_bind(
            cg,
            "CGEventTapCreate",
            vp,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint64,
            EVENT_TAP_CALLBACK,
            vp,
        ),
        CGEventTapEnable=_bind(cg, "CGEventTapEnable", None, vp, ctypes.c_bool),
        CGEventGetIntegerValueField=_bind(
            cg, "CGEventGetIntegerValueField", ctypes.c_int64, vp, ctypes.c_uint32
        ),
        CGEventGetFlags=_bind(cg, "CGEventGetFlags", ctypes.c_uint64, vp),
        CGEventSetFlags=_bind(cg, "CGEventSetFlags", None, vp, ctypes.c_uint64),
        CGEventCreateKeyboardEvent=_bind(
            cg, "CGEventCreateKeyboardEvent", vp, vp, ctypes.c_uint16, ctypes.c_bool
        ),
        CGEventKeyboardSetUnicodeString=_bind(
            cg,
            "CGEventKeyboardSetUnicodeString",
            None,
            vp,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_uint16),
        ),
        CGEventPost=_bind(cg, "CGEventPost", None, ctypes.c_uint32, vp),
        CGWindowListCopyWindowInfo=_bind(
            cg, "CGWindowListCopyWindowInfo", vp, ctypes.c_uint32, ctypes.c_uint32
        ),
        CFMachPortCreateRunLoopSource=_bind(
            cf, "CFMachPortCreateRunLoopSource", vp, vp, vp, ctypes.c_long
        ),
        CFMachPortInvalidate=_bind(cf, "CFMachPortInvalidate", None, vp),
        CFRunLoopGetCurrent=_bind(cf, "CFRunLoopGetCurrent", vp),
        CFRunLoopAddSource=_bind(cf, "CFRunLoopAddSource", None, vp, vp, vp),
        CFRunLoopRun=_bind(cf, "CFRunLoopRun", None),
        CFRunLoopStop=_bind(cf, "CFRunLoopStop", None, vp),
        CFRelease=_bind(cf, "CFRelease", None, vp),
        CFArrayGetCount=_bind(cf, "CFArrayGetCount", ctypes.c_long, vp),
        CFArrayGetValueAtIndex=_bind(
            cf, "CFArrayGetValueAtIndex", vp, vp, ctypes.c_long
        ),
        CFDictionaryGetValue=_bind(cf, "CFDictionaryGetValue", vp, vp, vp),
        CFNumberGetValue=_bind(
            cf, "CFNumberGetValue", ctypes.c_bool, vp, ctypes.c_long, vp
        ),
        IsSecureEventInputEnabled=_bind(
            carbon, "IsSecureEventInputEnabled", ctypes.c_bool
        ),
        CFStringCreateWithCString=_bind(
            cf, "CFStringCreateWithCString", vp, vp, ctypes.c_char_p, ctypes.c_uint32
        ),
        kCFRunLoopCommonModes=vp.in_dll(cf, "kCFRunLoopCommonModes").value,
    )


@cache
def _cf_key(name: str) -> int:
    """A CFString dictionary key, created once and kept for the process lifetime."""
    return load().CFStringCreateWithCString(
        None, name.encode("utf-8"), CF_STRING_ENCODING_UTF8
    )


def _dict_number(
    q: SimpleNamespace, info: int, key: str, as_float: bool = False
) -> float | None:
    number = q.CFDictionaryGetValue(info, _cf_key(key))
    if not number:
        return None
    value = ctypes.c_double() if as_float else ctypes.c_int64()
    number_type = CF_NUMBER_FLOAT64 if as_float else CF_NUMBER_SINT64
    if not q.CFNumberGetValue(number, number_type, ctypes.byref(value)):
        return None
    return value.value


def secure_input_enabled() -> bool:
    """True while a password field or Secure Keyboard Entry is active.

    macOS then drops posted keystrokes silently, so typing would look
    successful while the text is lost.
    """
    return bool(load().IsSecureEventInputEnabled())


def frontmost_pid() -> int | None:
    """PID of the app owning the frontmost normal (layer 0) on-screen window.

    CGWindowList is queried fresh on every call. NSWorkspace's
    frontmostApplication was avoided on purpose: without a running main
    NSRunLoop it is not refreshed and goes stale.
    """
    q = load()
    windows = q.CGWindowListCopyWindowInfo(
        CG_WINDOW_LIST_ON_SCREEN_ONLY | CG_WINDOW_LIST_EXCLUDE_DESKTOP, 0
    )
    if not windows:
        return None
    try:
        for index in range(q.CFArrayGetCount(windows)):
            info = q.CFArrayGetValueAtIndex(windows, index)
            if _dict_number(q, info, "kCGWindowLayer") != 0:
                continue
            # Skip invisible layer-0 helper windows some apps keep on top.
            alpha = _dict_number(q, info, "kCGWindowAlpha", as_float=True)
            if alpha is not None and alpha <= 0.0:
                continue
            pid = _dict_number(q, info, "kCGWindowOwnerPID")
            return int(pid) if pid is not None else None
        return None
    finally:
        q.CFRelease(windows)
