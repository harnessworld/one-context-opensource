"""Portable identifiers shared by manifest loaders."""

from __future__ import annotations

import re


_SAFE_MANIFEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def is_portable_manifest_id(value: str) -> bool:
    """Return whether *value* is a safe filename component on all targets."""
    return bool(
        len(value) <= 64
        and value == value.casefold()
        and _SAFE_MANIFEST_ID.fullmatch(value)
        and not value.endswith(".")
        and value.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED_NAMES
    )
