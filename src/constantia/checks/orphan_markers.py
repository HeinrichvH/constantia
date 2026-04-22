"""Check: TODO/FIXME/skipped-test markers without a ticket, author, or URL.

Drift pattern surfaced across Rust/Python/TypeScript/C# audits: `TODO:`,
`FIXME:`, `it.skip(...)`, `[Ignore]`, `#[allow(dead_code)]` and friends
accumulate in mature codebases. When a marker carries a reference —
`TODO(#123)`, `TODO(alice):`, a Jira key, a URL, or a version target —
it's trackable. When it doesn't, it rots.

The check is deliberately language-agnostic: it scans any source file for
the marker patterns, then looks at the marker line and the two
surrounding lines for a reference pattern. If none is found, the marker
is an orphan and surfaces as a finding.

This is intentionally strict. Prose-only explanations ("skipped because
flaky") don't count: that's exactly the drift the check is pushing
against — reasons written for past-you that future-you can't action.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Check, Finding, register


# Markers split into two families:
#  - "marker" patterns are TODO/FIXME/XXX/HACK anywhere in a comment.
#  - "skip" patterns are test-framework opt-outs.
_TODO_RE = re.compile(
    r"(?P<kind>TODO|FIXME|XXX|HACK)"
    r"(?:\((?P<attrib>[^)\n]{1,60})\))?"   # optional author/issue inside parens
    r"\s*[:\-]?",
)

_SKIP_RES = [
    (re.compile(r"(?:^|\s)(?:it|test|describe)\.skip\s*\("), "test.skip"),
    (re.compile(r"(?:^|\s)(?:it|test)\.skipIf\s*\("), "test.skipIf"),
    (re.compile(r"(?:^|\s)xit\s*\("), "xit"),
    (re.compile(r"@pytest\.mark\.skip\b"), "pytest.mark.skip"),
    (re.compile(r"#\[ignore\]"), "#[ignore]"),
    (re.compile(r"\[Ignore(?:\]|\()"), "[Ignore]"),
    (re.compile(r"#\[allow\(dead_code\)\]"), "#[allow(dead_code)]"),
]

# A reference pattern: issue number, URL, version target, Jira-style key,
# or an explicit author attribution inside TODO(...).
_REF_RE = re.compile(
    r"#\d+"                                # #123
    r"|https?://"                          # full URL
    r"|\b[A-Z]{2,5}-\d+\b"                 # ABC-123 Jira-style
    r"|\bv\d+(?:\.\d+)*\b"                 # v8, v8.1
    r"|\bsince\s+\d"                       # since 1.2
    r"|\buntil\s+\d",
    re.IGNORECASE,
)

_COMMENT_RE = re.compile(r"(?://|#|--|;;|/\*|\*\s|<!--)")


@register
class OrphanMarkers(Check):
    name = "orphan-markers-without-reference"

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
            # Skip obvious test fixtures for the regex patterns themselves.
            if "_REF_RE" in raw or "_TODO_RE" in raw:
                continue

            # --- TODO/FIXME/XXX/HACK ---------------------------------
            for m in _TODO_RE.finditer(raw):
                # Only count when in a comment context — avoid string literals
                # whose content happens to contain "TODO".
                if not _looks_like_comment(raw, m.start()):
                    continue
                attrib = (m.group("attrib") or "").strip()
                if attrib:  # TODO(alice) / TODO(#123) / TODO(v8) etc.
                    continue
                if _has_reference_near(lines, lineno):
                    continue
                key = (lineno, m.group("kind"))
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule_id=rule_id, concept_id=concept_id, severity=severity,
                    file=rel, line=lineno,
                    message=f"{m.group('kind')} without ticket, author, or URL",
                    evidence={"kind": m.group("kind"), "text": raw.strip()[:160]},
                ))

            # --- Test-skip / dead-code markers ------------------------
            for pat, label in _SKIP_RES:
                if not pat.search(raw):
                    continue
                if _has_reference_near(lines, lineno):
                    continue
                key = (lineno, label)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule_id=rule_id, concept_id=concept_id, severity=severity,
                    file=rel, line=lineno,
                    message=f"{label} without ticket, author, or URL",
                    evidence={"kind": label, "text": raw.strip()[:160]},
                ))

        return findings


def _looks_like_comment(line: str, idx: int) -> bool:
    """Return True if the character at `idx` is inside a comment on this line.

    Cheap heuristic: a comment-starter (`//`, `#`, `--`, `;;`, `/*`, `<!--`,
    or `*` as the first non-space char of the line) appears before `idx`.
    Misses block-comment continuations that don't start with `*`, but
    catches the overwhelming majority of cases across C/C++/Rust/Go/TS/
    Python/shell/HTML/Razor.
    """
    prefix = line[:idx]
    # Python/Ruby/shell comment at start-of-line or after whitespace.
    m = _COMMENT_RE.search(prefix)
    if m:
        return True
    # Markdown/HTML block-comment body: leading whitespace then `*`.
    stripped = line.lstrip()
    if stripped.startswith("*") and line.index(stripped[0]) < idx:
        return True
    return False


def _has_reference_near(lines: list[str], lineno: int) -> bool:
    """Search the marker line ±2 for any reference pattern."""
    lo = max(1, lineno - 2)
    hi = min(len(lines), lineno + 2)
    for i in range(lo, hi + 1):
        if _REF_RE.search(lines[i - 1]):
            return True
    return False
