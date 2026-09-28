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
| `test_scanner.py` | offline KAT suite + synthetic end-to-end scan |

KAT vectors are generated from compiled C (`kat_glibc.c`, `kat_random_base.c`) and from
running the original JavaScript logic in Node (`kat_mwc1616.mjs`, `kat_jsbn.mjs`).

Run everything:

    python scanner/test_scanner.py

All modules are standard-library only.
