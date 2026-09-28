#!/usr/bin/env python3
"""GPU hunt driver.

Wraps the vendored kernel from tongriyaotxt/gpu-keyhunt (MIT License,
https://github.com/tongriyaotxt/gpu-keyhunt) which performs, per key:
    k*G (64-window 4-bit fixed-base table, Montgomery batch inversion)
    -> HASH160 of the compressed pubkey   (P2PKH compressed / P2WPKH)
    -> HASH160 of the uncompressed pubkey (P2PKH uncompressed)
    -> HASH160 of the P2SH-P2WPKH redeem script (0x0014||h_c)
    -> binary search in a sorted 20-byte HASH160 database in VRAM.

Our adapter feeds candidate keys produced by the chain generator
(scanner/chain) instead of random/stride keys.
"""

import os

import numpy as np

from cuda_src import CUDA_SRC  # vendored CUDA source

HERE = os.path.dirname(os.path.abspath(__file__))
BLOCK_SIZE = 128
MAX_HITS_PER_LAUNCH = 4096
_MODULE_CACHE = {}


def _sm_arch():
    import cupy as cp

    properties = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
    return f"sm_{properties['major']}{properties['minor']}"


def build_g_table_np():
    table = np.load(os.path.join(HERE, "g_table.npy"))
    assert table.shape == (64, 16, 16) and table.dtype == np.uint32
    return table


def compile_cuda(stride=1):
    """Compile the hunt kernel for the local device (cached per stride)."""
    if stride in _MODULE_CACHE:
        return _MODULE_CACHE[stride]
    import cupy as cp

    source = CUDA_SRC.replace("// @STRIDE_DEFINE@", f"#define STRIDE {stride}")
    source = source.encode("ascii", "ignore").decode("ascii")  # NVRTC writes sources as ASCII
    # CuPy injects its own --gpu-architecture for the active device, so we must
    # not pass -arch ourselves; the PTX fallback is for older toolchains.
    errors = []
    for description, ptx in (("cupy default arch", False), ("PTX fallback", True)):
        try:
            if ptx:
                import cupy.cuda.compiler as _cp_compiler

                _cp_compiler._use_ptx = True
            module = cp.RawModule(code=source, options=())
            stack = stride * 128 + 64 * 1024
            if cp.cuda.runtime.deviceGetLimit(0) < stack:
                cp.cuda.runtime.deviceSetLimit(0, stack)
            _MODULE_CACHE[stride] = module
            return module
        except Exception as error:  # noqa: BLE001 - tried in order
            errors.append(f"{description}: {type(error).__name__}: {str(error)[:200]}")
    raise RuntimeError("CUDA compile failed:\n  " + "\n  ".join(errors))


def limbs_from_bytes(keys_bytes):
    """Chain output (32-byte big-endian keys) -> (n, 8) uint32 little-endian limbs."""
    raw = np.frombuffer(keys_bytes, dtype=np.uint8)
    if raw.size % 32:
        raise ValueError("key buffer must be a multiple of 32 bytes")
    big_endian = raw.reshape(-1, 32)
    little_endian = np.ascontiguousarray(big_endian[:, ::-1])
    return little_endian.view(np.uint32).reshape(-1, 8)


def int_to_limbs(value):
    return [(value >> (32 * i)) & 0xFFFFFFFF for i in range(8)]


def limbs_to_int(limbs):
    return sum(int(v) << (32 * i) for i, v in enumerate(limbs))


def load_db_gpu(path):
    """Load a sorted concatenation of 20-byte HASH160 records into VRAM."""
    import cupy as cp

    db_np = np.fromfile(path, dtype=np.uint8)
    n20 = len(db_np) // 20
    return cp.asarray(db_np), n20


def hunt_batch(keys_np, gtab_gpu, db_gpu, n20, module, max_hits=MAX_HITS_PER_LAUNCH):
    """Run the kernel over (n,8) uint32 key limbs; returns [(key_int, hit_type), ...]."""
    import cupy as cp

    n = int(keys_np.shape[0])
    if n == 0:
        return []
    hit_count = cp.zeros(1, dtype=cp.uint32)
    hits = cp.zeros(max_hits * 9, dtype=cp.uint32)
    kernel = module.get_function("hunt")
    grid = (n + BLOCK_SIZE - 1) // BLOCK_SIZE
    kernel((grid,), (BLOCK_SIZE,), (
        cp.asarray(keys_np),
        gtab_gpu,
        db_gpu,
        np.uint64(n20),
        hit_count,
        hits,
        np.uint32(max_hits),
        np.uint32(n),
    ))
    count = int(hit_count.get()[0])
    if count == 0:
        return []
    rows = hits.get().reshape(-1, 9)[:count]
    return [(limbs_to_int(rows[i, :8]), int(rows[i, 8])) for i in range(count)]
