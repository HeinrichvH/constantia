"""File selection for rules.

A selector is glob + optional file-content regex + excludes. It returns
every matched file as a repo-relative POSIX path. Selection is the
*only* mechanism deciding which files a rule evaluates — there is no
hidden filtering elsewhere, so `len(selected_files)` equals the number
of verdicts the rule will produce.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_BRACE_RE = re.compile(r"\{([^{}]*)\}")


def expand_braces(pattern: str) -> list[str]:
    """Expand shell-style brace alternation (e.g. '*.{ts,vue}' → ['*.ts', '*.vue']).

    `pathlib.Path.glob` doesn't support braces; this does one level of
    expansion, recursing for nested braces. Not a full shell-glob
    implementation — good enough for the rule selector's needs.
    """
    m = _BRACE_RE.search(pattern)
    if not m:
        return [pattern]
    prefix, suffix = pattern[: m.start()], pattern[m.end() :]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(expand_braces(prefix + alt + suffix))
    return out


@dataclass(frozen=True)
class Selector:
    file_glob: str
    file_contains_regex: str | None = None
    exclude_globs: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Selector":
        return cls(
            file_glob=cfg["file_glob"],
            file_contains_regex=cfg.get("file_contains_regex"),
            exclude_globs=tuple(cfg.get("exclude_globs", ())),
        )


def select_files(sel: Selector, repo_root: Path) -> list[Path]:
    """Return files under repo_root that match the selector.

    `file_glob` supports shell-style brace alternation for extensions
    (e.g. `src/**/*.{ts,vue}`). Results are deduped and sorted.
    """
    compiled = re.compile(sel.file_contains_regex, re.MULTILINE) if sel.file_contains_regex else None
    patterns = expand_braces(sel.file_glob)
    seen: set[Path] = set()
    for pat in patterns:
        for path in repo_root.glob(pat):
            if not path.is_file() or path in seen:
                continue
            rel = path.relative_to(repo_root).as_posix()
            if any(fnmatch.fnmatchcase(rel, g) for g in sel.exclude_globs):
                continue
            if compiled is not None:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not compiled.search(text):
                    continue
            seen.add(path)
    return sorted(seen)
