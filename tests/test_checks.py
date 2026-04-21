"""Tests for guided checks + runner.

Stdlib only. Run:  PYTHONPATH=src python3 -m tests.test_checks
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

from constantia.checks import get_check, registered_names
from constantia.checks.proto_handlers import _INDEX_CACHE, _parse_rpcs
from constantia.config import Rule
from constantia.runner import run_guided_rule


def test_registry_has_expected_checks() -> None:
    names = registered_names()
    assert "proto-rpcs-have-handlers" in names, names
    assert "markdown-cited-paths-exist" in names, names


def test_proto_rpc_parse() -> None:
    proto = textwrap.dedent(
        """
        syntax = "proto3";
        service Greeter {
          rpc SayHello (pkg.HelloReq) returns (pkg.HelloResp);
          rpc Farewell ( pkg.ByeReq ) returns ( pkg.ByeResp ) ;
        }
        """
    )
    rpcs = _parse_rpcs(proto)
    assert [(r[0], r[1], r[2]) for r in rpcs] == [
        ("SayHello", "HelloReq", "HelloResp"),
        ("Farewell", "ByeReq", "ByeResp"),
    ]


def test_proto_rpc_parse_requires_service_block() -> None:
    # No service block → ignore (schema-only file)
    assert _parse_rpcs("message Foo { string x = 1; }") == []


def test_proto_handlers_detects_missing_and_present() -> None:
    _INDEX_CACHE.clear()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        proto_dir = root / "src/proto/services"
        proto_dir.mkdir(parents=True)
        (proto_dir / "MyV1.proto").write_text(
            textwrap.dedent(
                """
                syntax = "proto3";
                service GrpcMy {
                  rpc DoesExist (pkg.GrpcDoesExistRequest) returns (pkg.GrpcDoesExistResponse);
                  rpc MissingOne (pkg.GrpcMissingOneRequest) returns (pkg.GrpcMissingOneResponse);
                }
                """
            )
        )
        handler_dir = root / "src/MyService/Handlers"
        handler_dir.mkdir(parents=True)
        (handler_dir / "DoesExistHandler.cs").write_text(
            "public class DoesExistHandler : "
            "IRequestHandler<GrpcDoesExistRequest, GrpcDoesExistResponse> {}\n"
        )
        rule = Rule(
            id="proto-services-have-handlers",
            concept_id="grpc-proto-first",
            name="n",
            description="d",
            severity="high",
            type="guided",
            selector={"file_glob": "src/proto/**/*.proto"},
            guided={"check": "proto-rpcs-have-handlers", "config": {"handler_search_glob": "src/**/*.cs"}},
        )
        res = run_guided_rule(rule, root)
        assert res.files_scanned == 1
        assert len(res.findings) == 1, [f.message for f in res.findings]
        f = res.findings[0]
        assert "MissingOne" in f.message
        assert f.evidence["request_type"] == "GrpcMissingOneRequest"


def test_markdown_paths_detects_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "real/dir").mkdir(parents=True)
        (root / "real/dir/file.txt").write_text("x")
        (root / "docs").mkdir()
        (root / "docs/guide.md").write_text(
            "See `real/dir/file.txt` and also `real/missing.txt`.\n"
            "URL `https://example.com/x` should be ignored.\n"
            "Package `@scope/package` should be ignored.\n"
            "MIME `text/plain` should be ignored.\n"
        )
        check = get_check("markdown-cited-paths-exist")
        assert check is not None
        findings = check.run(
            file_path=root / "docs/guide.md",
            repo_root=root,
            config={},
            rule_id="r",
            concept_id="c",
            severity="medium",
        )
        msgs = [f.message for f in findings]
        assert len(findings) == 1, msgs
        assert "real/missing.txt" in findings[0].message


def test_markdown_paths_resolves_doc_relative() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs").mkdir()
        (root / "docs/sibling.md").write_text("hi")
        (root / "docs/guide.md").write_text("See `./sibling.md`.\n")
        check = get_check("markdown-cited-paths-exist")
        findings = check.run(
            file_path=root / "docs/guide.md",
            repo_root=root,
            config={},
            rule_id="r",
            concept_id="c",
            severity="medium",
        )
        assert findings == [], [f.message for f in findings]


def test_markdown_paths_ignores_cidr_notations() -> None:
    """Test that CIDR notations like 10.10.69.0/24 are not treated as file paths."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs").mkdir()
        (root / "docs/network.md").write_text(
            "Network subnets: `10.10.69.0/24`, `10.10.70.0/24`, `10.100.0.0/16`.\n"
            "Also `192.168.1.0/24` and `172.16.0.0/12`.\n"
            "But real path `docs/real-file.txt` should still be checked.\n"
        )
        # Create the real file so it passes
        (root / "docs/real-file.txt").write_text("content")
        
        check = get_check("markdown-cited-paths-exist")
        findings = check.run(
            file_path=root / "docs/network.md",
            repo_root=root,
            config={},
            rule_id="r",
            concept_id="c",
            severity="medium",
        )
        # Should have no findings - CIDR notations should be ignored, real file exists
        assert findings == [], [f.message for f in findings]


def test_markdown_paths_detects_missing_even_with_cidr() -> None:
    """Test that real missing paths are still detected even when CIDR notations are present."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs").mkdir()
        (root / "docs/network.md").write_text(
            "Network: `10.10.69.0/24`.\n"
            "Real file: `docs/missing.txt`.\n"
        )
        check = get_check("markdown-cited-paths-exist")
        findings = check.run(
            file_path=root / "docs/network.md",
            repo_root=root,
            config={},
            rule_id="r",
            concept_id="c",
            severity="medium",
        )
        # Should find the missing real file but ignore the CIDR
        assert len(findings) == 1, [f.message for f in findings]
        assert "docs/missing.txt" in findings[0].message
        assert "10.10.69.0/24" not in findings[0].message


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
