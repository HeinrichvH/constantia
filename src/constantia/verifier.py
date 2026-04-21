"""Adversarial second pass on LLM findings.

Each surviving finding from the investigator is handed to a separate
goose+mistral subprocess with the concept principle + the finding's
citation. The verifier re-reads the file, judges whether the claim
actually holds, and returns keep/drop. Dropped findings never reach
the report.

This is the third phase of the three-phase model (discovery → scan →
verify). It's cheap relative to the investigator because it runs only
on findings, not on every selector-matched file.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from .checks import Finding
from .config import Concept, Rule
from .llm import GooseRunner, _default_goose_runner, _escape_nl, _extract_json, LlmError


RECIPE_PATH = Path(__file__).resolve().parents[2] / "recipes" / "verifier" / "recipe.yaml"


@dataclass(frozen=True)
class VerifierDecision:
    finding: Finding
    verdict: str  # keep | drop
    reason: str


def verify_finding(
    concept: Concept,
    rule: Rule,
    finding: Finding,
    repo_root: Path,
    *,
    max_turns: int = 15,
    recipe_path: Path = RECIPE_PATH,
    runner: GooseRunner = _default_goose_runner,
) -> VerifierDecision:
    params = {
        "concept_id": concept.id,
        "concept_principle": concept.principle,
        "rule_id": rule.id,
        "file_path": finding.file,
        "line": str(finding.line or 0),
        "symbol": finding.evidence.get("symbol", "") if isinstance(finding.evidence, dict) else "",
        "message": finding.message,
        "evidence": finding.evidence.get("evidence", "") if isinstance(finding.evidence, dict) else "",
    }
    argv = ["goose", "run", "--recipe", str(recipe_path),
            "--no-session", "--max-turns", str(max_turns),
            "--max-tool-repetitions", "6"]
    for k, v in params.items():
        argv.extend(["--params", f"{k}={_escape_nl(v)}"])

    raw = runner(argv, str(repo_root))
    try:
        obj = _extract_json(raw)
    except LlmError:
        # On parse failure default to keep — the report's job is to
        # surface candidates; we don't silently swallow on tool error.
        return VerifierDecision(finding=finding, verdict="keep", reason="verifier output unparseable; defaulting to keep")

    verdict = obj.get("verdict", "keep")
    reason = obj.get("reason", "")
    if verdict not in ("keep", "drop"):
        verdict = "keep"
    return VerifierDecision(finding=finding, verdict=verdict, reason=reason)


def verify_findings(
    concept: Concept,
    rule: Rule,
    findings: list[Finding],
    repo_root: Path,
    *,
    runner: GooseRunner = _default_goose_runner,
    concurrency: int = 1,
    progress: Callable[[int, int, Finding], None] | None = None,
) -> tuple[list[Finding], list[VerifierDecision]]:
    """Return (kept_findings, all_decisions). Decisions preserve audit trail."""
    total = len(findings)
    decisions: list[VerifierDecision] = [None] * total  # type: ignore[list-item]

    def _one(idx: int) -> tuple[int, VerifierDecision]:
        return idx, verify_finding(concept, rule, findings[idx], repo_root, runner=runner)

    lock = Lock()
    done = {"n": 0}

    def _record(idx: int, d: VerifierDecision) -> None:
        with lock:
            decisions[idx] = d
            done["n"] += 1
            if progress:
                progress(done["n"], total, findings[idx])

    if concurrency <= 1:
        for i in range(total):
            idx, d = _one(i)
            _record(idx, d)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_one, i) for i in range(total)]
            for fut in as_completed(futs):
                idx, d = fut.result()
                _record(idx, d)

    kept = [f for f, d in zip(findings, decisions) if d.verdict == "keep"]
    return kept, decisions
