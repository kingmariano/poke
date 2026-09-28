#!/usr/bin/env python3
"""GPU self-test: chain generation, limb conversion, and a synthetic hunt hit.

Runs on a CUDA host (Modal). Validates:
  1. the C chain vs the Python reference (differential, small parameter set)
  2. big-endian key bytes -> little-endian limb conversion
  3. the vendored GPU kernel finds known keys (compressed + uncompressed +
     P2SH-P2WPKH) placed into a synthetic sorted database
  4. throughput of the hunt kernel (keys/s)
"""

import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCANNER = os.path.dirname(HERE)
CHAIN = os.path.join(SCANNER, "chain")
sys.path.insert(0, SCANNER)
sys.path.insert(0, CHAIN)

import numpy as np  # noqa: E402

import chain  # noqa: E402
import hunt_cuda  # noqa: E402
from reference import generate_reference  # noqa: E402
from ec import hash160, pubkey_bytes  # noqa: E402


def test_chain_differential():
    rng = random.Random(7)
    params = {
        "seed_start": 0x4E0B2C40,
        "context_index": 1,
        "math_offset": 0,
        "key_count": 3,
        "rc4_offset": 1,
        "seed_count": 4,
    }
    t1_array = [0x4E0B2C40, 0x4E0B2C41]
    dt2_array = [0, 4000]
    c_bytes, written = chain.generate(params, t1_array, dt2_array, 0, 6)
    keys_c = [c_bytes[i * 32:(i + 1) * 32].hex() for i in range(written)]
    keys_py = generate_reference(params, t1_array, dt2_array, 0, 6)
    assert keys_c == keys_py, (keys_c[:2], keys_py[:2])
    print("chain differential ok")


def test_limb_conversion():
    params = {
        "seed_start": 123456789,
        "context_index": 0,
        "math_offset": 0,
        "key_count": 2,
        "rc4_offset": 0,
        "seed_count": 2,
    }
    keys_bytes, _ = chain.generate(params, [0x4E0B2C40], [0], 0, 4)
    limbs = hunt_cuda.limbs_from_bytes(keys_bytes)
    for index in range(limbs.shape[0]):
        key_int = int.from_bytes(keys_bytes[index * 32:(index + 1) * 32], "big")
        assert list(limbs[index]) == hunt_cuda.int_to_limbs(key_int), index
    print("limb conversion ok")


def test_synthetic_hit():
    import cupy as cp

    # deterministic key from the chain (no need to hide anything in a test)
    params = {
        "seed_start": 123456789,
        "context_index": 1,
        "math_offset": 0,
        "key_count": 1,
        "rc4_offset": 0,
        "seed_count": 1,
    }
    keys_bytes, written = chain.generate(params, [0x4E0B2C40], [4000], 0, 1)
    assert written == 1
    key_int = int.from_bytes(keys_bytes[:32], "big")

    compressed = pubkey_bytes(key_int, compressed=True)
    uncompressed = pubkey_bytes(key_int, compressed=False)
    h_c = hash160(compressed)
    h_u = hash160(uncompressed)
    h_sh = hash160(b"\x00\x14" + h_c)

    db_records = sorted({h_c, h_u, h_sh})
    db_np = np.frombuffer(b"".join(db_records), dtype=np.uint8)
    db_gpu = cp.asarray(db_np)
    gtab = cp.asarray(hunt_cuda.build_g_table_np())
    module = hunt_cuda.compile_cuda(stride=1)
    limbs = hunt_cuda.limbs_from_bytes(keys_bytes)
    hits = hunt_cuda.hunt_batch(limbs, gtab, db_gpu, len(db_records), module)
    found = set(hits)
    expected = {(key_int, 0), (key_int, 1), (key_int, 2)}
    assert expected.issubset(found), (found, expected)
    print("synthetic GPU hit ok:", sorted(found))


def bench(seconds=10):
    import cupy as cp

    module = hunt_cuda.compile_cuda(stride=1)
    gtab = cp.asarray(hunt_cuda.build_g_table_np())
    db_gpu = cp.zeros(20, dtype=cp.uint8)
    params = {
        "seed_start": 987654321,
        "context_index": 0,
        "math_offset": 0,
        "key_count": 1,
        "rc4_offset": 0,
        "seed_count": 1 << 32,
    }
    batch_keys = 1 << 20
    total = 0
    started = time.time()
    flat = 0
    while time.time() - started < seconds:
        keys_bytes, written = chain.generate(params, [0x4E0B2C40], [0], flat, batch_keys)
        limbs = hunt_cuda.limbs_from_bytes(keys_bytes)
        hunt_cuda.hunt_batch(limbs, gtab, db_gpu, 0, module)
        total += written
        flat += written
    elapsed = time.time() - started
    print(f"pipeline throughput: {total / elapsed:,.0f} keys/s ({total:,} keys in {elapsed:.1f}s)")


def main():
    print("GPU:", cp_device_name())
    test_chain_differential()
    test_limb_conversion()
    test_synthetic_hit()
    bench()
    print("ALL GPU SELFTESTS PASSED")


def cp_device_name():
    import cupy as cp
    properties = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
    return properties["name"].decode() if isinstance(properties["name"], bytes) else properties["name"]


if __name__ == "__main__":
    main()
