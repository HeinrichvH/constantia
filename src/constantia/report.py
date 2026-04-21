"""Two-section report: guided findings and llm_investigated findings.

The sections are strictly separated — not by convention but by how
they're built: guided rules produce `RuleResult` objects, LLM rules
produce `LlmRuleResult` objects, and each section has its own
`content_hash`. The hashes are sha256 of the section's canonical JSON
(sorted keys, stable floats), so identical inputs always yield
identical hashes — useful for "no change since last run" gating in CI.

The rule schema already enforces `guided` xor `llm_investigated` via
a oneOf, so a single rule can never appear in both sections.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .checks import Finding
from .config import Catalogue
from .runner import LlmRuleResult, RuleResult
from .verifier import VerifierDecision


@dataclass(frozen=True)
class Report:
    generated_at: str
    constantia_version: str
    repo_root: str
    config_path: str
    guided: dict[str, Any]
    llm: dict[str, Any]

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(asdict(self), indent=indent, sort_keys=True)


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        "rule_id": f.rule_id,
        "concept_id": f.concept_id,
        "severity": f.severity,
        "file": f.file,
        "line": f.line,
        "message": f.message,
        "evidence": f.evidence,
    }


def _guided_section(results: list[RuleResult]) -> dict[str, Any]:
    rules = [
        {
            "rule_id": r.rule.id,
            "concept_id": r.rule.concept_id,
            "severity": r.rule.severity,
            "files_scanned": r.files_scanned,
            "findings": [_finding_to_dict(f) for f in r.findings],
        }
        for r in results
    ]
    body = {"rules": rules}
    return {**body, "content_hash": _canonical_hash(body)}


def _decision_to_dict(d: VerifierDecision) -> dict[str, Any]:
    return {
        "finding": _finding_to_dict(d.finding),
        "verdict": d.verdict,
        "reason": d.reason,
    }


def _llm_section(results: list[LlmRuleResult]) -> dict[str, Any]:
    rules = [
        {
            "rule_id": r.rule.id,
            "concept_id": r.rule.concept_id,
            "severity": r.rule.severity,
            "files_scanned": r.files_scanned,
            "verdict_counts": _verdict_counts(r),
            "findings": [_finding_to_dict(f) for f in r.findings],
            "dropped": [_decision_to_dict(d) for d in r.dropped],
        }
        for r in results
    ]
    body = {"rules": rules}
    return {**body, "content_hash": _canonical_hash(body)}


def _verdict_counts(r: LlmRuleResult) -> dict[str, int]:
    counts = {"fit": 0, "violation": 0, "uncertain": 0, "not_applicable": 0, "error": 0}
    for v in r.verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return counts


def _canonical_hash(obj: Any) -> str:
    """sha256 of JSON with sorted keys + stable separators."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_report(
    cat: Catalogue,
    repo_root: Path,
    config_path: Path,
    *,
    guided_results: list[RuleResult],
    llm_results: list[LlmRuleResult],
) -> Report:
    return Report(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        constantia_version=__version__,
        repo_root=str(repo_root),
        config_path=str(config_path),
        guided=_guided_section(guided_results),
        llm=_llm_section(llm_results),
    )


def render_markdown(report: Report) -> str:
    """Terse markdown renderer — one section per rule, no huge tables."""
    lines: list[str] = []
    lines.append(f"# Constantia report")
    lines.append("")
    lines.append(f"- generated: `{report.generated_at}`")
    lines.append(f"- constantia: `{report.constantia_version}`")
    lines.append(f"- repo: `{report.repo_root}`")
    lines.append(f"- config: `{report.config_path}`")
    lines.append("")
    lines.append("## Guided findings")
    lines.append(f"_content hash: `{report.guided['content_hash']}`_")
    lines.append("")
    _render_rules(lines, report.guided["rules"], llm=False)
    lines.append("")
    lines.append("## LLM-investigated findings")
    lines.append(f"_content hash: `{report.llm['content_hash']}`_")
    lines.append("")
    _render_rules(lines, report.llm["rules"], llm=True)
    return "\n".join(lines) + "\n"


def _render_rules(lines: list[str], rules: list[dict[str, Any]], *, llm: bool) -> None:
    if not rules:
        lines.append("_(no rules in this section)_")
        return
    for r in rules:
        n = len(r["findings"])
        head = (
            f"### `{r['rule_id']}` → `{r['concept_id']}` "
            f"[{r['severity']}] — {r['files_scanned']} file(s), {n} finding(s)"
        )
        lines.append(head)
        if llm:
            vc = r["verdict_counts"]
            lines.append(
                f"_verdicts: {vc['fit']} fit · {vc['violation']} violation · "
                f"{vc['uncertain']} uncertain · {vc['not_applicable']} n/a · "
                f"{vc.get('error', 0)} error_"
            )
        if n == 0:
            lines.append("")
            lines.append("_clean_")
            lines.append("")
            continue
        for f in r["findings"]:
            loc = f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
            lines.append(f"- `{loc}` — {f['message']}")
        if llm and r.get("dropped"):
            lines.append("")
            lines.append(f"_verifier dropped {len(r['dropped'])} finding(s):_")
            for d in r["dropped"]:
                fd = d["finding"]
                loc = f"{fd['file']}:{fd['line']}" if fd.get("line") else fd["file"]
                lines.append(f"  - `{loc}` — {d['reason']}")
        lines.append("")
