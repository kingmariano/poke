#!/usr/bin/env python3
"""Bit-exact reproduction of glibc's TYPE_3 random()/srandom().

This is the libc PRNG that V8 seeded with `TimeCurrentMillis() ^ (pid << 16)`
(Linux/macOS) or `TimeCurrentMillis()` (Windows) before the 2013-09-10 V8
commit, and consumed to seed Math.random's per-context state.

Ported from glibc stdlib/random_r.c (TYPE_3: x**31 + x**3 + 1, degree 31,
separation 3, 310 warm-up outputs).
"""

MASK32 = 0xFFFFFFFF
DEG = 31
SEP = 3


def _trunc_div(a, b):
    return -((-a) // b) if a < 0 else a // b


class GlibcRandom:
    def __init__(self, seed):
        if seed == 0:
            seed = 1  # glibc: srandom(0) behaves as srandom(1)
        seed &= MASK32
        state = [0] * DEG
        state[0] = seed
        word = seed if seed < 0x80000000 else seed - 0x100000000
        for index in range(1, DEG):
            hi = _trunc_div(word, 127773)
            lo = word - hi * 127773
            word = 16807 * lo - 2836 * hi
            if word < 0:
                word += 2147483647
            state[index] = word & MASK32
        self.state = state
        self.fptr = SEP
        self.rptr = 0
        for _ in range(DEG * 10):
            self.next()

    def next(self):
        value = (self.state[self.fptr] + self.state[self.rptr]) & MASK32
        self.state[self.fptr] = value
        result = value >> 1
        self.fptr += 1
        if self.fptr >= DEG:
            self.fptr = 0
            self.rptr += 1
        else:
            self.rptr += 1
            if self.rptr >= DEG:
                self.rptr = 0
        return result


def check_against_kats(path):
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if not parts or parts[0] != "random":
                continue
            seed = int(parts[1])
            expected = [int(value) for value in parts[2:]]
            generator = GlibcRandom(seed)
            actual = [generator.next() for _ in expected]
            assert actual == expected, (seed, actual, expected)


if __name__ == "__main__":
    import os
    import sys

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kats_glibc.txt")
    check_against_kats(path)
    print("glibc_random self-test ok")
    sys.exit(0)
