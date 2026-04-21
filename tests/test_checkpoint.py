"""Tests for JSONL checkpoint + resume.

Stdlib only. Run: PYTHONPATH=src python3 -m tests.test_checkpoint
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from constantia.checkpoint import Checkpoint, checkpoint_fingerprint
from constantia.config import Catalogue, Concept, Rule
from constantia.llm import Verdict
from constantia.runner import run_llm_rule


def _concept() -> Concept:
    return Concept(id="c", name="n", principle="forward base", rationale="r")


def _rule(inv: str = "inv") -> Rule:
    return Rule(
        id="r", concept_id="c", name="n", description="d", severity="high",
        type="llm_investigated",
        selector={"file_glob": "src/**/*.cs"},
        llm_investigated={"investigation": inv},
    )


def test_fingerprint_changes_with_prompt() -> None:
    c, r1, r2 = _concept(), _rule("A"), _rule("B")
    assert checkpoint_fingerprint(c, r1) != checkpoint_fingerprint(c, r2)


def test_checkpoint_append_and_load_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "r.jsonl"
        fp = "sha256:abc"
        ck = Checkpoint(path, fp)
        ck.ensure_header()
        v = Verdict(file="src/x.cs", verdict="fit", summary="s", findings=(), raw={})
        ck.append(v)
        ck.append(Verdict(file="src/y.cs", verdict="violation", summary="",
                          findings=({"message": "m", "citation": "src/y.cs:1", "symbol": "T"},),
                          raw={}))
        ck2 = Checkpoint(path, fp)
        loaded = ck2.load_existing()
        assert set(loaded) == {"src/x.cs", "src/y.cs"}
        assert loaded["src/y.cs"].findings[0]["symbol"] == "T"


def test_checkpoint_discarded_on_fingerprint_mismatch() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "r.jsonl"
        Checkpoint(path, "sha256:old").ensure_header()
        Checkpoint(path, "sha256:old").append(
            Verdict(file="src/x.cs", verdict="fit", summary="", findings=(), raw={})
        )
        # New run with different fingerprint → load returns empty.
        fresh = Checkpoint(path, "sha256:new").load_existing()
        assert fresh == {}


def test_run_llm_rule_resumes_from_checkpoint() -> None:
    """Second invocation must not re-call the investigator for files already done."""
    calls: list[str] = []

    def _runner(argv, cwd):
        # Extract file_path param from argv.
        for i, a in enumerate(argv):
            if a == "--params" and i + 1 < len(argv) and argv[i + 1].startswith("file_path="):
                calls.append(argv[i + 1].split("=", 1)[1])
        return 'trace\n{"verdict":"fit","summary":"","findings":[]}\n'

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        for i in range(3):
            (root / f"src/F{i}.cs").write_text(f"class F{i} {{}}\n")
        cat = Catalogue(concepts=(_concept(),), rules=(_rule(),))
        ck_dir = root / ".ckpt"

        # First run — investigator called 3 times.
        res1 = run_llm_rule(_rule(), cat, root, runner=_runner, checkpoint_dir=ck_dir, verify=False)
        assert res1.files_scanned == 3
        assert len(calls) == 3

        # Second run — should resume all 3 without calling investigator again.
        calls.clear()
        res2 = run_llm_rule(_rule(), cat, root, runner=_runner, checkpoint_dir=ck_dir, verify=False)
        assert res2.files_scanned == 3
        assert calls == [], f"expected no new investigator calls, got {calls}"


def test_run_llm_rule_partial_resume_only_missing_files() -> None:
    """Checkpoint with 1/3 done → second run investigates only the missing 2."""
    calls: list[str] = []

    def _runner(argv, cwd):
        for i, a in enumerate(argv):
            if a == "--params" and i + 1 < len(argv) and argv[i + 1].startswith("file_path="):
                calls.append(argv[i + 1].split("=", 1)[1])
        return 'x\n{"verdict":"fit","summary":"","findings":[]}\n'

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        for i in range(3):
            (root / f"src/F{i}.cs").write_text(f"class F{i} {{}}\n")
        cat = Catalogue(concepts=(_concept(),), rules=(_rule(),))
        ck_dir = root / ".ckpt"

        # Pre-populate checkpoint with 1 verdict for src/F0.cs.
        fp = checkpoint_fingerprint(_concept(), _rule())
        ck = Checkpoint(ck_dir / "r.jsonl", fp)
        ck.ensure_header()
        ck.append(Verdict(file="src/F0.cs", verdict="fit", summary="", findings=(), raw={}))

        res = run_llm_rule(_rule(), cat, root, runner=_runner, checkpoint_dir=ck_dir, verify=False)
        assert res.files_scanned == 3
        # Only the two missing files should hit the investigator.
        assert sorted(calls) == ["src/F1.cs", "src/F2.cs"], calls


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
