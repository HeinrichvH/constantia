"""Check: deprecation markers must carry a migration path.

Drift pattern surfaced across all four audit repos: symbols marked
`@deprecated` (JSDoc/TSDoc/Python), `[Obsolete]` (.NET), `#[deprecated]`
(Rust) accumulate without telling the reader *what to use instead* or
*when it goes away*. An agent reading the code as ground truth then
either (a) uses the deprecated symbol because nothing points elsewhere,
or (b) rips it out because nothing says it's still load-bearing.

Per-file check: each deprecation marker line must carry — on the same
line, or on one of the next three lines — one of:
  - a replacement hint ("use X", "prefer X", "replaced by", "instead")
  - a tracking reference (issue #123, URL, Jira key ABC-123)
  - a version target ("since 1.2", "until 2.0", "v8", "in v2.0")

Bare `@deprecated`, bare `[Obsolete]`, bare `#[deprecated]` — all
orphan.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Check, Finding, register


_DEPRECATION_MARKERS = [
    # JSDoc / TSDoc / Python docstring
    (re.compile(r"@deprecated\b"), "@deprecated"),
    # .NET / C#
    (re.compile(r"\[Obsolete(?:\]|\()"), "[Obsolete]"),
    # Rust
    (re.compile(r"#\[deprecated(?:\]|\()"), "#[deprecated]"),
    # Python
    (re.compile(r"(?:warnings\.warn|warn)\s*\(\s*['\"][^'\"]*['\"]\s*,\s*DeprecationWarning"), "DeprecationWarning"),
    (re.compile(r"\b@deprecated\b"), "python @deprecated"),
]

# A "migration hint" that would satisfy the check: replacement words,
# version targets, or tracking references.
_HINT_RE = re.compile(
    r"\b(?:use|Use|prefer|Prefer|replaced\s+by|Replaced\s+by|instead)\b"
    r"|#\d+"
    r"|https?://"
    r"|\b[A-Z]{2,5}-\d+\b"                      # ABC-123
    r"|\bv\d+(?:\.\d+)*\b"                      # v8 / v8.1
    r"|\bsince\s+\d"
    r"|\buntil\s+\d"
    r"|\bremoved?\s+in\s+\d",
)


@register
class DeprecationMigration(Check):
    name = "deprecation-has-migration-path"

    def run(
        self,
        file_path: Path,
        repo_root: Path,
        config: dict[str, Any],
        rule_id: str,
        concept_id: str,
        severity: str,
    ) -> list[Finding]:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        rel = file_path.relative_to(repo_root).as_posix()
        lines = text.splitlines()
        findings: list[Finding] = []
        seen: set[tuple[int, str]] = set()

        for lineno, raw in enumerate(lines, start=1):
            # Skip the check file itself when scanning constantia's own repo.
            if "_DEPRECATION_MARKERS" in raw or "_HINT_RE" in raw:
                continue
            for pat, label in _DEPRECATION_MARKERS:
                m = pat.search(raw)
                if not m:
                    continue
                # [Obsolete("message")] with a non-empty string argument counts
                # as a hint only if the argument itself matches _HINT_RE. We
                # cover that below by scanning the same line.
                if _has_hint_near(lines, lineno):
                    continue
                key = (lineno, label)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule_id=rule_id, concept_id=concept_id, severity=severity,
                    file=rel, line=lineno,
                    message=f"{label} without migration path (no 'use X', version, or ticket nearby)",
                    evidence={"marker": label, "text": raw.strip()[:160]},
                ))
                break  # one finding per line is enough
        return findings


def _has_hint_near(lines: list[str], lineno: int) -> bool:
    """Check the marker line plus the five preceding and five following lines.

    Both directions: JSDoc blocks carry the `@deprecated` tag before the
    migration hint; .NET XML doc and Python docstrings can carry it after.
    ±5 is wide enough to cover multi-line deprecation decorators (e.g.
    Python @deprecated("... very long message ...")) whose URL lands
    several lines down from the tag.
    """
    lo = max(1, lineno - 5)
    hi = min(len(lines), lineno + 5)
    for i in range(lo, hi + 1):
        if _HINT_RE.search(lines[i - 1]):
            return True
    return False
