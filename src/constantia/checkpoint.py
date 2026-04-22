"""Per-file JSONL checkpointing for LLM rule runs.

An LLM rule run can take tens of minutes and cost real money. A single
flaky goose call (network blip, Mistral rate-limit) used to torch the
whole run. Now each verdict is appended to a JSONL file as it completes,
and a resumed run skips files already present in the checkpoint.

Layout: `<checkpoint_dir>/<rule_id>.jsonl`. One JSON object per line:

    {"fingerprint": "<rule-fingerprint>", "file": "...", "verdict": "fit", ...}

The first line also carries the rule fingerprint — a hash of the rule's
investigation prompt, selector, and concept principle. On resume, if
the fingerprint doesn't match the current rule, the checkpoint is
discarded: we won't stitch findings from an old prompt into a new
rule's report.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from threading import Lock

from .config import Concept, Rule
from .llm import Verdict


def checkpoint_fingerprint(concept: Concept, rule: Rule) -> str:
    """Hash what makes a rule's LLM output meaning-changing.

    Anything that would make an old verdict wrong-in-spirit for the new
    run invalidates the checkpoint: investigation prompt text, selector,
    and the concept principle itself.
    """
    payload = {
        "concept_id": concept.id,
        "concept_principle": concept.principle,
        "rule_id": rule.id,
        "selector": rule.selector,
        "investigation": (rule.llm_investigated or {}).get("investigation", ""),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Checkpoint:
    """Append-only JSONL store of verdicts for one rule run."""

    def __init__(self, path: Path, fingerprint: str):
        self.path = path
        self.fingerprint = fingerprint
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_existing(self) -> dict[str, Verdict]:
        """Return verdicts from disk matching this fingerprint; {} if missing or stale."""
        if not self.path.exists():
            return {}
        by_file: dict[str, Verdict] = {}
        header_fp: str | None = None
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("__type") == "header":
                header_fp = obj.get("fingerprint")
                continue
            if "file" not in obj or "verdict" not in obj:
                continue
            # Back-compat: old checkpoints wrote `findings`; map onto items.
            legacy_findings = obj.get("findings")
            items = obj.get("items")
            if items is None and legacy_findings is not None:
                items = [{**f, "verdict": "violation"} for f in legacy_findings]
            by_file[obj["file"]] = Verdict(
                file=obj["file"],
                verdict=obj["verdict"],
                summary=obj.get("summary", ""),
                items=tuple(items or ()),
                raw=obj.get("raw", {}),
            )
        if header_fp != self.fingerprint:
            # Stale checkpoint: discard to avoid mixing old/new verdicts.
            return {}
        return by_file

    def ensure_header(self) -> None:
        """Write a header line if the file is empty / new."""
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"__type": "header", "fingerprint": self.fingerprint}) + "\n")

    def append(self, v: Verdict) -> None:
        """Append one verdict atomically."""
        record = {
            "file": v.file,
            "verdict": v.verdict,
            "summary": v.summary,
            "items": list(v.items),
            "raw": v.raw,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def discard(self) -> None:
        """Remove the checkpoint — call after a full clean run."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
