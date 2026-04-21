"""Tests for the two-section report.

Stdlib only. Run: PYTHONPATH=src python3 -m tests.test_report
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from constantia.checks import Finding
from constantia.config import Catalogue, Concept, Rule
from constantia.llm import Verdict
from constantia.report import build_report, render_markdown
from constantia.runner import LlmRuleResult, RuleResult
from constantia.verifier import VerifierDecision


def _cat() -> Catalogue:
    return Catalogue(
        concepts=(Concept(id="c1", name="c1", principle="p", rationale="r"),),
        rules=(
            Rule(id="g1", concept_id="c1", name="n", description="d", severity="high",
                 type="guided", selector={"file_glob": "*"}, guided={"check": "x"}),
            Rule(id="l1", concept_id="c1", name="n", description="d", severity="critical",
                 type="llm_investigated", selector={"file_glob": "*"},
                 llm_investigated={"investigation": "i"}),
        ),
    )


def _finding(rule_id: str, line: int = 10) -> Finding:
    return Finding(
        rule_id=rule_id, concept_id="c1", severity="high",
        file="src/x.cs", line=line, message=f"msg for {rule_id}", evidence={"symbol": "S"},
    )


def test_report_sections_have_stable_content_hashes() -> None:
    cat = _cat()
    guided = cat.rules[0]
    llm = cat.rules[1]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        guided_results = [RuleResult(rule=guided, files_scanned=2, findings=(_finding("g1"),))]
        verdict = Verdict(file="src/x.cs", verdict="violation", summary="s", findings=(), raw={})
        llm_results = [LlmRuleResult(
            rule=llm, files_scanned=1,
            verdicts=(verdict,),
            findings=(_finding("l1"),),
            dropped=(),
        )]
        r1 = build_report(cat, root, Path("examples/aquilo"),
                          guided_results=guided_results, llm_results=llm_results)
        r2 = build_report(cat, root, Path("examples/aquilo"),
                          guided_results=guided_results, llm_results=llm_results)
        assert r1.guided["content_hash"] == r2.guided["content_hash"]
        assert r1.llm["content_hash"] == r2.llm["content_hash"]
        assert r1.guided["content_hash"].startswith("sha256:")


def test_report_hash_changes_when_findings_change() -> None:
    cat = _cat()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        base = [RuleResult(rule=cat.rules[0], files_scanned=1, findings=(_finding("g1", line=10),))]
        mutated = [RuleResult(rule=cat.rules[0], files_scanned=1, findings=(_finding("g1", line=11),))]
        r1 = build_report(cat, root, Path("."), guided_results=base, llm_results=[])
        r2 = build_report(cat, root, Path("."), guided_results=mutated, llm_results=[])
        assert r1.guided["content_hash"] != r2.guided["content_hash"]
        # llm section is empty in both → hashes identical
        assert r1.llm["content_hash"] == r2.llm["content_hash"]


def test_report_sections_are_disjoint() -> None:
    """The rule schema enforces guided xor llm; report must reflect that."""
    cat = _cat()
    guided_results = [RuleResult(rule=cat.rules[0], files_scanned=1, findings=(_finding("g1"),))]
    llm_results = [LlmRuleResult(
        rule=cat.rules[1], files_scanned=1,
        verdicts=(Verdict(file="src/x.cs", verdict="fit", summary="", findings=(), raw={}),),
        findings=(),
        dropped=(VerifierDecision(finding=_finding("l1"), verdict="drop", reason="mock"),),
    )]
    with tempfile.TemporaryDirectory() as td:
        r = build_report(_cat(), Path(td), Path("."),
                         guided_results=guided_results, llm_results=llm_results)
        guided_ids = {g["rule_id"] for g in r.guided["rules"]}
        llm_ids = {g["rule_id"] for g in r.llm["rules"]}
        assert guided_ids == {"g1"}
        assert llm_ids == {"l1"}
        assert guided_ids.isdisjoint(llm_ids)


def test_report_json_round_trip_and_markdown_renders() -> None:
    cat = _cat()
    guided_results = [RuleResult(rule=cat.rules[0], files_scanned=3, findings=(_finding("g1"),))]
    llm_results = [LlmRuleResult(
        rule=cat.rules[1], files_scanned=2,
        verdicts=(
            Verdict(file="src/a.cs", verdict="fit", summary="", findings=(), raw={}),
            Verdict(file="src/b.cs", verdict="violation", summary="", findings=(), raw={}),
        ),
        findings=(_finding("l1"),),
        dropped=(VerifierDecision(finding=_finding("l1", line=99), verdict="drop", reason="mock-fixture"),),
    )]
    with tempfile.TemporaryDirectory() as td:
        r = build_report(cat, Path(td), Path("."),
                         guided_results=guided_results, llm_results=llm_results)
        obj = json.loads(r.to_json())
        assert obj["guided"]["content_hash"] == r.guided["content_hash"]
        md = render_markdown(r)
        assert "## Guided findings" in md
        assert "## LLM-investigated findings" in md
        assert "verifier dropped" in md
        assert "1 fit · 1 violation" in md


def _run() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
