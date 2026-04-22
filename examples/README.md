# examples

The concepts and rules Aquilo Solution.S GmbH runs against its monorepo
in production. This is the set that produced the 267 findings quoted in
the top-level README.

It is published as a **shape, not a prescription**. Your team's scar
tissue will not match ours. Copy, adapt, delete — but do not run these
rules unchanged against a codebase that does not share the same idioms.

## What's here

- **`concepts.yaml`** — nine concepts in two layers.
- **`rules.yaml`** — thirteen rules pairing those concepts with selectors
  and either guided (Python) or LLM-investigated checks.

## Two layers

### Universal (four concepts, four rules)

Language and stack agnostic. These run usefully against any codebase.

| Concept | Rule | Shape | Severity |
| --- | --- | --- | --- |
| `markers-are-trackable` | `orphan-markers-scan` | guided | low |
| `tests-assert-their-name` | `test-names-claim-behaviour` | llm_investigated | medium |
| `deprecations-point-somewhere` | `deprecation-migration-scan` | guided | medium |
| `agent-docs-match-code` | `agent-docs-match-code-scan` | llm_investigated | high |

**`orphan-markers-scan`** — scans source for TODO / FIXME / XXX / HACK
comments and test-framework skips (`it.skip(...)`, `@pytest.mark.skip`,
`[Ignore]`, `#[ignore]`, `#[allow(dead_code)]`, `xit(...)`). Each must
carry, within ±2 lines: an issue reference (`#123`, `ABC-123`), a URL, an
author attribution (`TODO(alice):`), or a version target (`v8`, `since 1.2`).
Prose-only explanations don't satisfy it by design.

**`test-names-claim-behaviour`** — LLM-investigated per test block: does
the body assert the outcome the name claims? Fixture-only tests with no
assertion, or assertions unrelated to the named claim, are violations.
Generic test names ("works", "sanity") are `not_applicable`.

**`deprecation-migration-scan`** — scans source for `@deprecated`,
`[Obsolete]`, `#[deprecated]`, `DeprecationWarning`. Each must carry
within ±3 lines: a replacement hint, a tracking reference, or a version
target. Bare markers with prose-only reason strings don't satisfy it.

**`agent-docs-match-code-scan`** — for each concrete, verifiable claim in
`CLAUDE.md` / `AGENTS.md` / `.cursorrules`, checks that code, config, or
tooling actually backs the claim. Abstract principles are skipped. This is
the highest-leverage drift surface in an AI-collaborative codebase: one
rotten prose line mis-trains every future agent invocation.

### Aquilo-specific (five concepts, nine rules)

Scoped to our .NET / gRPC / MediatR handler + Nuxt 4 / Vue 3 frontend.
Take these as a template for what team-specific rules look like, not as
rules to run as-is on a different stack.

| Concept | Rules | Shape | Severity |
| --- | --- | --- | --- |
| `grpc-request-base-forwarding` | `base-forwarded-not-fabricated-csharp`, `base-forwarded-not-fabricated-typescript` | llm_investigated | critical |
| `grpc-filter-organization-ids` | `filter-request-sets-organization-ids-csharp`, `filter-request-sets-organization-ids-typescript` | llm_investigated | high |
| `grpc-proto-first` | `proto-services-have-handlers`, `no-handrolled-http-to-grpc-services` | guided + llm_investigated | high |
| `translatable-error-keys` | `handler-failures-use-translatable-keys` | llm_investigated | medium |
| `docs-path-citations` | `cited-path-exists`, `cited-path-purpose-matches` | guided + llm_investigated | medium |

## Writing your own

Start by asking your team: *what convention did we learn the hard way
that we still sometimes forget?* Write the principle as a single
declarative sentence. That becomes a concept. Then figure out where in
the codebase a violation would be visible — a file glob, a regex — and
decide whether a Python function can judge it (guided) or whether it
needs an LLM reading intent (llm_investigated). That becomes a rule.

Keep concepts few and load-bearing. A repository with fifty concepts
has a governance problem, not a drift problem.

## Running it

```bash
# Guided phase only — fast, no API calls, runs offline:
constantia scan examples --repo-root . --skip-llm --output-dir ./drift

# Full scan including LLM investigation:
constantia scan examples --repo-root . --output-dir ./drift
```
