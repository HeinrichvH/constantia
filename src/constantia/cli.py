"""constantia CLI — step 2: loading + selection (no LLM yet)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .checks import registered_names
from .config import Catalogue, ConfigError, load_catalogue, load_concepts, load_rules
from .report import build_report, render_markdown
from .runner import run_all_guided, run_llm_rule
from .selector import Selector, select_files


BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _sev_colour(sev: str) -> str:
    return {"critical": RED, "high": YELLOW, "medium": CYAN, "low": DIM}.get(sev, "")


def cmd_list_concepts(args: argparse.Namespace) -> int:
    concepts = load_concepts(Path(args.path))
    print(f"{BOLD}Concepts ({len(concepts)}):{RESET}")
    for c in concepts:
        disc = f" {DIM}(has discovery){RESET}" if c.discovery else ""
        print(f"  {GREEN}{c.id}{RESET}{disc}")
        print(f"    {c.name}")
        print(f"    {DIM}{c.principle.strip().splitlines()[0][:120]}…{RESET}")
    return 0


def cmd_list_rules(args: argparse.Namespace) -> int:
    rules = load_rules(Path(args.path))
    print(f"{BOLD}Rules ({len(rules)}):{RESET}")
    for r in rules:
        sev = f"{_sev_colour(r.severity)}{r.severity}{RESET}"
        kind = f"{CYAN}{r.type}{RESET}"
        regex = r.selector.get("file_contains_regex")
        regex_str = f" {DIM}+regex{RESET}" if regex else ""
        print(f"  {GREEN}{r.id}{RESET}  {DIM}→ {r.concept_id}{RESET}  [{sev}, {kind}]")
        print(f"    {r.name}")
        print(f"    {DIM}selector: {r.selector['file_glob']}{regex_str}{RESET}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cat: Catalogue = load_catalogue(Path(args.path))
    print(f"{GREEN}OK{RESET}  {len(cat.concepts)} concepts, {len(cat.rules)} rules")
    for c in cat.concepts:
        rs = cat.rules_for(c.id)
        g = sum(1 for r in rs if r.type == "guided")
        l = sum(1 for r in rs if r.type == "llm_investigated")
        print(f"  {c.id}: {len(rs)} rule(s)  {DIM}({g} guided, {l} llm_investigated){RESET}")
        if not rs:
            print(f"    {YELLOW}warn: concept has no rules{RESET}")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    """Run each rule's selector over the repo and report match counts.

    No checks are evaluated. This is the step-2 local run — it proves the
    selector model produces tractable, countable scan sizes before any LLM
    gets involved.
    """
    cat = load_catalogue(Path(args.path))
    repo_root = Path(args.repo_root).resolve()
    print(f"{BOLD}Selection over {repo_root}:{RESET}")
    total = 0
    for r in cat.rules:
        if args.rule and r.id != args.rule:
            continue
        sel = Selector.from_config(r.selector)
        files = select_files(sel, repo_root)
        total += len(files)
        sev = f"{_sev_colour(r.severity)}{r.severity}{RESET}"
        kind = f"{CYAN}{r.type}{RESET}"
        print(f"\n  {GREEN}{r.id}{RESET}  [{sev}, {kind}]  {BOLD}{len(files)} file(s){RESET}")
        print(f"    {DIM}glob: {sel.file_glob}{RESET}")
        if sel.file_contains_regex:
            print(f"    {DIM}regex: {sel.file_contains_regex}{RESET}")
        if args.show and files:
            for p in files[: args.show]:
                print(f"      {p.relative_to(repo_root).as_posix()}")
            if len(files) > args.show:
                print(f"      {DIM}… and {len(files) - args.show} more{RESET}")
    print(f"\n{BOLD}Total matched files across rules: {total}{RESET}")
    return 0


def cmd_list_checks(args: argparse.Namespace) -> int:
    names = registered_names()
    print(f"{BOLD}Registered guided checks ({len(names)}):{RESET}")
    for n in names:
        print(f"  {GREEN}{n}{RESET}")
    return 0


def cmd_scan_guided(args: argparse.Namespace) -> int:
    """Run all guided rules and print findings grouped by rule.

    LLM-investigated rules are skipped here by design — they live in a
    separate report section (step 6). Exit code is non-zero iff any
    finding is produced, so CI can gate on guided signal alone.
    """
    cat = load_catalogue(Path(args.path))
    repo_root = Path(args.repo_root).resolve()
    results = run_all_guided(cat, repo_root, rule_id=args.rule)
    if not results:
        print(f"{YELLOW}No guided rules matched.{RESET}")
        return 0

    print(f"{BOLD}Guided scan over {repo_root}:{RESET}")
    total_findings = 0
    total_scanned = 0
    for res in results:
        r = res.rule
        sev = f"{_sev_colour(r.severity)}{r.severity}{RESET}"
        head = (
            f"\n  {GREEN}{r.id}{RESET}  {DIM}→ {r.concept_id}{RESET}  [{sev}]  "
            f"{BOLD}{res.files_scanned} file(s) scanned, {len(res.findings)} finding(s){RESET}"
        )
        print(head)
        total_scanned += res.files_scanned
        total_findings += len(res.findings)
        for f in res.findings[: args.max_per_rule] if args.max_per_rule else res.findings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            print(f"    {RED}•{RESET} {loc}  {f.message}")
        if args.max_per_rule and len(res.findings) > args.max_per_rule:
            print(f"    {DIM}… {len(res.findings) - args.max_per_rule} more suppressed{RESET}")
    print(
        f"\n{BOLD}Totals: {total_scanned} file(s) scanned, "
        f"{total_findings} finding(s){RESET}"
    )
    return 1 if total_findings else 0


def cmd_scan_llm(args: argparse.Namespace) -> int:
    """Run one llm_investigated rule via the goose+mistral investigator recipe.

    LLM rules are gated behind `--rule` because each file is a subprocess +
    network call (~$0.005–$0.02 per file depending on size). Use
    `--limit N` to calibrate cost on a subset before a full run. Findings
    live in a separate section from guided findings by schema; here we
    print them distinctly and tag every line with the verdict.
    """
    cat = load_catalogue(Path(args.path))
    repo_root = Path(args.repo_root).resolve()
    rule = next((r for r in cat.rules if r.id == args.rule), None)
    if rule is None:
        print(f"{RED}error:{RESET} no rule with id '{args.rule}'", file=sys.stderr)
        return 2
    if rule.type != "llm_investigated":
        print(f"{RED}error:{RESET} rule '{args.rule}' is {rule.type}, not llm_investigated", file=sys.stderr)
        return 2

    from .selector import Selector, select_files
    sel = Selector.from_config(rule.selector)
    files = select_files(sel, repo_root)
    capped = files[: args.limit] if args.limit else files
    print(f"{BOLD}LLM scan:{RESET} {rule.id}  {DIM}→ {rule.concept_id}{RESET}")
    print(f"  {DIM}selector matched {len(files)} file(s); investigating {len(capped)}{RESET}")

    if args.dry_run:
        for p in capped:
            print(f"    {p.relative_to(repo_root).as_posix()}")
        return 0

    def _progress(i: int, total: int, rel: str) -> None:
        print(f"  {DIM}[{i}/{total}]{RESET} {rel}", flush=True)

    def _vprogress(i: int, total: int, f) -> None:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  {DIM}verify [{i}/{total}]{RESET} {loc}", flush=True)

    ckpt_dir = Path(args.checkpoint_dir).resolve() if args.checkpoint_dir else None

    def _resumed(n: int, total: int) -> None:
        print(f"  {GREEN}resumed {n}/{total} verdict(s) from checkpoint{RESET}")

    res = run_llm_rule(
        rule, cat, repo_root,
        limit=args.limit,
        verify=not args.no_verify,
        concurrency=args.concurrency,
        checkpoint_dir=ckpt_dir,
        discard_checkpoint_on_success=args.clear_checkpoint,
        progress=_progress,
        verify_progress=_vprogress,
        resumed_progress=_resumed,
    )

    counts = {"fit": 0, "violation": 0, "uncertain": 0, "not_applicable": 0, "error": 0}
    for v in res.verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    print(
        f"\n{BOLD}Verdicts ({res.files_scanned} file(s)):{RESET} "
        f"{GREEN}{counts['fit']} fit{RESET}  "
        f"{RED}{counts['violation']} violation{RESET}  "
        f"{YELLOW}{counts['uncertain']} uncertain{RESET}  "
        f"{DIM}{counts['not_applicable']} n/a{RESET}  "
        f"{YELLOW}{counts['error']} error{RESET}"
    )
    r = res.resolution
    if r.dropped_symbol_absent or r.dropped_wrong_file or r.line_corrected:
        print(
            f"\n{DIM}Citation resolution: {r.kept} kept, "
            f"{r.dropped_symbol_absent} dropped (symbol absent), "
            f"{r.dropped_wrong_file} dropped (wrong file), "
            f"{r.line_corrected} line-corrected{RESET}"
        )
    if res.findings:
        print(f"\n{BOLD}Findings ({len(res.findings)}):{RESET}")
        for f in res.findings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            print(f"    {RED}•{RESET} {loc}  {f.message}")
            if f.evidence.get("symbol"):
                print(f"      {DIM}symbol: {f.evidence['symbol']}{RESET}")
            if f.evidence.get("cited_line"):
                print(
                    f"      {DIM}line corrected: LLM said {f.evidence['cited_line']} "
                    f"→ resolved {f.evidence['resolved_line']}{RESET}"
                )
    if res.dropped:
        print(f"\n{DIM}Verifier dropped {len(res.dropped)} finding(s):{RESET}")
        for d in res.dropped:
            loc = f"{d.finding.file}:{d.finding.line}" if d.finding.line else d.finding.file
            print(f"    {DIM}- {loc}  {d.reason}{RESET}")
    return 1 if res.findings else 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Full orchestrator: run guided rules then llm rules; emit one report.

    Guided + LLM findings live in strictly separated sections, each with
    its own content hash (sha256 of canonical JSON). Exit code is
    non-zero iff any finding survives in either section — so CI can
    gate on the unified signal or on a single section (use
    `scan-guided` / `scan-llm` for the latter).
    """
    cat = load_catalogue(Path(args.path))
    repo_root = Path(args.repo_root).resolve()
    config_path = Path(args.path).resolve()

    rule_filter = set(args.rule) if args.rule else None
    concept_filter = set(args.concept) if args.concept else None

    def _include(r) -> bool:
        if rule_filter and r.id not in rule_filter:
            return False
        if concept_filter and r.concept_id not in concept_filter:
            return False
        return True

    # ---- Guided phase (deterministic, cheap) ----
    guided_results = []
    guided_rules = [r for r in cat.rules if r.type == "guided" and _include(r)]
    print(f"{BOLD}Guided phase:{RESET} {len(guided_rules)} rule(s)")
    for r in guided_rules:
        from .runner import run_guided_rule
        res = run_guided_rule(r, repo_root)
        guided_results.append(res)
        print(
            f"  {GREEN}{r.id}{RESET}  "
            f"{DIM}{res.files_scanned} file(s), {len(res.findings)} finding(s){RESET}"
        )

    # ---- LLM phase (paid; opt-out with --skip-llm) ----
    llm_results = []
    llm_rules = [r for r in cat.rules if r.type == "llm_investigated" and _include(r)]
    if args.skip_llm:
        print(f"{YELLOW}LLM phase skipped ({len(llm_rules)} rule(s) would have run).{RESET}")
    else:
        print(f"{BOLD}LLM phase:{RESET} {len(llm_rules)} rule(s)")
        for r in llm_rules:
            def _p(i: int, total: int, rel: str, rid=r.id) -> None:
                print(f"  {DIM}{rid} [{i}/{total}]{RESET} {rel}", flush=True)

            def _vp(i: int, total: int, f, rid=r.id) -> None:
                loc = f"{f.file}:{f.line}" if f.line else f.file
                print(f"  {DIM}{rid} verify [{i}/{total}]{RESET} {loc}", flush=True)

            ckpt_dir = Path(args.checkpoint_dir).resolve() if args.checkpoint_dir else None

            def _resumed(n: int, total: int, rid=r.id) -> None:
                print(f"  {GREEN}{rid}: resumed {n}/{total} verdict(s){RESET}")

            res = run_llm_rule(
                r, cat, repo_root,
                limit=args.limit,
                verify=not args.no_verify,
                concurrency=args.concurrency,
                checkpoint_dir=ckpt_dir,
                discard_checkpoint_on_success=args.clear_checkpoint,
                progress=_p,
                verify_progress=_vp,
                resumed_progress=_resumed,
            )
            llm_results.append(res)
            print(
                f"  {GREEN}{r.id}{RESET}  "
                f"{DIM}{res.files_scanned} file(s), "
                f"{len(res.findings)} finding(s) kept, "
                f"{len(res.dropped)} dropped{RESET}"
            )

    report = build_report(
        cat, repo_root, config_path,
        guided_results=guided_results,
        llm_results=llm_results,
    )
    md = render_markdown(report)

    if args.json_out:
        Path(args.json_out).write_text(report.to_json())
        print(f"\n{GREEN}wrote{RESET} {args.json_out}")
    if args.md_out:
        Path(args.md_out).write_text(md)
        print(f"{GREEN}wrote{RESET} {args.md_out}")
    if not args.json_out and not args.md_out:
        print()
        print(md)

    total = (
        sum(len(r.findings) for r in guided_results)
        + sum(len(r.findings) for r in llm_results)
    )
    print(
        f"{BOLD}Hashes:{RESET} guided={report.guided['content_hash']}  "
        f"llm={report.llm['content_hash']}"
    )

    if args.forgejo_issue:
        import os
        token = os.environ.get("FORGEJO_TOKEN")
        url = os.environ.get("FORGEJO_URL")
        repo = os.environ.get("FORGEJO_REPO")
        if not (token and url and repo):
            print(f"{RED}error:{RESET} --forgejo-issue needs FORGEJO_TOKEN/URL/REPO env vars", file=sys.stderr)
            return 2
        from .reporter_forgejo import upsert_report_issue
        combined_hash = f"{report.guided['content_hash']}+{report.llm['content_hash']}"
        upsert_report_issue(
            forgejo_url=url, repo=repo, token=token,
            body_md=md, content_hash=combined_hash,
            any_findings=bool(total),
        )
        # When the scheduler delivers the report via Forgejo, the issue IS
        # the signal — don't also fail the workflow on findings, or Argo
        # flags every run with drift as broken.
        return 0

    if args.github_issue:
        import os
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPO")
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        if not (token and repo):
            print(f"{RED}error:{RESET} --github-issue needs GITHUB_TOKEN and GITHUB_REPO env vars "
                  f"(GITHUB_API_URL optional, defaults to https://api.github.com)", file=sys.stderr)
            return 2
        from .reporter_github import upsert_report_issue as upsert_gh
        combined_hash = f"{report.guided['content_hash']}+{report.llm['content_hash']}"
        upsert_gh(
            api_url=api_url, repo=repo, token=token,
            body_md=md, content_hash=combined_hash,
            any_findings=bool(total),
        )
        return 0

    return 1 if total else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="constantia", description="consistency scanner (concepts × rules → findings)")
    p.add_argument("--version", action="version", version=f"constantia {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    lc = sub.add_parser("list-concepts", help="load and pretty-print concepts.yaml")
    lc.add_argument("path", help="path to concepts.yaml")
    lc.set_defaults(func=cmd_list_concepts)

    lr = sub.add_parser("list-rules", help="load and pretty-print rules.yaml")
    lr.add_argument("path", help="path to rules.yaml")
    lr.set_defaults(func=cmd_list_rules)

    v = sub.add_parser("validate", help="validate concepts.yaml + rules.yaml in a config directory")
    v.add_argument("path", help="directory containing concepts.yaml and rules.yaml")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("select", help="resolve each rule's selector to concrete file counts")
    s.add_argument("path", help="directory containing concepts.yaml and rules.yaml")
    s.add_argument("--repo-root", default=".", help="repo root to scan (default: cwd)")
    s.add_argument("--rule", help="only evaluate this rule id")
    s.add_argument("--show", type=int, default=0, help="print first N matched files per rule")
    s.set_defaults(func=cmd_select)

    lch = sub.add_parser("list-checks", help="list registered guided checks")
    lch.set_defaults(func=cmd_list_checks)

    sc = sub.add_parser("scan-guided", help="run all guided rules and print findings")
    sc.add_argument("path", help="directory containing concepts.yaml and rules.yaml")
    sc.add_argument("--repo-root", default=".", help="repo root to scan (default: cwd)")
    sc.add_argument("--rule", help="only evaluate this rule id")
    sc.add_argument("--max-per-rule", type=int, default=20, help="cap findings printed per rule (0 = no cap)")
    sc.set_defaults(func=cmd_scan_guided)

    sl = sub.add_parser("scan-llm", help="run one llm_investigated rule via goose+mistral")
    sl.add_argument("path", help="directory containing concepts.yaml and rules.yaml")
    sl.add_argument("--repo-root", default=".", help="repo root to scan (default: cwd)")
    sl.add_argument("--rule", required=True, help="rule id to evaluate (required — LLM costs $$)")
    sl.add_argument("--limit", type=int, help="cap files investigated (for cost calibration)")
    sl.add_argument("--dry-run", action="store_true", help="list files that would be investigated; don't call goose")
    sl.add_argument("--no-verify", action="store_true", help="skip adversarial verifier pass (faster, noisier output)")
    sl.add_argument("--concurrency", type=int, default=1, help="parallel goose subprocesses (default 1; try 4-8 for full runs)")
    sl.add_argument("--checkpoint-dir", help="JSONL checkpoint directory; resumes previous run if fingerprint matches")
    sl.add_argument("--clear-checkpoint", action="store_true", help="delete the checkpoint file on success")
    sl.set_defaults(func=cmd_scan_llm)

    sa = sub.add_parser("scan", help="run guided + llm phases; emit two-section report")
    sa.add_argument("path", help="directory containing concepts.yaml and rules.yaml")
    sa.add_argument("--repo-root", default=".", help="repo root to scan (default: cwd)")
    sa.add_argument("--rule", action="append", help="only run these rule ids (may repeat)")
    sa.add_argument("--concept", action="append", help="only run rules for these concept ids (may repeat)")
    sa.add_argument("--limit", type=int, help="cap files per LLM rule (cost calibration)")
    sa.add_argument("--no-verify", action="store_true", help="skip verifier pass on LLM findings")
    sa.add_argument("--skip-llm", action="store_true", help="only run guided phase")
    sa.add_argument("--concurrency", type=int, default=1, help="parallel goose subprocesses per LLM rule (default 1)")
    sa.add_argument("--checkpoint-dir", help="JSONL checkpoint directory (per rule); resumes matching runs")
    sa.add_argument("--clear-checkpoint", action="store_true", help="delete checkpoints on successful completion")
    sa.add_argument("--json-out", help="write JSON report to this path")
    sa.add_argument("--md-out", help="write markdown report to this path")
    sa.add_argument("--forgejo-issue", action="store_true",
                    help="upsert a single Forgejo issue with the report (needs FORGEJO_TOKEN/URL/REPO env)")
    sa.add_argument("--github-issue", action="store_true",
                    help="upsert a single GitHub issue with the report "
                         "(needs GITHUB_TOKEN/GITHUB_REPO env; GITHUB_API_URL optional for Enterprise)")
    sa.set_defaults(func=cmd_scan)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"{RED}error:{RESET} {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
