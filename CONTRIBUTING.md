# Contributing to constantia

Thanks for considering a contribution. The project is small and opinionated
— this page is short on purpose.

## What kinds of contributions fit

- **New guided checks.** Anything that can be decided deterministically
  from repo contents: regex, AST walks, cross-file lookups, file-existence
  tests. The `src/constantia/checks/` directory ships two reference
  implementations (`proto_handlers.py`, `markdown_paths.py`) and a README
  describing the contract.
- **Concept templates.** `examples/aquilo/` is the shape of a production
  set, not a prescription. If you've written a concept set that generalises
  (gRPC conventions, React prop discipline, Terraform module hygiene),
  open a PR adding it under `examples/<name>/`.
- **Reporters.** Forgejo and GitHub ship today. GitLab, Bitbucket, a
  Slack poster, or a static HTML dashboard all fit the same shape —
  mirror `src/constantia/reporter_github.py`.
- **Bug reports with a failing test.** The deterministic layer is
  testable end-to-end without a Mistral key; a repro as a `tests/`
  fixture is worth ten paragraphs of prose.

## What doesn't fit

- **LLM-replaces-regex PRs.** If the violation is cheaply decidable by
  grep, it belongs in the guided path. Paying Mistral to do grep's job is
  exactly what this tool is pushing against.
- **Codemods wearing a disguise.** Checks are read-only. Anything that
  writes files or calls out to the network during a scan belongs in a
  different tool.
- **Feature flags, compatibility shims, hypothetical-future scaffolding.**
  The scanner is pre-1.0; breaking changes are fine when they simplify
  the model. We'll note them in the release.

## Development

```bash
git clone https://github.com/boreas-aquilo/constantia
cd constantia
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest

PYTHONPATH=src pytest tests/ -v
```

The guided stage has no runtime dependencies outside `pyyaml` and
`jsonschema`. Keep it that way — guided checks need to run anywhere,
including pre-commit hooks and offline CI.

LLM work (investigator, verifier) additionally requires
[goose](https://github.com/block/goose) on `PATH` and a `MISTRAL_API_KEY`
in the environment. `--skip-llm` on the `scan` command bypasses it for
local iteration.

## Submitting a PR

- One change per PR. Two related changes in one PR are fine; seven
  drive-by cleanups are not.
- Tests for new guided checks are mandatory. See `tests/test_checks.py`
  for the pattern (stdlib-only tempdir fixtures, no network).
- Match existing style — the code is terse and comment-light on purpose.
  If you find yourself writing a docstring longer than the function,
  rename the function.
- By opening a pull request, you agree to license your contribution
  under the Apache License 2.0 (the project licence).

## Reporting security issues

Please do not open a public issue for anything that looks like a security
vulnerability in the scanner itself (e.g. a concept file that could be
crafted to execute code). Email the maintainer directly — the address is
on the GitHub profile.
