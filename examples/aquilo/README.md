# examples/aquilo

The concepts and rules Aquilo Solution.S GmbH runs against its monorepo
in production. This is the set that produced the 267 findings quoted in
the top-level README.

It is published as a **shape, not a prescription**. Your team's scar
tissue will not match ours. Copy, adapt, delete — but do not run these
rules unchanged against a codebase that does not share the same idioms
(.NET + gRPC + MediatR-style handlers, Nuxt 4 / Vue 3 frontend,
proto-first contracts).

## What's here

- **`concepts.yaml`** — five concepts covering gRPC base forwarding,
  tenant-scoped filter requests, proto-first interface discipline,
  translatable error keys, and markdown doc-path citation health.
- **`rules.yaml`** — nine rules pairing those concepts with selectors
  and either guided (Python) or LLM-investigated checks.

## Writing your own

Start by asking your team: *what convention did we learn the hard way
that we still sometimes forget?* Write the principle as a single
declarative sentence. That becomes a concept. Then figure out where in
the codebase a violation would be visible — a file glob, a regex — and
decide whether a Python function can judge it (guided) or whether it
needs an LLM reading intent (llm_investigated). That becomes a rule.

Keep concepts few and load-bearing. A repository with fifty concepts
has a governance problem, not a drift problem.
