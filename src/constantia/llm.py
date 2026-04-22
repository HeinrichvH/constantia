"""LLM investigator: subprocess call to goose + Mistral, per-file verdicts.

Each `llm_investigated` rule evaluates one file at a time. The recipe
at `recipes/investigator/recipe.yaml` is invoked as a Goose subprocess
with the concept's principle + rule's investigation prompt + file
path. Goose returns one JSON object on its last stdout line shaped as
`{summary, items: [...]}` — each item carries a per-item `verdict`
enum (`fit`, `not_applicable`, `violation`). We derive the file-level
verdict from the items, verify every item's `symbol` is actually
present near its `citation` line (the explore-recipe idiom — cheap
fabrication guard), and promote ONLY `violation` items into findings.

The per-item verdict is a tool-call-style contract: the LLM commits
per item, structurally preventing the "message contradicts verdict"
drift that a single file-level verdict plus prose invites.

No silent skips: every selector-matched file produces exactly one
verdict (fit / violation / uncertain / not_applicable). The coverage
guarantee — `len(selector_matches) == len(verdicts)` — is enforced by
the caller (`run_llm_rule` in runner.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .checks import Finding
from .config import Concept, Rule


RECIPE_PATH = Path(__file__).resolve().parents[2] / "recipes" / "investigator" / "recipe.yaml"


class LlmError(RuntimeError):
    """Raised when the investigator subprocess fails or returns unparseable output."""


@dataclass(frozen=True)
class Verdict:
    """Raw per-file result — before item-level citation verification.

    `items` is the structured per-item array from the recipe — each
    element has its own `verdict` enum (fit / not_applicable /
    violation). `verdict` on this dataclass is the file-level roll-up
    derived from the items.
    """

    file: str  # repo-relative POSIX
    verdict: str  # fit | violation | uncertain | not_applicable | error
    summary: str
    items: tuple[dict[str, Any], ...]
    raw: dict[str, Any]


# The goose subprocess runner is a plain callable so tests can inject a
# canned response without needing the `goose` binary or network.
GooseRunner = Callable[[list[str], str], str]


def _default_goose_runner(argv: list[str], cwd: str) -> str:
    """Invoke goose and return combined stdout+stderr as one string."""
    if shutil.which(argv[0]) is None:
        raise LlmError(f"executable not found on PATH: {argv[0]}")
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.stdout or ""


_JSON_LINE_RE = re.compile(r"^\{.*\}$")


def _extract_json(raw: str) -> dict[str, Any]:
    """Find the last line that parses as a JSON object. Mirrors the `explore` CLI."""
    last: dict[str, Any] | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not _JSON_LINE_RE.match(line):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last = obj
    if last is None:
        raise LlmError("no JSON object on goose stdout")
    return last


def _escape_nl(s: str) -> str:
    """Goose --params can't handle literal newlines; encode as backslash-n."""
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def invoke_investigator(
    concept: Concept,
    rule: Rule,
    file_rel: str,
    repo_root: Path,
    *,
    max_turns: int = 40,
    recipe_path: Path = RECIPE_PATH,
    runner: GooseRunner = _default_goose_runner,
) -> Verdict:
    """Run the investigator recipe against one file and return the parsed verdict."""
    assert rule.type == "llm_investigated"
    assert rule.llm_investigated is not None
    # Don't use .format(): investigation prompts legitimately contain literal
    # braces (TS object literals, C# initializers) that would be parsed as
    # placeholders. Narrow substitution instead.
    investigation = (
        rule.llm_investigated.get("investigation", "")
        .replace("{concept.id}", concept.id)
        .replace("{concept.name}", concept.name)
    )

    params = {
        "concept_id": concept.id,
        "concept_name": concept.name,
        "concept_principle": concept.principle,
        "rule_id": rule.id,
        "rule_name": rule.name,
        "investigation": investigation,
        "file_path": file_rel,
    }
    argv = ["goose", "run", "--recipe", str(recipe_path),
            "--no-session", "--max-turns", str(max_turns),
            "--max-tool-repetitions", "8"]
    for k, v in params.items():
        argv.extend(["--params", f"{k}={_escape_nl(v)}"])

    try:
        raw = runner(argv, str(repo_root))
        obj = _extract_json(raw)
    except LlmError as exc:
        # Mistral/goose transient failure — record as error verdict so the
        # coverage guarantee holds (one verdict per selector match) and a
        # single flaky call doesn't torch the whole run.
        return Verdict(
            file=file_rel,
            verdict="error",
            summary=f"investigator failed: {exc}",
            items=(),
            raw={"error": str(exc)},
        )

    summary = obj.get("summary", "")
    items = tuple(obj.get("items") or ())
    # Back-compat: an old-shape response with `findings`+`verdict` is
    # lifted into the new items shape so mixed fixtures don't explode.
    if not items and "findings" in obj:
        legacy_verdict = obj.get("verdict", "uncertain")
        items = tuple(
            {**f, "verdict": "violation"} for f in (obj.get("findings") or ())
        )
        file_verdict = legacy_verdict
    else:
        file_verdict = _derive_file_verdict(items, explicit=obj.get("verdict"))
    return Verdict(file=file_rel, verdict=file_verdict, summary=summary, items=items, raw=obj)


def _derive_file_verdict(
    items: tuple[dict[str, Any], ...],
    *,
    explicit: str | None = None,
) -> str:
    """Roll the per-item verdicts up to one file-level verdict.

    Priority: any `violation` → violation; else any `fit` → fit; else
    any `not_applicable` → not_applicable; empty → uncertain. An
    explicit `uncertain` at the top level wins (model couldn't decide).
    """
    if explicit == "uncertain":
        return "uncertain"
    if not items:
        return "uncertain"
    per_item = [str(i.get("verdict", "")).lower() for i in items]
    if "violation" in per_item:
        return "violation"
    if "fit" in per_item:
        return "fit"
    if "not_applicable" in per_item:
        return "not_applicable"
    return "uncertain"


@dataclass(frozen=True)
class ResolutionStats:
    """Audit of what happened to each raw finding during citation resolution."""

    kept: int = 0
    dropped_wrong_file: int = 0
    dropped_symbol_absent: int = 0
    line_corrected: int = 0  # symbol found, but not at cited line


def verdict_to_findings(
    verdict: Verdict,
    rule: Rule,
    repo_root: Path,
) -> list[Finding]:
    """Thin wrapper — returns only the findings. Use `resolve_verdict` for stats."""
    findings, _ = resolve_verdict(verdict, rule, repo_root)
    return findings


def resolve_verdict(
    verdict: Verdict,
    rule: Rule,
    repo_root: Path,
) -> tuple[list[Finding], ResolutionStats]:
    """Convert a verdict into Findings with symbol-anchored line resolution.

    Devstral names the right symbol but often hallucinates the line
    number. Instead of trusting the cited line, we grep for the symbol
    locally and resolve the real line:

    - If the symbol isn't in the file at all → drop (fabrication).
    - If it appears once → use that line.
    - If it appears multiple times → pick the occurrence closest to
      the cited line (the LLM's line is a hint, not authoritative).

    When resolution shifts the line, we record both `cited_line` and
    `resolved_line` in evidence for audit.
    """
    if verdict.verdict != "violation":
        return [], ResolutionStats()

    abs_path = repo_root / verdict.file
    try:
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], ResolutionStats()

    out: list[Finding] = []
    kept = corrected = dropped_file = dropped_sym = 0
    # Only items the LLM structurally committed as violations become
    # findings. `fit` / `not_applicable` items are audit trail, not
    # signal.
    violation_items = [i for i in verdict.items if str(i.get("verdict", "")).lower() == "violation"]
    for f in violation_items:
        citation = f.get("citation", "")
        symbol = (f.get("symbol") or "").strip()
        message = f.get("message", "")
        cited_file, cited_line = _split_citation(citation)
        if cited_file != verdict.file:
            dropped_file += 1
            continue
        hits = _find_symbol_lines(lines, symbol) if symbol else []
        if not hits:
            dropped_sym += 1
            continue
        resolved = _pick_nearest(hits, cited_line)
        if cited_line is not None and resolved != cited_line:
            corrected += 1
        evidence = {
            "symbol": symbol,
            "evidence": f.get("evidence", ""),
            "llm_summary": verdict.summary,
        }
        if cited_line is not None and cited_line != resolved:
            evidence["cited_line"] = cited_line
            evidence["resolved_line"] = resolved
        if len(hits) > 1:
            evidence["symbol_occurrences"] = hits
        out.append(
            Finding(
                rule_id=rule.id,
                concept_id=rule.concept_id,
                severity=rule.severity,
                file=verdict.file,
                line=resolved,
                message=message,
                evidence=evidence,
            )
        )
        kept += 1
    stats = ResolutionStats(
        kept=kept,
        dropped_wrong_file=dropped_file,
        dropped_symbol_absent=dropped_sym,
        line_corrected=corrected,
    )
    return out, stats


def _find_symbol_lines(lines: list[str], symbol: str) -> list[int]:
    """Return 1-indexed line numbers where `symbol` appears literally."""
    if not symbol:
        return []
    return [i + 1 for i, line in enumerate(lines) if symbol in line]


def _pick_nearest(hits: list[int], hint: int | None) -> int:
    if hint is None or not hits:
        return hits[0] if hits else 0
    return min(hits, key=lambda h: abs(h - hint))


def _split_citation(citation: str) -> tuple[str | None, int | None]:
    """Parse `path:line` or `path:start-end` → (path, start_line)."""
    if ":" not in citation:
        return None, None
    path, _, tail = citation.rpartition(":")
    start = tail.split("-", 1)[0]
    try:
        return path, int(start)
    except ValueError:
        return None, None


def _symbol_near(lines: list[str], cited_line: int, symbol: str, window: int = 3) -> bool:
    if not symbol:
        return False
    # lines is 0-indexed; citations are 1-indexed.
    idx = cited_line - 1
    if idx < 0 or idx >= len(lines):
        return False
    lo = max(0, idx - window)
    hi = min(len(lines), idx + window + 1)
    haystack = "\n".join(lines[lo:hi])
    return symbol in haystack
