#!/usr/bin/env python3
"""Differential test: C chain generator vs the Python reference."""

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chain  # noqa: E402
from reference import generate_reference  # noqa: E402


def run_case(params, t1_array, dt2_array, label, ranges=None):
    total = chain.flat_total(params["seed_count"], len(t1_array), len(dt2_array), params["key_count"])
    if ranges is None:
        ranges = [(0, total)]
    for start, count in ranges:
        c_bytes, written = chain.generate(params, t1_array, dt2_array, start, count)
        assert written == count, (label, start, count, written)
        keys_c = [c_bytes[i * 32:(i + 1) * 32].hex() for i in range(written)]
        keys_py = generate_reference(params, t1_array, dt2_array, start, count)
        assert keys_c == keys_py, (label, start, count, keys_c[:2], keys_py[:2])
    print(f"ok: {label}")


def main():
    rng = random.Random(20260928)
    t1_pool = [0x4E0B2C40, 0x4E0B2C41, 0x4E1A0000, 0x5A1F1234, 0x00000001, 0xFFFFFFFF]

    # full-range differential over a mixed parameter set
    for case in range(6):
        params = {
            "seed_start": rng.randrange(1 << 32),
            "context_index": rng.randrange(0, 3),
            "math_offset": rng.randrange(0, 3),
            "key_count": rng.randrange(1, 4),
            "rc4_offset": rng.randrange(0, 4),
            "seed_count": rng.randrange(1, 4),
        }
        t1_array = rng.sample(t1_pool, rng.randrange(1, 3))
        dt2_array = [rng.randrange(0, 5000) for _ in range(rng.randrange(1, 3))]
        run_case(params, t1_array, dt2_array, f"full case {case}")

    # sub-range differential: start mid-group, partial groups, single keys
    params = {
        "seed_start": 123456789,
        "context_index": 1,
        "math_offset": 0,
        "key_count": 3,
        "rc4_offset": 1,
        "seed_count": 5,
    }
    t1_array = [0x4E0B2C40, 0x4E0B2C41, 0x4E1A0000]
    dt2_array = [0, 7, 4000]
    total = chain.flat_total(5, 3, 3, 3)
    ranges = [(0, 1), (1, 1), (2, 4), (7, 5), (13, 11), (total - 3, 3), (total - 1, 1)]
    run_case(params, t1_array, dt2_array, "sub-ranges", ranges)

    # edge: single key per group, zero offsets
    params = {"seed_start": 0xFFFFFFFF, "context_index": 0, "math_offset": 0,
              "key_count": 1, "rc4_offset": 0, "seed_count": 2}
    run_case(params, [0], [0], "edge wrap seed / single key")

    print("ALL CHAIN DIFFERENTIAL TESTS PASSED")


if __name__ == "__main__":
    main()
