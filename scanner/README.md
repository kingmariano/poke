# RNG chain reference implementation

Bit-exact models for the browser-side key generation flaw research line
(BitcoinJS / JSBN SecureRandom, 2011–2015), with offline known-answer tests.

| File | Purpose |
|---|---|
| `glibc_random.py` | glibc TYPE_3 `random()`/`srandom()` port (time^pid seeding path) |
| `v8_random.py` | V8 `random_base` (pre-2013 Math.random), MWC1616, 48-bit LCG |
| `jsbn.py` | JSBN SecureRandom pool, time XORs, RC4, `BigInteger(256, rng)` key extraction |
| `ec.py` | pure-Python secp256k1 + RIPEMD-160/HASH160 (verification only) |
| `scan_cpu.py` | reference scan loop over (libc seed, context index, time window, key index) |
| `state_recovery.py` | Z3 recovery of the `random_base` state from leaked Math.random outputs |
| `chain/` | C generator (libc-seed **and** recovered-state modes), Python reference, differential tests |
| `gpu/` | vendored secp256k1+HASH160+DB match kernel, GPU driver, GPU self-test |
| `canary.py` | false-negative canaries (seed mode and recovered-state mode) |
| `scan_state.py`, `run_campaign.py`, `modal_scan.py` | resumable campaigns on Modal with GitLab-backed state |

KAT vectors are generated from compiled C (`kat_glibc.c`, `kat_random_base.c`) and from
running the original JavaScript logic in Node (`kat_mwc1616.mjs`, `kat_jsbn.mjs`).

## Scan modes

**Seed mode (blind).** Enumerates libc seeds × page-load times × contexts × key
indices. Exact-hypothesis cost is 2^32 keys (~$2.6 on a T4); suitable only for
tightly pinned windows.

**State mode (leads).** When any Math.random-derived artifact from the target's
browser session is available (e.g. a Blockchain.info wallet GUID/IV generated in
the same page), `state_recovery.recover_state()` solves the 64-bit `random_base`
state with Z3 from as few as 2 raw outputs, and the 2^32 seed enumeration
disappears: only the time grid (T1 × T2) remains. A full day at millisecond
resolution × 3 key indices ≈ 7.8e8 candidates ≈ 40 minutes on one T4.

Validated on Modal (Tesla T4):
- seed-mode canary: contexts 0/1, dt2 0/500ms, key indices 0–2, RC4 offsets 0/33,
  compressed + uncompressed + P2SH-P2WPKH → **4/4 found**
- state-mode canary: exact state recovered from 4 outputs → **3/3 found** with
  only 606 candidates per hypothesis

## Running

    python scanner/ec.py                       # EC/HASH160 self-test
    python scanner/state_recovery.py           # Z3 recovery self-test
    python scanner/chain/test_chain.py         # C vs Python chain differential
    python scanner/test_scanner.py             # offline KAT suite

On Modal (from GitHub Actions, `modal-scan` workflow):

    modal run scanner/modal_scan.py::main --task selftest
    modal run scanner/modal_scan.py::main --task canary
    modal run scanner/modal_scan.py::main --task state_canary
    modal run scanner/modal_scan.py::main --task campaign --campaign <name> --max-shards N

Campaign state lives in the private GitLab repository (`state/campaigns/...`), so
runs resume across Modal accounts: re-execute and completed shards are skipped.

## Ethics

Publicly disclosed flaw, third-party wallets. Derive keys only for research and
disclosure; never move funds that are not yours.
