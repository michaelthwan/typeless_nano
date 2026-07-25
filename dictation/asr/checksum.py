from __future__ import annotations

import hashlib
from pathlib import Path


def verify_sha256_manifest(model_directory: Path) -> None:
    manifest = model_directory / "SHA256SUMS"
    if not manifest.is_file():
        raise FileNotFoundError(f"Missing checksum manifest: {manifest}")

    checked = 0
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            expected, relative_name = line.split(maxsplit=1)
        except ValueError as exc:
            raise ValueError(
                f"Invalid SHA256SUMS line {line_number}"
            ) from exc
        relative_name = relative_name.lstrip("*").replace("/", "\\")
        target = (model_directory / relative_name).resolve()
        if model_directory.resolve() not in target.parents:
            raise ValueError(f"Checksum path escapes model directory: {relative_name}")
        if not target.is_file():
            raise FileNotFoundError(f"Missing model file: {relative_name}")
        actual = _sha256(target)
        if actual.lower() != expected.lower():
            raise ValueError(f"Checksum mismatch: {relative_name}")
        checked += 1
    if checked == 0:
        raise ValueError("SHA256SUMS contains no files")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

