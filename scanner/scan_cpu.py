#!/usr/bin/env python3
"""CPU reference scanner for the Randstorm search (slow, verification-grade).

Enumerates the pre-2013-09 V8 model (E1: libc-seeded MWC Math.random) and
matches candidate keys against a sorted HASH160 index. The GPU stage will
re-implement this enumeration with batched EC arithmetic.
"""

import bisect

from ec import hash160, pubkey_bytes
from jsbn import JsbnSecureRandom, derive_key_hex
from v8_random import e1_math_random_u32s


def load_index(path):
    with open(path, "rb") as handle:
        data = handle.read()
    return [data[i:i + 20] for i in range(0, len(data), 20)]


def index_contains(index, digest):
    position = bisect.bisect_left(index, digest)
    return position < len(index) and index[position] == digest


def e1_scan(libc_seeds, context_index, t1_values, t2_deltas, key_numbers, index,
            compressed=True, on_hit=None):
    """Search candidates and return hits: dicts with parameters + key/address hash."""
    hits = []
    for seed in libc_seeds:
        for t1 in t1_values:
            for delta in t2_deltas:
                generator = JsbnSecureRandom(e1_math_random_u32s(seed, context_index))
                generator.seed_time(t1)
                generator.second_time = t1 + delta
                for key_number in range(key_numbers):
                    key_hex = derive_key_hex(generator.stream(33))
                    private_key = int(key_hex, 16)
                    public = pubkey_bytes(private_key, compressed=compressed)
                    digest = hash160(public)
                    if index_contains(index, digest):
                        hit = {
                            "libc_seed": seed,
                            "context_index": context_index,
                            "t1": t1,
                            "t2": t1 + delta,
                            "key_number": key_number,
                            "private_key": key_hex,
                            "hash160": digest.hex(),
                            "compressed": compressed,
                        }
                        hits.append(hit)
                        if on_hit:
                            on_hit(hit)
    return hits
