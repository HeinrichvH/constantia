"""Smoke tests for config loading + cross-ref validation.

Stdlib-only so they run anywhere without extra setup.
Run:  PYTHONPATH=src python3 -m tests.test_config
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from constantia.config import ConfigError, load_catalogue
from constantia.selector import Selector, select_files


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_example_aquilo_loads_clean() -> None:
    cat = load_catalogue(EXAMPLES)
    assert {c.id for c in cat.concepts} == {
        "markers-are-trackable",
        "tests-assert-their-name",
        "deprecations-point-somewhere",
        "agent-docs-match-code",
        "grpc-request-base-forwarding",
        "grpc-filter-organization-ids",
        "grpc-proto-first",
        "translatable-error-keys",
        "docs-path-citations",
    }, {c.id for c in cat.concepts}
    assert len(cat.rules) >= 4
    for r in cat.rules:
        assert cat.concept_by_id(r.concept_id) is not None, r.concept_id
        assert r.selector.get("file_glob"), f"rule {r.id} missing file_glob"


def _write_minimal(tmp: Path, rule_block: str) -> None:
    (tmp / "concepts.yaml").write_text(
        "version: 1\nconcepts:\n"
        "  - id: known-concept\n"
        "    name: known\n"
        "    principle: a principle long enough to satisfy schema\n"
        "    rationale: a rationale long enough to satisfy schema\n"
    )
    (tmp / "rules.yaml").write_text("version: 1\nrules:\n" + rule_block)


def test_dangling_concept_id_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_minimal(
            tmp,
            "  - id: bad-rule\n    concept_id: ghost-concept\n    name: n\n"
            "    description: d\n    severity: low\n    type: guided\n"
            "    selector: {file_glob: '**/*'}\n"
            "    guided: {check: symbol-grep}\n",
        )
        try:
            load_catalogue(tmp)
        except ConfigError as exc:
            assert "dangling concept_id" in str(exc), str(exc)
        else:
            raise AssertionError("expected ConfigError on dangling concept_id")


def test_guided_rule_with_llm_block_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_minimal(
            tmp,
            "  - id: rr\n    concept_id: known-concept\n    name: n\n"
            "    description: d\n    severity: low\n    type: guided\n"
            "    selector: {file_glob: '**/*'}\n"
            "    guided: {check: symbol-grep}\n"
            "    llm_investigated: {investigation: 'must not appear here xxxxxxxxxxxxxxxxxxxx'}\n",
        )
        try:
            load_catalogue(tmp)
        except ConfigError as exc:
            assert "schema validation failed" in str(exc)
        else:
            raise AssertionError("expected ConfigError on mixed guided+llm_investigated block")


def test_rule_without_selector_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_minimal(
            tmp,
            "  - id: rr\n    concept_id: known-concept\n    name: n\n"
            "    description: d\n    severity: low\n    type: guided\n"
            "    guided: {check: symbol-grep}\n",
        )
        try:
            load_catalogue(tmp)
        except ConfigError as exc:
            assert "schema validation failed" in str(exc)
        else:
            raise AssertionError("expected ConfigError on missing selector")


def test_selector_brace_expansion() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "a.ts").write_text("")
        (tmp / "b.vue").write_text("")
        (tmp / "c.js").write_text("")
        matched = select_files(Selector(file_glob="*.{ts,vue}"), tmp)
        assert sorted(p.name for p in matched) == ["a.ts", "b.vue"]


def test_selector_file_glob_and_regex_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "a.py").write_text("print('hello')\n")
        (tmp / "b.py").write_text("print('TARGET')\n")
        (tmp / "c.txt").write_text("TARGET\n")
        all_py = select_files(Selector(file_glob="*.py"), tmp)
        assert sorted(p.name for p in all_py) == ["a.py", "b.py"]
        narrow = select_files(Selector(file_glob="*.py", file_contains_regex="TARGET"), tmp)
        assert [p.name for p in narrow] == ["b.py"]


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
