"""Config loading + validation for constantia.

Loads concepts.yaml and rules.yaml, validates each against its JSON
Schema, and cross-checks that every rule points at a declared concept.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator


SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


class ConfigError(ValueError):
    """Raised on any config defect — schema violation or cross-ref break."""


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    principle: str
    rationale: str
    discovery: dict[str, Any] | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    concept_id: str
    name: str
    description: str
    severity: str
    type: str  # guided | llm_investigated
    selector: dict[str, Any] = field(default_factory=dict)
    guided: dict[str, Any] | None = None
    llm_investigated: dict[str, Any] | None = None


@dataclass(frozen=True)
class Catalogue:
    concepts: tuple[Concept, ...]
    rules: tuple[Rule, ...]

    def concept_by_id(self, concept_id: str) -> Concept | None:
        return next((c for c in self.concepts if c.id == concept_id), None)

    def rules_for(self, concept_id: str) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.concept_id == concept_id)


def _load_schema(name: str) -> dict[str, Any]:
    with (SCHEMAS_DIR / name).open() as f:
        return json.load(f)


def _validate(data: dict[str, Any], schema_name: str, source: Path) -> None:
    schema = _load_schema(schema_name)
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: e.absolute_path)
    if errors:
        formatted = "\n".join(
            f"  - {source.name}:{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in errors
        )
        raise ConfigError(f"schema validation failed:\n{formatted}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"file not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level must be a mapping")
    return data


def load_concepts(path: Path) -> tuple[Concept, ...]:
    data = _read_yaml(path)
    _validate(data, "concept.schema.json", path)
    return tuple(
        Concept(
            id=c["id"],
            name=c["name"],
            principle=c["principle"],
            rationale=c["rationale"],
            discovery=c.get("discovery"),
        )
        for c in data["concepts"]
    )


def load_rules(path: Path) -> tuple[Rule, ...]:
    data = _read_yaml(path)
    _validate(data, "rule.schema.json", path)
    return tuple(
        Rule(
            id=r["id"],
            concept_id=r["concept_id"],
            name=r["name"],
            description=r["description"],
            severity=r["severity"],
            type=r["type"],
            selector=r["selector"],
            guided=r.get("guided"),
            llm_investigated=r.get("llm_investigated"),
        )
        for r in data["rules"]
    )


def load_catalogue(config_dir: Path) -> Catalogue:
    """Load concepts.yaml + rules.yaml and cross-validate references."""
    concepts = load_concepts(config_dir / "concepts.yaml")
    rules = load_rules(config_dir / "rules.yaml")

    concept_ids = {c.id for c in concepts}
    dangling = [r for r in rules if r.concept_id not in concept_ids]
    if dangling:
        formatted = "\n".join(f"  - rule '{r.id}' → missing concept '{r.concept_id}'" for r in dangling)
        raise ConfigError(f"dangling concept_id references:\n{formatted}")

    dup_concepts = _duplicates(c.id for c in concepts)
    dup_rules = _duplicates(r.id for r in rules)
    if dup_concepts:
        raise ConfigError(f"duplicate concept ids: {sorted(dup_concepts)}")
    if dup_rules:
        raise ConfigError(f"duplicate rule ids: {sorted(dup_rules)}")

    return Catalogue(concepts=concepts, rules=rules)


def _duplicates(iterable) -> set[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for item in iterable:
        if item in seen:
            dups.add(item)
        seen.add(item)
    return dups
