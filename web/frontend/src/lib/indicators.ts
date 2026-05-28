/**
 * 技术指标 — 纯函数，输入 OHLCV 数组，输出指标时序。
 * 所有计算客户端进行，避免给后端加 endpoint。
 *
 *   sma(values, n)           — Simple Moving Average
 *   ema(values, n)           — Exponential Moving Average
 *   macd(closes)             — { macd, signal, hist }
 *   rsi(closes, n=14)        — Relative Strength Index
 *   boll(closes, n=20, k=2)  — Bollinger Bands { upper, middle, lower }
 *   kdj(highs, lows, closes) — { k, d, j }
 */

export type Bar = {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  vol: number | null;
};

function nan(n: number): number[] {
  return Array(n).fill(NaN);
}

export function sma(values: number[], n: number): number[] {
  const out: number[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    out.push(i >= n - 1 ? sum / n : NaN);
  }
  return out;
}

export function ema(values: number[], n: number): number[] {
  const alpha = 2 / (n + 1);
  const out: number[] = [];
  let prev = NaN;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isNaN(prev)) {
      // Seed with the SMA of the first n values once we have them
      if (i === n - 1) {
        let sum = 0;
        for (let j = 0; j < n; j++) sum += values[j];
        prev = sum / n;
        out.push(prev);
      } else {
        out.push(NaN);
      }
    } else {
      prev = v * alpha + prev * (1 - alpha);
      out.push(prev);
    }
  }
  return out;
}

export function macd(closes: number[], fast = 12, slow = 26, sig = 9):
  { macd: number[]; signal: number[]; hist: number[] } {
  const ef = ema(closes, fast);
  const es = ema(closes, slow);
  const m = closes.map((_, i) => ef[i] - es[i]);
  const signal = ema(m.map((v) => (Number.isFinite(v) ? v : 0)), sig);
  // Mask signal positions where m wasn't ready
  for (let i = 0; i < m.length; i++) {
    if (!Number.isFinite(m[i])) signal[i] = NaN;
  }
  const hist = m.map((v, i) => v - signal[i]);
  return { macd: m, signal, hist };
}

export function rsi(closes: number[], n = 14): number[] {
  const out: number[] = [];
  let gain = 0;
  let loss = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) {
      out.push(NaN);
      continue;
    }
    const ch = closes[i] - closes[i - 1];
    const up = ch > 0 ? ch : 0;
    const down = ch < 0 ? -ch : 0;
    if (i < n) {
      gain += up;
      loss += down;
      out.push(NaN);
      if (i === n - 1) {
        // Won't compute until i==n
      }
    } else if (i === n) {
      gain = (gain + up) / n;
      loss = (loss + down) / n;
      out.push(loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
    } else {
      gain = (gain * (n - 1) + up) / n;
      loss = (loss * (n - 1) + down) / n;
      out.push(loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
    }
  }
  return out;
}

export function boll(closes: number[], n = 20, k = 2):
  { upper: number[]; middle: number[]; lower: number[] } {
  const middle = sma(closes, n);
  const upper: number[] = [];
  const lower: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) {
      upper.push(NaN);
      lower.push(NaN);
      continue;
    }
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) {
      s += (closes[j] - middle[i]) ** 2;
    }
    const std = Math.sqrt(s / n);
    upper.push(middle[i] + k * std);
    lower.push(middle[i] - k * std);
  }
  return { upper, middle, lower };
}

export function kdj(
  highs: number[],
  lows: number[],
  closes: number[],
  n = 9,
): { k: number[]; d: number[]; j: number[] } {
  const k: number[] = [];
  const d: number[] = [];
  const j: number[] = [];
  let prevK = 50;
  let prevD = 50;
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) {
      k.push(NaN);
      d.push(NaN);
      j.push(NaN);
      continue;
    }
    let hh = -Infinity;
    let ll = Infinity;
    for (let m = i - n + 1; m <= i; m++) {
      if (highs[m] > hh) hh = highs[m];
      if (lows[m] < ll) ll = lows[m];
    }
    const rsv = hh === ll ? 50 : ((closes[i] - ll) / (hh - ll)) * 100;
    const ki = (2 / 3) * prevK + (1 / 3) * rsv;
    const di = (2 / 3) * prevD + (1 / 3) * ki;
    k.push(ki);
    d.push(di);
    j.push(3 * ki - 2 * di);
    prevK = ki;
    prevD = di;
  }
  return { k, d, j };
}
