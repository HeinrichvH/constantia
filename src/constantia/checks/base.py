"""Check ABC + registry + Finding dataclass."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass(frozen=True)
class Finding:
    rule_id: str
    concept_id: str
    severity: str
    file: str  # repo-relative POSIX
    line: int | None = None
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


class Check(abc.ABC):
    name: ClassVar[str]

    @abc.abstractmethod
    def run(
        self,
        file_path: Path,
        repo_root: Path,
        config: dict[str, Any],
        rule_id: str,
        concept_id: str,
        severity: str,
    ) -> list[Finding]:
        ...


_REGISTRY: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    if not getattr(cls, "name", None):
        raise ValueError(f"check {cls.__name__} missing .name class attribute")
    _REGISTRY[cls.name] = cls()
    return cls


def get_check(name: str) -> Check | None:
    return _REGISTRY.get(name)


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
