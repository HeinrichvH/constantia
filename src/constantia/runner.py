"""Run guided rules against a repo and collect findings.

Guided rules only — llm_investigated rules are the orchestrator's job
(step 6). This is the deterministic half: each selected file is handed
to the rule's registered check, and every Finding gets tagged back to
the originating rule + concept. No silent skips: a file that matched
the selector either appears clean or produces findings.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from .checkpoint import Checkpoint, checkpoint_fingerprint
from .checks import Finding, get_check
from .config import Catalogue, Concept, Rule
from .llm import (
    GooseRunner,
    ResolutionStats,
    Verdict,
    _default_goose_runner,
    invoke_investigator,
    resolve_verdict,
)
from .selector import Selector, select_files
from .verifier import VerifierDecision, verify_findings


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    files_scanned: int
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class LlmRuleResult:
    """LLM run — carries per-file verdicts so the coverage guarantee
    (`len(verdicts) == files_scanned`) is visible to the report writer.

    `findings` holds what survived verification (or the unverified
    investigator output if verification was skipped). `dropped`
    preserves what the verifier rejected, with reasons — this is the
    audit trail for the third phase.
    """

    rule: Rule
    files_scanned: int
    verdicts: tuple[Verdict, ...]
    findings: tuple[Finding, ...]
    dropped: tuple["VerifierDecision", ...] = ()
    resolution: ResolutionStats = ResolutionStats()


def run_guided_rule(rule: Rule, repo_root: Path) -> RuleResult:
    if rule.type != "guided":
        raise ValueError(f"rule {rule.id} is not guided (type={rule.type})")
    assert rule.guided is not None, f"guided rule {rule.id} missing guided block"
    check_name = rule.guided["check"]
    check = get_check(check_name)
    if check is None:
        raise LookupError(f"rule {rule.id}: no check registered under name '{check_name}'")

    sel = Selector.from_config(rule.selector)
    files = select_files(sel, repo_root)
    config = rule.guided.get("config", {}) or {}

    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(
            check.run(
                file_path=f,
                repo_root=repo_root,
                config=config,
                rule_id=rule.id,
                concept_id=rule.concept_id,
                severity=rule.severity,
            )
        )
    return RuleResult(rule=rule, files_scanned=len(files), findings=tuple(all_findings))


def run_llm_rule(
    rule: Rule,
    cat: Catalogue,
    repo_root: Path,
    *,
    limit: int | None = None,
    runner: GooseRunner = _default_goose_runner,
    verifier_runner: GooseRunner | None = None,
    max_turns: int = 40,
    verify: bool = True,
    concurrency: int = 1,
    checkpoint_dir: Path | None = None,
    discard_checkpoint_on_success: bool = False,
    progress: "Callable[[int, int, str], None] | None" = None,
    verify_progress: "Callable[[int, int, Finding], None] | None" = None,
    resumed_progress: "Callable[[int, int], None] | None" = None,
) -> LlmRuleResult:
    """Invoke the investigator once per selected file; parse + verify citations.

    `limit` caps how many files are actually investigated (for cost
    calibration). Files beyond the cap aren't counted in `files_scanned`
    — the cap is explicit; the coverage guarantee holds for the subset
    that was actually investigated. A full CI run leaves `limit=None`.
    """
    if rule.type != "llm_investigated":
        raise ValueError(f"rule {rule.id} is not llm_investigated (type={rule.type})")
    concept = cat.concept_by_id(rule.concept_id)
    if concept is None:
        raise LookupError(f"rule {rule.id}: concept {rule.concept_id} not found")

    sel = Selector.from_config(rule.selector)
    files = select_files(sel, repo_root)
    if limit is not None:
        files = files[:limit]

    verdicts: list[Verdict] = [None] * len(files)  # type: ignore[list-item]
    findings: list[Finding] = []
    agg = {"kept": 0, "dropped_wrong_file": 0, "dropped_symbol_absent": 0, "line_corrected": 0}
    total = len(files)
    rels = [f.relative_to(repo_root).as_posix() for f in files]

    # Checkpoint: load existing verdicts for this rule; resumed ones skip investigation.
    ckpt: Checkpoint | None = None
    resumed_verdicts: dict[str, Verdict] = {}
    if checkpoint_dir is not None:
        fp = checkpoint_fingerprint(concept, rule)
        ckpt = Checkpoint(Path(checkpoint_dir) / f"{rule.id}.jsonl", fp)
        resumed_verdicts = ckpt.load_existing()
        ckpt.ensure_header()

    pending_idxs: list[int] = []
    for i, rel in enumerate(rels):
        cached = resumed_verdicts.get(rel)
        if cached is not None:
            verdicts[i] = cached
            ff, stats = resolve_verdict(cached, rule, repo_root)
            findings.extend(ff)
            agg["kept"] += stats.kept
            agg["dropped_wrong_file"] += stats.dropped_wrong_file
            agg["dropped_symbol_absent"] += stats.dropped_symbol_absent
            agg["line_corrected"] += stats.line_corrected
        else:
            pending_idxs.append(i)

    if resumed_progress and resumed_verdicts:
        resumed_progress(len(resumed_verdicts), total)

    def _do_one(idx: int) -> tuple[int, Verdict, list[Finding], ResolutionStats]:
        v = invoke_investigator(
            concept, rule, rels[idx], repo_root,
            max_turns=max_turns, runner=runner,
        )
        if ckpt is not None:
            ckpt.append(v)
        file_findings, stats = resolve_verdict(v, rule, repo_root)
        return idx, v, file_findings, stats

    lock = Lock()
    done = {"n": len(resumed_verdicts)}

    def _record(idx: int, v: Verdict, ff: list[Finding], stats: ResolutionStats) -> None:
        with lock:
            verdicts[idx] = v
            findings.extend(ff)
            agg["kept"] += stats.kept
            agg["dropped_wrong_file"] += stats.dropped_wrong_file
            agg["dropped_symbol_absent"] += stats.dropped_symbol_absent
            agg["line_corrected"] += stats.line_corrected
            done["n"] += 1
            if progress:
                progress(done["n"], total, rels[idx])

    if concurrency <= 1:
        for i in pending_idxs:
            idx, v, ff, stats = _do_one(i)
            _record(idx, v, ff, stats)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_do_one, i) for i in pending_idxs]
            for fut in as_completed(futures):
                idx, v, ff, stats = fut.result()
                _record(idx, v, ff, stats)
    resolution = ResolutionStats(**agg)
    dropped: tuple[VerifierDecision, ...] = ()
    if verify and findings:
        kept, decisions = verify_findings(
            concept, rule, findings, repo_root,
            runner=verifier_runner or runner,
            concurrency=concurrency,
            progress=verify_progress,
        )
        findings = kept
        dropped = tuple(d for d in decisions if d.verdict == "drop")

    if ckpt is not None and discard_checkpoint_on_success:
        ckpt.discard()

    return LlmRuleResult(
        rule=rule,
        files_scanned=len(files),
        verdicts=tuple(verdicts),
        findings=tuple(findings),
        dropped=dropped,
        resolution=resolution,
    )


def run_all_guided(cat: Catalogue, repo_root: Path, *, rule_id: str | None = None) -> list[RuleResult]:
    results: list[RuleResult] = []
    for rule in cat.rules:
        if rule.type != "guided":
            continue
        if rule_id and rule.id != rule_id:
            continue
        results.append(run_guided_rule(rule, repo_root))
    return results
