#!/usr/bin/env python3
"""Canary validation for the Randstorm campaign pipeline.

Builds wallets with the *Python reference* chain (independent of the C
generator and the GPU kernel) across several hypothesis classes, merges their
HASH160s into the real victims index, and returns the index bytes plus the
expected (hash160, hypothesis) records. A campaign run over the canary axes
must find every record — that is the false-negative check for the whole
pipeline (C chain -> GPU kernel -> index matching -> campaign state).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "chain"))
sys.path.insert(0, os.path.join(HERE, "gpu"))

from ec import hash160, pubkey_bytes  # noqa: E402
from reference import _Page  # noqa: E402

SEED_START = 0x00C0FFEE
T1 = 0x4E0B2C40  # arbitrary page-load millisecond value used by the canary axes

# (label, seed_offset, context, math_offset, dt2, key_index, rc4_offset, hash_type)
CANARIES = [
    ("compressed@ctx0", 123, 0, 0, 0, 1, 0, 0),
    ("uncompressed@ctx1", 456, 1, 0, 500, 0, 0, 1),
    ("p2sh@key2", 789, 0, 0, 0, 2, 33, 2),
    ("compressed@offset33", 1010, 1, 0, 500, 1, 33, 0),
]


def canary_config():
    """Campaign axes that cover every canary hypothesis."""
    return {
        "id": "canary",
        "note": "false-negative validation across contexts, dt2, key indices, rc4 offsets, address types",
        "seed_start": SEED_START,
        "seed_count": 2048,
        "context_indexes": [0, 1],
        "math_offsets": [0],
        "t1_values": [T1],
        "dt2_values": [0, 500],
        "key_count": 3,
        "rc4_offsets": [0, 33],
        "shard_keys": 500_000,
    }


def hash_for_type(private_key, hash_type):
    compressed = pubkey_bytes(private_key, compressed=True)
    if hash_type == 0:
        return hash160(compressed)
    if hash_type == 1:
        return hash160(pubkey_bytes(private_key, compressed=False))
    if hash_type == 2:
        return hash160(b"\x00\x14" + hash160(compressed))
    raise ValueError(hash_type)


def build_canary_index(victims_bytes):
    """Merge canary records into the sorted victims index.

    Returns (index_bytes, expected) where expected maps hash160 hex -> hypothesis.
    """
    records = {victims_bytes[i:i + 20] for i in range(0, len(victims_bytes), 20)}
    expected = {}
    for label, offset, context, math_offset, dt2, key_index, rc4_offset, hash_type in CANARIES:
        seed = SEED_START + offset
        page = _Page(seed, context, math_offset, T1, T1 + dt2, rc4_offset)
        key_hex = None
        for _ in range(key_index + 1):
            key_hex = page.next_key_hex()
        key_int = int(key_hex, 16)
        digest = hash_for_type(key_int, hash_type)
        records.add(digest)
        expected[digest.hex()] = {
            "label": label,
            "seed": seed,
            "context": context,
            "math_offset": math_offset,
            "t1": T1,
            "dt2": dt2,
            "key_index": key_index,
            "rc4_offset": rc4_offset,
            "hash_type": hash_type,
            "private_key": key_hex,
        }
    index_bytes = b"".join(sorted(records))
    return index_bytes, expected


def verify_hits(scan_state, campaign_id, expected):
    """Check that every canary hash appears as a hit in the stored shard results."""
    found = {}
    for shard_id in sorted(scan_state.list_shard_results(campaign_id)):
        result = scan_state.read_json(f"campaigns/{campaign_id}/shards/{shard_id}.json")
        if not result:
            continue
        for hit in result.get("hits", []):
            found[hit["hash160"]] = hit
    missing = [label for digest, label in
               ((digest, info["label"]) for digest, info in expected.items())
               if digest not in found]
    print(f"canary verification: {len(expected) - len(missing)}/{len(expected)} found")
    for digest, info in sorted(expected.items(), key=lambda item: item[1]["label"]):
        status = "FOUND" if digest in found else "MISSING"
        print(f"  [{status}] {info['label']}: {digest}")
    if missing:
        raise SystemExit(f"canary validation failed: missing {missing}")
    return True
