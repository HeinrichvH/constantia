# AGENTS.md

Orientation for AI coding agents (Claude Code, Cursor, Aider, Goose,
Devstral-as-editor, etc.) working inside this repository. Human readers:
start with [README.md](./README.md) and [CONTRIBUTING.md](./CONTRIBUTING.md)
instead — this file assumes both as read.

## What this project is, in one sentence

A deterministic + LLM drift scanner that reads a repository, compares it
against YAML-declared *concepts*, and emits findings in two never-mixed
sections: guided (regex/AST) and llm_investigated (verdicted by one
model, adversarially re-checked by another).

## Layout you'll be editing

| Path | Purpose |
| --- | --- |
| `src/constantia/` | Scanner code. Stdlib + `pyyaml` + `jsonschema`, nothing else. |
| `src/constantia/checks/` | Guided checks. Each is a `Check` subclass registered via `@register`. Read `checks/README.md` before adding. |
| `src/constantia/reporter_*.py` | Issue reporters (Forgejo, GitHub). Mirror one to add another (GitLab, Slack, static HTML). |
| `schemas/` | JSON Schema for `concepts.yaml` and `rules.yaml`. Public `$id` URLs — change them only with a version bump. |
| `recipes/` | Goose recipe files. Prompts + model pins. Treat as data, not code. |
| `examples/aquilo/` | Production concept set. Shape reference, not a starting template — fork and specialise. |
| `tests/` | Stdlib-only, no network. `PYTHONPATH=src pytest tests/ -v` runs all 39. |

## Invariants you must not break

- **Guided checks are read-only and offline.** No writes, no network, no
  re-walking the filesystem — the runner has already filtered to matched
  files. Violating this turns the scanner into a codemod in disguise.
- **Deterministic and LLM findings never mix in output.** Separate
  sections, separate content hashes. If you're tempted to merge them in
  a reporter, you're undoing the whole design.
- **Coverage is total or the run fails.** `len(selector_matches) ==
  len(verdicts)` is asserted at the end of every LLM pass. No silent
  skips.
- **Stdlib-only on the guided path.** Adding a dependency to
  `requirements.txt` for a guided check is almost always wrong — the
  stdlib has `re`, `pathlib`, `subprocess`, and that's the budget.

## Before you commit

```bash
PYTHONPATH=src pytest tests/ -v    # must pass
PYTHONPATH=src python3 -m constantia.cli validate examples/aquilo  # schema sanity
```

If you touched `Dockerfile` or `requirements.txt`, build the image
locally (`docker build -t constantia:dev .`) before pushing — CI catches
it, but local roundtrips are faster.

## Writing a new guided check

1. Read `src/constantia/checks/README.md` (contract + idioms).
2. Copy an existing check closest to your shape:
   - Per-file scan, self-contained evidence → `markdown_paths.py`.
   - Cross-file lookup with cached index → `proto_handlers.py`.
3. Re-export from `src/constantia/checks/__init__.py`.
4. Add a test to `tests/test_checks.py` — stdlib `tempfile.TemporaryDirectory`,
   no mocks, no network. Assert both a positive and a negative case.

## Using an LLM to discover rule candidates

Constantia is a drift scanner; it does not invent concepts, it enforces
ones you've written down. Finding *which* concepts are worth writing
down is a different task — and one a local agent is good at.

Here's a starter prompt you can paste into Claude Code, Cursor, or any
agent with repo read access. It surfaces *convention candidates* — patterns
that look rule-like but aren't currently enforced:

```text
You are auditing this repository for implicit conventions that would be
worth encoding as constantia concepts. A convention-candidate is any
pattern that:

  (a) is followed in most files of a given shape but violated in a
      visible minority, AND
  (b) the violation looks like drift rather than an intentional exception
      (no comment, no linked ticket, no obvious reason), AND
  (c) could be cheaply checked — either by regex/AST (guided) or by
      reading intent with an LLM (llm_investigated).

Walk the repo. For each candidate, output:

  - **Name:** short kebab-case id.
  - **Principle:** one sentence stating the convention.
  - **Evidence for:** 3-5 file:line citations of files that follow it.
  - **Evidence against:** 2-3 file:line citations of files that violate
    it, with a one-line note on why it looks unintentional.
  - **Check shape:** "guided (regex)", "guided (AST)", or
    "llm_investigated", with a one-sentence justification.
  - **Prior art:** does `examples/aquilo/concepts.yaml` already cover
    this? If so, skip it.

Prioritise conventions where the violation would mislead an AI agent
reading the repo as ground truth — stale references, fabricated
identity-bearing objects, silent omissions that pass type-check. Skip
cosmetic style (the formatter handles those). Skip anything that
requires reading runtime behaviour — we only scan source.

Output 5-10 candidates, ranked by confidence that the violation set is
genuine drift rather than intentional variation.
```

The output is the *input* to a concept-authoring session: each surviving
candidate becomes an entry in `concepts.yaml` with a paired rule in
`rules.yaml`. Let the agent crawl; you decide what to encode.

## Style notes

- Terse over verbose. The code is comment-light on purpose — names
  should carry the weight.
- Match the surrounding file. If you find yourself adding a new pattern
  (dataclasses vs plain classes, `click` vs `argparse`), the answer is
  almost always: don't.
- No speculative scaffolding. Pre-1.0 means we delete code that nothing
  calls; we don't preserve it "in case".
