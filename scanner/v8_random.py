#!/usr/bin/env python3
"""V8 Math.random models used by the Randstorm scanner.

E1 (<= 2013-09-10): per-context Marsaglia MWC (18273/36969) seeded lazily with
two libc random() values; libc itself was seeded with `time_ms ^ (pid << 16)`.
E2 (~2013-09 .. 2016): JS MWC1616 (18030/36969) with four 16-bit seeds drawn
from V8's internal 48-bit LCG (seeded from /dev/urandom).
"""

from glibc_random import GlibcRandom

MASK32 = 0xFFFFFFFF


class RandomBaseE1:
    """V8 `random_base` (src/v8.cc, pre-2013-09): 2x32-bit state, uint32 output.

    state[0] = 18273 * (state[0] & 0xFFFF) + (state[0] >> 16)
    state[1] = 36969 * (state[1] & 0xFFFF) + (state[1] >> 16)
    return (state[0] << 14) + (state[1] & 0x3FFFF)
    """

    def __init__(self, s0, s1):
        self.s0 = s0 & MASK32
        self.s1 = s1 & MASK32

    def next_u32(self):
        self.s0 = (18273 * (self.s0 & 0xFFFF) + (self.s0 >> 16)) & MASK32
        self.s1 = (36969 * (self.s1 & 0xFFFF) + (self.s1 >> 16)) & MASK32
        return ((self.s0 << 14) + (self.s1 & 0x3FFFF)) & MASK32

    def pool_value(self):
        """The value JSBN's pool records: floor(65536 * Math.random())."""
        return self.next_u32() >> 16


def e1_seed_words(libc_seed, context_index=0, extra_calls=0):
    """Words consumed when a context's Math.random is first used.

    Each context seeds with two consecutive libc random() outputs; `context_index`
    counts whole 2-word groups already consumed by earlier contexts/uses.
    """
    generator = GlibcRandom(libc_seed)
    for _ in range(context_index * 2 + extra_calls):
        generator.next()
    return generator.next(), generator.next()


def e1_math_random_u32s(libc_seed, context_index=0):
    """Infinite iterator of raw 32-bit Math.random values for the E1 model."""
    generator = RandomBaseE1(*e1_seed_words(libc_seed, context_index))
    while True:
        yield generator.next_u32()


class Lcg48:
    """V8 base::RandomNumberGenerator: 48-bit LCG (drand48/java constants)."""

    MULTIPLIER = 25214903917
    ADDEND = 11
    MASK = (1 << 48) - 1

    def __init__(self, seed):
        self.seed = (seed ^ self.MULTIPLIER) & self.MASK

    def next(self, bits):
        self.seed = (self.seed * self.MULTIPLIER + self.ADDEND) & self.MASK
        return self.seed >> (48 - bits)

    def next_bytes(self, count):
        return bytes(self.next(8) for _ in range(count))


class Mwc1616:
    """V8 Math.random (src/js/math.js, 2015 era).

    r0 = imul(18030, a) + b; r1 = imul(36969, c) + d (int32)
    a = r0 & 0xFFFF; b = r0 >>> 16; c = r1 & 0xFFFF; d = r1 >>> 16
    r  = (r0 ^ r1) >>> 0
    """

    def __init__(self, a, b, c, d):
        self.a = a & 0xFFFF
        self.b = b & 0xFFFF
        self.c = c & 0xFFFF
        self.d = d & 0xFFFF

    @classmethod
    def from_lcg_seed(cls, seed48):
        """Mirror Runtime_InitializeRNG + Math.js %InitializeRNG."""
        generator = Lcg48(seed48)
        while True:
            raw = generator.next_bytes(8)
            values = [raw[index] | (raw[index + 1] << 8) for index in range(0, 8, 2)]
            if all(values):  # the do/while rejects any zero seed
                return cls(*values)

    def next_u32(self):
        r0 = (18030 * self.a + self.b) & MASK32
        r1 = (36969 * self.c + self.d) & MASK32
        self.a = r0 & 0xFFFF
        self.b = (r0 >> 16) & 0xFFFF
        self.c = r1 & 0xFFFF
        self.d = (r1 >> 16) & 0xFFFF
        return (r0 ^ r1) & MASK32

    def pool_value(self):
        """floor(65536 * Math.random()) for the JSBN pool.

        Verified against the actual ConstructDouble-based JS: equals
        (r & 0xFFFFF) >> 4, i.e. the top 16 bits of the 52-bit fraction.
        """
        return (self.next_u32() & 0xFFFFF) >> 4
