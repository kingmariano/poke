"""CUDA source for the secp256k1 + HASH160 + funded-DB match kernel.

Vendored from tongriyaotxt/gpu-keyhunt (MIT License),
https://github.com/tongriyaotxt/gpu-keyhunt/blob/main/gpu_hunt.py
"""

CUDA_SRC = r"""
// @STRIDE_DEFINE@  // 主机编译时注入 #define STRIDE n（步进扫描长度，compile-time）

typedef unsigned int u32;
typedef unsigned long long u64;

// secp256k1 域素数 P = 2^256 - 2^32 - 977（小端 limb）
__device__ __forceinline__ u32 p_limb(int i) {
    return i == 0 ? 0xFFFFFC2Fu : (i == 1 ? 0xFFFFFFFEu : 0xFFFFFFFFu);
}

// P - 2 的小端 limb（费马小定理求逆指数）
__device__ __forceinline__ u32 pm2_limb(int i) {
    return i == 0 ? 0xFFFFFC2Du : (i == 1 ? 0xFFFFFFFEu : 0xFFFFFFFFu);
}

__device__ int fp_iszero(const u32 a[8]) {
    u32 acc = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) acc |= a[i];
    return acc == 0;
}

__device__ int fp_geq_p(const u32 a[8]) {
    #pragma unroll
    for (int i = 7; i >= 0; i--) {
        u32 p = p_limb(i);
        if (a[i] != p) return a[i] > p;
    }
    return 1;  // 相等也算 >=
}

__device__ void fp_sub_p(u32 r[8]) {  // 要求 r >= P
    u64 bor = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 t = (u64)r[i] - p_limb(i) - bor;
        r[i] = (u32)t;
        bor = (t >> 32) & 1ull;
    }
}

// r += C，其中 C = 2^32 + 977（因为 2^256 ≡ C (mod P)）。溢出 2^256 时再加一次 C。
__device__ void fp_add_c(u32 r[8]) {
    u64 c = 1;
    while (c) {
        u64 t = (u64)r[0] + 977u; r[0] = (u32)t; c = t >> 32;
        t = (u64)r[1] + 1u + c;   r[1] = (u32)t; c = t >> 32;
        #pragma unroll
        for (int i = 2; i < 8; i++) {
            if (!c) break;
            t = (u64)r[i] + c; r[i] = (u32)t; c = t >> 32;
        }
    }
}

__device__ void fp_add(u32 r[8], const u32 a[8], const u32 b[8]) {
    u64 c = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 t = (u64)a[i] + b[i] + c;
        r[i] = (u32)t; c = t >> 32;
    }
    if (c) fp_add_c(r);              // 2^256 ≡ C (mod P)
    if (fp_geq_p(r)) fp_sub_p(r);
}

__device__ void fp_sub(u32 r[8], const u32 a[8], const u32 b[8]) {
    u64 bor = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 t = (u64)a[i] - b[i] - bor;
        r[i] = (u32)t;
        bor = (t >> 32) & 1ull;
    }
    if (bor) {  // 借位：加回 P
        u64 c = 0;
        #pragma unroll
        for (int i = 0; i < 8; i++) {
            u64 t = (u64)r[i] + p_limb(i) + c;
            r[i] = (u32)t; c = t >> 32;
        }
    }
}

// 8x8 -> 16 limb 学校乘法（64 位累加器，逐行进位）
__device__ void fp_mul_raw(u32 t[16], const u32 a[8], const u32 b[8]) {
    #pragma unroll
    for (int i = 0; i < 16; i++) t[i] = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 carry = 0;
        #pragma unroll
        for (int j = 0; j < 8; j++) {
            u64 uv = (u64)a[i] * b[j] + t[i + j] + carry;
            t[i + j] = (u32)uv;
            carry = uv >> 32;
        }
        #pragma unroll
        for (int k = i + 8; k < 16 && carry; k++) {
            u64 uv = (u64)t[k] + carry;
            t[k] = (u32)uv;
            carry = uv >> 32;
        }
    }
}

// 伪梅森快速约减：2^256 ≡ C (mod P)，C = 2^32 + 977。
// 反复把高 limb 折叠（乘 C 加回低位），直到高 limb 清零，最后一次条件减 P。
// 收敛性：每轮 H -> H*C/2^256（缩小 2^223 倍），仅当低位贴近 2^256 时 H=1
// 会再持续一两轮（低位每轮减 P），8 轮上界足够。
__device__ void fp_reduce(u32 r[8], const u32 t[16]) {
    u32 v[10];
    #pragma unroll
    for (int i = 0; i < 8; i++) v[i] = t[i];
    v[8] = 0; v[9] = 0;
    {   // 第一轮：H = t[8..15]，v += H*977 + (H << 32)
        u64 c = 0;
        #pragma unroll
        for (int i = 0; i < 8; i++) {
            u64 a = (u64)v[i] + (u64)t[8 + i] * 977u + c;
            v[i] = (u32)a; c = a >> 32;
        }
        #pragma unroll
        for (int k = 8; k < 10 && c; k++) {
            u64 a = (u64)v[k] + c; v[k] = (u32)a; c = a >> 32;
        }
        c = 0;
        #pragma unroll
        for (int i = 0; i < 8; i++) {
            u64 a = (u64)v[i + 1] + t[8 + i] + c;
            v[i + 1] = (u32)a; c = a >> 32;
        }
        if (c) { v[9] = (u32)((u64)v[9] + c); }  // 总量 < 2^289，不会再溢出
    }
    #pragma unroll 1
    for (int it = 0; it < 8; it++) {  // 后续轮：H 只剩 v[8..9]
        u32 h0 = v[8], h1 = v[9];
        if ((h0 | h1) == 0) break;
        v[8] = 0; v[9] = 0;
        u64 a = (u64)v[0] + (u64)h0 * 977u; v[0] = (u32)a; u64 c = a >> 32;
        a = (u64)v[1] + (u64)h1 * 977u + h0 + c; v[1] = (u32)a; c = a >> 32;
        a = (u64)v[2] + h1 + c; v[2] = (u32)a; c = a >> 32;
        #pragma unroll
        for (int k = 3; k < 10 && c; k++) {
            a = (u64)v[k] + c; v[k] = (u32)a; c = a >> 32;
        }
    }
    if (fp_geq_p(v)) fp_sub_p(v);
    #pragma unroll
    for (int i = 0; i < 8; i++) r[i] = v[i];
}

__device__ void fp_mul(u32 r[8], const u32 a[8], const u32 b[8]) {
    u32 t[16];
    fp_mul_raw(t, a, b);
    fp_reduce(r, t);
}

__device__ void fp_sqr(u32 r[8], const u32 a[8]) { fp_mul(r, a, a); }

// 费马小定理：a^(P-2) mod P（通用位扫描，256 轮）
__device__ void fp_inv(u32 r[8], const u32 a[8]) {
    u32 res[8], base[8];
    res[0] = 1;
    #pragma unroll
    for (int i = 1; i < 8; i++) res[i] = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) base[i] = a[i];
    #pragma unroll 1
    for (int i = 0; i < 256; i++) {
        if ((pm2_limb(i >> 5) >> (i & 31)) & 1u) fp_mul(res, res, base);
        if (i < 255) fp_sqr(base, base);
    }
    #pragma unroll
    for (int i = 0; i < 8; i++) r[i] = res[i];
}

// 群阶 N 的小端 limb（k 溢出 mod N 用）
__device__ __forceinline__ u32 n_limb(int i) {
    switch (i) {
        case 0: return 0xD0364141u;
        case 1: return 0xBFD25E8Cu;
        case 2: return 0xAF48A03Bu;
        case 3: return 0xBAAEDCE6u;
        case 4: return 0xFFFFFFFEu;
        default: return 0xFFFFFFFFu;
    }
}

__device__ int fp_geq_n(const u32 a[8]) {
    #pragma unroll
    for (int i = 7; i >= 0; i--) {
        u32 n = n_limb(i);
        if (a[i] != n) return a[i] > n;
    }
    return 1;
}

__device__ void fp_sub_n(u32 r[8]) {  // 要求 r >= N
    u64 bor = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 t = (u64)r[i] - n_limb(i) - bor;
        r[i] = (u32)t;
        bor = (t >> 32) & 1ull;
    }
}

// ------------------------------------------------------------------
// Jacobian 坐标点运算（a=0），公式与 btc_key.py 的
// _jacobian_double / _jacobian_add_mixed / _jacobian_to_affine 一致
// ------------------------------------------------------------------
__device__ void jac_double(u32 X[8], u32 Y[8], u32 Z[8], int* inf) {
    if (*inf || fp_iszero(Y)) { *inf = 1; return; }
    u32 yy[8], s[8], m[8], t[8], e[8];
    fp_sqr(yy, Y);                  // yy = y^2
    fp_mul(t, X, yy);               // t = x*yy
    fp_add(s, t, t); fp_add(s, s, s);        // s = 4*x*yy
    fp_sqr(m, X); fp_add(t, m, m); fp_add(m, t, m);  // m = 3*x^2
    u32 x3[8], y3[8];
    fp_sqr(x3, m); fp_sub(x3, x3, s); fp_sub(x3, x3, s);  // x3 = m^2 - 2s
    fp_sub(t, s, x3); fp_mul(t, m, t);       // t = m*(s - x3)
    fp_sqr(e, yy);                            // e = yy^2
    fp_add(e, e, e); fp_add(e, e, e); fp_add(e, e, e);  // e = 8*yy^2
    fp_sub(y3, t, e);                         // y3 = m*(s-x3) - 8*yy^2
    fp_mul(Z, Y, Z); fp_add(Z, Z, Z);         // z3 = 2*y*z
    #pragma unroll
    for (int i = 0; i < 8; i++) { X[i] = x3[i]; Y[i] = y3[i]; }
}

__device__ void jac_add_mixed(u32 X[8], u32 Y[8], u32 Z[8], int* inf,
                              const u32 qx[8], const u32 qy[8]) {
    if (*inf) {
        #pragma unroll
        for (int i = 0; i < 8; i++) { X[i] = qx[i]; Y[i] = qy[i]; Z[i] = 0; }
        Z[0] = 1; *inf = 0;
        return;
    }
    u32 zz[8], u2[8], s2[8], h[8], rr[8], t[8];
    fp_sqr(zz, Z);                  // z1z1
    fp_mul(u2, qx, zz);             // u2 = x2*z1z1
    fp_mul(t, qy, Z); fp_mul(s2, t, zz);   // s2 = y2*z1*z1z1
    fp_sub(h, u2, X);
    fp_sub(rr, s2, Y);
    if (fp_iszero(h)) {
        if (fp_iszero(rr)) { jac_double(X, Y, Z, inf); return; }
        *inf = 1; return;  // 结果为无穷远点
    }
    u32 hh[8], hhh[8], x1hh[8], x3[8], y3[8];
    fp_sqr(hh, h);
    fp_mul(hhh, h, hh);
    fp_mul(x1hh, X, hh);
    // x3 = r^2 - hhh - 2*x1*hh
    fp_sqr(x3, rr); fp_sub(x3, x3, hhh);
    fp_sub(x3, x3, x1hh); fp_sub(x3, x3, x1hh);
    // y3 = r*(x1*hh - x3) - y1*hhh
    fp_sub(t, x1hh, x3); fp_mul(t, rr, t);
    fp_mul(y3, Y, hhh); fp_sub(y3, t, y3);
    // z3 = z1*h
    fp_mul(Z, Z, h);
    #pragma unroll
    for (int i = 0; i < 8; i++) { X[i] = x3[i]; Y[i] = y3[i]; }
}

// k*G（Jacobian，不转仿射）：4-bit 窗口 x 64 窗，表在全局内存
//（64*16 个仿射点，每点 x||y 各 8 limb）。调用方保证 k != 0。
__device__ void jac_mul_g(const u32 k[8], const u32* gtab,
                          u32 X[8], u32 Y[8], u32 Z[8], int* inf) {
    *inf = 1;
    #pragma unroll 1
    for (int w = 0; w < 64; w++) {
        u32 digit = (k[w >> 3] >> ((w & 7) * 4)) & 0xFu;
        if (digit) {
            const u32* q = gtab + (size_t)(w * 16 + digit) * 16;
            jac_add_mixed(X, Y, Z, inf, q, q + 8);
        }
    }
}

// k*G 并转仿射（自检用；正式 kernel 走 Montgomery 批量求逆）
__device__ void mul_g(const u32 k[8], const u32* gtab, u32 ax[8], u32 ay[8]) {
    u32 X[8], Y[8], Z[8];
    int inf;
    jac_mul_g(k, gtab, X, Y, Z, &inf);
    u32 zi[8], zi2[8];
    fp_inv(zi, Z);
    fp_sqr(zi2, zi);
    fp_mul(ax, X, zi2);
    fp_mul(ay, Y, zi2);
    fp_mul(ay, ay, zi);
}

// ------------------------------------------------------------------
// SHA256（支持 <= 119 字节的小消息，1-2 个块）
// ------------------------------------------------------------------
__device__ __forceinline__ u32 rotr32(u32 x, int n) { return (x >> n) | (x << (32 - n)); }

__device__ void sha256_compress(u32 st[8], const unsigned char* blk) {
    const u32 K[64] = {
        0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
        0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
        0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
        0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
        0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
        0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
        0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
        0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
    };
    u32 w[64];
    #pragma unroll
    for (int i = 0; i < 16; i++)
        w[i] = ((u32)blk[4*i] << 24) | ((u32)blk[4*i+1] << 16) |
               ((u32)blk[4*i+2] << 8) | (u32)blk[4*i+3];
    #pragma unroll
    for (int i = 16; i < 64; i++) {
        u32 s0 = rotr32(w[i-15], 7) ^ rotr32(w[i-15], 18) ^ (w[i-15] >> 3);
        u32 s1 = rotr32(w[i-2], 17) ^ rotr32(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    u32 a = st[0], b = st[1], c = st[2], d = st[3];
    u32 e = st[4], f = st[5], g = st[6], h = st[7];
    #pragma unroll
    for (int i = 0; i < 64; i++) {
        u32 S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        u32 ch = (e & f) ^ (~e & g);
        u32 t1 = h + S1 + ch + K[i] + w[i];
        u32 S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        u32 maj = (a & b) ^ (a & c) ^ (b & c);
        u32 t2 = S0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    st[0] += a; st[1] += b; st[2] += c; st[3] += d;
    st[4] += e; st[5] += f; st[6] += g; st[7] += h;
}

__device__ void sha256_small(const unsigned char* msg, int len, unsigned char out[32]) {
    unsigned char buf[128];
    #pragma unroll 1
    for (int i = 0; i < len; i++) buf[i] = msg[i];
    buf[len] = 0x80;
    int blocks = (len <= 55) ? 1 : 2;
    int end = blocks * 64;
    #pragma unroll 1
    for (int i = len + 1; i < end; i++) buf[i] = 0;
    u64 bitlen = (u64)len * 8u;
    #pragma unroll
    for (int i = 0; i < 8; i++) buf[end - 1 - i] = (unsigned char)(bitlen >> (8 * i));
    u32 st[8] = {0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
                 0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u};
    sha256_compress(st, buf);
    if (blocks == 2) sha256_compress(st, buf + 64);
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        out[4*i]   = (unsigned char)(st[i] >> 24);
        out[4*i+1] = (unsigned char)(st[i] >> 16);
        out[4*i+2] = (unsigned char)(st[i] >> 8);
        out[4*i+3] = (unsigned char)(st[i]);
    }
}

// ------------------------------------------------------------------
// RIPEMD160（输入固定 32 字节 = SHA256 输出，单块）
// ------------------------------------------------------------------
__device__ __forceinline__ u32 rol32(u32 x, int n) { return (x << n) | (x >> (32 - n)); }

__device__ u32 rmd_f(int j, u32 x, u32 y, u32 z) {
    if (j < 16) return x ^ y ^ z;
    if (j < 32) return (x & y) | (~x & z);
    if (j < 48) return (x | ~y) ^ z;
    if (j < 64) return (x & z) | (y & ~z);
    return x ^ (y | ~z);
}

// RIPEMD160 常量表放 __constant__：kernel 内动态索引时走常量缓存，
// 避免每线程局部内存副本（函数内 const 数组 + 动态索引 = local memory）。
__device__ __constant__ unsigned char RMD_R1[80] = {
    0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
    7,4,13,1,10,6,15,3,12,0,9,5,2,14,11,8,
    3,10,14,4,9,15,8,1,2,7,0,6,13,11,5,12,
    1,9,11,10,0,8,12,4,13,3,7,15,14,5,6,2,
    4,0,5,9,7,12,2,10,14,1,3,8,11,6,15,13};
__device__ __constant__ unsigned char RMD_R2[80] = {
    5,14,7,0,9,2,11,4,13,6,15,8,1,10,3,12,
    6,11,3,7,0,13,5,10,14,15,8,12,4,9,1,2,
    15,5,1,3,7,14,6,9,11,8,12,2,10,0,4,13,
    8,6,4,1,3,11,15,0,5,12,2,13,9,7,10,14,
    12,15,10,4,1,5,8,7,6,2,13,14,0,3,9,11};
__device__ __constant__ unsigned char RMD_S1[80] = {
    11,14,15,12,5,8,7,9,11,13,14,15,6,7,9,8,
    7,6,8,13,11,9,7,15,7,12,15,9,11,7,13,12,
    11,13,6,7,14,9,13,15,14,8,13,6,5,12,7,5,
    11,12,14,15,14,15,9,8,9,14,5,6,8,6,5,12,
    9,15,5,11,6,8,13,12,5,12,13,14,11,8,5,6};
__device__ __constant__ unsigned char RMD_S2[80] = {
    8,9,9,11,13,15,15,5,7,7,8,11,14,14,12,6,
    9,13,15,7,12,8,9,11,7,7,12,7,6,15,13,11,
    9,7,15,11,8,6,6,14,12,13,5,14,13,13,7,5,
    15,5,8,11,14,14,6,14,6,9,12,9,12,5,15,8,
    8,5,12,9,12,5,14,6,8,13,6,5,15,13,11,11};
__device__ __constant__ u32 RMD_KL[5] = {0x00000000u,0x5A827999u,0x6ED9EBA1u,0x8F1BBCDCu,0xA953FD4Eu};
__device__ __constant__ u32 RMD_KR[5] = {0x50A28BE6u,0x5C4DD124u,0x6D703EF3u,0x7A6D76E9u,0x00000000u};

__device__ void ripemd160_32(const unsigned char msg[32], unsigned char out[20]) {

    // 单块：32 字节消息 + 0x80 + 0 填充 + 64 位小端位长(256)
    u32 X[16];
    #pragma unroll
    for (int i = 0; i < 8; i++)
        X[i] = (u32)msg[4*i] | ((u32)msg[4*i+1] << 8) |
               ((u32)msg[4*i+2] << 16) | ((u32)msg[4*i+3] << 24);
    X[8] = 0x80u;
    #pragma unroll
    for (int i = 9; i < 14; i++) X[i] = 0;
    X[14] = 256u;  // 位长 256 bit，小端 64 位：低字在 X[14]
    X[15] = 0;

    u32 h0 = 0x67452301u, h1 = 0xEFCDAB89u, h2 = 0x98BADCFEu,
        h3 = 0x10325476u, h4 = 0xC3D2E1F0u;
    u32 al = h0, bl = h1, cl = h2, dl = h3, el = h4;
    u32 ar = h0, br = h1, cr = h2, dr = h3, er = h4;
    #pragma unroll 1
    for (int j = 0; j < 80; j++) {
        u32 t = rol32(al + rmd_f(j, bl, cl, dl) + X[RMD_R1[j]] + RMD_KL[j >> 4], RMD_S1[j]) + el;
        al = el; el = dl; dl = rol32(cl, 10); cl = bl; bl = t;
        t = rol32(ar + rmd_f(79 - j, br, cr, dr) + X[RMD_R2[j]] + RMD_KR[j >> 4], RMD_S2[j]) + er;
        ar = er; er = dr; dr = rol32(cr, 10); cr = br; br = t;
    }
    u32 t = h1 + cl + dr;
    h1 = h2 + dl + er;
    h2 = h3 + el + ar;
    h3 = h4 + al + br;
    h4 = h0 + bl + cr;
    h0 = t;
    const u32 hs[5] = {h0, h1, h2, h3, h4};
    #pragma unroll
    for (int i = 0; i < 5; i++) {
        out[4*i]   = (unsigned char)(hs[i]);
        out[4*i+1] = (unsigned char)(hs[i] >> 8);
        out[4*i+2] = (unsigned char)(hs[i] >> 16);
        out[4*i+3] = (unsigned char)(hs[i] >> 24);
    }
}

__device__ void hash160_small(const unsigned char* msg, int len, unsigned char out[20]) {
    unsigned char d[32];
    sha256_small(msg, len, d);
    ripemd160_32(d, out);
}

// ------------------------------------------------------------------
// 公钥序列化：limb 小端 -> 大端字节序（比特币序列化约定）
// ------------------------------------------------------------------
__device__ void limbs_to_be(const u32 v[8], unsigned char out[32]) {
    #pragma unroll
    for (int i = 0; i < 32; i++)
        out[i] = (unsigned char)(v[7 - (i >> 2)] >> (8 * (3 - (i & 3))));
}

// 仿射点 -> 3 个 20 字节候选键（h_c / h_u / h_sh）
// 注：P2TR（BIP-341）需要再做一次 tweak*G 标量乘，v1 不实现。
__device__ void hash3(const u32 ax[8], const u32 ay[8],
                      unsigned char hc[20], unsigned char hu[20],
                      unsigned char hsh[20]) {
    unsigned char pub[65];
    pub[0] = 0x02u | (ay[0] & 1u);   // 压缩公钥：02/03 || x（大端）
    limbs_to_be(ax, pub + 1);
    hash160_small(pub, 33, hc);
    pub[0] = 0x04u;                  // 未压缩公钥：04 || x || y
    limbs_to_be(ay, pub + 33);
    hash160_small(pub, 65, hu);
    unsigned char script[22];        // P2SH-P2WPKH redeem script: 0x0014 || h_c
    script[0] = 0x00u; script[1] = 0x14u;
    #pragma unroll
    for (int i = 0; i < 20; i++) script[2 + i] = hc[i];
    hash160_small(script, 22, hsh);
}

// 私钥 k -> 3 个候选键（自检用便捷封装）
__device__ void derive_hash160(const u32 k[8], const u32* gtab,
                               unsigned char hc[20], unsigned char hu[20],
                               unsigned char hsh[20]) {
    u32 ax[8], ay[8];
    mul_g(k, gtab, ax, ay);
    hash3(ax, ay, hc, hu, hsh);
}

// ------------------------------------------------------------------
// 排序 20 字节键库上的二分查找
// ------------------------------------------------------------------
__device__ int db20_contains(const unsigned char* db, u64 n, const unsigned char key[20]) {
    u64 lo = 0, hi = n;
    while (lo < hi) {
        u64 mid = (lo + hi) >> 1;
        const unsigned char* p = db + mid * 20;
        int cmp = 0;
        #pragma unroll
        for (int i = 0; i < 20; i++) {
            if (p[i] != key[i]) { cmp = p[i] < key[i] ? -1 : 1; break; }
        }
        if (cmp == 0) return 1;
        if (cmp < 0) lo = mid + 1; else hi = mid;
    }
    return 0;
}

// ------------------------------------------------------------------
// Kernels
// ------------------------------------------------------------------

// 自检 a：域运算对拍。输入每组 16 limb (a||b)，输出 40 limb：
// mul | sqr(a) | add | sub | inv(a)
extern "C" __global__ void test_field(const u32* in_ab, u32* out, u32 n) {
    u32 idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;
    const u32* a = in_ab + (size_t)idx * 16;
    const u32* b = a + 8;
    u32* o = out + (size_t)idx * 40;
    fp_mul(o, a, b);
    fp_sqr(o + 8, a);
    fp_add(o + 16, a, b);
    fp_sub(o + 24, a, b);
    fp_inv(o + 32, a);
}

// 自检 b：k*G 对拍。输入每组 8 limb 私钥，输出 16 limb (x||y)
extern "C" __global__ void test_mulg(const u32* ks, const u32* gtab, u32* out, u32 n) {
    u32 idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;
    const u32* k = ks + (size_t)idx * 8;
    u32* o = out + (size_t)idx * 16;
    mul_g(k, gtab, o, o + 8);
}

// 自检 c：hash160 对拍。输出每组 60 字节 (h_c || h_u || h_sh)
extern "C" __global__ void test_hashes(const u32* ks, const u32* gtab,
                                       unsigned char* out, u32 n) {
    u32 idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;
    const u32* k = ks + (size_t)idx * 8;
    unsigned char* o = out + (size_t)idx * 60;
    derive_hash160(k, gtab, o, o + 20, o + 40);
}

// 候选私钥 kj = k0 + j（mod N，j < 2^32）。返回 0 表示该候选无效（wrap 到 0，跳过）。
__device__ int cand_key(u32 kj[8], const u32 k0[8], u32 j) {
    u64 c = (u64)j;
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        u64 t = (u64)k0[i] + c;
        kj[i] = (u32)t; c = t >> 32;
    }
    // k0 < N 且 j 小，k0+j < 2^256，c 必为 0
    if (fp_geq_n(kj)) fp_sub_n(kj);   // k0+j 最多跨 N 一次
    return !fp_iszero(kj);            // k0+j ≡ 0 (mod N)：点为无穷远，跳过
}

// 命中记录
__device__ void record_hit(const u32 kj[8], u32 cand, u32* hit_count,
                           u32* hits, u32 max_hits) {
    u32 pos = atomicAdd(hit_count, 1u);
    if (pos < max_hits) {
        u32* e = hits + (size_t)pos * 9;
        #pragma unroll
        for (int i = 0; i < 8; i++) e[i] = kj[i];
        e[8] = cand;
    }
}

// 步进扫描核心：从 k0*G（Jacobian）出发，逐部 P += G，Montgomery 批量求逆后
// 逐候选处理。process=1 时算 hash+查库；process=0 只转仿射（预留）。
// out60 非 NULL 时把每候选 60B (h_c||h_u||h_sh) 写出（自检用，不查库）。
__device__ void stride_scan(const u32 k0[8], const u32* gtab,
                            const unsigned char* db20, u64 n20,
                            u32* hit_count, u32* hits, u32 max_hits,
                            unsigned char* out60) {
    const u32* G = gtab + 16;  // 窗口0 digit1 即生成点 G（仿射 x||y）
    u32 X[8], Y[8], Z[8];
    int inf;
    jac_mul_g(k0, gtab, X, Y, Z, &inf);

    // 前向：存储 S 个 Jacobian 点并累积 Z 的前缀积
    u32 XS[STRIDE * 8], YS[STRIDE * 8], ZS[STRIDE * 8], PS[STRIDE * 8];  // 16KB/线程@S=128
    u32 acc[8] = {1u, 0, 0, 0, 0, 0, 0, 0};
    #pragma unroll 1
    for (int j = 0; j < STRIDE; j++) {
        #pragma unroll
        for (int i = 0; i < 8; i++) {
            XS[j * 8 + i] = X[i]; YS[j * 8 + i] = Y[i]; ZS[j * 8 + i] = Z[i];
        }
        fp_mul(acc, acc, Z);
        #pragma unroll
        for (int i = 0; i < 8; i++) PS[j * 8 + i] = acc[i];
        if (j < STRIDE - 1) jac_add_mixed(X, Y, Z, &inf, G, G + 8);
        // 注：若 k0+(j+1) ≡ 0 (mod N)，点恰为无穷远（inf=1，X/Y/Z 保留旧值，
        // Z 非零不影响批量求逆）；下一步 add 会从 inf 恢复为 G，扫描自愈。
        // 该候选由 cand_key 的零检查跳过。
    }

    // Montgomery 批量求逆：1 次 fp_inv + 每候选 2 次 fp_mul
    u32 t[8];
    fp_inv(t, acc);
    #pragma unroll 1
    for (int j = STRIDE - 1; j >= 0; j--) {
        u32 zi[8];
        if (j) {
            fp_mul(zi, t, PS + (j - 1) * 8);
            fp_mul(t, t, ZS + j * 8);
        } else {
            #pragma unroll
            for (int i = 0; i < 8; i++) zi[i] = t[i];
        }
        // 转仿射
        u32 zi2[8], ax[8], ay[8];
        fp_sqr(zi2, zi);
        fp_mul(ax, XS + j * 8, zi2);
        fp_mul(ay, YS + j * 8, zi2);
        fp_mul(ay, ay, zi);
        // 候选私钥 kj = k0 + j (mod N)，0 跳过
        u32 kj[8];
        if (!cand_key(kj, k0, (u32)j)) {
            if (out60) {
                #pragma unroll
                for (int i = 0; i < 60; i++) out60[j * 60 + i] = 0;
            }
            continue;
        }
        unsigned char hc[20], hu[20], hsh[20];
        hash3(ax, ay, hc, hu, hsh);
        if (out60) {
            #pragma unroll
            for (int i = 0; i < 20; i++) {
                out60[j * 60 + i] = hc[i];
                out60[j * 60 + 20 + i] = hu[i];
                out60[j * 60 + 40 + i] = hsh[i];
            }
            continue;
        }
        if (n20 == 0) continue;  // bench 模式：纯生成不查库
        if (db20_contains(db20, n20, hc))  record_hit(kj, 0, hit_count, hits, max_hits);
        if (db20_contains(db20, n20, hu))  record_hit(kj, 1, hit_count, hits, max_hits);
        if (db20_contains(db20, n20, hsh)) record_hit(kj, 2, hit_count, hits, max_hits);
    }
}

// 自检 c2：步进扫描对拍。每线程输出 STRIDE x 60B（跳过的候选全 0）
extern "C" __global__ void test_stride(const u32* ks, const u32* gtab,
                                       unsigned char* out, u32 nthreads) {
    u32 idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= nthreads) return;
    stride_scan(ks + (size_t)idx * 8, gtab, NULL, 0, NULL, NULL, 0,
                out + (size_t)idx * STRIDE * 60);
}

// 正式碰撞：随机起点 + 步进扫描 + Montgomery 批量求逆
extern "C" __global__ void hunt(const u32* ks, const u32* gtab,
                                const unsigned char* db20, u64 n20,
                                u32* hit_count, u32* hits, u32 max_hits, u32 nthreads) {
    u32 idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= nthreads) return;
    stride_scan(ks + (size_t)idx * 8, gtab, db20, n20, hit_count, hits, max_hits, NULL);
}
"""
