from __future__ import annotations

import logging

from dictation.macos.inject import copy_to_clipboard

LOGGER = logging.getLogger("dictation.overlay")


class ConsoleOverlay:
    """Stand-in for the Windows capsule until a native macOS overlay exists.

    Recording and Thinking states are signalled by the sound cues only. The
    rescue path still must not lose text, so without a panel it goes to the
    clipboard -- even with --no-clipboard-fallback, which on Windows only
    governs the automatic copy, never the panel. Console lines never contain
    the transcript itself.
    """

    def start(self) -> None:
        pass

    def show_recording(self, target_window: int | None) -> None:
        del target_window

    def show_processing(self) -> None:
        pass

    def show_notice(self, text: str = "", target_window: int | None = None) -> None:
        del target_window
        print(text or "No speech detected", flush=True)

    def show_rescue(self, text: str, target_window: int | None = None) -> None:
        del target_window
        if copy_to_clipboard(text):
            LOGGER.info("rescue_copied_to_clipboard")
            print(
                "Could not type into the original app; text copied to clipboard.",
                flush=True,
            )
        else:
            LOGGER.error("rescue_clipboard_failed")
            print(
                "Could not type the text, and copying it to the clipboard failed.",
                flush=True,
            )

    def hide(self) -> None:
        pass

    def close(self) -> None:
        pass
