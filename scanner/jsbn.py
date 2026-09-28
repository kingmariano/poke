#!/usr/bin/env python3
"""Bit-exact port of JSBN SecureRandom (rng.js/prng4.js) and BitcoinJS key extraction.

Chain: 256-byte pool from 128 Math.random() draws -> two Date.now() XORs
(bytes 0..3 at page load, 4..7 at first byte request) -> RC4 -> 33 keystream
bytes consumed by `new BigInteger(256, rng)` (x[0] zeroed) -> private key =
(x mod (n-1)) + 1.
"""

POOL_SIZE = 256
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def build_pool(math_random_u32):
    """Build the JSBN pool from an iterator of raw 32-bit Math.random values."""
    pool = bytearray(POOL_SIZE)
    pointer = 0
    while pointer < POOL_SIZE:
        value = next(math_random_u32)
        t = value >> 16  # floor(65536 * (value / 2**32))
        pool[pointer] = t >> 8
        pointer += 1
        pool[pointer] = t & 0xFF
        pointer += 1
    return pool


class JsbnSecureRandom:
    """Mirror of jsbn rng.js + prng4.js usage in BitcoinJS 0.1.x."""

    def __init__(self, math_random_u32):
        self.pool = build_pool(math_random_u32)
        self.pointer = 0
        self.state = None
        self.i = 0
        self.j = 0

    def seed_time(self, timestamp_ms):
        value = timestamp_ms & 0xFFFFFFFF
        for shift in (0, 8, 16, 24):
            self.pool[self.pointer] ^= (value >> shift) & 0xFF
            self.pointer += 1
            if self.pointer >= POOL_SIZE:
                self.pointer -= POOL_SIZE

    def _rc4_init(self):
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + self.pool[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self.s = s
        self.i = 0
        self.j = 0

    def next_byte(self):
        if self.state is None:
            self.state = True
            self.seed_time(self.second_time)
            self._rc4_init()
        self.i = (self.i + 1) & 0xFF
        self.j = (self.j + self.s[self.i]) & 0xFF
        t = self.s[self.i]
        self.s[self.i] = self.s[self.j]
        self.s[self.j] = t
        return self.s[(t + self.s[self.i]) & 0xFF]

    def stream(self, count):
        return bytes(self.next_byte() for _ in range(count))


def keystream_and_pool(seed_words_iter, t1, t2):
    """Convenience: pool + 64 stream bytes for KAT comparisons."""
    generator = JsbnSecureRandom(seed_words_iter)
    generator.seed_time(t1)
    generator.second_time = t2
    stream = generator.stream(64)
    return generator.pool, stream


def derive_key_hex(stream):
    """BitcoinJS `new BigInteger(256, rng).mod(n-1).add(1)` from 64+ stream bytes."""
    consumed = stream[:33]
    x = int.from_bytes(consumed[1:33], "big")  # x[0] is zeroed by jsbn
    key = (x % (SECP256K1_N - 1)) + 1
    return key.to_bytes(32, "big").hex()
