# Security Policy

## Supported versions

Constantia is pre-1.0. Only the `main` branch receives security fixes.
When 1.0 ships, this policy will be updated with an explicit support
window.

## Reporting a vulnerability

Please do **not** open a public issue for security-sensitive reports.

Email: **heinrich.vonhelmolt@aquilo-solutions.com**

Subject line: `[constantia security]` plus a short descriptor.

Include, if you can:

- A minimal reproduction (concept/rule YAML, input repo structure, command).
- The version or commit SHA you tested against.
- Your assessment of impact (what an attacker could achieve).

Expect an acknowledgement within **3 working days** and a substantive
reply within **10 working days**. If you don't hear back in that window,
escalate by opening an issue *without* disclosure details, just pinging
the maintainer.

## What counts as a vulnerability here

The scanner's threat model is narrow — it reads code and emits a
report. Things we treat as security issues:

- **Arbitrary code or command execution** triggered by a crafted
  `concepts.yaml`, `rules.yaml`, or scanned repo contents. We use
  `yaml.safe_load` and do not `eval` anything; a bypass is in-scope.
- **Injection in guided checks** that shell out (`rg`, `git`), where
  repo-controlled input reaches the command line unescaped.
- **Token or secret leakage** from a reporter (Forgejo, GitHub, future
  integrations) — e.g. logging a token, sending it to the wrong host,
  writing it to the report body.
- **Path traversal** in markdown-path resolution or any future check
  that reads files — candidates escaping the repo root.
- **SSRF** via a reporter URL or any future feature that fetches
  user-controlled URLs during a scan.

## Out of scope

- Findings that are "wrong" because a concept was written poorly —
  that's a concept-authoring issue, not a scanner one.
- LLM hallucinations. The adversarial verifier reduces them; it does
  not eliminate them. Treat the LLM section of the report as a
  review queue, not ground truth.
- Denial of service from scanning a deliberately hostile repo (billion
  tiny files, pathological regex targets). The scanner is a local tool
  run by its operator; they choose what to point it at.

## Disclosure

We'll coordinate a disclosure timeline with you. Default: fix in a
private branch, release a patch, credit you (if you want) in the
release notes, then open-disclose within 14 days of the patched
release.
