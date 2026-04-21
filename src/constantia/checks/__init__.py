"""Registry for guided checks.

A guided check is a plain Python class that runs against each
selector-matched file and returns zero or more candidate findings.
Importing a submodule here registers it. Checks are deliberately
thin — most of the interesting scoring lives in llm_investigated
rules.
"""
from .base import Check, Finding, get_check, register, registered_names

# Importing submodules registers their checks as a side effect.
from . import markdown_paths  # noqa: F401
from . import proto_handlers  # noqa: F401

__all__ = ["Check", "Finding", "get_check", "register", "registered_names"]
