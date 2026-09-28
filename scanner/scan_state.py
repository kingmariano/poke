#!/usr/bin/env python3
"""GitLab-backed persistent state for scan campaigns.

State lives in the private `group18185918/randstorm` repository under `state/`:

    state/campaigns/<id>/manifest.json      campaign definition + shard list
    state/campaigns/<id>/progress.json      counters (updated after each shard)
    state/campaigns/<id>/shards/<sid>.json  per-shard result (hits, cursors)

Because the state is external to Modal, a scan can resume after switching
Modal accounts entirely: re-run the campaign task and completed shards are
skipped.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

GITLAB_API = "https://gitlab.com/api/v4"
PROJECT = "group18185918/randstorm"
BRANCH = "main"


def _token():
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise RuntimeError("GITLAB_TOKEN is not set")
    return token


def _request(method, path, payload=None, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        GITLAB_API + path,
        data=data,
        method=method,
        headers={
            "PRIVATE-TOKEN": _token(),
            "Content-Type": "application/json",
            "User-Agent": "randstorm-scan",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return response.status, (body if raw else (json.loads(body) if body else {}))
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def _project():
    return urllib.parse.quote(PROJECT, safe="")


def _file(key):
    return urllib.parse.quote(f"state/{key}", safe="")


def exists(key):
    status, _ = _request("GET", f"/projects/{_project()}/repository/files/{_file(key)}?ref={BRANCH}")
    return status == 200


def read_bytes(key, default=None):
    status, body = _request("GET", f"/projects/{_project()}/repository/files/{_file(key)}/raw?ref={BRANCH}", raw=True)
    if status != 200:
        return default
    return body


def read_json(key, default=None):
    body = read_bytes(key)
    if body is None:
        return default
    try:
        return json.loads(body.decode())
    except ValueError:
        return default


def write_json(key, payload, message):
    """Create or update a JSON state file (idempotent upsert)."""
    content = json.dumps(payload, indent=1)
    path = f"/projects/{_project()}/repository/files/{_file(key)}"
    body = {"branch": BRANCH, "content": content, "commit_message": message}
    if exists(key):
        status, response = _request("PUT", path, body)
    else:
        status, response = _request("POST", path, body)
    if status not in (200, 201):
        raise RuntimeError(f"state write failed ({status}): {str(response)[:300]}")
    return response


def list_shard_results(campaign_id):
    """Set of shard ids that already have a stored result."""
    path = f"/projects/{_project()}/repository/tree"
    query = urllib.parse.urlencode({
        "path": f"state/campaigns/{campaign_id}/shards",
        "ref": BRANCH,
        "per_page": 100,
    })
    status, body = _request("GET", f"{path}?{query}")
    if status != 200 or not isinstance(body, list):
        return set()
    return {entry["name"][:-5] for entry in body if entry["name"].endswith(".json")}
