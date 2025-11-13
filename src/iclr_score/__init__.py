from __future__ import annotations

from importlib import metadata

__all__ = ("__version__",)


def _resolve_version() -> str:
    try:
        return metadata.version("iclr-score")
    except metadata.PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()
