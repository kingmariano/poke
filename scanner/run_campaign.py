#!/usr/bin/env python3
"""Campaign orchestration for the Randstorm scan (runs in GitHub Actions).

A campaign is a JSON file under scanner/campaigns/. It describes the search
axes; shards are fixed-size slices of the resulting flat space. Every shard is
executed by the Modal GPU function `run_shard` and its result is stored in the
private GitLab state repository, so the campaign is resumable across Modal
accounts.

Usage (inside the Modal local entrypoint):
    python -m modal run scanner/modal_scan.py::main --task campaign \
        --campaign demo --max-shards 2 --budget-seconds 120
"""

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scan_state  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_campaign(name):
    path = os.path.join(HERE, "campaigns", f"{name}.json")
    with open(path) as handle:
        config = json.load(handle)
    config.setdefault("id", name)
    return config


def enumerate_shards(config):
    """Deterministic shard list for the campaign axes."""
    shards = []
    shard_keys = int(config.get("shard_keys", 4_000_000))
    state_mode = config.get("mode") == "state"
    seed_count = 1 if state_mode else int(config.get("seed_count", 0))
    for context in config.get("context_indexes", [0]):
        for math_offset in config.get("math_offsets", [0]):
            for rc4_offset in config.get("rc4_offsets", [config.get("rc4_offset", 0)]):
                total = (
                    seed_count
                    * len(config.get("t1_values", []))
                    * len(config.get("dt2_values", []))
                    * int(config.get("key_count", 1))
                )
                start = 0
                while start < total:
                    count = min(shard_keys, total - start)
                    shard = {
                        "shard_id": f"c{context}-m{math_offset}-o{rc4_offset}-{start}-{count}",
                        "seed_start": int(config.get("seed_start", 0)),
                        "seed_count": seed_count,
                        "context_index": int(context),
                        "math_offset": int(math_offset),
                        "key_count": int(config["key_count"]),
                        "rc4_offset": int(rc4_offset),
                        "t1_values": [int(v) for v in config["t1_values"]],
                        "dt2_values": [int(v) for v in config["dt2_values"]],
                        "flat_start": start,
                        "flat_count": count,
                    }
                    if state_mode:
                        shard["mode"] = 1
                        shard["mwc_s0"] = int(config["mwc_s0"])
                        shard["mwc_s1"] = int(config["mwc_s1"])
                    shards.append(shard)
                    start += count
    return shards


def fetch_victims_index(repo="group18185918/randstorm"):
    """Download the latest victims HASH160 index artifact from GitLab."""
    token = os.environ["GITLAB_TOKEN"]
    api = "https://gitlab.com/api/v4"

    def get(url, raw=False):
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "User-Agent": "randstorm-scan"})
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read()
            return body if raw else json.loads(body or b"{}")

    project = urllib.parse.quote(repo, safe="")
    pipelines = get(f"{api}/projects/{project}/pipelines?ref=main&status=success&per_page=20")
    for pipeline in pipelines:
        jobs = get(f"{api}/projects/{project}/pipelines/{pipeline['id']}/jobs?per_page=50")
        for job in jobs:
            if job["name"] == "victims" and job["status"] == "success":
                archive = get(f"{api}/projects/{project}/jobs/{job['id']}/artifacts", raw=True)
                with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                    for name in zf.namelist():
                        if name.endswith("victims.hash160.bin"):
                            return zf.read(name)
    raise RuntimeError("victims index artifact not found")


def init_campaign(name):
    config = load_campaign(name)
    shards = enumerate_shards(config)
    manifest = {"config": config, "shards": shards, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    scan_state.write_json(f"campaigns/{config['id']}/manifest.json", manifest,
                          f"campaign {config['id']}: plan {len(shards)} shards")
    print(f"campaign {config['id']}: {len(shards)} shards planned; "
          f"total keys = {sum(s['flat_count'] for s in shards):,}")
    return manifest


def run_campaign(name, max_shards=1, budget_seconds=900, run_shard=None, index_bytes=None):
    config = name if isinstance(name, dict) else load_campaign(name)
    campaign_id = config["id"]
    manifest = scan_state.read_json(f"campaigns/{campaign_id}/manifest.json")
    if manifest is None:
        manifest = {"config": config, "shards": enumerate_shards(config),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        scan_state.write_json(f"campaigns/{campaign_id}/manifest.json", manifest,
                              f"campaign {campaign_id}: plan {len(manifest['shards'])} shards")
    if isinstance(config, dict) and "extra" in config:
        manifest.setdefault("extra", config["extra"])
    shards = manifest["shards"]
    done = scan_state.list_shard_results(campaign_id)
    pending = [s for s in shards if s["shard_id"] not in done]
    print(f"campaign {campaign_id}: {len(done)} done, {len(pending)} pending, running up to {max_shards}")
    if not pending:
        print("campaign complete")
        return 0
    if index_bytes is None:
        index_bytes = fetch_victims_index()
        print(f"victims index: {len(index_bytes):,} bytes")
    executed = 0
    for shard in pending[:max_shards]:
        print(f"shard {shard['shard_id']}: {shard['flat_count']:,} keys, budget {budget_seconds}s")
        result = run_shard.remote(json.dumps(shard), index_bytes, budget_seconds)
        result["campaign_id"] = campaign_id
        scan_state.write_json(
            f"campaigns/{campaign_id}/shards/{shard['shard_id']}.json",
            result,
            f"campaign {campaign_id}: shard {shard['shard_id']} "
            f"({result['done']:,}/{result['flat_count']:,} keys, {len(result['hits'])} hits)",
        )
        executed += 1
        print(f"  done {result['done']:,}/{result['flat_count']:,} keys in {result['elapsed_seconds']:.1f}s "
              f"({result['keys_per_second']:,.0f} keys/s), hits: {len(result['hits'])}")
    print(f"executed {executed} shard(s)")
    return 0


def status(name):
    config = load_campaign(name)
    manifest = scan_state.read_json(f"campaigns/{config['id']}/manifest.json")
    if manifest is None:
        print("campaign not planned yet")
        return 1
    done = scan_state.list_shard_results(config["id"])
    shards = manifest["shards"]
    total_keys = sum(s["flat_count"] for s in shards)
    done_keys = sum(s["flat_count"] for s in shards if s["shard_id"] in done)
    hits = 0
    for shard in shards:
        if shard["shard_id"] in done:
            result = scan_state.read_json(f"campaigns/{config['id']}/shards/{shard['shard_id']}.json")
            if result:
                hits += len(result.get("hits", []))
    print(f"campaign {config['id']}: {len(done)}/{len(shards)} shards, "
          f"{done_keys:,}/{total_keys:,} keys ({100.0 * done_keys / max(total_keys, 1):.2f}%), hits: {hits}")
    return 0
