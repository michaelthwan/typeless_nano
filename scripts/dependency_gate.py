from __future__ import annotations

import importlib.metadata
import json
import platform
import sys

DIRECT = {
    "sounddevice": "0.5.5",
    "pywin32": "312",
    "torch": "2.9.0",
    "transformers": "5.14.1",
}
# pywin32 has no macOS build; the macOS backend uses ctypes instead.
if sys.platform != "win32":
    del DIRECT["pywin32"]


def main() -> int:
    result: dict[str, object] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "direct_dependencies": {},
        "transformers_requirements": [],
    }
    ok = sys.version_info[:2] == (3, 12)
    installed: dict[str, str] = {}
    for name, expected in DIRECT.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "NOT_INSTALLED"
        installed[name] = actual
        ok = ok and actual == expected
    result["direct_dependencies"] = installed

    try:
        distribution = importlib.metadata.distribution("transformers")
        result["transformers_requirements"] = sorted(
            requirement
            for requirement in (distribution.requires or [])
            if "extra ==" not in requirement
        )
    except importlib.metadata.PackageNotFoundError:
        pass

    try:
        import numpy
        import torch

        result["numpy"] = numpy.__version__
        result["torch_cuda_available"] = torch.cuda.is_available()
        result["torch_cuda_version"] = torch.version.cuda
    except ImportError as exc:
        result["runtime_import_error"] = type(exc).__name__
        ok = False

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
