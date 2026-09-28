// Exact MWC1616 Math.random logic from V8 src/js/math.js (2015 era).
// Emits the 32-bit `r` and the JSBN pool value floor(65536*Math.random()).
function makeMwc(a, b, c, d) {
  let A = a, B = b, C = c, D = d;
  const view = new DataView(new ArrayBuffer(8));
  return function () {
    const r0 = (Math.imul(18030, A) + B) | 0;
    const r1 = (Math.imul(36969, C) + D) | 0;
    A = r0 & 0xFFFF; B = r0 >>> 16;
    C = r1 & 0xFFFF; D = r1 >>> 16;
    const r = (r0 ^ r1) >>> 0;
    const hi = (0x3FF00000 | (r & 0x000FFFFF)) >>> 0;
    const lo = (r & 0xFFF00000) >>> 0;
    const bits = (BigInt(hi) << 32n) | BigInt(lo);
    view.setBigUint64(0, bits);
    const value = view.getFloat64(0);
    return { r, pick: Math.floor(65536 * (value - 1)), analytic: (r & 0xFFFFF) >> 4 };
  };
}
const seeds = [[1,2,3,4],[0xFFFF,0xFFFF,0xFFFF,0xFFFF],[0x1234,0x5678,0x9ABC,0xDEF0],[1,0,0,1],[0x8000,0x7FFF,0x0001,0xFFFE]];
const out = [];
for (const [a,b,c,d] of seeds) {
  const gen = makeMwc(a,b,c,d);
  const rows = [];
  for (let i=0;i<8;i++) {
    const {r,pick,analytic} = gen();
    if (pick !== analytic) throw new Error(`mismatch r=${r} pick=${pick} analytic=${analytic}`);
    rows.push([r,pick]);
  }
  out.push({seed:[a,b,c,d], rows});
}
console.log(JSON.stringify(out, null, 1));
