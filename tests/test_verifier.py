"""Tests for the adversarial verifier.

Stdlib only. Goose subprocess is replaced by a canned-response runner.
Run: PYTHONPATH=src python3 -m tests.test_verifier
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from constantia.checks import Finding
from constantia.config import Catalogue, Concept, Rule
from constantia.runner import run_llm_rule
from constantia.verifier import verify_finding, verify_findings


def _runner_returning(obj: dict):
    def _run(argv: list[str], cwd: str) -> str:
        return "trace line\n" + json.dumps(obj) + "\n"
    return _run


def _runner_cycle(responses: list[dict]):
    """Return a runner that cycles through the given responses call-by-call."""
    i = {"n": 0}

    def _run(argv: list[str], cwd: str) -> str:
        obj = responses[i["n"] % len(responses)]
        i["n"] += 1
        return "trace\n" + json.dumps(obj) + "\n"

    return _run


def _concept() -> Concept:
    return Concept(id="c1", name="c1", principle="p", rationale="r")


def _rule() -> Rule:
    return Rule(
        id="r1", concept_id="c1", name="n", description="d",
        severity="high", type="llm_investigated",
        selector={"file_glob": "src/**/*.cs"},
        llm_investigated={"investigation": "inv"},
    )


def _finding(file: str = "src/x.cs", line: int = 1) -> Finding:
    return Finding(
        rule_id="r1", concept_id="c1", severity="high",
        file=file, line=line, message="claims violation",
        evidence={"symbol": "Foo", "evidence": "quoted line"},
    )


def test_verify_finding_keeps() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = verify_finding(
            _concept(), _rule(), _finding(), Path(td),
            runner=_runner_returning({"verdict": "keep", "reason": "genuine drift"}),
        )
        assert d.verdict == "keep"
        assert "genuine" in d.reason


def test_verify_finding_drops() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = verify_finding(
            _concept(), _rule(), _finding(), Path(td),
            runner=_runner_returning({"verdict": "drop", "reason": "investigator misread; this is a mock fixture"}),
        )
        assert d.verdict == "drop"


def test_verify_finding_defaults_to_keep_on_parse_failure() -> None:
    def _broken(argv, cwd):
        return "tool trace with no json at all\n"

    with tempfile.TemporaryDirectory() as td:
        d = verify_finding(_concept(), _rule(), _finding(), Path(td), runner=_broken)
        assert d.verdict == "keep"
        assert "unparseable" in d.reason


def test_verify_findings_partitions() -> None:
    findings = [_finding(line=10), _finding(line=20), _finding(line=30)]
    runner = _runner_cycle([
        {"verdict": "keep", "reason": "ok"},
        {"verdict": "drop", "reason": "misread"},
        {"verdict": "keep", "reason": "ok"},
    ])
    with tempfile.TemporaryDirectory() as td:
        kept, decisions = verify_findings(_concept(), _rule(), findings, Path(td), runner=runner)
        assert len(kept) == 2
        assert len(decisions) == 3
        assert sum(1 for d in decisions if d.verdict == "drop") == 1


def test_run_llm_rule_applies_verifier_pipeline() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        file_rel = "src/Bad.cs"
        (root / file_rel).write_text(
            "class Bad {\n"
            "    void Call() {\n"
            "        var req = new GrpcRequestBase { CorrelationId = Guid.NewGuid() };\n"
            "    }\n"
            "}\n"
        )

        investigator = _runner_returning({
            "verdict": "violation",
            "summary": "fabricates base",
            "findings": [
                {"message": "fabricates base", "citation": f"{file_rel}:3", "symbol": "new GrpcRequestBase"},
            ],
        })
        dropper = _runner_returning({"verdict": "drop", "reason": "mock fixture, allowed"})

        cat = Catalogue(concepts=(_concept(),), rules=(_rule(),))
        res = run_llm_rule(
            _rule(), cat, root,
            runner=investigator,
            verifier_runner=dropper,
        )
        assert res.files_scanned == 1
        assert res.findings == ()  # dropped by verifier
        assert len(res.dropped) == 1
        assert res.dropped[0].verdict == "drop"


def test_run_llm_rule_no_verify_preserves_findings() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        file_rel = "src/Bad.cs"
        (root / file_rel).write_text(
            "class Bad {\n"
            "    void Call() {\n"
            "        var req = new GrpcRequestBase { CorrelationId = Guid.NewGuid() };\n"
            "    }\n"
            "}\n"
        )
        investigator = _runner_returning({
            "verdict": "violation",
            "summary": "fabricates base",
            "findings": [
                {"message": "fabricates base", "citation": f"{file_rel}:3", "symbol": "new GrpcRequestBase"},
            ],
        })
        cat = Catalogue(concepts=(_concept(),), rules=(_rule(),))
        res = run_llm_rule(_rule(), cat, root, runner=investigator, verify=False)
        assert len(res.findings) == 1
        assert res.dropped == ()


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
