"""Glob semantics for ``phases[].deliverables`` (pure Python, no dependencies).

A deliverable is a project-relative path that may contain globs. Semantics:

* Paths are POSIX style. ``\\`` is treated as ``/``; a leading ``./`` is ignored.
  Matching is case-sensitive and relative to the project root, so an absolute
  path (or a pattern) never matches.
* ``*`` matches any run of characters inside ONE path segment (never ``/``),
  ``?`` one character, ``[abc]`` / ``[!abc]`` a character class. Dotfiles are
  matched like any other name.
* A segment that is exactly ``**`` matches zero or more whole segments, so
  ``docs/**/x.md`` matches ``docs/x.md`` and ``docs/a/b/x.md``. A trailing
  ``**`` needs at least one segment: ``results/**`` matches everything below
  ``results`` but not ``results`` itself. ``**`` inside a longer segment
  (``a**b``) behaves like ``*``.
* A pattern without glob characters matches exactly that path.

Only the strings are compared; the file system is never touched. The dashboard
expands patterns against a list of existing paths it collected itself.
"""

from __future__ import annotations

import fnmatch
import os
from functools import lru_cache
from typing import Iterable, Optional, Union

__all__ = ["glob_match", "glob_matches"]


def _segments(path: str) -> Optional[list[str]]:
    """Split a relative path into segments; ``None`` if it is not project-relative."""
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or text.startswith("~"):
        return None
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():  # Windows drive
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    return parts or None


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> Optional[tuple[str, ...]]:
    segs = _segments(pattern)
    return tuple(segs) if segs is not None else None


def _match(pat: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    if not pat:
        return not parts
    head = pat[0]
    if head == "**":
        rest = pat[1:]
        if not rest:  # trailing ** : one or more segments
            return len(parts) >= 1
        return any(_match(rest, parts[i:]) for i in range(len(parts) + 1))
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match(pat[1:], parts[1:])


def glob_match(pattern: str, path: str) -> bool:
    """True if ``path`` (project-relative) matches ``pattern``."""
    pat = _compile(pattern)
    parts = _segments(path)
    if pat is None or parts is None:
        return False
    return _match(pat, tuple(parts))


def glob_matches(pattern: str, paths: Iterable[Union[str, "os.PathLike[str]"]]) -> list:
    """Return the entries of ``paths`` that match ``pattern``, in input order.

    Entries are returned unchanged (strings or path-like objects).
    """
    return [p for p in paths if glob_match(pattern, os.fspath(p))]
