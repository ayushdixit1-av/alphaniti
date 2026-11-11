import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  CandlestickChart,
  CheckCircle2,
  Loader2,
  Search,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react';

interface StrategyCandle {
  time: string;
  minute: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap: number;
  ema5: number;
  ema15: number;
  rvol: number;
  turnover_lakhs?: number;
  amihud_impact?: number;
  liquidity_score?: number;
  liquidity_tier?: 'DEEP' | 'NORMAL' | 'THIN';
}

interface StrategyTrade {
  direction: 'LONG' | 'SHORT';
  entry_idx: number;
  entry_time: string;
  entry_price: number;
  stop_loss: number;
  exit_idx: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  qty: number;
  capital_base: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  ret_pct: number;
}

interface StrategyDiagnostics {
  prev_close: number;
  day_open: number;
  day_close: number;
  day_high: number;
  day_low: number;
  gap_pct: number;
  atr14_pct: number;
  nifty_prev_close: number | null;
  nifty_sma20: number | null;
  nifty_day_ret_pct: number | null;
  max_rvol_0930_1100: number;
  avg_liquidity_score?: number;
  entry_window_liquidity_score?: number;
  total_turnover_cr?: number;
  avg_amihud_impact?: number;
  liquidity_regime?: 'DEEP' | 'NORMAL' | 'THIN';
  gates: {
    gap_ge_1_8_pct: boolean;
    atr_le_3_5_pct: boolean;
    macro_nifty_sma20_ok: boolean;
    orb15_break_ok: boolean;
    rvol_ge_1_8x_ok: boolean;
    vwap_align_ok: boolean;
    ema5_15_align_ok: boolean;
    liquidity_ge_45_ok?: boolean;
  };
  a8_triggered: boolean;
}

interface PresetItem {
  symbol: string;
  date: string | null;
  tag: string;
  label: string;
}

interface StrategyChartResponse {
  status: string;
  symbol: string;
  session_date: string;
  source?: string;
  interval_used?: string;
  sanitized_rejected?: boolean;
  reason?: string;
  error?: string;
  bar_count?: number;
  orb15?: { high: number; low: number; start_time: string; end_time: string } | null;
  candles: StrategyCandle[];
  trade: StrategyTrade | null;
  diagnostics?: StrategyDiagnostics;
  presets?: PresetItem[];
}

interface StrategyChartWorkspaceProps {
  initialSymbol?: string;
  defaultCapital?: number;
}

const POPULAR_ANY_NSE = [
  'RELIANCE',
  'SBIN',
  'TCS',
  'INFY',
  'HDFCBANK',
  'ICICIBANK',
  'TATAMOTORS',
  'TATASTEEL',
  'BAJFINANCE',
  'ADANIENT',
  'TRENT',
  'ZOMATO',
  'HAL',
  'BEL',
];

export const StrategyChartWorkspace: React.FC<StrategyChartWorkspaceProps> = ({
  initialSymbol = 'ADANIENT',
  defaultCapital = 10000,
}) => {
  const [symbolInput, setSymbolInput] = useState<string>(initialSymbol || 'ADANIENT');
  const [dateInput, setDateInput] = useState<string>('2024-11-21');
  const [capitalInput, setCapitalInput] = useState<number>(Math.max(5000, Math.round(defaultCapital || 10000)));
  const [loading, setLoading] = useState<boolean>(false);
  const [data, setData] = useState<StrategyChartResponse | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const [showOrb, setShowOrb] = useState<boolean>(true);
  const [showVwap, setShowVwap] = useState<boolean>(true);
  const [showEma, setShowEma] = useState<boolean>(true);
  const [showSignals, setShowSignals] = useState<boolean>(true);
  const [showLiquidity, setShowLiquidity] = useState<boolean>(true);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const priceCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const volCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const liqCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [canvasWidth, setCanvasWidth] = useState<number>(980);

  const fetchChart = useCallback(
    async (sym: string, dt: string | null, cap: number, silent: boolean = false) => {
      const clean = sym.trim().toUpperCase();
      if (!clean) return;
      if (!silent) {
        setLoading(true);
        setHoverIdx(null);
      }
      try {
        const params = new URLSearchParams({
          symbol: clean,
          capital: String(Math.max(5000, cap || 10000)),
        });
        if (dt && dt.trim()) params.set('date', dt.trim());
        const res = await fetch(`/api/strategy_chart?${params.toString()}`);
        const json: StrategyChartResponse = await res.json();
        setData(json);
      } catch (err: any) {
        if (!silent) {
          setData({
            status: 'ERROR',
            symbol: clean,
            session_date: dt || '',
            error: err?.message || 'Network error fetching strategy chart',
            candles: [],
            trade: null,
          });
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    []
  );

  // Initial load on mount
  useEffect(() => {
    fetchChart('ADANIENT', '2024-11-21', capitalInput);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Silent 10s live auto-refresh when viewing Latest Session
  useEffect(() => {
    if (!autoRefresh || dateInput.trim() !== '') return;
    const id = setInterval(() => {
      if (!document.hidden) {
        fetchChart(symbolInput, null, capitalInput, true);
      }
    }, 10000);
    return () => clearInterval(id);
  }, [autoRefresh, dateInput, symbolInput, capitalInput, fetchChart]);

  // Measure container width cleanly
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 240) setCanvasWidth(w);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const drawCharts = useCallback(() => {
    const priceCanvas = priceCanvasRef.current;
    const volCanvas = volCanvasRef.current;
    if (!priceCanvas || !volCanvas || !data || !data.candles || data.candles.length === 0) return;

    const candles = data.candles;
    const n = candles.length;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(360, canvasWidth);
    const pHeight = 430;
    const vHeight = 135;

    priceCanvas.width = width * dpr;
    priceCanvas.height = pHeight * dpr;
    priceCanvas.style.width = `${width}px`;
    priceCanvas.style.height = `${pHeight}px`;

    volCanvas.width = width * dpr;
    volCanvas.height = vHeight * dpr;
    volCanvas.style.width = `${width}px`;
    volCanvas.style.height = `${vHeight}px`;

    const ctx = priceCanvas.getContext('2d');
    const vctx = volCanvas.getContext('2d');
    if (!ctx || !vctx) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    vctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const pad = { l: 58, r: 74, t: 20, b: 22 };
    const pw = width - pad.l - pad.r;
    const ph = pHeight - pad.t - pad.b;

    const vpad = { l: 58, r: 74, t: 16, b: 24 };
    const vw = width - vpad.l - vpad.r;
    const vh = vHeight - vpad.t - vpad.b;

    // Background
    ctx.fillStyle = '#070b12';
    ctx.fillRect(0, 0, width, pHeight);
    vctx.fillStyle = '#070b12';
    vctx.fillRect(0, 0, width, vHeight);

    // Price domain
    let pMin = Infinity;
    let pMax = -Infinity;
    for (const c of candles) {
      if (c.low < pMin) pMin = c.low;
      if (c.high > pMax) pMax = c.high;
      if (showVwap && c.vwap > 0) {
        if (c.vwap < pMin) pMin = c.vwap;
        if (c.vwap > pMax) pMax = c.vwap;
      }
    }
    if (showOrb && data.orb15) {
      pMin = Math.min(pMin, data.orb15.low);
      pMax = Math.max(pMax, data.orb15.high);
    }
    if (showSignals && data.trade) {
      pMin = Math.min(pMin, data.trade.stop_loss, data.trade.entry_price, data.trade.exit_price);
      pMax = Math.max(pMax, data.trade.stop_loss, data.trade.entry_price, data.trade.exit_price);
    }
    const span = Math.max(0.5, pMax - pMin);
    pMin -= span * 0.08;
    pMax += span * 0.08;

    const xOf = (i: number) => pad.l + ((i + 0.5) / n) * pw;
    const yOf = (p: number) => pad.t + ((pMax - p) / (pMax - pMin)) * ph;

    // Highlight A8 signal window 09:30..11:00 IST (570..660 min)
    let winStartIdx = -1;
    let winEndIdx = -1;
    for (let i = 0; i < n; i++) {
      if (candles[i].minute >= 570 && winStartIdx === -1) winStartIdx = i;
      if (candles[i].minute <= 660) winEndIdx = i;
    }
    if (winStartIdx >= 0 && winEndIdx >= winStartIdx) {
      const wx1 = xOf(winStartIdx);
      const wx2 = xOf(winEndIdx);
      ctx.fillStyle = 'rgba(56, 189, 248, 0.045)';
      ctx.fillRect(wx1, pad.t, Math.max(2, wx2 - wx1), ph);
      ctx.fillStyle = 'rgba(56, 189, 248, 0.45)';
      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.fillText('A8 ENTRY WINDOW (09:30–11:00)', wx1 + 4, pad.t + 12);
    }

    // Horizontal grid & right Y-axis labels
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.09)';
    ctx.lineWidth = 1;
    ctx.font = '10.5px "JetBrains Mono", monospace';
    for (let g = 0; g <= 5; g++) {
      const val = pMin + ((pMax - pMin) * g) / 5;
      const y = yOf(val);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(width - pad.r, y);
      ctx.stroke();

      ctx.fillStyle = '#94a3b8';
      ctx.textAlign = 'left';
      ctx.fillText(`₹${val.toFixed(2)}`, width - pad.r + 6, y + 3.5);
    }

    // ORB15 box & levels
    if (showOrb && data.orb15) {
      const yHi = yOf(data.orb15.high);
      const yLo = yOf(data.orb15.low);
      ctx.fillStyle = 'rgba(168, 85, 247, 0.08)';
      ctx.fillRect(pad.l, yHi, pw, Math.max(1, yLo - yHi));

      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = 'rgba(168, 85, 247, 0.75)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.moveTo(pad.l, yHi);
      ctx.lineTo(width - pad.r, yHi);
      ctx.moveTo(pad.l, yLo);
      ctx.lineTo(width - pad.r, yLo);
      ctx.stroke();
      ctx.restore();

      ctx.fillStyle = '#c084fc';
      ctx.textAlign = 'left';
      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.fillText(`ORB15 H ₹${data.orb15.high.toFixed(1)}`, pad.l + 4, yHi - 3);
      ctx.fillText(`ORB15 L ₹${data.orb15.low.toFixed(1)}`, pad.l + 4, yLo + 10);
    }

    // Trade price levels (Stop Loss & Entry line)
    if (showSignals && data.trade) {
      const tr = data.trade;
      const ySl = yOf(tr.stop_loss);
      const yEp = yOf(tr.entry_price);

      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = '#f43f5e';
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(pad.l, ySl);
      ctx.lineTo(width - pad.r, ySl);
      ctx.stroke();

      ctx.strokeStyle = tr.direction === 'LONG' ? '#10b981' : '#f59e0b';
      ctx.beginPath();
      ctx.moveTo(pad.l, yEp);
      ctx.lineTo(width - pad.r, yEp);
      ctx.stroke();
      ctx.restore();

      ctx.fillStyle = '#f43f5e';
      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(`1.2% SL ₹${tr.stop_loss.toFixed(2)}`, width - pad.r - 4, ySl - 3);
    }

    // Candlesticks
    const candleW = Math.max(1.4, Math.min(8, (pw / n) * 0.68));
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const x = xOf(i);
      const up = c.close >= c.open;
      const col = up ? '#10b981' : '#f43f5e';
      const yH = yOf(c.high);
      const yL = yOf(c.low);
      const yO = yOf(c.open);
      const yC = yOf(c.close);

      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yH);
      ctx.lineTo(x, yL);
      ctx.stroke();

      ctx.fillStyle = col;
      const bodyTop = Math.min(yO, yC);
      const bodyH = Math.max(1.2, Math.abs(yC - yO));
      ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);
    }

    // Line overlays helper
    const drawLineOverlay = (getter: (c: StrategyCandle) => number, color: string, lineWidth: number, dash: number[] = []) => {
      ctx.save();
      if (dash.length) ctx.setLineDash(dash);
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = xOf(i);
        const y = yOf(getter(candles[i]));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.restore();
    };

    if (showVwap) drawLineOverlay((c) => c.vwap, '#38bdf8', 1.6, [6, 3]);
    if (showEma) {
      drawLineOverlay((c) => c.ema15, '#f59e0b', 1.35);
      drawLineOverlay((c) => c.ema5, '#4ade80', 1.25);
    }

    // Entry & Exit Signal Markers
    if (showSignals && data.trade) {
      const tr = data.trade;
      const exX = xOf(tr.entry_idx);
      const exY = yOf(tr.entry_price);

      // Entry Marker
      ctx.fillStyle = tr.direction === 'LONG' ? '#10b981' : '#f43f5e';
      ctx.beginPath();
      ctx.arc(exX, exY, 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      const eTag = `${tr.direction} @ ${tr.entry_time} (₹${tr.entry_price.toFixed(1)})`;
      ctx.font = 'bold 10.5px "JetBrains Mono", monospace';
      const eTextW = ctx.measureText(eTag).width + 10;
      const eBoxX = Math.min(width - pad.r - eTextW, Math.max(pad.l, exX - eTextW / 2));
      const eBoxY = tr.direction === 'LONG' ? Math.max(pad.t, exY - 26) : Math.min(pHeight - pad.b - 18, exY + 10);
      ctx.fillStyle = tr.direction === 'LONG' ? 'rgba(16, 185, 129, 0.92)' : 'rgba(244, 63, 94, 0.92)';
      ctx.fillRect(eBoxX, eBoxY, eTextW, 17);
      ctx.fillStyle = '#06111a';
      ctx.textAlign = 'left';
      ctx.fillText(eTag, eBoxX + 5, eBoxY + 12);

      // Exit Marker
      const outX = xOf(tr.exit_idx);
      const outY = yOf(tr.exit_price);
      ctx.fillStyle = tr.net_pnl >= 0 ? '#38bdf8' : '#fb923c';
      ctx.beginPath();
      ctx.arc(outX, outY, 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      const outTag = `${tr.exit_reason} @ ${tr.exit_time} (${tr.net_pnl >= 0 ? '+' : ''}₹${tr.net_pnl.toFixed(0)})`;
      const oTextW = ctx.measureText(outTag).width + 10;
      const oBoxX = Math.min(width - pad.r - oTextW, Math.max(pad.l, outX - oTextW / 2));
      const oBoxY = Math.max(pad.t + 18, Math.min(pHeight - pad.b - 18, outY - 24));
      ctx.fillStyle = tr.net_pnl >= 0 ? 'rgba(56, 189, 248, 0.94)' : 'rgba(251, 146, 60, 0.94)';
      ctx.fillRect(oBoxX, oBoxY, oTextW, 17);
      ctx.fillStyle = '#06111a';
      ctx.fillText(outTag, oBoxX + 5, oBoxY + 12);
    }

    // Volume Subplot
    let maxV = 1;
    for (const c of candles) if (c.volume > maxV) maxV = c.volume;

    vctx.strokeStyle = 'rgba(148, 163, 184, 0.08)';
    vctx.beginPath();
    vctx.moveTo(vpad.l, vpad.t);
    vctx.lineTo(width - vpad.r, vpad.t);
    vctx.moveTo(vpad.l, vHeight - vpad.b);
    vctx.lineTo(width - vpad.r, vHeight - vpad.b);
    vctx.stroke();

    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const x = xOf(i);
      const hBar = (c.volume / maxV) * vh;
      const highRvol = c.rvol >= 1.8;
      vctx.fillStyle = highRvol
        ? 'rgba(245, 158, 11, 0.92)'
        : c.close >= c.open
          ? 'rgba(16, 185, 129, 0.45)'
          : 'rgba(244, 63, 94, 0.45)';
      vctx.fillRect(x - candleW / 2, vHeight - vpad.b - hBar, candleW, Math.max(1, hBar));
    }

    vctx.fillStyle = '#94a3b8';
    vctx.font = '10px "JetBrains Mono", monospace';
    vctx.textAlign = 'left';
    vctx.fillText('VOL / RVOL>=1.8x (Amber)', vpad.l + 4, 12);
    vctx.fillText(`${Math.round(maxV / 1000)}k`, width - vpad.r + 6, vpad.t + 8);

    // Time ticks along bottom of Volume canvas
    const tickCount = Math.min(8, n);
    vctx.textAlign = 'center';
    vctx.fillStyle = '#64748b';
    for (let k = 0; k < tickCount; k++) {
      const idx = Math.floor((k * (n - 1)) / Math.max(1, tickCount - 1));
      vctx.fillText(candles[idx].time, xOf(idx), vHeight - 6);
    }

    // Hover Crosshair
    if (hoverIdx !== null && hoverIdx >= 0 && hoverIdx < n) {
      const hx = xOf(hoverIdx);
      const hy = yOf(candles[hoverIdx].close);
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = 'rgba(226, 232, 240, 0.45)';
      ctx.beginPath();
      ctx.moveTo(hx, pad.t);
      ctx.lineTo(hx, pHeight - pad.b);
      ctx.moveTo(pad.l, hy);
      ctx.lineTo(width - pad.r, hy);
      ctx.stroke();
      ctx.restore();

      vctx.save();
      vctx.setLineDash([3, 3]);
      vctx.strokeStyle = 'rgba(226, 232, 240, 0.45)';
      vctx.beginPath();
      vctx.moveTo(hx, vpad.t);
      vctx.lineTo(hx, vHeight - vpad.b);
      vctx.stroke();
      vctx.restore();
    }

    // Dedicated Liquidity Factor [0..100] + Bar Turnover (₹L) Sub-Panel
    const liqCanvas = liqCanvasRef.current;
    if (liqCanvas && showLiquidity) {
      const lHeight = 112;
      liqCanvas.width = width * dpr;
      liqCanvas.height = lHeight * dpr;
      liqCanvas.style.width = `${width}px`;
      liqCanvas.style.height = `${lHeight}px`;
      const lctx = liqCanvas.getContext('2d');
      if (lctx) {
        lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const lpad = { l: 58, r: 74, t: 18, b: 22 };
        const lh = lHeight - lpad.t - lpad.b;
        const yLiq = (score: number) => lpad.t + ((100 - Math.max(0, Math.min(100, score))) / 100) * lh;

        lctx.fillStyle = '#070b12';
        lctx.fillRect(0, 0, width, lHeight);

        // Background Rupee Turnover Bars
        let maxTurn = 1;
        for (const c of candles) {
          const tl = c.turnover_lakhs ?? (c.close * c.volume) / 100000;
          if (tl > maxTurn) maxTurn = tl;
        }
        for (let i = 0; i < n; i++) {
          const c = candles[i];
          const tl = c.turnover_lakhs ?? (c.close * c.volume) / 100000;
          const bH = (tl / maxTurn) * (lh * 0.72);
          lctx.fillStyle = 'rgba(56, 189, 248, 0.16)';
          lctx.fillRect(xOf(i) - candleW / 2, lHeight - lpad.b - bH, candleW, Math.max(1, bH));
        }

        // Reference Lines: 70 (DEEP) and 45 (MIN LIQUIDITY GATE)
        lctx.save();
        lctx.setLineDash([4, 4]);
        lctx.strokeStyle = 'rgba(16, 185, 129, 0.45)';
        lctx.beginPath();
        lctx.moveTo(lpad.l, yLiq(70));
        lctx.lineTo(width - lpad.r, yLiq(70));
        lctx.stroke();

        lctx.strokeStyle = 'rgba(245, 158, 11, 0.55)';
        lctx.beginPath();
        lctx.moveTo(lpad.l, yLiq(45));
        lctx.lineTo(width - lpad.r, yLiq(45));
        lctx.stroke();
        lctx.restore();

        // Liquidity Factor Area + Line
        lctx.beginPath();
        for (let i = 0; i < n; i++) {
          const sc = candles[i].liquidity_score ?? 50;
          const x = xOf(i);
          const y = yLiq(sc);
          if (i === 0) lctx.moveTo(x, y);
          else lctx.lineTo(x, y);
        }
        lctx.strokeStyle = '#38bdf8';
        lctx.lineWidth = 1.65;
        lctx.stroke();

        lctx.lineTo(xOf(n - 1), lHeight - lpad.b);
        lctx.lineTo(xOf(0), lHeight - lpad.b);
        lctx.closePath();
        lctx.fillStyle = 'rgba(56, 189, 248, 0.12)';
        lctx.fill();

        lctx.fillStyle = '#94a3b8';
        lctx.font = '10px "JetBrains Mono", monospace';
        lctx.textAlign = 'left';
        lctx.fillText('LIQUIDITY FACTOR [0-100] (RVOL + Turnover ₹L - Amihud Impact)', lpad.l + 4, 12);
        lctx.fillStyle = '#10b981';
        lctx.fillText('70 DEEP', width - lpad.r + 6, yLiq(70) + 3);
        lctx.fillStyle = '#f59e0b';
        lctx.fillText('45 MIN', width - lpad.r + 6, yLiq(45) + 3);

        if (hoverIdx !== null && hoverIdx >= 0 && hoverIdx < n) {
          const hx = xOf(hoverIdx);
          lctx.save();
          lctx.setLineDash([3, 3]);
          lctx.strokeStyle = 'rgba(226, 232, 240, 0.45)';
          lctx.beginPath();
          lctx.moveTo(hx, lpad.t);
          lctx.lineTo(hx, lHeight - lpad.b);
          lctx.stroke();
          lctx.restore();
        }
      }
    }
  }, [data, canvasWidth, hoverIdx, showOrb, showVwap, showEma, showSignals, showLiquidity]);

  useEffect(() => {
    drawCharts();
  }, [drawCharts]);

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!data?.candles?.length || !priceCanvasRef.current) return;
    const rect = priceCanvasRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const padL = 58;
    const padR = 74;
    const pw = rect.width - padL - padR;
    if (x < padL || x > rect.width - padR) {
      setHoverIdx(null);
      return;
    }
    const ratio = (x - padL) / Math.max(1, pw);
    const idx = Math.min(data.candles.length - 1, Math.max(0, Math.floor(ratio * data.candles.length)));
    setHoverIdx(idx);
  };

  const activeBar =
    hoverIdx !== null && data?.candles?.[hoverIdx]
      ? data.candles[hoverIdx]
      : data?.candles?.[data.candles.length - 1] || null;

  const presets = data?.presets || [
    { symbol: 'ADANIENT', date: '2024-11-21', tag: 'SHORT', label: 'ADANIENT · 2024-11-21' },
    { symbol: 'ICICIBANK', date: '2026-11-05', tag: 'LONG', label: 'ICICIBANK · 2026-11-05' },
    { symbol: 'RELIANCE', date: '2025-04-28', tag: 'LONG', label: 'RELIANCE · 2025-04-28' },
    { symbol: 'BAJFINANCE', date: '2026-03-30', tag: 'SHORT', label: 'BAJFINANCE · 2026-03-30' },
    { symbol: 'TATASTEEL', date: '2026-05-18', tag: 'LONG', label: 'TATASTEEL · 2026-05-18' },
    { symbol: 'SBIN', date: null, tag: 'LATEST', label: 'SBIN · Latest' },
    { symbol: 'TRENT', date: null, tag: 'ANY NSE', label: 'TRENT · Latest' },
  ];

  const diag = data?.diagnostics;
  const tr = data?.trade;

  return (
    <div className="workspace-scroll" style={{ padding: '12px 16px 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header & Any NSE Company Search Controls */}
      <div
        className="panel"
        style={{
          padding: '12px 14px',
          background: 'var(--bg-panel)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CandlestickChart size={20} color="var(--accent-cyan)" />
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-main)' }}>
                A8 Gap + ORB15 Strategy Visualizer (1-Minute Groww Candles · Any NSE Stock)
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                Inspect ORB15 box, cumulative VWAP, 1m EMA5/EMA15, RVOL (&gt;=1.8x), 1.2% hard SL, and causal A8 signals on any NSE company.
              </div>
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              fetchChart(symbolInput, dateInput || null, capitalInput);
            }}
            style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-active)',
                borderRadius: 7,
                padding: '5px 10px',
              }}
            >
              <Search size={14} color="var(--accent-cyan)" />
              <input
                aria-label="Any NSE Company Symbol"
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
                placeholder="Any NSE symbol (TRENT, RELIANCE...)"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: 'var(--text-main)',
                  fontFamily: 'var(--mono)',
                  fontSize: 12.5,
                  width: 170,
                }}
              />
            </div>

            <input
              aria-label="Session Date"
              type="date"
              value={dateInput}
              onChange={(e) => setDateInput(e.target.value)}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 7,
                padding: '5px 8px',
                color: 'var(--text-main)',
                fontFamily: 'var(--mono)',
                fontSize: 12,
              }}
            />

            <button
              type="button"
              onClick={() => {
                setDateInput('');
                fetchChart(symbolInput, null, capitalInput);
              }}
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 7,
                padding: '6px 10px',
                color: 'var(--text-muted)',
                fontSize: 11.5,
                cursor: 'pointer',
              }}
              title="Clear date and fetch latest active NSE session"
            >
              Latest Session
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11.5, color: 'var(--text-muted)' }}>
              <span>Cap ₹</span>
              <input
                aria-label="Simulation Capital"
                type="number"
                min={5000}
                step={1000}
                value={capitalInput}
                onChange={(e) => setCapitalInput(Number(e.target.value) || 10000)}
                style={{
                  width: 78,
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 7,
                  padding: '5px 6px',
                  color: 'var(--text-main)',
                  fontFamily: 'var(--mono)',
                  fontSize: 12,
                }}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              style={{
                background: 'linear-gradient(135deg, #0ea5e9, #2563eb)',
                border: 'none',
                borderRadius: 7,
                padding: '6px 14px',
                color: '#fff',
                fontWeight: 700,
                fontSize: 12,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              {loading && <Loader2 size={13} className="spin" />}
              Load Chart
            </button>
          </form>
        </div>

        {/* Presets + Any NSE Quick Watchlist */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--text-dim)', marginRight: 4 }}>7Y A8 TRADES:</span>
          {presets.map((p) => (
            <button
              key={`${p.symbol}-${p.date || 'latest'}`}
              onClick={() => {
                setSymbolInput(p.symbol);
                setDateInput(p.date || '');
                fetchChart(p.symbol, p.date, capitalInput);
              }}
              style={{
                background:
                  data?.symbol === p.symbol && (data?.session_date === p.date || (!p.date && dateInput === ''))
                    ? 'rgba(56, 189, 248, 0.18)'
                    : 'var(--bg-surface)',
                border: `1px solid ${
                  p.tag === 'SHORT'
                    ? 'rgba(244, 63, 94, 0.35)'
                    : p.tag === 'LONG'
                      ? 'rgba(16, 185, 129, 0.35)'
                      : 'var(--border-subtle)'
                }`,
                borderRadius: 6,
                padding: '3px 8px',
                fontSize: 11,
                fontFamily: 'var(--mono)',
                color: 'var(--text-main)',
                cursor: 'pointer',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--text-dim)', marginRight: 4 }}>ANY NSE QUICK PICKS:</span>
            {POPULAR_ANY_NSE.map((sym) => (
              <button
                key={sym}
                onClick={() => {
                  setSymbolInput(sym);
                  fetchChart(sym, dateInput || null, capitalInput);
                }}
                style={{
                  background: data?.symbol === sym ? 'rgba(56, 189, 248, 0.16)' : 'transparent',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 5,
                  padding: '2px 7px',
                  fontSize: 10.5,
                  fontFamily: 'var(--mono)',
                  color: data?.symbol === sym ? 'var(--accent-cyan)' : 'var(--text-muted)',
                  cursor: 'pointer',
                }}
              >
                {sym}
              </button>
            ))}
          </div>

          {/* Overlay toggles */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11.5, color: 'var(--text-muted)' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={showOrb} onChange={(e) => setShowOrb(e.target.checked)} /> ORB15 Box
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={showVwap} onChange={(e) => setShowVwap(e.target.checked)} /> VWAP
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={showEma} onChange={(e) => setShowEma(e.target.checked)} /> EMA 5/15
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={showSignals} onChange={(e) => setShowSignals(e.target.checked)} /> A8 Signals & SL
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={showLiquidity} onChange={(e) => setShowLiquidity(e.target.checked)} /> Liquidity Factor
            </label>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                cursor: 'pointer',
                color: autoRefresh && !dateInput ? 'var(--accent-green)' : 'var(--text-dim)',
              }}
              title="Auto-refresh every 10s when viewing Latest Session"
            >
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> Live 10s
            </label>
          </div>
        </div>
      </div>

      {/* Diagnostic / Trade Result Strip */}
      {data && data.status === 'SUCCESS' && diag && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 10,
          }}
        >
          {/* Left: Session Context */}
          <div
            style={{
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 10,
              padding: '10px 12px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span className="mono" style={{ fontSize: 14, fontWeight: 800, color: 'var(--accent-cyan)' }}>
                NSE:{data.symbol} · {data.session_date}
              </span>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-dim)' }}>
                {data.interval_used || '1m'} · {data.source}
              </span>
            </div>
            <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 11.5, color: 'var(--text-muted)' }}>
              <span>PrevC: <strong style={{ color: 'var(--text-main)' }}>₹{diag.prev_close}</strong></span>
              <span>Open: <strong style={{ color: 'var(--text-main)' }}>₹{diag.day_open}</strong></span>
              <span>
                Gap:{' '}
                <strong style={{ color: Math.abs(diag.gap_pct) >= 1.8 ? 'var(--accent-amber)' : 'var(--text-main)' }}>
                  {diag.gap_pct >= 0 ? '+' : ''}
                  {diag.gap_pct}%
                </strong>
              </span>
              <span>ATR14: <strong style={{ color: diag.atr14_pct <= 3.5 ? 'var(--accent-green)' : 'var(--accent-red)' }}>{diag.atr14_pct}%</strong></span>
              <span>Max RVOL: <strong>{diag.max_rvol_0930_1100}x</strong></span>
              {diag.avg_liquidity_score !== undefined && (
                <span>
                  Liq:{' '}
                  <strong
                    style={{
                      color:
                        diag.avg_liquidity_score >= 70
                          ? 'var(--accent-green)'
                          : diag.avg_liquidity_score >= 45
                            ? 'var(--accent-cyan)'
                            : 'var(--accent-red)',
                    }}
                  >
                    {diag.avg_liquidity_score}/100 ({diag.liquidity_regime || 'NORMAL'})
                  </strong>{' '}
                  · <strong>₹{diag.total_turnover_cr ?? 0}Cr</strong>
                </span>
              )}
            </div>
          </div>

          {/* Middle/Right: Trade Triggered or Gate Breakdown */}
          {tr ? (
            <div
              style={{
                background: tr.net_pnl >= 0 ? 'rgba(16, 185, 129, 0.08)' : 'rgba(244, 63, 94, 0.08)',
                border: `1px solid ${tr.net_pnl >= 0 ? 'rgba(16, 185, 129, 0.38)' : 'rgba(244, 63, 94, 0.38)'}`,
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 12.5 }}>
                  {tr.direction === 'LONG' ? <TrendingUp size={15} color="#10b981" /> : <TrendingDown size={15} color="#f43f5e" />}
                  A8 {tr.direction} EXECUTED ({tr.qty} qty)
                </span>
                <span
                  className="mono"
                  style={{
                    fontWeight: 800,
                    fontSize: 14,
                    color: tr.net_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}
                >
                  {tr.net_pnl >= 0 ? '+' : ''}₹{tr.net_pnl.toFixed(2)} ({tr.ret_pct >= 0 ? '+' : ''}
                  {tr.ret_pct}%) Net
                </span>
              </div>
              <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 11.5, color: 'var(--text-muted)' }}>
                <span>Entry: <strong style={{ color: 'var(--text-main)' }}>₹{tr.entry_price} @ {tr.entry_time}</strong></span>
                <span>1.2% SL: <strong style={{ color: '#f43f5e' }}>₹{tr.stop_loss}</strong></span>
                <span>Exit: <strong style={{ color: 'var(--text-main)' }}>₹{tr.exit_price} @ {tr.exit_time} ({tr.exit_reason})</strong></span>
                <span>Groww Fees+Slip: <strong>₹{tr.fees}</strong></span>
              </div>
            </div>
          ) : (
            <div
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent-amber)', marginBottom: 6 }}>
                No A8 Trade Triggered on {data.session_date} — Gate Audit:
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 11 }}>
                {[
                  ['|Gap| >= 1.8%', diag.gates.gap_ge_1_8_pct],
                  ['ATR14 <= 3.5%', diag.gates.atr_le_3_5_pct],
                  ['NIFTY vs SMA20', diag.gates.macro_nifty_sma20_ok],
                  ['ORB15 Break', diag.gates.orb15_break_ok],
                  ['RVOL >= 1.8x', diag.gates.rvol_ge_1_8x_ok],
                  ['VWAP Align', diag.gates.vwap_align_ok],
                  ['EMA5/15 Align', diag.gates.ema5_15_align_ok],
                  ...(diag.gates.liquidity_ge_45_ok !== undefined ? [['Liquidity >= 45', diag.gates.liquidity_ge_45_ok]] : []),
                ].map(([label, ok]) => (
                  <span
                    key={String(label)}
                    className="mono"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '2px 7px',
                      borderRadius: 5,
                      background: ok ? 'rgba(16, 185, 129, 0.12)' : 'rgba(244, 63, 94, 0.1)',
                      color: ok ? '#34d399' : '#fda4af',
                    }}
                  >
                    {ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
                    {label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Error / Quarantined / Empty Banner */}
      {data && data.status !== 'SUCCESS' && (
        <div
          style={{
            background: 'rgba(244, 63, 94, 0.08)',
            border: '1px solid rgba(244, 63, 94, 0.35)',
            borderRadius: 10,
            padding: '14px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            color: '#fecdd3',
            fontSize: 13,
          }}
        >
          <ShieldAlert size={18} color="#f43f5e" />
          <div>
            <strong>{data.symbol || symbolInput}:</strong> {data.reason || data.error || 'Unable to load candles for that symbol/date.'}
          </div>
        </div>
      )}

      {/* Main Chart Container */}
      <div
        ref={containerRef}
        style={{
          background: '#070b12',
          border: '1px solid var(--border-subtle)',
          borderRadius: 10,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Live Crosshair OHLCV Readout Strip */}
        {activeBar && (
          <div
            className="mono"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 14,
              padding: '8px 14px',
              background: 'rgba(13, 21, 34, 0.92)',
              borderBottom: '1px solid var(--border-subtle)',
              fontSize: 11.5,
            }}
          >
            <span style={{ color: 'var(--accent-cyan)', fontWeight: 700 }}>{activeBar.time} IST</span>
            <span>O: <strong>₹{activeBar.open.toFixed(2)}</strong></span>
            <span>H: <strong style={{ color: '#10b981' }}>₹{activeBar.high.toFixed(2)}</strong></span>
            <span>L: <strong style={{ color: '#f43f5e' }}>₹{activeBar.low.toFixed(2)}</strong></span>
            <span>C: <strong>₹{activeBar.close.toFixed(2)}</strong></span>
            <span style={{ color: '#38bdf8' }}>VWAP: ₹{activeBar.vwap.toFixed(2)}</span>
            <span style={{ color: '#4ade80' }}>EMA5: ₹{activeBar.ema5.toFixed(2)}</span>
            <span style={{ color: '#f59e0b' }}>EMA15: ₹{activeBar.ema15.toFixed(2)}</span>
            <span>Vol: <strong>{activeBar.volume.toLocaleString('en-IN')}</strong></span>
            <span style={{ color: activeBar.rvol >= 1.8 ? '#f59e0b' : 'var(--text-muted)' }}>
              RVOL: <strong>{activeBar.rvol.toFixed(2)}x</strong>
            </span>
            {activeBar.liquidity_score !== undefined && (
              <span>
                Liq:{' '}
                <strong
                  style={{
                    color:
                      activeBar.liquidity_score >= 70
                        ? '#10b981'
                        : activeBar.liquidity_score >= 45
                          ? '#38bdf8'
                          : '#f43f5e',
                  }}
                >
                  {activeBar.liquidity_score.toFixed(1)}/100 ({activeBar.liquidity_tier || 'NORMAL'})
                </strong>
              </span>
            )}
            {activeBar.turnover_lakhs !== undefined && (
              <span>Turn: <strong>₹{activeBar.turnover_lakhs.toFixed(1)}L</strong></span>
            )}
            {activeBar.amihud_impact !== undefined && (
              <span style={{ color: 'var(--text-muted)' }}>Impact: {activeBar.amihud_impact.toFixed(2)} bps/L</span>
            )}
          </div>
        )}

        <canvas
          ref={priceCanvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverIdx(null)}
          style={{ display: 'block', cursor: 'crosshair' }}
        />
        <canvas
          ref={volCanvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverIdx(null)}
          style={{ display: 'block', borderTop: '1px solid rgba(148, 163, 184, 0.1)', cursor: 'crosshair' }}
        />
        {showLiquidity && (
          <canvas
            ref={liqCanvasRef}
            onMouseMove={handleMouseMove}
            onMouseLeave={() => setHoverIdx(null)}
            style={{ display: 'block', borderTop: '1px solid rgba(148, 163, 184, 0.1)', cursor: 'crosshair' }}
          />
        )}
      </div>
    </div>
  );
};
