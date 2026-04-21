"""Upsert a single GitHub issue with the latest constantia drift report.

Mirrors reporter_forgejo.py: one open issue tagged `constantia`, body
rewritten each run. A content hash embedded in the body lets us no-op
when nothing changed.

Stdlib only (urllib). Supports GitHub.com and GitHub Enterprise via
GITHUB_API_URL (defaults to https://api.github.com).
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request

ISSUE_TITLE = "Constantia Drift Report"
ISSUE_LABEL = "constantia"
LABEL_COLOR = "5a3fc0"  # GitHub rejects a leading '#'
LABEL_DESCRIPTION = "Automated concept-vs-code drift detection"
USER_AGENT = "constantia-drift-scanner"

_SSL_CTX = ssl.create_default_context()


def _api(
    method: str,
    url: str,
    token: str,
    data: dict | None = None,
) -> dict | list | None:
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, context=_SSL_CTX)
        raw = resp.read().decode()
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "GET":
            return None
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        print(f"  API error: {method} {url} → {e.code}: {err_body[:200]}", flush=True)
        return None


def _base(api_url: str, repo: str) -> str:
    return f"{api_url.rstrip('/')}/repos/{repo}"


def _ensure_label(api_url: str, repo: str, token: str) -> None:
    base = _base(api_url, repo)
    existing = _api("GET", f"{base}/labels/{ISSUE_LABEL}", token)
    if existing:
        return
    _api("POST", f"{base}/labels", token, {
        "name": ISSUE_LABEL,
        "color": LABEL_COLOR,
        "description": LABEL_DESCRIPTION,
    })


def _find_open_issue(api_url: str, repo: str, token: str) -> dict | None:
    base = _base(api_url, repo)
    issues = _api(
        "GET",
        f"{base}/issues?state=open&labels={ISSUE_LABEL}&per_page=50",
        token,
    ) or []
    for iss in issues:
        # GitHub's /issues endpoint returns PRs too — filter them out.
        if "pull_request" in iss:
            continue
        if iss["title"] == ISSUE_TITLE:
            return iss
    return None


def _extract_hash(body: str) -> str | None:
    m = re.search(r"<!-- hash:([\w:+]+) -->", body)
    return m.group(1) if m else None


def upsert_report_issue(
    *,
    api_url: str,
    repo: str,
    token: str,
    body_md: str,
    content_hash: str,
    any_findings: bool,
) -> None:
    """Create, update, or close the drift-report issue.

    - Any findings → open/update with current body (no-op if hash matches).
    - Zero findings → close any open issue so the dashboard stays green.
    """
    base = _base(api_url, repo)
    existing = _find_open_issue(api_url, repo, token)

    if not any_findings:
        if existing:
            _api("POST", f"{base}/issues/{existing['number']}/comments", token, {
                "body": "All concepts green. Closing automatically.",
            })
            _api("PATCH", f"{base}/issues/{existing['number']}", token, {"state": "closed"})
            print(f"issue #{existing['number']} closed — no findings.")
        else:
            print("no findings; no open issue; nothing to do.")
        return

    body = f"{body_md}\n\n<!-- hash:{content_hash} -->\n"
    if existing:
        existing_hash = _extract_hash(existing.get("body") or "")
        if existing_hash == content_hash:
            print(f"issue #{existing['number']} unchanged (hash {content_hash}).")
            return
        _api("PATCH", f"{base}/issues/{existing['number']}", token, {"body": body})
        _api("POST", f"{base}/issues/{existing['number']}/comments", token, {
            "body": f"Updated. Previous hash `{existing_hash}`, new `{content_hash}`.",
        })
        print(f"issue #{existing['number']} updated.")
        return

    _ensure_label(api_url, repo, token)
    result = _api("POST", f"{base}/issues", token, {
        "title": ISSUE_TITLE,
        "body": body,
        "labels": [ISSUE_LABEL],
    })
    if result:
        print(f"created issue #{result['number']}: {ISSUE_TITLE}")
