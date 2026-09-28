#!/usr/bin/env python3
"""Minimal pure-Python secp256k1 and HASH160 (reference implementation only).

The GPU stage replaces these with batch-optimized kernels; this module exists
so the whole Randstorm chain can be verified end-to-end with stdlib only.
"""

import hashlib

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G = (GX, GY)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        slope = (3 * p[0] * p[0]) * pow(2 * p[1], P - 2, P) % P
    else:
        slope = (q[1] - p[1]) * pow(q[0] - p[0], P - 2, P) % P
    x = (slope * slope - p[0] - q[0]) % P
    y = (slope * (p[0] - x) - p[1]) % P
    return x, y


def _mul(k, point=G):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def pubkey_bytes(private_key, compressed=True):
    x, y = _mul(private_key % N)
    if compressed:
        return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


_ROL = lambda x, n: ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF
_R1 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
       7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
       3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
       1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
       4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13]
_R2 = [5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
       6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
       15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
       8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
       12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11]
_S1 = [11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
       7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
       11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
       11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
       9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6]
_S2 = [8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
       9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
       9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
       15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
       8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11]
_K1 = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
_K2 = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]


def _f(j, x, y, z):
    if j < 16:
        return x ^ y ^ z
    if j < 32:
        return (x & y) | (~x & z)
    if j < 48:
        return (x | ~y) ^ z
    if j < 64:
        return (x & z) | (y & ~z)
    return x ^ (y | ~z)


def _ripemd160_pure(message):
    h0, h1, h2, h3, h4 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0
    padded = bytearray(message)
    bit_length = (8 * len(message)) & 0xFFFFFFFFFFFFFFFF
    padded.append(0x80)
    while len(padded) % 64 != 56:
        padded.append(0)
    padded += bit_length.to_bytes(8, "little")
    for offset in range(0, len(padded), 64):
        block = padded[offset:offset + 64]
        x = [int.from_bytes(block[i * 4:i * 4 + 4], "little") for i in range(16)]
        al, bl, cl, dl, el = h0, h1, h2, h3, h4
        ar, br, cr, dr, er = h0, h1, h2, h3, h4
        for j in range(80):
            t = (_ROL((al + _f(j, bl, cl, dl) + x[_R1[j]] + _K1[j // 16]) & 0xFFFFFFFF, _S1[j]) + el) & 0xFFFFFFFF
            al, el, dl, cl, bl = el, dl, _ROL(cl, 10), bl, t
            t = (_ROL((ar + _f(79 - j, br, cr, dr) + x[_R2[j]] + _K2[j // 16]) & 0xFFFFFFFF, _S2[j]) + er) & 0xFFFFFFFF
            ar, er, dr, cr, br = er, dr, _ROL(cr, 10), br, t
        t = (h1 + cl + dr) & 0xFFFFFFFF
        h1 = (h2 + dl + er) & 0xFFFFFFFF
        h2 = (h3 + el + ar) & 0xFFFFFFFF
        h3 = (h4 + al + br) & 0xFFFFFFFF
        h4 = (h0 + bl + cr) & 0xFFFFFFFF
        h0 = t
    return b"".join(value.to_bytes(4, "little") for value in (h0, h1, h2, h3, h4))


def ripemd160(message):
    try:
        digest = hashlib.new("ripemd160")
        digest.update(message)
        return digest.digest()
    except Exception:
        return _ripemd160_pure(message)


def hash160(data):
    return ripemd160(hashlib.sha256(data).digest())


def self_test():
    vectors = {
        b"": "9c1185a5c5e9fc54612808977ee8f548b2258d31",
        b"abc": "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc",
        b"message digest": "5d0689ef49d2fae572b881b123a85ffa21595f36",
    }
    for message, expected in vectors.items():
        assert _ripemd160_pure(message).hex() == expected, message
    compressed = pubkey_bytes(1, True)
    assert compressed.hex() == "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
    assert hash160(compressed).hex() == "751e76e8199196d454941c45d1b3a323f1433bd6"
    return True


if __name__ == "__main__":
    self_test()
    print("ec self-test ok")
