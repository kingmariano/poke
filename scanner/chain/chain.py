#!/usr/bin/env python3
"""ctypes wrapper for randstorm_chain.c (E1 Randstorm candidate generator)."""

import ctypes
import os
import subprocess
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "randstorm_chain.c")
LIBRARY = os.path.join(HERE, "librandstorm_chain.so")


class RsParams(ctypes.Structure):
    _fields_ = [
        ("seed_start", ctypes.c_uint32),
        ("context_index", ctypes.c_uint32),
        ("math_offset", ctypes.c_uint32),
        ("t1_count", ctypes.c_uint32),
        ("dt2_count", ctypes.c_uint32),
        ("key_count", ctypes.c_uint32),
        ("rc4_offset", ctypes.c_uint32),
        ("seed_count", ctypes.c_uint64),
    ]


_lock = threading.Lock()
_library = None


def build(force=False):
    if not force and os.path.exists(LIBRARY) and os.path.getmtime(LIBRARY) > os.path.getmtime(SOURCE):
        return LIBRARY
    subprocess.run(["gcc", "-O3", "-shared", "-fPIC", "-o", LIBRARY, SOURCE], check=True)
    return LIBRARY


def load():
    global _library
    with _lock:
        if _library is None:
            build()
            lib = ctypes.CDLL(LIBRARY)
            lib.rs_generate.restype = ctypes.c_uint64
            lib.rs_generate.argtypes = [
                ctypes.POINTER(RsParams),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_uint64,
                ctypes.c_uint64,
                ctypes.POINTER(ctypes.c_uint8),
            ]
            lib.rs_flat_total.restype = ctypes.c_uint64
            lib.rs_flat_total.argtypes = [ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
            _library = lib
    return _library


def flat_total(seed_count, t1_count, dt2_count, key_count):
    return load().rs_flat_total(seed_count, t1_count, dt2_count, key_count)


def generate(params, t1_array, dt2_array, start, count):
    """params: dict with seed_start, context_index, math_offset, key_count,
    rc4_offset, seed_count. Returns (bytes, written)."""
    lib = load()
    p = RsParams(
        seed_start=params["seed_start"] & 0xFFFFFFFF,
        context_index=params["context_index"],
        math_offset=params.get("math_offset", 0),
        t1_count=len(t1_array),
        dt2_count=len(dt2_array),
        key_count=params["key_count"],
        rc4_offset=params.get("rc4_offset", 0),
        seed_count=params["seed_count"],
    )
    t1 = (ctypes.c_uint32 * len(t1_array))(*t1_array)
    dt2 = (ctypes.c_uint32 * len(dt2_array))(*dt2_array)
    capacity = max(1, count) * 32
    buffer = (ctypes.c_uint8 * capacity)()
    written = lib.rs_generate(ctypes.byref(p), t1, dt2, start, count, buffer)
    return bytes(buffer[: written * 32]), written


def limbs_from_key(key_bytes):
    """32-byte big-endian key -> list of 8 little-endian uint32 limbs."""
    value = int.from_bytes(key_bytes, "big")
    return [(value >> (32 * i)) & 0xFFFFFFFF for i in range(8)]


if __name__ == "__main__":
    build()
    print("chain library built:", LIBRARY)
