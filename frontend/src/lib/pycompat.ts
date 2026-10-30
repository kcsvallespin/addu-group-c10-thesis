/** Byte-for-byte equivalents of the Python behaviour run_scenario.py relies on, so the app builds the same
 * candidate pool, the same prompt and the same checker messages as the benchmark. */

const N = 624;
const M = 397;

/** CPython's random.Random (MT19937): seeding with an int and shuffle() give the same sequence as Python 3.12. */
export class PyRandom {
  private mt = new Uint32Array(N);
  private mti = N + 1;

  constructor(seed: number) {
    // random.seed(int) -> init_by_array(abs(seed) split into 32-bit words, least significant first)
    const key: number[] = [];
    let n = Math.abs(Math.trunc(seed));
    do {
      key.push(n % 0x100000000);
      n = Math.floor(n / 0x100000000);
    } while (n > 0);
    this.initByArray(key);
  }

  private initGenrand(s: number) {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < N; i++) {
      mt[i] = (Math.imul(1812433253, mt[i - 1] ^ (mt[i - 1] >>> 30)) + i) >>> 0;
    }
    this.mti = N;
  }

  private initByArray(key: number[]) {
    const mt = this.mt;
    this.initGenrand(19650218);
    let i = 1;
    let j = 0;
    for (let k = Math.max(N, key.length); k > 0; k--) {
      const s = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(s, 1664525)) + key[j] + j) >>> 0;
      i++;
      j++;
      if (i >= N) {
        mt[0] = mt[N - 1];
        i = 1;
      }
      if (j >= key.length) j = 0;
    }
    for (let k = N - 1; k > 0; k--) {
      const s = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(s, 1566083941)) - i) >>> 0;
      i++;
      if (i >= N) {
        mt[0] = mt[N - 1];
        i = 1;
      }
    }
    mt[0] = 0x80000000;
  }

  private genrandUint32(): number {
    const mt = this.mt;
    if (this.mti >= N) {
      let kk = 0;
      for (; kk < N - M; kk++) {
        const y = (mt[kk] & 0x80000000) | (mt[kk + 1] & 0x7fffffff);
        mt[kk] = mt[kk + M] ^ (y >>> 1) ^ (y & 1 ? 0x9908b0df : 0);
      }
      for (; kk < N - 1; kk++) {
        const y = (mt[kk] & 0x80000000) | (mt[kk + 1] & 0x7fffffff);
        mt[kk] = mt[kk + (M - N)] ^ (y >>> 1) ^ (y & 1 ? 0x9908b0df : 0);
      }
      const y = (mt[N - 1] & 0x80000000) | (mt[0] & 0x7fffffff);
      mt[N - 1] = mt[M - 1] ^ (y >>> 1) ^ (y & 1 ? 0x9908b0df : 0);
      this.mti = 0;
    }
    let y = mt[this.mti++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  /** random._randbelow_with_getrandbits(n), for 0 < n < 2**32. */
  private randbelow(n: number): number {
    const k = 32 - Math.clz32(n);
    let r = this.genrandUint32() >>> (32 - k);
    while (r >= n) r = this.genrandUint32() >>> (32 - k);
    return r;
  }

  /** random.shuffle(x), in place. */
  shuffle<T>(x: T[]): void {
    for (let i = x.length - 1; i > 0; i--) {
      const j = this.randbelow(i + 1);
      [x[i], x[j]] = [x[j], x[i]];
    }
  }
}

/** Keys whose values are Python floats; json.dumps writes 4.0 where JSON.stringify writes 4. */
const FLOAT_KEYS = new Set(["average_rating", "latitude", "longitude", "pop"]);

/** json.dumps(value, ensure_ascii=False) with the default ", " and ": " separators. */
export function pyDumps(value: unknown, key?: string): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (key !== undefined && FLOAT_KEYS.has(key) && Number.isInteger(value)) return `${value}.0`;
    return String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => pyDumps(v)).join(", ")}]`;
  const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}: ${pyDumps(v, k)}`).join(", ")}}`;
}

/** repr() of a str. */
function pyStrRepr(s: string): string {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  const body = s
    .replace(/\\/g, "\\\\")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t")
    .split(q)
    .join(`\\${q}`);
  return `${q}${body}${q}`;
}

/** repr() of a value parsed from JSON. (Whole-number floats inside lists/dicts print as ints here.) */
function pyRepr(x: unknown): string {
  if (typeof x === "string") return pyStrRepr(x);
  if (Array.isArray(x)) return `[${x.map(pyRepr).join(", ")}]`;
  if (typeof x === "object" && x !== null) {
    return `{${Object.entries(x).map(([k, v]) => `${pyStrRepr(k)}: ${pyRepr(v)}`).join(", ")}}`;
  }
  return pyStr(x);
}

/** str(x) in an f-string, for a value parsed from the model's JSON (run_scenario.py's py_str). */
export function pyStr(x: unknown): string {
  if (x === null || x === undefined) return "None";
  if (x === true) return "True";
  if (x === false) return "False";
  if (typeof x === "string") return x;
  if (typeof x === "number") return String(x);
  return pyRepr(x);
}

/** repr() of a list of plain strings, e.g. ['a', 'b']. */
export function pyListRepr(items: string[]): string {
  return `[${items.map((s) => `'${s}'`).join(", ")}]`;
}

/** f"{n:,}" for an integer. */
export function fmtThousands(n: number): string {
  const s = String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return n < 0 ? `-${s}` : s;
}

/** Python's round(x) to an integer: ties go to the even neighbour. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Number.isInteger(x * 2) && !Number.isInteger(x) && r % 2 !== 0 ? r - 1 : r;
}

/** f"{x:.{digits}f}" for digits 0 or 1. toFixed breaks exact ties upward; Python breaks them to even. */
export function pyFixed(x: number, digits: 0 | 1): string {
  const scale = digits === 0 ? 1 : 10;
  const tie = digits === 0 ? Number.isInteger(x * 2) && !Number.isInteger(x) : Number.isInteger(x * 4) && !Number.isInteger(x * 2);
  if (!tie) return x.toFixed(digits);
  const lo = Math.floor(x * scale);
  const n = lo % 2 === 0 ? lo : lo + 1;
  return (n / scale).toFixed(digits);
}
