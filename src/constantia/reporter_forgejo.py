"""Upsert a single Forgejo issue with the latest constantia drift report.

Pattern mirrors tools/stale-ref-checker/src/reporting/github_issue.py:
one open issue tagged `constantia`, body rewritten each run. A content
hash embedded in the body lets us no-op when nothing changed — this
keeps issue notifications quiet on identical repeat runs.

Stdlib only (urllib), same as the scanner itself.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request

ISSUE_TITLE = "Constantia Drift Report"
ISSUE_LABEL = "constantia"
LABEL_COLOR = "#5a3fc0"
LABEL_DESCRIPTION = "Automated concept-vs-code drift detection"

_SSL_CTX = ssl.create_default_context()


def _api(method: str, url: str, token: str, data: dict | None = None) -> dict | list | None:
    sep = "&" if "?" in url else "?"
    full_url = f"{url}{sep}token={token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(full_url, data=body, method=method)
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


def _base(forgejo_url: str, repo: str) -> str:
    return f"{forgejo_url}/api/v1/repos/{repo}"


def _ensure_label(forgejo_url: str, repo: str, token: str) -> int:
    base = _base(forgejo_url, repo)
    labels = _api("GET", f"{base}/labels?limit=50", token) or []
    for lbl in labels:
        if lbl["name"] == ISSUE_LABEL:
            return lbl["id"]
    result = _api("POST", f"{base}/labels", token, {
        "name": ISSUE_LABEL,
        "color": LABEL_COLOR,
        "description": LABEL_DESCRIPTION,
    })
    if result is not None:
        return result["id"]
    labels = _api("GET", f"{base}/labels?limit=50", token) or []
    for lbl in labels:
        if lbl["name"] == ISSUE_LABEL:
            return lbl["id"]
    raise RuntimeError(f"Failed to create or find label '{ISSUE_LABEL}'")


def _find_open_issue(forgejo_url: str, repo: str, token: str) -> dict | None:
    base = _base(forgejo_url, repo)
    issues = _api("GET", f"{base}/issues?state=open&labels={ISSUE_LABEL}&type=issues&limit=50", token) or []
    for iss in issues:
        if iss["title"] == ISSUE_TITLE:
            return iss
    return None


def _extract_hash(body: str) -> str | None:
    m = re.search(r"<!-- hash:([\w:]+) -->", body)
    return m.group(1) if m else None


def upsert_report_issue(
    *,
    forgejo_url: str,
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
    base = _base(forgejo_url, repo)
    existing = _find_open_issue(forgejo_url, repo, token)

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

    label_id = _ensure_label(forgejo_url, repo, token)
    result = _api("POST", f"{base}/issues", token, {
        "title": ISSUE_TITLE,
        "body": body,
        "labels": [label_id],
    })
    if result:
        print(f"created issue #{result['number']}: {ISSUE_TITLE}")
