#!/usr/bin/env python3

"""Setup script for `nputop`."""

from __future__ import annotations

import contextlib
import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING, Generator

from setuptools import setup

if TYPE_CHECKING:
    from types import ModuleType

HERE = Path(__file__).absolute().parent


@contextlib.contextmanager
def vcs_version(name: str, path: Path | str) -> Generator[ModuleType]:
    """
    Context manager to temporarily rewrite the __version__ in a version module
    to match the git tag when building.
    """
    path = Path(path).absolute()
    assert path.is_file(), f"Version file not found: {path}"
    spec = spec_from_file_location(name=name, location=path)
    assert spec is not None and spec.loader is not None
    module = sys.modules.get(name)
    if module is None:
        module = module_from_spec(spec)
        sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    # If this is already a release, do nothing
    if getattr(module, "__release__", False):
        yield module  # type: ignore[return-value]
        return

    # Otherwise, rewrite the __version__ in the file
    original = None
    try:
        original = path.read_text(encoding="utf-8")
        new_content = re.sub(
            r"""__version__\s*=\s*('[^']+'|"[^"]+")""",
            f"__version__ = {module.__version__!r}",
            original,
        )
        path.write_text(new_content, encoding="utf-8")
    except OSError:
        original = None

    try:
        yield module  # type: ignore[return-value]
    finally:
        # Restore the original file if we modified it
        if original is not None:
            path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    extras: dict[str, list[str]] = {
        "lint": [
            "black>=24.0.0,<25.0.0a0",
            "isort",
            "pylint[spelling]",
            "mypy",
            "typing-extensions",
            "pre-commit",
        ],
    }

    with vcs_version(
        name="nputop.version",
        path=HERE / "nputop" / "version.py",
    ) as version:
        setup(
            name="ascend-nputop",
            version=version.__version__,
            extras_require=extras,
        )
