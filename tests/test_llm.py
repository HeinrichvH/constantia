"""Tests for the LLM investigator path.

Stdlib only. The goose subprocess is replaced by a canned-response
runner, so these tests are hermetic — no network, no goose binary.
Run: PYTHONPATH=src python3 -m tests.test_llm
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from constantia.config import Concept, Rule
from constantia.llm import (
    _extract_json,
    _find_symbol_lines,
    _pick_nearest,
    _split_citation,
    invoke_investigator,
    resolve_verdict,
    verdict_to_findings,
)
from constantia.runner import run_llm_rule
from constantia.config import Catalogue


def _stub_runner(response: dict):
    """Return a GooseRunner that replays the given JSON object on the last line."""

    def _run(argv: list[str], cwd: str) -> str:
        noise = "tool: text_editor view\n...trace lines...\n"
        return noise + json.dumps(response) + "\n"

    return _run


def test_extract_json_picks_last_json_line() -> None:
    raw = 'noise\n{"early": 1}\nmore trace\n{"summary": "", "items": []}\n'
    assert _extract_json(raw)["items"] == []


def test_split_citation_handles_range() -> None:
    assert _split_citation("src/x.cs:42") == ("src/x.cs", 42)
    assert _split_citation("src/x.cs:10-20") == ("src/x.cs", 10)
    assert _split_citation("nope") == (None, None)


def test_find_symbol_lines_returns_all_occurrences() -> None:
    lines = ["a", "b", "HELLO", "d", "HELLO again", "e"]
    assert _find_symbol_lines(lines, "HELLO") == [3, 5]
    assert _find_symbol_lines(lines, "missing") == []


def test_pick_nearest_prefers_occurrence_near_hint() -> None:
    assert _pick_nearest([10, 50, 100], 48) == 50
    assert _pick_nearest([10, 50, 100], None) == 10
    assert _pick_nearest([], 5) == 0


def _sample_concept() -> Concept:
    return Concept(
        id="grpc-request-base-forwarding",
        name="Forward request.Base",
        principle="Handlers forward request.Base, never fabricate.",
        rationale="Preserves userId/trace/locale across hops.",
    )


def _sample_rule() -> Rule:
    return Rule(
        id="base-forwarded-not-fabricated-csharp",
        concept_id="grpc-request-base-forwarding",
        name="forward base",
        description="d",
        severity="critical",
        type="llm_investigated",
        selector={"file_glob": "src/**/*.cs"},
        llm_investigated={"investigation": "Apply {concept.id}."},
    )


def test_invoke_investigator_returns_error_verdict_on_garbage() -> None:
    """A single flaky goose call must not torch the whole rule's run."""

    def _broken(argv, cwd):
        return "no JSON here, just noise\nanother line\n"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src/X.cs").write_text("class X {}\n")
        v = invoke_investigator(
            _sample_concept(), _sample_rule(),
            "src/X.cs", root,
            runner=_broken,
        )
        assert v.verdict == "error"
        assert "failed" in v.summary


def test_invoke_investigator_parses_verdict() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src/Handler.cs").write_text("class Foo { void Bar() {} }\n")
        stub = _stub_runner({
            "summary": "All outbound calls forward request.Base.",
            "items": [
                {
                    "verdict": "fit",
                    "citation": "src/Handler.cs:1",
                    "symbol": "class Foo",
                    "message": "Handler forwards request.Base.",
                },
            ],
        })
        v = invoke_investigator(
            _sample_concept(), _sample_rule(),
            "src/Handler.cs", root,
            runner=stub,
        )
        assert v.verdict == "fit"
        assert len(v.items) == 1
        assert v.items[0]["verdict"] == "fit"


def test_invoke_investigator_derives_violation_when_any_item_violates() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src/Handler.cs").write_text("class Foo { void Bar() {} }\n")
        stub = _stub_runner({
            "summary": "mixed",
            "items": [
                {"verdict": "fit", "citation": "src/Handler.cs:1", "symbol": "class Foo", "message": "ok"},
                {"verdict": "violation", "citation": "src/Handler.cs:1", "symbol": "void Bar", "message": "bad"},
                {"verdict": "not_applicable", "citation": "src/Handler.cs:1", "symbol": "class Foo", "message": "n/a"},
            ],
        })
        v = invoke_investigator(
            _sample_concept(), _sample_rule(),
            "src/Handler.cs", root,
            runner=stub,
        )
        assert v.verdict == "violation"


def test_invoke_investigator_back_compat_legacy_findings_shape() -> None:
    """Old-shape response (verdict + findings[]) still parses — maps onto items."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src/Handler.cs").write_text("class Foo { void Bar() {} }\n")
        stub = _stub_runner({
            "verdict": "violation",
            "summary": "legacy",
            "findings": [
                {"message": "m", "citation": "src/Handler.cs:1", "symbol": "class Foo"},
            ],
        })
        v = invoke_investigator(
            _sample_concept(), _sample_rule(),
            "src/Handler.cs", root,
            runner=stub,
        )
        assert v.verdict == "violation"
        assert v.items[0]["verdict"] == "violation"
        assert v.items[0]["symbol"] == "class Foo"


def test_resolve_verdict_drops_fabricated_and_line_corrects() -> None:
    """LLM often gets the symbol right but the line wrong. Resolver must:
    - drop findings whose symbol isn't in the file,
    - use the actual line of the symbol (closest to hint) when it IS.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        file_rel = "src/Bad.cs"
        (root / file_rel).write_text(
            "class Bad {\n"                                              # 1
            "    void Call() {\n"                                        # 2
            "        // padding\n"                                       # 3
            "        // padding\n"                                       # 4
            "        // padding\n"                                       # 5
            "        var req = new GrpcRequestBase { Corr = 1 };\n"      # 6
            "        stub.Do(req);\n"                                    # 7
            "    }\n"                                                    # 8
            "}\n"                                                        # 9
        )
        from constantia.llm import Verdict
        v = Verdict(
            file=file_rel,
            verdict="violation",
            summary="fabricates base",
            items=(
                # Real violation — LLM said line 2 but symbol is on line 6.
                {"verdict": "violation", "message": "fabricated base", "citation": f"{file_rel}:2", "symbol": "new GrpcRequestBase"},
                # Ghost — symbol doesn't appear in file.
                {"verdict": "violation", "message": "ghost", "citation": f"{file_rel}:5", "symbol": "NotInFile"},
                # A fit item — audit trail only, must NOT become a finding.
                {"verdict": "fit", "message": "ok", "citation": f"{file_rel}:1", "symbol": "class Bad"},
            ),
            raw={},
        )
        findings, stats = resolve_verdict(v, _sample_rule(), root)
        assert len(findings) == 1, [f.message for f in findings]
        assert findings[0].line == 6  # resolved via grep, not the cited 2
        assert findings[0].evidence["cited_line"] == 2
        assert findings[0].evidence["resolved_line"] == 6
        assert stats.kept == 1
        assert stats.dropped_symbol_absent == 1
        assert stats.line_corrected == 1


def test_resolve_verdict_picks_closest_when_symbol_repeats() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        file_rel = "src/Many.cs"
        (root / file_rel).write_text("\n".join([
            "// line1",
            "Foo.Do();",   # 2
            "// line3",
            "// line4",
            "Foo.Do();",   # 5
            "// line6",
            "Foo.Do();",   # 7
        ]) + "\n")
        from constantia.llm import Verdict
        v = Verdict(
            file=file_rel, verdict="violation", summary="",
            items=({"verdict": "violation", "message": "m", "citation": f"{file_rel}:6", "symbol": "Foo.Do()"},),
            raw={},
        )
        findings, _ = resolve_verdict(v, _sample_rule(), root)
        assert findings[0].line == 5  # closest to hint 6
        assert findings[0].evidence["symbol_occurrences"] == [2, 5, 7]


def test_run_llm_rule_preserves_coverage_guarantee() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        for i in range(3):
            (root / f"src/File{i}.cs").write_text(f"class F{i} {{}}\n")
        concept = _sample_concept()
        rule = _sample_rule()
        cat = Catalogue(concepts=(concept,), rules=(rule,))
        stub = _stub_runner({"summary": "", "items": [
            {"verdict": "fit", "citation": "x:1", "symbol": "class", "message": "ok"},
        ]})
        res = run_llm_rule(rule, cat, root, runner=stub)
        assert res.files_scanned == 3
        assert len(res.verdicts) == 3
        assert res.findings == ()


def test_run_llm_rule_respects_limit() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        for i in range(5):
            (root / f"src/File{i}.cs").write_text(f"class F{i} {{}}\n")
        cat = Catalogue(concepts=(_sample_concept(),), rules=(_sample_rule(),))
        stub = _stub_runner({"summary": "", "items": [
            {"verdict": "fit", "citation": "x:1", "symbol": "class", "message": "ok"},
        ]})
        res = run_llm_rule(_sample_rule(), cat, root, limit=2, runner=stub)
        assert res.files_scanned == 2
        assert len(res.verdicts) == 2


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
