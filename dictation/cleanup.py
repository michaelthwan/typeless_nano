from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"[^\S\r\n]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_NEW_PARAGRAPH = re.compile(r"\bnew paragraph\b", flags=re.IGNORECASE)


def clean_transcript(text: str, *, convert_new_paragraph: bool = False) -> str:
    if convert_new_paragraph:
        text = _NEW_PARAGRAPH.sub("\n\n", text)

    safe_characters: list[str] = []
    for character in text:
        if character in "\r\n\t":
            safe_characters.append(character)
            continue
        if unicodedata.category(character) != "Cc":
            safe_characters.append(character)

    cleaned = "".join(safe_characters).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "\n".join(_SPACES.sub(" ", line).strip() for line in cleaned.split("\n"))
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()

