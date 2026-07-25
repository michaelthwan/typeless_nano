from __future__ import annotations

import ctypes
import unittest

from dictation.inject import INPUT, _utf16_code_units


class InjectionEncodingTests(unittest.TestCase):
    def test_ascii_and_unicode_are_utf16_code_units(self) -> None:
        self.assertEqual(_utf16_code_units("A"), [0x0041])
        self.assertEqual(_utf16_code_units("😀"), [0xD83D, 0xDE00])

    def test_input_structure_has_native_windows_size(self) -> None:
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)


if __name__ == "__main__":
    unittest.main()
