"""Check: every backtick-quoted repo-relative path in a markdown doc exists.

Extracts backtick tokens that look like paths (contain `/`, no
whitespace, no URL scheme), resolves each relative to repo_root, and
emits a Finding for every miss. Pure existence — the *meaning* check
(does the file still do what the paragraph claims?) is the
llm_investigated sibling rule.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Check, Finding, register


# `some/path` or `some/path#anchor` — no spaces, no schemes, at least one slash.
_PATH_RE = re.compile(r"`([^`\n\s]*?/[^`\n\s]*?)`")
_URL_PREFIXES = ("http://", "https://", "mailto:", "ftp://")
# CIDR notation: IP address followed by slash and prefix length (e.g., 10.10.69.0/24)
_CIDR_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$")
# Kubernetes label namespaces (e.g., node-role.kubernetes.io/control-plane). The
# kubernetes.io / k8s.io suffixes match the standard upstream label spaces; teams
# running custom label domains can extend this regex in their own fork.
_K8S_LABEL_RE = re.compile(r"^[a-z0-9.-]+\.(kubernetes\.io|k8s\.io)/.*")


@register
class MarkdownCitedPathsExist(Check):
    name = "markdown-cited-paths-exist"

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
        doc_dir = file_path.parent
        findings: list[Finding] = []
        seen: set[tuple[int, str]] = set()
        for m in _PATH_RE.finditer(text):
            raw = m.group(1).split("#", 1)[0].split("?", 1)[0]
            if not raw or raw.startswith(_URL_PREFIXES):
                continue
            # Skip glob-ish tokens and code-y fragments masquerading as paths.
            if any(ch in raw for ch in "*{}<>()"):
                continue
            # Skip things that are obviously not repo paths (e.g. npm scoped
            # package names like `@scope/package`, MIME types like `text/plain`).
            if raw.startswith("@") or ":" in raw or "=" in raw:
                continue
            # Absolute (`/usr/...`, `/loop` slash commands) and home-relative
            # (`~/.config/...`) paths aren't repo-relative citations.
            if raw.startswith("/") or raw.startswith("~"):
                continue
            # Skip CIDR notations (e.g., 10.10.69.0/24) — these are network subnets,
            # not file paths.
            if _CIDR_RE.match(raw):
                continue
            # Skip Kubernetes labels (e.g., node-role.kubernetes.io/control-plane) —
            # these are labels, not file paths.
            if _K8S_LABEL_RE.match(raw):
                continue
            # Require a file extension somewhere OR a doc-relative prefix —
            # MIME types (`text/plain`) have neither.
            if "." not in raw and not raw.startswith(("./", "../")):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            if (line_no, raw) in seen:
                continue
            seen.add((line_no, raw))

            resolved = _resolve(raw, repo_root, doc_dir)
            if resolved is None or not resolved.exists():
                findings.append(
                    Finding(
                        rule_id=rule_id,
                        concept_id=concept_id,
                        severity=severity,
                        file=rel,
                        line=line_no,
                        message=f"cited path `{raw}` does not exist",
                        evidence={"path": raw},
                    )
                )
        return findings


def _resolve(raw: str, repo_root: Path, doc_dir: Path) -> Path | None:
    """Try repo-root-relative first, then doc-relative. Returns first existing candidate.

    Both candidates must stay inside repo_root (no `../` escapes). If
    neither exists, return the repo-root candidate so the caller can
    report a clean "does not exist" message.
    """
    raw = raw.lstrip("/")
    root_resolved = repo_root.resolve()
    fallback: Path | None = None
    for base in (repo_root, doc_dir):
        try:
            candidate = (base / raw).resolve()
            candidate.relative_to(root_resolved)
        except (ValueError, OSError):
            continue
        if candidate.exists():
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback
