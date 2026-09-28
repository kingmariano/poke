// Reproduce JSBN SecureRandom (rng.js + prng4.js) and BitcoinJS key extraction
// with a deterministic stub Math.random. Emits pool, RC4 stream prefix, key.
const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const POOL = 256;

function makeMathRandom(seed) {          // Numerical Recipes LCG (stub)
  let s = BigInt(seed >>> 0);
  return function () {
    s = (s * 1664525n + 1013904223n) & 0xFFFFFFFFn;
    return Number(s) / 4294967296;
  };
}

function chain(seed, t1, t2) {
  const rand = makeMathRandom(seed);
  const pool = new Array(POOL);
  let pptr = 0;
  while (pptr < POOL) {
    const t = Math.floor(65536 * rand());
    pool[pptr++] = t >>> 8;
    pool[pptr++] = t & 255;
  }
  pptr = 0;
  const seedTime = (x) => {
    pool[pptr++] ^= x & 255;
    pool[pptr++] ^= (x >> 8) & 255;
    pool[pptr++] ^= (x >> 16) & 255;
    pool[pptr++] ^= (x >> 24) & 255;
    if (pptr >= POOL) pptr -= POOL;
  };
  seedTime(t1);   // page load
  seedTime(t2);   // first byte request
  // RC4 (prng4.js)
  const S = Array.from({length: 256}, (_, i) => i);
  let j = 0;
  for (let i = 0; i < 256; i++) {
    j = (j + S[i] + pool[i % pool.length]) & 255;
    const t = S[i]; S[i] = S[j]; S[j] = t;
  }
  let i2 = 0; j = 0;
  const stream = [];
  const nextByte = () => {
    i2 = (i2 + 1) & 255;
    j = (j + S[i2]) & 255;
    const t = S[i2]; S[i2] = S[j]; S[j] = t;
    return S[(t + S[i2]) & 255];
  };
  for (let k = 0; k < 64; k++) stream.push(nextByte());
  // BigInteger(256, rng): 33 bytes consumed, x[0]=0, value = bytes 1..32 BE
  const keyBytes = [0, ...stream.slice(1, 33)];
  let X = 0n;
  for (const b of keyBytes) X = (X << 8n) | BigInt(b);
  const key = (X % (N - 1n)) + 1n;
  const keyHex = key.toString(16).padStart(64, '0');
  const hex = (arr) => arr.map((b) => b.toString(16).padStart(2, '0')).join('');
  return { pool: hex(pool), stream: hex(stream), key: keyHex };
}

const vectors = [
  { seed: 1, t1: 1310691661000, t2: 1310691665000 },
  { seed: 42, t1: 1393635661000, t2: 1393635700000 },
  { seed: 0xDEADBEEF, t1: 1330000000000, t2: 1330000060000 },
  { seed: 123456789, t1: 1350000000123, t2: 1350000000999 },
];
const out = vectors.map((v) => ({ ...v, ...chain(v.seed, v.t1, v.t2) }));
console.log(JSON.stringify(out, null, 1));
