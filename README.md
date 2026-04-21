# constantia

**A drift scanner for codebases that AI agents now read as ground truth.** Deterministic rules, an LLM investigator, and an 
adversarial verifier — in that order, never mixed.

> Status: production at Aquilo since April 2026. First run surfaced 267 findings across twelve concepts in a 20 000-file monorepo.

---

## When the repository disagrees with itself

A pull request lands. The handler is correct. The test is correct. But three files away a markdown document cites a function 
that was renamed six months ago, and two services over a gRPC handler fabricates an identity base it was explicitly told never 
to fabricate. No compiler catches it. No linter catches it. The test suite is green. An AI agent reading the repo tomorrow 
will follow the broken citation, copy the forbidden pattern, and confidently produce code that conforms to documentation that 
no longer describes anything real.

This is drift — the accumulated disagreement between a repository's code, its documentation, and its conventions. Humans 
survive drift because they bring skepticism. Agents do not. Every stale reference becomes a factual claim they will act on; 
every fabricated pattern becomes a template they will replicate. When the readership shifts from human to agent, the cost of 
internal contradiction stops being cosmetic — it becomes load-bearing.

Constantia measures it.

## How it works

Constantia splits the work into two halves that match what each technology is actually good at.

**Stage 1 — deterministic selection.** A guided rule engine walks the repository against a set of YAML-encoded *concepts*. 
Each concept is one declarative claim: "every proto RPC has a C# handler for its request type," or "every cited file path in a 
markdown document refers to a file that exists." The engine enumerates, resolves, and emits candidates. Cheap, reproducible, 
and cannot hallucinate.

**Stage 2 — LLM investigation.** Concepts that require reading code and judging whether it conforms to an *intent* are routed 
to an LLM investigator. The investigator receives the concept's principle, a handful of canonical and violating peer examples, 
and the file under inspection. It returns one of four verdicts: *fit*, *violation*, *uncertain*, or *not applicable*. A second 
LLM pass — the adversarial verifier — re-reads each claimed violation and tries to defeat it. Only findings that survive the 
adversary land in the report.

**Two decorrelated models, not two copies of the same.** The investigator runs on [Mistral Devstral](https://mistral.ai/) — 
code-tuned, cheap per call. The verifier runs on `mistral-medium-latest` — different lineage, different biases, no investment 
in the investigator's pattern-match. Redundant components only catch errors when their failure modes are uncorrelated; two 
copies of the same model confirm each other instead of checking each other.

**Goose as the runner.** LLM calls go through [block/goose](https://github.com/block/goose), whose recipe files are the 
correct abstraction for this use case: they are *data*, not code. The investigator and verifier prompts, models, and tool 
surfaces live in `recipes/investigator/recipe.yaml` and `recipes/verifier/recipe.yaml` — reviewable in isolation, swappable 
without a code change. Pinning a new model means editing one YAML field.

## Quick start

Constantia ships as a Docker image. You need a repository to scan, a Mistral API key, and — if you want to post findings back 
to a forge — a Forgejo or GitHub token.

```bash docker run --rm \
  -v "$(pwd):/repo:ro" \ -v "$(pwd)/examples/aquilo:/config:ro" \ -e MISTRAL_API_KEY=$MISTRAL_API_KEY \ 
  ghcr.io/boreas-aquilo/constantia:latest \ scan /config --repo-root /repo
```

Add `--skip-llm` to run only the deterministic stage — useful in pre-commit hooks or fast CI where you want a cheap drift 
signal without the Mistral round-trip.

Add `--forgejo-issue` with the appropriate environment variables (`FORGEJO_URL`, `FORGEJO_TOKEN`, `FORGEJO_REPO`) to have the 
report posted as an issue instead of printed.

## The concept file is the interesting artifact

The scanner code is the least interesting thing in this repository. The real payload is the concept definitions — 
machine-readable written agreements a team has with itself:

```yaml - id: grpc-request-base-forwarding
  name: GrpcRequestBase forwarding principle: |
    When a gRPC handler issues a downstream call, it reuses the incoming request.Base (preserving userId, trace, locale, and 
    organization_id). It does NOT fabricate a new GrpcRequestBase populated only with correlationId.
  rationale: | Fabricated bases silently drop identity, tracing, and locale.
```

Every concept encodes a convention that was previously either tribal knowledge or a scar-tissue comment in a pull request 
review. Making it explicit, versioned, and checkable turns folklore into invariant.

Rules pair a concept with a selector (glob + regex) and either a guided check (Python) or an LLM investigator (Goose recipe):

```yaml - id: dotnet-handlers-forward-request-base
  concept_id: grpc-request-base-forwarding selector:
    file_glob: "src/**/*Handler.cs" file_contains_regex: "request\\.Base" exclude_globs: ["**/bin/**", "**/obj/**", 
    "**/*.g.cs"]
  llm_investigated: recipe: investigator
```

See [`examples/aquilo/`](./examples/aquilo/) for a full production set — five concepts, nine rules — that catches gRPC base 
fabrication, filter omissions, stale documentation citations, and translatable error keys.

## Design principles

**The scanner is a meter, not a gate.** A constantia run succeeds with exit code 0 even when drift is found. The finding is 
the signal; the forge issue is the alert. A red run means the scanner itself broke — a clone failure, an unreachable model, a 
schema error in a concept file. Conflating "drift exists" with "run failed" trains operators to mute the alert.

**Deterministic and LLM findings never mix.** Each has its own section in the report, its own content hash, its own 
reproducibility guarantee. A reader who trusts only the deterministic layer can ignore the LLM section entirely and still get 
a cheap, reliable signal. A single hallucinated claim buried in a list of grep-level truths poisons the whole list.

**Coverage is total or the run fails.** `len(selector_matches) == len(verdicts)` is asserted at the end of every LLM pass. No 
silent skips. If a file in the selector set did not get a verdict, the run raises rather than quietly under-reporting.

**Concepts are data.** No concept lives in Python. Every rule, every selector, every principle is in YAML that is reviewable, 
diffable, and shareable across projects.

**Guided or LLM-investigated? Choose deliberately.** A rule belongs in the guided path when the violation is cheaply decidable 
by regex, AST, or file-existence checks — the deterministic layer is fast, runs in pre-commit, and can absorb some 
false-positive noise. A rule belongs in the LLM-investigated path when judging it requires *reading intent* rather than 
matching a pattern. The two are complementary, not competitive: most mature concept sets will use both. If you catch yourself 
reaching for the LLM layer to avoid writing a regex, you're paying Mistral to do grep's job.

**Guided checks are read-only, offline, and scoped.** A guided check may grep, read neighbouring files, and build caches — but 
never writes, never hits the network, and never re-scans the world. The runner has already filtered to matched files; a check 
that re-walks the filesystem is probably a codemod wearing a disguise, and belongs elsewhere. Drift detection is a 
measurement; measurements don't modify the thing they measure.

## What one scan found

From the first production run against a 20 000-file monorepo, twelve concepts, 267 findings, three concepts clean:

| Finding | Count |
| --- | --- |
| Fabricated `GrpcRequestBase` instances across C# handlers | 62 / 278 files |
| Stale path citations across markdown + `CLAUDE.md` files | 76 / 358 files |
| Cited-path purpose mismatches (path exists, content doesn't match prose) | 47 |
| `base` fabrications on the TypeScript side of the same convention | 16 |
| Proto RPCs defined with no C# handler | 5 |
| `OrganizationIds` filter omissions | 3 |

Every non-trivial finding mapped onto a previously-written lesson the team had learned and recorded. Constantia did not 
discover the rules; it re-discovered, from the code alone, which rules the repository was still silently violating.

## Status

| | |
| --- | --- |
| Deterministic stage | Stable. Stdlib-only runtime (`pyyaml`, `jsonschema`). |
| LLM stage | Stable. Requires Goose + Mistral API key. |
| Distribution | Docker today. `pipx install constantia` planned. |
| Guided checks | Two registered (`proto-rpcs-have-handlers`, `markdown-cited-paths-exist`). New ones welcome as PRs. |
| API stability | Pre-1.0. Concept and rule schemas may change; breaking changes will be noted in release notes. |

## Further reading

The design choices above — two decorrelated models, meter-not-gate, never mixing deterministic and LLM output — are argued at 
length in the companion article:

- **The Drift Scanner — When the Repository Disagrees With Itself** *(Article URL will be added when it publishes, 
  2026-04-22.)*

Part of the *Building with AI* series on what changes when a codebase's primary reader stops being human.

## License

Apache License 2.0. See [LICENSE](./LICENSE).

Copyright 2026 Heinrich von Helmolt. Sponsored by [Aquilo Solution.S GmbH](https://aquilo-solutions.com).
