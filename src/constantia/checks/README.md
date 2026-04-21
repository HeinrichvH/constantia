# src/constantia/checks

**Guided** checks — the deterministic half of constantia. Each check is a
Python class that reads a matched file (and optionally grep around it) and
returns a list of `Finding`s. No LLM involved. Fast, reproducible, cannot
hallucinate.

A rule in your `rules.yaml` with `type: guided` invokes one of these by name:

```yaml
- id: proto-services-have-handlers
  concept_id: grpc-proto-first
  type: guided
  guided:
    check: proto-rpcs-have-handlers
    config:
      handler_search_glob: "src/**/*.cs"
```

## What's shipped

Two reference checks, chosen because they illustrate the two most common
shapes a guided check takes. They are **not** off-the-shelf functionality
for your repo — they encode Aquilo-flavoured assumptions (MediatR-style
C# handlers, markdown-in-repo doc conventions). Read them as templates,
not as a library.

- **`proto_handlers.py`** — parses `.proto` files, extracts every `rpc`
  declaration, and asserts each one has a corresponding
  `IRequestHandler<TRequest, TResponse>` symbol somewhere under a
  configured glob. Pattern: *cross-file lookup — one file claims a
  contract, another file must satisfy it.*
- **`markdown_paths.py`** — scans markdown for backticked repo-relative
  paths and reports ones that don't exist on disk. Pattern: *per-file
  scan — the file's own content is sufficient evidence, no cross-lookup.*

## Writing your own

Most teams will want to write their own. A guided check is worth the
effort when:

- The violation is cheaply decidable by regex, AST, or file-existence
  checks — an LLM is overkill and stochastic where you want
  determinism.
- The cost of a false positive is low (the deterministic layer is
  allowed to be noisy; the LLM layer can't afford to be).
- You want the check to run in pre-commit or fast CI without a
  Mistral round-trip.

If any of those break down — especially if judging the violation
requires *reading intent* rather than matching a pattern — the rule
belongs in the LLM-investigated path instead. Guided and
LLM-investigated rules are complementary, not competitive.

### The contract

```python
from pathlib import Path
from typing import Any

from .base import Check, Finding, register


@register
class MyCheck(Check):
    name = "my-check-id"

    def run(
        self,
        file_path: Path,
        repo_root: Path,
        config: dict[str, Any],
        rule_id: str,
        concept_id: str,
        severity: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        # ... read file_path, decide, append Findings ...
        return findings
```

Register the class with `@register` and re-export it from
`checks/__init__.py` so it's imported on package load. After that, any
rule whose `guided.check` names your `name` will invoke it.

### Findings

A `Finding` is a repo-relative citation. Give it a line number when you
can and a terse, concrete `message`. The `evidence` dict is free-form;
the reporter renders it alongside the finding for human reviewers.

### Keep checks honest

A guided check is allowed to shell out to `git`, `rg`, or read other
files — see `proto_handlers._build_handler_index` for an example of a
cached cross-file scan. But:

- **No network.** Guided checks must be reproducible from a local
  checkout.
- **No writes.** Drift detection is read-only. If your check needs to
  modify the repo, it's a codemod, not a drift check.
- **Short-circuit on the selector.** The runner already filtered to
  matched files — don't re-scan the world.

Keep the check as small as possible. If it's growing into its own DSL,
the logic probably belongs in an LLM-investigated rule with a sharper
principle instead.
