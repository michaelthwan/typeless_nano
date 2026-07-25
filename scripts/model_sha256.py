from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a SHA256SUMS manifest for an approved local model."
    )
    parser.add_argument("model_directory", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write SHA256SUMS in the model directory instead of stdout.",
    )
    args = parser.parse_args()
    root = args.model_directory.resolve()
    if not root.is_dir():
        parser.error(f"Not a directory: {root}")

    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and path.name != "SHA256SUMS"
            and ".cache" not in path.relative_to(root).parts
        ):
            relative = path.relative_to(root).as_posix()
            lines.append(f"{sha256(path)}  {relative}")
    output = "\n".join(lines) + "\n"
    if args.write:
        manifest = root / "SHA256SUMS"
        manifest.write_text(output, encoding="utf-8", newline="\n")
        print(manifest)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
