"""Check: every proto rpc has a handler somewhere in the repo.

Parses each .proto file that the selector matched, extracts the
(service, rpc, request-type) triples, and for each triple greps the
configured handler_search_glob for a symbol that looks like the C#
MediatR handler (`IRequestHandler<{RequestType}`). Missing handlers
produce one Finding per rpc.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Check, Finding, register


# service Foo {                   — captures service name
_SERVICE_RE = re.compile(r"^\s*service\s+(\w+)\s*\{", re.MULTILINE)
# rpc Bar (pkg.Req) returns (pkg.Resp); — captures rpc + request type
_RPC_RE = re.compile(
    r"^\s*rpc\s+(\w+)\s*\(\s*([\w.]+)\s*\)\s*returns\s*\(\s*([\w.]+)\s*\)\s*[;{]",
    re.MULTILINE,
)


def _parse_rpcs(text: str) -> list[tuple[str, str, str, int]]:
    """Return (rpc_name, request_type_simple, response_type_simple, line_no) per rpc.

    Service boundary is tracked but not returned — a single proto may have
    zero or many services, but handler resolution is service-agnostic
    (request-type match is enough).
    """
    out: list[tuple[str, str, str, int]] = []
    # Ensure there's at least one service before trusting rpcs.
    if not _SERVICE_RE.search(text):
        return out
    for m in _RPC_RE.finditer(text):
        rpc_name = m.group(1)
        req = m.group(2).rsplit(".", 1)[-1]
        resp = m.group(3).rsplit(".", 1)[-1]
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((rpc_name, req, resp, line_no))
    return out


@register
class ProtoRpcsHaveHandlers(Check):
    name = "proto-rpcs-have-handlers"

    def run(
        self,
        file_path: Path,
        repo_root: Path,
        config: dict[str, Any],
        rule_id: str,
        concept_id: str,
        severity: str,
    ) -> list[Finding]:
        handler_glob = config.get("handler_search_glob", "src/**/*.cs")
        text = file_path.read_text(encoding="utf-8", errors="replace")
        rpcs = _parse_rpcs(text)
        if not rpcs:
            return []

        handler_index = _build_handler_index(repo_root, handler_glob)

        rel = file_path.relative_to(repo_root).as_posix()
        findings: list[Finding] = []
        for rpc_name, req_type, resp_type, line_no in rpcs:
            if req_type in handler_index:
                continue
            findings.append(
                Finding(
                    rule_id=rule_id,
                    concept_id=concept_id,
                    severity=severity,
                    file=rel,
                    line=line_no,
                    message=f"proto rpc `{rpc_name}` has no handler for `{req_type}`",
                    evidence={
                        "rpc": rpc_name,
                        "request_type": req_type,
                        "response_type": resp_type,
                        "handler_search_glob": handler_glob,
                    },
                )
            )
        return findings


_HANDLER_RE = re.compile(r"IRequestHandler\s*<\s*(\w+)\s*,")
# Direct gRPC service override: `public override [async] Task<TResp> RpcName(TReq request, ...)`
_OVERRIDE_RE = re.compile(
    r"override\s+(?:async\s+)?Task\s*<[^>]+>\s+\w+\s*\(\s*(\w+)\s+\w+",
)
_INDEX_CACHE: dict[tuple[str, str], set[str]] = {}


def _build_handler_index(repo_root: Path, handler_glob: str) -> set[str]:
    """Scan once per (repo_root, glob), cache the resulting set of request-type names.

    Handler detection is intentionally lexical — we look for
    `IRequestHandler<RequestType,` anywhere in the matched files. That
    matches the MediatR `IRequestHandler<TRequest, TResponse>` pattern
    common in .NET gRPC handler codebases.
    """
    key = (str(repo_root), handler_glob)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    seen: set[str] = set()
    from ..selector import expand_braces
    for pat in expand_braces(handler_glob):
        for path in repo_root.glob(pat):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _HANDLER_RE.finditer(text):
                seen.add(m.group(1))
            for m in _OVERRIDE_RE.finditer(text):
                seen.add(m.group(1))
    _INDEX_CACHE[key] = seen
    return seen
