#!/usr/bin/env python3
"""Offline known-answer tests for the Randstorm scanner chain.

Validates: glibc random() (vs compiled C vectors), V8 random_base (vs compiled C
vectors), MWC1616 (vs the actual V8 math.js logic in Node), the JSBN pool/RC4/key
chain (vs the actual rng.js/prng4.js logic in Node), and an end-to-end synthetic
scan (create a target from known parameters, then find it again).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ec
from glibc_random import check_against_kats
from jsbn import JsbnSecureRandom, derive_key_hex
from scan_cpu import e1_scan
from v8_random import Mwc1616, RandomBaseE1, e1_math_random_u32s

HERE = os.path.dirname(os.path.abspath(__file__))


def nr_lcg(seed):
    state = seed & 0xFFFFFFFF
    while True:
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        yield state


def test_glibc():
    check_against_kats(os.path.join(HERE, "kats_glibc.txt"))


def test_random_base():
    with open(os.path.join(HERE, "kat_v8_randombase.txt")) as handle:
        for line in handle:
            parts = line.split()
            if not parts or parts[0] != "state":
                continue
            s0, s1 = int(parts[1]), int(parts[2])
            expected = [int(value) for value in parts[3:]]
            generator = RandomBaseE1(s0, s1)
            actual = [generator.next_u32() for _ in expected]
            assert actual == expected, (s0, s1, actual, expected)


def test_mwc1616():
    vectors = json.load(open(os.path.join(HERE, "kat_mwc1616.json")))
    for vector in vectors:
        generator = Mwc1616(*vector["seed"])
        for expected_r, _ in vector["rows"]:
            assert generator.next_u32() == expected_r, (vector["seed"], expected_r)
        generator = Mwc1616(*vector["seed"])
        for _, expected_pick in vector["rows"]:
            assert generator.pool_value() == expected_pick, (vector["seed"], expected_pick)


def test_jsbn():
    vectors = json.load(open(os.path.join(HERE, "kat_jsbn.json")))
    for vector in vectors:
        generator = JsbnSecureRandom(nr_lcg(vector["seed"]))
        generator.seed_time(vector["t1"])
        generator.second_time = vector["t2"]
        stream = generator.stream(64)
        assert bytes(generator.pool).hex() == vector["pool"], vector
        assert stream.hex() == vector["stream"], vector
        assert derive_key_hex(stream) == vector["key"], vector


def test_end_to_end():
    ec.self_test()
    seed, context_index = 123456789, 1
    t1, delta = 1310691661000, 4000
    generator = JsbnSecureRandom(e1_math_random_u32s(seed, context_index))
    generator.seed_time(t1)
    generator.second_time = t1 + delta
    key0 = derive_key_hex(generator.stream(33))
    key1 = derive_key_hex(generator.stream(33))
    targets = sorted({ec.hash160(ec.pubkey_bytes(int(key, 16), True)) for key in (key0, key1)})

    hits = e1_scan(
        range(seed - 3, seed + 4),
        context_index,
        [t1 - 2000, t1, t1 + 2000],
        [delta - 1000, delta, delta + 1000],
        key_numbers=2,
        index=targets,
    )
    found = {(hit["private_key"], hit["libc_seed"], hit["t1"], hit["t2"]) for hit in hits}
    expected = {(key0, seed, t1, t1 + delta), (key1, seed, t1, t1 + delta)}
    assert expected.issubset(found), (sorted(found), expected)
    wrong_seed = [hit for hit in hits if hit["libc_seed"] != seed]
    assert not wrong_seed, wrong_seed


if __name__ == "__main__":
    test_glibc()
    print("glibc KAT ok")
    test_random_base()
    print("random_base KAT ok")
    test_mwc1616()
    print("mwc1616 KAT ok")
    test_jsbn()
    print("jsbn KAT ok")
    test_end_to_end()
    print("end-to-end synthetic scan ok")
    print("ALL SCANNER TESTS PASSED")
