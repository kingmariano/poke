#!/usr/bin/env python3
"""Modal app: run the Randstorm chain + GPU hunt pipeline.

Usage (from the repository root, GitHub Actions or locally):

    modal run scanner/modal_scan.py::main --task selftest
    modal run scanner/modal_scan.py::main --task bench

The GPU functions consume the exact chain implementation in `scanner/chain`
(C, validated against the Python reference) and the vendored secp256k1 +
HASH160 + funded-DB match kernel in `scanner/gpu`.

Scan state (campaigns, shard cursors, hits) is intentionally NOT stored on
Modal; it lives in the private GitLab repository so a run can resume after
switching Modal accounts entirely.
"""

import subprocess
import sys

import modal

app = modal.App("randstorm-scan")

CUDA_BASE = "nvidia/cuda:12.4.1-devel-ubuntu22.04"

image = (
    modal.Image.from_registry(CUDA_BASE, add_python="3.11")
    .pip_install("numpy", "cupy-cuda12x")
    .add_local_dir("scanner", remote_path="/root/scanner", copy=True)
    .run_commands(
        "gcc -O3 -shared -fPIC -o /root/scanner/chain/librandstorm_chain.so "
        "/root/scanner/chain/randstorm_chain.c",
        "ls -la /root/scanner/chain/librandstorm_chain.so",
    )
)


@app.function(image=image, gpu="T4", timeout=3600)
def run_gpu_selftest():
    result = subprocess.run(
        [sys.executable, "/root/scanner/gpu/selftest_gpu.py"],
        capture_output=True,
        text=True,
    )
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


@app.local_entrypoint()
def main(task: str = "selftest"):
    if task == "selftest":
        payload = run_gpu_selftest.remote()
        print(payload["stdout"])
        if payload["returncode"] != 0:
            print(payload["stderr"], file=sys.stderr)
            raise SystemExit(payload["returncode"])
    else:
        raise SystemExit(f"unknown task: {task}")
