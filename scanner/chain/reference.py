#!/usr/bin/env python3
"""Pure-Python reference for the C chain's flat enumeration (differential tests)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsbn import JsbnSecureRandom, derive_key_hex  # noqa: E402
from v8_random import RandomBaseE1, e1_seed_words  # noqa: E402


class _Page:
    """One (seed, context_index, math_offset, t1, t2, rc4_offset) hypothesis."""

    def __init__(self, seed, context_index, math_offset, t1, t2, rc4_offset):
        words = e1_seed_words(seed, context_index)
        generator = RandomBaseE1(words[0], words[1])
        for _ in range(math_offset):
            generator.next_u32()
        draws = iter([generator.next_u32() for _ in range(128)])
        self.sr = JsbnSecureRandom(draws)
        self.sr.seed_time(t1)
        self.sr.second_time = t2
        self.sr.stream(rc4_offset)  # triggers pool/RC4 init and skips

    def next_key_hex(self):
        return derive_key_hex(self.sr.stream(33))


def generate_reference(params, t1_array, dt2_array, start, count):
    """Mirror rs_generate: flat = ((seed*t1 + t1i)*dt2 + dt2i)*keys + keyi."""
    key_count = params["key_count"]
    dt2_count = len(dt2_array)
    t1_count = len(t1_array)
    out = []
    page = None
    page_key = None
    position = 0
    for flat in range(start, start + count):
        r = flat
        key_idx = r % key_count
        r //= key_count
        dt2_idx = r % dt2_count
        r //= dt2_count
        t1_idx = r % t1_count
        r //= t1_count
        seed_idx = r
        if seed_idx >= params["seed_count"]:
            break
        seed = (params["seed_start"] + seed_idx) & 0xFFFFFFFF
        t1 = t1_array[t1_idx]
        t2 = (t1 + dt2_array[dt2_idx]) & 0xFFFFFFFF
        current = (seed, t1_idx, dt2_idx)
        if current != page_key:
            page = _Page(seed, params["context_index"], params.get("math_offset", 0),
                         t1, t2, params.get("rc4_offset", 0))
            for _ in range(key_idx):
                page.next_key_hex()
            page_key = current
            position = key_idx
        # successive calls advance the stream naturally
        out.append(page.next_key_hex())
        position += 1
    return out


if __name__ == "__main__":
    print("reference module ok")
