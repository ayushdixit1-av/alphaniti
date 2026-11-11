import { CandleBar } from '../types/api';

export interface NormalizedBar extends CandleBar {
  ts: number;
  ema9: number;
  vwap: number;
  vwapUpper: number;
  vwapLower: number;
  rvol: number;
  turnoverLakhs: number;
  amihudImpact: number;
  liquidityScore: number;
  liquidityTier: 'DEEP' | 'NORMAL' | 'THIN';
}

export interface ComputedVPBin {
  low: number;
  high: number;
  mid: number;
  buyVol: number;
  sellVol: number;
  totalVol: number;
  isPoc: boolean;
  isValueArea: boolean;
}

export interface ComputedVolumeProfile {
  pocPrice: number;
  vahPrice: number;
  valPrice: number;
  totalVolume: number;
  bins: ComputedVPBin[];
}

export function normalizeAndComputeIndicators(candles: CandleBar[]): NormalizedBar[] {
  const withTs = candles
    .map((c, idx) => {
      const ts =
        typeof c.timestamp === 'number'
          ? c.timestamp
          : typeof c.time === 'number'
          ? c.time
          : Math.floor(Date.now() / 1000) - (candles.length - idx) * 300;
      return { ...c, ts };
    })
    .filter((c) => c.close > 0 && c.high >= c.low)
    .sort((a, b) => a.ts - b.ts);

  const deduped: typeof withTs = [];
  const seen = new Set<number>();
  for (const bar of withTs) {
    if (!seen.has(bar.ts)) {
      seen.add(bar.ts);
      deduped.push(bar);
    }
  }

  let emaPrev: number | null = null;
  const k = 2 / (9 + 1);
  let cumTPV = 0;
  let cumVol = 0;
  let cumTP2V = 0;
  const volHist: number[] = [];

  return deduped.map((bar) => {
    emaPrev = emaPrev === null ? bar.close : bar.close * k + emaPrev * (1 - k);
    const tp = (bar.high + bar.low + bar.close) / 3;
    const rawVol = Math.max(0, bar.volume || 0);
    const v = Math.max(1, rawVol);
    cumTPV += tp * v;
    cumTP2V += tp * tp * v;
    cumVol += v;
    const vwap = cumTPV / cumVol;
    const variance = Math.max(0, cumTP2V / cumVol - vwap * vwap);
    const std = Math.sqrt(variance);

    // Causal bar Liquidity Factor [0..100]: RVOL depth + Turnover depth - Amihud price impact
    const winVols = volHist.slice(-20);
    const avgV = winVols.length > 0 ? winVols.reduce((a, b) => a + b, 0) / winVols.length : Math.max(1, rawVol);
    const rvol = avgV > 0 ? rawVol / avgV : 1;
    volHist.push(rawVol);

    const turnoverLakhs = (bar.close * rawVol) / 100000;
    const barMoveBps = (Math.max(Math.abs(bar.close - bar.open), bar.high - bar.low) / Math.max(1e-6, bar.close)) * 10000;
    const amihudImpact = barMoveBps / Math.max(0.05, turnoverLakhs);
    const volDepth = Math.min(1, rvol / 2);
    const turnoverDepth = Math.min(1, Math.log1p(turnoverLakhs) / Math.log1p(50));
    const impactPenalty = Math.min(1, amihudImpact / 50);
    const liquidityScore = Number(
      (100 * Math.max(0, Math.min(1, 0.45 * volDepth + 0.4 * turnoverDepth + 0.15 * (1 - impactPenalty)))).toFixed(1)
    );
    const liquidityTier: 'DEEP' | 'NORMAL' | 'THIN' =
      liquidityScore >= 70 ? 'DEEP' : liquidityScore >= 40 ? 'NORMAL' : 'THIN';

    return {
      ...bar,
      ema9: Number(emaPrev.toFixed(2)),
      vwap: Number(vwap.toFixed(2)),
      vwapUpper: Number((vwap + std).toFixed(2)),
      vwapLower: Number((vwap - std).toFixed(2)),
      rvol: Number(rvol.toFixed(2)),
      turnoverLakhs: Number(turnoverLakhs.toFixed(2)),
      amihudImpact: Number(amihudImpact.toFixed(2)),
      liquidityScore,
      liquidityTier,
    };
  });
}

export function computeVolumeProfile(bars: NormalizedBar[], binsCount = 32): ComputedVolumeProfile | null {
  if (bars.length < 2) return null;
  let minP = Infinity;
  let maxP = -Infinity;
  for (const b of bars) {
    if (b.low < minP) minP = b.low;
    if (b.high > maxP) maxP = b.high;
  }
  if (!isFinite(minP) || !isFinite(maxP) || maxP <= minP) return null;

  const step = (maxP - minP) / binsCount;
  const buyDist = new Array<number>(binsCount).fill(0);
  const sellDist = new Array<number>(binsCount).fill(0);

  for (const b of bars) {
    const v = Math.max(1, b.volume || 1);
    const i0 = Math.max(0, Math.min(binsCount - 1, Math.floor((b.low - minP) / step)));
    const i1 = Math.max(0, Math.min(binsCount - 1, Math.floor((b.high - minP) / step)));
    const span = Math.max(1, i1 - i0 + 1);
    const isUp = b.close >= b.open;
    const buyShare = isUp ? 0.68 : 0.32;
    for (let idx = i0; idx <= i1; idx++) {
      buyDist[idx] += (v * buyShare) / span;
      sellDist[idx] += (v * (1 - buyShare)) / span;
    }
  }

  const totDist = buyDist.map((bv, i) => bv + sellDist[i]);
  const totalVolume = totDist.reduce((a, b) => a + b, 0);
  if (totalVolume <= 0) return null;

  let pocIdx = 0;
  for (let i = 1; i < binsCount; i++) {
    if (totDist[i] > totDist[pocIdx]) pocIdx = i;
  }
  const pocPrice = Number((minP + (pocIdx + 0.5) * step).toFixed(2));

  const targetVa = totalVolume * 0.7;
  let accum = totDist[pocIdx];
  let upIdx = pocIdx;
  let downIdx = pocIdx;

  while (accum < targetVa && (upIdx < binsCount - 1 || downIdx > 0)) {
    const upVol = upIdx < binsCount - 1 ? totDist[upIdx + 1] : -1;
    const downVol = downIdx > 0 ? totDist[downIdx - 1] : -1;
    if (upVol >= downVol && upIdx < binsCount - 1) {
      upIdx++;
      accum += upVol;
    } else if (downIdx > 0) {
      downIdx--;
      accum += downVol;
    } else {
      break;
    }
  }

  const vahPrice = Number((minP + (upIdx + 1) * step).toFixed(2));
  const valPrice = Number((minP + downIdx * step).toFixed(2));

  const bins: ComputedVPBin[] = totDist.map((tv, i) => ({
    low: minP + i * step,
    high: minP + (i + 1) * step,
    mid: minP + (i + 0.5) * step,
    buyVol: Math.round(buyDist[i]),
    sellVol: Math.round(sellDist[i]),
    totalVol: Math.round(tv),
    isPoc: i === pocIdx,
    isValueArea: i >= downIdx && i <= upIdx,
  }));

  return {
    pocPrice,
    vahPrice,
    valPrice,
    totalVolume: Math.round(totalVolume),
    bins,
  };
}

export function findNearestBarIndex(bars: NormalizedBar[], hhmm: string): number {
  if (!bars.length || !hhmm) return 0;
  const [hh, mm] = hhmm.split(':').map((n) => parseInt(n, 10) || 0);
  const targetMins = hh * 60 + mm;
  let bestIdx = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < bars.length; i++) {
    const ist = new Date((bars[i].ts + 19800) * 1000);
    const mins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
    const diff = Math.abs(mins - targetMins);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIdx = i;
    }
  }
  return bestIdx;
}
