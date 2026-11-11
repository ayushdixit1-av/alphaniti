import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  CandlestickData,
  HistogramData,
  LineData,
  UTCTimestamp,
  SeriesMarker,
  Time,
  IPriceLine,
} from 'lightweight-charts';
import {
  Camera,
  Maximize2,
  Minimize2,
  Scan,
  Crosshair,
  SlidersHorizontal,
  ZoomIn,
  ZoomOut,
  Magnet,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  RotateCcw,
} from 'lucide-react';
import { AuditTrade, CandleBar, CockpitSymbolData } from '../../types/api';
import {
  computeVolumeProfile,
  findNearestBarIndex,
  NormalizedBar,
  normalizeAndComputeIndicators,
} from '../../utils/chartMath';

export interface ActiveBracketOverlay {
  direction: 'LONG' | 'SHORT';
  entryLow: number;
  entryHigh: number;
  entryMid: number;
  stopLoss: number;
  profitFloor?: number | null;
  target1: number;
  target2?: number | null;
}

interface SuperChartProps {
  symbol: string;
  timeframe: string;
  onTimeframeChange: (tf: string) => void;
  candles: CandleBar[];
  sessionDate?: string;
  isLiveToday?: boolean;
  cockpit: CockpitSymbolData | null;
  trades: AuditTrade[];
  externalReplayIndex?: number | null;
  onExternalReplayIndexChange?: (idx: number | null) => void;
  overrideBracket?: ActiveBracketOverlay | null;
}

export const SuperChart: React.FC<SuperChartProps> = ({
  symbol,
  timeframe,
  onTimeframeChange,
  candles,
  sessionDate,
  isLiveToday,
  cockpit,
  trades,
  externalReplayIndex,
  onExternalReplayIndexChange,
  overrideBracket,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapUpperRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapLowerRef = useRef<ISeriesApi<'Line'> | null>(null);
  const liquiditySeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const barMapRef = useRef<Map<number, NormalizedBar>>(new Map());
  const prevSymbolRef = useRef<string>(symbol);

  const [showIndicators, setShowIndicators] = useState(true);
  const [showLiquidity, setShowLiquidity] = useState(true);
  const [showVP, setShowVP] = useState(true);
  const [showDB, setShowDB] = useState(true);
  const [showHL, setShowHL] = useState(true);
  const [showLevels, setShowLevels] = useState(true);
  const [magnetMode, setMagnetMode] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);

  // Restore full-window maximize on Escape key & lock background scroll when maximized
  useEffect(() => {
    if (!isMaximized) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsMaximized(false);
      }
    };
    window.addEventListener('keydown', onKey);
    const t = setTimeout(() => chartRef.current?.timeScale().fitContent(), 60);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKey);
      clearTimeout(t);
    };
  }, [isMaximized]);

  // Built-in Replay state (used when externalReplayIndex is not explicitly provided)
  const isControlledReplay = externalReplayIndex !== undefined;
  const [internalReplayIdx, setInternalReplayIdx] = useState<number | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playSpeedMs, setPlaySpeedMs] = useState<number>(450);

  const effectiveReplayIdx = isControlledReplay ? externalReplayIndex ?? null : internalReplayIdx;
  const setEffectiveReplayIdx = (next: number | null) => {
    if (isControlledReplay && onExternalReplayIndexChange) {
      onExternalReplayIndexChange(next);
    } else {
      setInternalReplayIdx(next);
    }
  };

  const [crosshairBar, setCrosshairBar] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    vol: number;
    vwap: number;
    pct: number;
    liquidityScore: number;
    liquidityTier: 'DEEP' | 'NORMAL' | 'THIN';
    turnoverLakhs: number;
    rvol: number;
  } | null>(null);

  const allNormalized = useMemo(() => normalizeAndComputeIndicators(candles), [candles]);

  // Reset internal replay cleanly when symbol or timeframe changes
  useEffect(() => {
    setIsPlaying(false);
    if (!isControlledReplay) {
      setInternalReplayIdx(null);
    }
  }, [symbol, timeframe, isControlledReplay]);

  // Causal slice for Synchronized Replay
  const visibleBars = useMemo(() => {
    if (effectiveReplayIdx === null || effectiveReplayIdx < 0 || allNormalized.length === 0) {
      return allNormalized;
    }
    const clamped = Math.min(allNormalized.length - 1, Math.max(0, effectiveReplayIdx));
    return allNormalized.slice(0, clamped + 1);
  }, [allNormalized, effectiveReplayIdx]);

  // Playback timer
  useEffect(() => {
    if (!isPlaying || allNormalized.length === 0) return;
    const id = setInterval(() => {
      const cur = effectiveReplayIdx ?? 0;
      if (cur >= allNormalized.length - 1) {
        setIsPlaying(false);
        return;
      }
      setEffectiveReplayIdx(cur + 1);
    }, playSpeedMs);
    return () => clearInterval(id);
  }, [isPlaying, playSpeedMs, effectiveReplayIdx, allNormalized.length]);

  // Compute causal Session High/Low & Volume Profile across visibleBars
  const sessionStats = useMemo(() => {
    if (visibleBars.length === 0) {
      return { high: 0, low: 0, lastClose: 0, lastVwap: 0, avgLiquidity: 0, totalTurnoverCr: 0, vp: null };
    }
    let sHigh = -Infinity;
    let sLow = Infinity;
    let sumLiq = 0;
    let sumTurnL = 0;
    for (const b of visibleBars) {
      if (b.high > sHigh) sHigh = b.high;
      if (b.low < sLow) sLow = b.low;
      sumLiq += b.liquidityScore || 0;
      sumTurnL += b.turnoverLakhs || 0;
    }
    const last = visibleBars[visibleBars.length - 1];
    const vp = computeVolumeProfile(visibleBars, 32);
    return {
      high: sHigh,
      low: sLow,
      lastClose: last.close,
      lastVwap: last.vwap,
      avgLiquidity: Number((sumLiq / visibleBars.length).toFixed(1)),
      totalTurnoverCr: Number((sumTurnL / 100).toFixed(2)),
      vp,
    };
  }, [visibleBars]);

  // Resolve bracket levels (from Replay override or Cockpit Execution Brief)
  const resolvedBracket: ActiveBracketOverlay | null = useMemo(() => {
    if (overrideBracket) return overrideBracket;
    const eb = cockpit?.execution_brief;
    if (!eb) return null;
    const parts = eb.entry_zone.replace(/,/g, '').split('-').map((s) => parseFloat(s.trim()));
    const eLow = Number.isFinite(parts[0]) ? parts[0] : cockpit?.ltp || 0;
    const eHigh = Number.isFinite(parts[1]) ? parts[1] : eLow;
    const eMid = (eLow + eHigh) / 2;
    const isShort = eb.stop_loss > eMid;
    return {
      direction: isShort ? 'SHORT' : 'LONG',
      entryLow: eLow,
      entryHigh: eHigh,
      entryMid: eMid,
      stopLoss: eb.stop_loss,
      profitFloor: null,
      target1: eb.target_1,
      target2: eb.target_2,
    };
  }, [overrideBracket, cockpit]);

  // Initialize TradingView Lightweight Chart once
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { color: '#060a11' },
        textColor: '#94a3b8',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10.5,
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.06)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.06)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(56, 189, 248, 0.55)',
          width: 1,
          style: 2,
          labelBackgroundColor: '#0f172a',
        },
        horzLine: {
          color: 'rgba(56, 189, 248, 0.55)',
          width: 1,
          style: 2,
          labelBackgroundColor: '#0f172a',
        },
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
      rightPriceScale: {
        borderColor: 'rgba(148, 163, 184, 0.16)',
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: 'rgba(148, 163, 184, 0.16)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 10,
        barSpacing: 7,
        minBarSpacing: 2,
        tickMarkFormatter: (t: number) => {
          const d = new Date((t + 19800) * 1000);
          return timeframe === 'D' ? d.toISOString().slice(0, 10) : d.toISOString().slice(11, 16);
        },
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: true,
      borderUpColor: '#10b981',
      borderDownColor: '#f43f5e',
      wickUpColor: '#34d399',
      wickDownColor: '#fb7185',
    });

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    chart.priceScale('').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const emaSeries = chart.addLineSeries({
      color: '#22d3ee',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'EMA9',
    });

    const vwapSeries = chart.addLineSeries({
      color: '#e879f9',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'VWAP',
    });

    const vwapUpper = chart.addLineSeries({
      color: 'rgba(192, 132, 252, 0.4)',
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const vwapLower = chart.addLineSeries({
      color: 'rgba(192, 132, 252, 0.4)',
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const liquiditySeries = chart.addLineSeries({
      color: '#38bdf8',
      lineWidth: 1,
      lineStyle: 0,
      priceScaleId: 'liq',
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'LIQ%',
    });
    chart.priceScale('liq').applyOptions({
      scaleMargins: { top: 0.72, bottom: 0.14 },
      borderVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volSeriesRef.current = volSeries;
    emaSeriesRef.current = emaSeries;
    vwapSeriesRef.current = vwapSeries;
    vwapUpperRef.current = vwapUpper;
    vwapLowerRef.current = vwapLower;
    liquiditySeriesRef.current = liquiditySeries;

    // Single Crosshair subscription (no stacked listeners)
    const onCrosshair = (param: any) => {
      if (!param.time) {
        setCrosshairBar(null);
        return;
      }
      const b = barMapRef.current.get(Number(param.time));
      if (!b) {
        setCrosshairBar(null);
        return;
      }
      const ist = new Date((b.ts + 19800) * 1000);
      const pct = b.open > 0 ? ((b.close - b.open) / b.open) * 100 : 0;
      setCrosshairBar({
        time: ist.toISOString().slice(11, 16),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        vol: b.volume || 0,
        vwap: b.vwap,
        pct,
        liquidityScore: b.liquidityScore,
        liquidityTier: b.liquidityTier,
        turnoverLakhs: b.turnoverLakhs,
        rvol: b.rvol,
      });
    };
    chart.subscribeCrosshairMove(onCrosshair);

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    });
    ro.observe(el);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshair);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // Update Magnet mode & timeFormatter cleanly
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.applyOptions({
      crosshair: {
        mode: magnetMode ? CrosshairMode.Magnet : CrosshairMode.Normal,
      },
      timeScale: {
        tickMarkFormatter: (t: number) => {
          const d = new Date((t + 19800) * 1000);
          return timeframe === 'D' ? d.toISOString().slice(0, 10) : d.toISOString().slice(11, 16);
        },
      },
      localization: {
        timeFormatter: (t: number) => {
          const d = new Date((t + 19800) * 1000);
          return timeframe === 'D' ? d.toISOString().slice(0, 10) : d.toISOString().slice(11, 16);
        },
      },
    });
  }, [magnetMode, timeframe]);

  // Populate series, markers & distinguishable price lines
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const vs = volSeriesRef.current;
    const es = emaSeriesRef.current;
    const vws = vwapSeriesRef.current;
    const vwu = vwapUpperRef.current;
    const vwl = vwapLowerRef.current;
    const ls = liquiditySeriesRef.current;
    const chart = chartRef.current;
    if (!cs || !vs || !es || !vws || !vwu || !vwl || !ls || !chart) return;

    const map = new Map<number, NormalizedBar>();
    const formatted: CandlestickData<Time>[] = [];
    const volData: HistogramData<Time>[] = [];
    const emaData: LineData<Time>[] = [];
    const vwapData: LineData<Time>[] = [];
    const upperData: LineData<Time>[] = [];
    const lowerData: LineData<Time>[] = [];
    const liqData: LineData<Time>[] = [];

    for (const bar of visibleBars) {
      map.set(bar.ts, bar);
      const t = bar.ts as UTCTimestamp;
      formatted.push({ time: t, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
      const isUp = bar.close >= bar.open;
      volData.push({
        time: t,
        value: bar.volume || 0,
        color: isUp ? 'rgba(16, 185, 129, 0.35)' : 'rgba(244, 63, 94, 0.35)',
      });
      emaData.push({ time: t, value: bar.ema9 });
      vwapData.push({ time: t, value: bar.vwap });
      upperData.push({ time: t, value: bar.vwapUpper });
      lowerData.push({ time: t, value: bar.vwapLower });
      liqData.push({ time: t, value: bar.liquidityScore });
    }
    barMapRef.current = map;

    cs.setData(formatted);
    vs.setData(volData);
    es.setData(showIndicators ? emaData : []);
    vws.setData(showIndicators ? vwapData : []);
    vwu.setData(showIndicators ? upperData : []);
    vwl.setData(showIndicators ? lowerData : []);
    ls.setData(showLiquidity ? liqData : []);

    // Causal Execution Markers (only show exit marker once replay reaches exit bar)
    const markers: SeriesMarker<Time>[] = [];
    const symTrades = trades.filter((t) => t.symbol.toUpperCase() === symbol.toUpperCase());
    if (symTrades.length > 0 && visibleBars.length > 0) {
      const lastVisibleTs = visibleBars[visibleBars.length - 1].ts;
      for (const tr of symTrades) {
        const isShort = tr.strategy.includes('SHORT');
        const entryIdx = findNearestBarIndex(allNormalized, tr.entry_time);
        const exitIdx = findNearestBarIndex(allNormalized, tr.exit_time);
        const entryBar = allNormalized[entryIdx];
        const exitBar = allNormalized[exitIdx];

        if (entryBar && entryBar.ts <= lastVisibleTs) {
          markers.push({
            time: entryBar.ts as UTCTimestamp,
            position: isShort ? 'aboveBar' : 'belowBar',
            color: isShort ? '#f43f5e' : '#10b981',
            shape: isShort ? 'arrowDown' : 'arrowUp',
            text: `${isShort ? 'SHORT' : 'BUY'} ₹${tr.entry_price.toFixed(1)}`,
          });
        }
        if (exitBar && exitBar.ts <= lastVisibleTs) {
          markers.push({
            time: exitBar.ts as UTCTimestamp,
            position: isShort ? 'belowBar' : 'aboveBar',
            color: '#38bdf8',
            shape: isShort ? 'arrowUp' : 'arrowDown',
            text: `EXIT ${tr.net_pnl >= 0 ? '+' : ''}₹${tr.net_pnl.toFixed(1)}`,
          });
        }
      }
      markers.sort((a, b) => (a.time as number) - (b.time as number));
    }
    cs.setMarkers(markers);

    // Clear old price lines
    for (const pl of priceLinesRef.current) {
      try {
        cs.removePriceLine(pl);
      } catch {
        // ignore
      }
    }
    priceLinesRef.current = [];

    // 1. Session High & Session Low
    if (showHL && sessionStats.high > 0 && sessionStats.low < Infinity) {
      priceLinesRef.current.push(
        cs.createPriceLine({
          price: sessionStats.high,
          color: 'rgba(45, 212, 191, 0.75)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'S-HIGH',
        }),
        cs.createPriceLine({
          price: sessionStats.low,
          color: 'rgba(251, 113, 133, 0.75)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'S-LOW',
        })
      );
    }

    // 2. Volume Profile Key Levels (VAH / POC / VAL)
    if (showVP && sessionStats.vp) {
      const vp = sessionStats.vp;
      priceLinesRef.current.push(
        cs.createPriceLine({
          price: vp.vahPrice,
          color: 'rgba(148, 163, 184, 0.65)',
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: true,
          title: 'VAH',
        }),
        cs.createPriceLine({
          price: vp.pocPrice,
          color: '#f59e0b',
          lineWidth: 2,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'POC',
        }),
        cs.createPriceLine({
          price: vp.valPrice,
          color: 'rgba(148, 163, 184, 0.65)',
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: true,
          title: 'VAL',
        })
      );
    }

    // 3. Visually Distinguishable Entry, Stop Loss, Profit Floor & Targets
    if (showLevels && resolvedBracket) {
      priceLinesRef.current.push(
        cs.createPriceLine({
          price: resolvedBracket.entryMid,
          color: '#38bdf8',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: 'ENTRY',
        }),
        cs.createPriceLine({
          price: resolvedBracket.stopLoss,
          color: '#f43f5e',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: 'SL -1.0R',
        }),
        cs.createPriceLine({
          price: resolvedBracket.target1,
          color: '#10b981',
          lineWidth: 2,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'T1 +1.0R',
        })
      );

      if (resolvedBracket.target2) {
        priceLinesRef.current.push(
          cs.createPriceLine({
            price: resolvedBracket.target2,
            color: '#34d399',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: 'T2 +1.8R',
          })
        );
      }

      if (resolvedBracket.profitFloor) {
        priceLinesRef.current.push(
          cs.createPriceLine({
            price: resolvedBracket.profitFloor,
            color: '#f59e0b',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'FLOOR',
          })
        );
      }
    }

    if (prevSymbolRef.current !== symbol || effectiveReplayIdx === null) {
      chart.timeScale().fitContent();
      prevSymbolRef.current = symbol;
    }
  }, [
    visibleBars,
    allNormalized,
    symbol,
    showIndicators,
    showLiquidity,
    showVP,
    showHL,
    showLevels,
    sessionStats,
    resolvedBracket,
    trades,
    effectiveReplayIdx,
  ]);

  // High-FPS Synced Overlay Canvas: Volume Profile Histogram + VA Bracket + Risk/Reward Zones + Dalton Base
  useEffect(() => {
    let rafId = 0;
    const drawOverlay = () => {
      const canvas = overlayCanvasRef.current;
      const container = containerRef.current;
      const cs = candleSeriesRef.current;
      if (!canvas || !container || !cs) return;

      const dpr = window.devicePixelRatio || 1;
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);
      const plotRight = w - 68;

      // 1. Shaded Entry / Risk / Reward Zones
      if (showLevels && resolvedBracket) {
        const yEntry = cs.priceToCoordinate(resolvedBracket.entryMid);
        const yELow = cs.priceToCoordinate(resolvedBracket.entryLow);
        const yEHigh = cs.priceToCoordinate(resolvedBracket.entryHigh);
        const ySL = cs.priceToCoordinate(resolvedBracket.stopLoss);
        const yT1 = cs.priceToCoordinate(resolvedBracket.target1);

        if (yEntry !== null && ySL !== null) {
          const topR = Math.min(yEntry, ySL);
          const heightR = Math.abs(ySL - yEntry);
          ctx.fillStyle = 'rgba(244, 63, 94, 0.065)';
          ctx.fillRect(0, topR, plotRight, heightR);
        }

        if (yEntry !== null && yT1 !== null) {
          const topG = Math.min(yEntry, yT1);
          const heightG = Math.abs(yT1 - yEntry);
          ctx.fillStyle = 'rgba(16, 185, 129, 0.055)';
          ctx.fillRect(0, topG, plotRight, heightG);
        }

        if (yELow !== null && yEHigh !== null) {
          const topE = Math.min(yELow, yEHigh);
          const heightE = Math.max(4, Math.abs(yEHigh - yELow));
          ctx.fillStyle = 'rgba(56, 189, 248, 0.14)';
          ctx.fillRect(0, topE, plotRight, heightE);
        }
      }

      // 2. Dalton Base Zone (DB)
      if (showDB && cockpit?.dalton_base) {
        const yHigh = cs.priceToCoordinate(cockpit.dalton_base.base_high);
        const yLow = cs.priceToCoordinate(cockpit.dalton_base.base_low);
        if (yHigh !== null && yLow !== null) {
          const top = Math.min(yHigh, yLow);
          const boxH = Math.max(4, Math.abs(yLow - yHigh));
          const left = w * 0.24;
          const boxW = w * 0.44;
          ctx.fillStyle = 'rgba(56, 189, 248, 0.045)';
          ctx.fillRect(left, top, boxW, boxH);
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.3)';
          ctx.setLineDash([4, 4]);
          ctx.strokeRect(left, top, boxW, boxH);
          ctx.setLineDash([]);
          ctx.fillStyle = 'rgba(56, 189, 248, 0.85)';
          ctx.font = "600 9.5px 'JetBrains Mono'";
          ctx.fillText(
            `DALTON BASE ${cockpit.dalton_base.base_low.toFixed(1)}–${cockpit.dalton_base.base_high.toFixed(1)}`,
            left + 6,
            top + 12
          );
        }
      }

      // 3. Upgraded Dual-Tone Volume Profile with Exact Bin Height + 70% VA Bracket
      if (showVP && sessionStats.vp) {
        const { bins, vahPrice, valPrice } = sessionStats.vp;
        const maxVol = Math.max(...bins.map((b) => b.totalVol), 1);
        const maxBarW = Math.min(145, w * 0.18);

        // Shaded 70% Value Area corridor
        const yVah = cs.priceToCoordinate(vahPrice);
        const yVal = cs.priceToCoordinate(valPrice);
        if (yVah !== null && yVal !== null) {
          const vaTop = Math.min(yVah, yVal);
          const vaH = Math.abs(yVal - yVah);
          ctx.fillStyle = 'rgba(56, 189, 248, 0.03)';
          ctx.fillRect(plotRight - maxBarW, vaTop, maxBarW, vaH);
          // VA vertical bracket on right edge
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.55)';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(plotRight - 2, vaTop);
          ctx.lineTo(plotRight - 2, vaTop + vaH);
          ctx.stroke();
        }

        for (const bin of bins) {
          const yTop = cs.priceToCoordinate(bin.high);
          const yBot = cs.priceToCoordinate(bin.low);
          if (yTop === null || yBot === null) continue;
          const top = Math.min(yTop, yBot);
          const binH = Math.max(2, Math.abs(yBot - yTop) - 1);
          if (top + binH < 8 || top > h - 22) continue;

          const totalW = (bin.totalVol / maxVol) * maxBarW;
          const buyRatio = bin.totalVol > 0 ? bin.buyVol / bin.totalVol : 0.5;
          const buyW = totalW * buyRatio;
          const sellW = totalW - buyW;

          if (bin.isPoc) {
            ctx.fillStyle = 'rgba(245, 158, 11, 0.85)';
            ctx.fillRect(plotRight - 4 - totalW, top, totalW, binH);
            ctx.strokeStyle = '#fbbf24';
            ctx.lineWidth = 1;
            ctx.strokeRect(plotRight - 4 - totalW, top, totalW, binH);
          } else if (bin.isValueArea) {
            // Sell segment (left portion) + Buy segment (right portion near price axis)
            ctx.fillStyle = 'rgba(244, 63, 94, 0.34)';
            ctx.fillRect(plotRight - 4 - totalW, top, sellW, binH);
            ctx.fillStyle = 'rgba(16, 185, 129, 0.44)';
            ctx.fillRect(plotRight - 4 - buyW, top, buyW, binH);
          } else {
            ctx.fillStyle = 'rgba(148, 163, 184, 0.22)';
            ctx.fillRect(plotRight - 4 - totalW, top, totalW, binH);
          }
        }
      }

      ctx.restore();
    };

    const tick = () => {
      drawOverlay();
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [cockpit, showVP, showDB, showLevels, resolvedBracket, sessionStats]);

  // Toolbar Zoom In / Zoom Out handlers
  const adjustZoom = (factor: number) => {
    const chart = chartRef.current;
    if (!chart) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const span = range.to - range.from;
    const center = (range.from + range.to) / 2;
    const newSpan = Math.max(10, Math.min(allNormalized.length * 1.5, span * factor));
    chart.timeScale().setVisibleLogicalRange({
      from: center - newSpan / 2,
      to: center + newSpan / 2,
    });
  };

  const handleToggleReplay = () => {
    if (effectiveReplayIdx === null) {
      const symTrade = trades.find((t) => t.symbol.toUpperCase() === symbol.toUpperCase());
      const startIdx = symTrade
        ? Math.max(2, findNearestBarIndex(allNormalized, symTrade.entry_time) - 2)
        : Math.min(allNormalized.length - 1, 5);
      setEffectiveReplayIdx(startIdx);
      setIsPlaying(false);
    } else {
      setIsPlaying(false);
      setEffectiveReplayIdx(null);
    }
  };

  const handleScreenshot = () => {
    const chart = chartRef.current;
    if (!chart) return;
    const sc = chart.takeScreenshot();
    const link = document.createElement('a');
    link.download = `${symbol}_${timeframe}_AlphaNiti.png`;
    link.href = sc.toDataURL('image/png');
    link.click();
  };

  const currentReplayBar =
    effectiveReplayIdx !== null && visibleBars.length > 0
      ? visibleBars[visibleBars.length - 1]
      : null;

  const vwapDistPct =
    sessionStats.lastVwap > 0
      ? ((sessionStats.lastClose - sessionStats.lastVwap) / sessionStats.lastVwap) * 100
      : 0;

  return (
    <div
      style={
        isMaximized
          ? {
              position: 'fixed',
              inset: 0,
              zIndex: 180,
              background: '#06090e',
              display: 'flex',
              flexDirection: 'column',
              width: '100vw',
              height: '100vh',
            }
          : { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 345 }
      }
    >
      {/* Compact Ticker Banner in Full-Window Mode */}
      {isMaximized && (
        <div
          style={{
            padding: '8px 14px',
            borderBottom: '1px solid var(--border-subtle)',
            background: '#0a1019',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 15, fontWeight: 800 }}>
              {symbol === 'NIFTY' || symbol === 'BANKNIFTY' ? symbol : `${symbol}.NS`}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {cockpit?.company_name || 'AlphaNiti SuperChart'}
            </span>
            {cockpit && (
              <>
                <span className="mono" style={{ fontSize: 15, fontWeight: 800, color: 'var(--accent-green)' }}>
                  ₹{cockpit.ltp.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 11.5,
                    fontWeight: 700,
                    color: cockpit.change_pts >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}
                >
                  {cockpit.change_pts >= 0 ? '+' : ''}
                  {cockpit.change_pts.toFixed(2)} ({cockpit.change_pct >= 0 ? '+' : ''}
                  {cockpit.change_pct.toFixed(2)}%)
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 10.5,
                    padding: '2px 8px',
                    borderRadius: 5,
                    background: 'rgba(56,189,248,0.12)',
                    border: '1px solid rgba(56,189,248,0.35)',
                    color: 'var(--accent-cyan)',
                  }}
                >
                  {cockpit.execution_brief.recommendation}
                </span>
              </>
            )}
          </div>

          <button
            className="tf-btn active"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            onClick={() => setIsMaximized(false)}
          >
            <Minimize2 size={13} />
            <span>Exit Full Window (Esc)</span>
          </button>
        </div>
      )}

      {/* Interactive Chart Toolbar */}
      <div className="chart-toolbar" style={{ flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap' }}>
          {['1m', '5m', '15m', '1h', 'D'].map((tf) => (
            <button
              key={tf}
              className={`tf-btn ${timeframe === tf ? 'active' : ''}`}
              onClick={() => onTimeframeChange(tf)}
            >
              {tf}
            </button>
          ))}
          <span style={{ color: 'rgba(148,163,184,0.2)', margin: '0 3px' }}>|</span>
          <button
            className={`tf-btn ${showIndicators ? 'active' : ''}`}
            onClick={() => setShowIndicators((s) => !s)}
            title="Toggle EMA(9) + VWAP ±1σ"
          >
            <SlidersHorizontal size={11} style={{ marginRight: 3, verticalAlign: '-1px' }} />
            VWAP/EMA
          </button>
          <button
            className={`tf-btn ${showVP ? 'active' : ''}`}
            onClick={() => setShowVP((s) => !s)}
            title="Volume Profile (Buy/Sell Split + VAH/POC/VAL)"
          >
            VP
          </button>
          <button
            className={`tf-btn ${showHL ? 'active' : ''}`}
            onClick={() => setShowHL((s) => !s)}
            title="Session High & Session Low"
          >
            H/L
          </button>
          <button
            className={`tf-btn ${showDB ? 'active' : ''}`}
            onClick={() => setShowDB((s) => !s)}
            title="Dalton Consolidation Base"
          >
            DB
          </button>
          <button
            className={`tf-btn ${showLevels ? 'active' : ''}`}
            onClick={() => setShowLevels((s) => !s)}
            title="Entry / Stop Loss / Target Zones"
          >
            Levels
          </button>
          <button
            className={`tf-btn ${showLiquidity ? 'active' : ''}`}
            onClick={() => setShowLiquidity((s) => !s)}
            title="Causal Intraday Liquidity Factor (0-100: RVOL + Turnover - Amihud Impact)"
          >
            LIQ
          </button>
          {!isControlledReplay && (
            <button
              className={`tf-btn ${effectiveReplayIdx !== null ? 'active' : ''}`}
              style={{
                borderColor: effectiveReplayIdx !== null ? 'var(--accent-purple)' : 'rgba(168,85,247,0.35)',
                color: effectiveReplayIdx !== null ? '#e9d5ff' : 'var(--accent-purple)',
                background: effectiveReplayIdx !== null ? 'rgba(168,85,247,0.2)' : 'transparent',
              }}
              onClick={handleToggleReplay}
              title="Toggle Bar-by-Bar Synchronized Replay"
            >
              <Play size={10} style={{ marginRight: 3, verticalAlign: '-1px' }} />
              Replay
            </button>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span
            className="mono"
            style={{
              fontSize: 10,
              padding: '2px 7px',
              borderRadius: 4,
              background: isLiveToday ? 'rgba(16,185,129,0.14)' : 'rgba(245,158,11,0.12)',
              color: isLiveToday ? 'var(--accent-green)' : 'var(--accent-amber)',
              border: `1px solid ${isLiveToday ? 'rgba(16,185,129,0.35)' : 'rgba(245,158,11,0.3)'}`,
            }}
          >
            {sessionDate || '2026-11-09'} {isLiveToday ? 'LIVE' : 'LAST SESSION'}
          </span>
          <button
            className={`icon-btn ${magnetMode ? 'active' : ''}`}
            style={{ color: magnetMode ? 'var(--accent-cyan)' : undefined }}
            title="Toggle OHLC Magnet Crosshair"
            onClick={() => setMagnetMode((m) => !m)}
          >
            <Magnet size={13} />
          </button>
          <button className="icon-btn" title="Zoom In" onClick={() => adjustZoom(0.72)}>
            <ZoomIn size={13} />
          </button>
          <button className="icon-btn" title="Zoom Out" onClick={() => adjustZoom(1.38)}>
            <ZoomOut size={13} />
          </button>
          <button
            className="icon-btn"
            title="Fit All Candles"
            onClick={() => chartRef.current?.timeScale().fitContent()}
          >
            <Scan size={13} />
          </button>
          <button
            className={`icon-btn ${isMaximized ? 'active' : ''}`}
            style={{ color: isMaximized ? 'var(--accent-cyan)' : undefined }}
            title={isMaximized ? 'Restore Window (Esc)' : 'Maximize Chart to Full Window'}
            onClick={() => setIsMaximized((m) => !m)}
          >
            {isMaximized ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
          <button className="icon-btn" title="Export Chart PNG" onClick={handleScreenshot}>
            <Camera size={13} />
          </button>
        </div>
      </div>

      {/* Main Chart Viewport */}
      <div style={{ position: 'relative', flex: 1, minHeight: 280 }}>
        <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
        <canvas
          ref={overlayCanvasRef}
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 4 }}
        />

        {/* Top-Left Interactive Crosshair + VWAP/H-L HUD */}
        <div
          className="mono"
          style={{
            position: 'absolute',
            top: 8,
            left: 10,
            zIndex: 6,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 10,
            fontSize: 10.5,
            padding: '4px 9px',
            borderRadius: 6,
            background: 'rgba(6, 10, 17, 0.86)',
            border: '1px solid rgba(148,163,184,0.16)',
            pointerEvents: 'none',
          }}
        >
          {crosshairBar ? (
            <>
              <span style={{ color: 'var(--accent-cyan)', fontWeight: 700 }}>{crosshairBar.time}</span>
              <span>O <strong>{crosshairBar.open.toFixed(2)}</strong></span>
              <span>H <strong style={{ color: 'var(--accent-green)' }}>{crosshairBar.high.toFixed(2)}</strong></span>
              <span>L <strong style={{ color: 'var(--accent-red)' }}>{crosshairBar.low.toFixed(2)}</strong></span>
              <span>C <strong style={{ color: 'var(--accent-cyan)' }}>{crosshairBar.close.toFixed(2)}</strong></span>
              <span style={{ color: crosshairBar.pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                ({crosshairBar.pct >= 0 ? '+' : ''}{crosshairBar.pct.toFixed(2)}%)
              </span>
              <span>VWAP <strong style={{ color: '#e879f9' }}>{crosshairBar.vwap.toFixed(2)}</strong></span>
              <span>V <strong style={{ color: 'var(--text-muted)' }}>{crosshairBar.vol.toLocaleString()}</strong></span>
              <span>
                Liq{' '}
                <strong
                  style={{
                    color:
                      crosshairBar.liquidityScore >= 70
                        ? 'var(--accent-green)'
                        : crosshairBar.liquidityScore >= 40
                          ? 'var(--accent-cyan)'
                          : 'var(--accent-red)',
                  }}
                >
                  {crosshairBar.liquidityScore}/100 ({crosshairBar.liquidityTier})
                </strong>{' '}
                <span style={{ color: 'var(--text-dim)' }}>₹{crosshairBar.turnoverLakhs.toFixed(1)}L</span>
              </span>
            </>
          ) : (
            <>
              <span style={{ color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 5 }}>
                <Crosshair size={11} />
                <span>Wheel: Zoom • Drag: Pan</span>
              </span>
              {sessionStats.lastVwap > 0 && (
                <span>
                  VWAP <strong style={{ color: '#e879f9' }}>₹{sessionStats.lastVwap.toFixed(2)}</strong>{' '}
                  <span style={{ color: vwapDistPct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                    ({vwapDistPct >= 0 ? '+' : ''}{vwapDistPct.toFixed(2)}%)
                  </span>
                </span>
              )}
              {sessionStats.high > 0 && (
                <span>
                  S-H/L <strong style={{ color: '#2dd4bf' }}>{sessionStats.high.toFixed(1)}</strong>/
                  <strong style={{ color: '#fb7185' }}>{sessionStats.low.toFixed(1)}</strong>
                </span>
              )}
              {sessionStats.vp && (
                <span>
                  POC <strong style={{ color: '#f59e0b' }}>{sessionStats.vp.pocPrice.toFixed(1)}</strong>
                </span>
              )}
              {sessionStats.avgLiquidity > 0 && (
                <span>
                  Liq <strong style={{ color: sessionStats.avgLiquidity >= 45 ? 'var(--accent-cyan)' : 'var(--accent-red)' }}>{sessionStats.avgLiquidity}/100</strong>{' '}
                  <span style={{ color: 'var(--text-dim)' }}>₹{sessionStats.totalTurnoverCr}Cr</span>
                </span>
              )}
            </>
          )}
        </div>

        {/* Bottom-Left Distinguishable Bracket Legend */}
        {showLevels && resolvedBracket && (
          <div
            className="mono"
            style={{
              position: 'absolute',
              bottom: 28,
              left: 10,
              zIndex: 6,
              display: 'flex',
              gap: 8,
              fontSize: 9.5,
              padding: '3px 8px',
              borderRadius: 5,
              background: 'rgba(6, 10, 17, 0.85)',
              border: '1px solid rgba(148,163,184,0.14)',
              pointerEvents: 'none',
            }}
          >
            <span style={{ color: '#38bdf8' }}>■ ENTRY {resolvedBracket.entryMid.toFixed(1)}</span>
            <span style={{ color: '#f43f5e' }}>■ SL {resolvedBracket.stopLoss.toFixed(1)}</span>
            {resolvedBracket.profitFloor && (
              <span style={{ color: '#f59e0b' }}>■ FLOOR {resolvedBracket.profitFloor.toFixed(1)}</span>
            )}
            <span style={{ color: '#10b981' }}>■ T1 {resolvedBracket.target1.toFixed(1)}</span>
            {resolvedBracket.target2 && (
              <span style={{ color: '#34d399' }}>■ T2 {resolvedBracket.target2.toFixed(1)}</span>
            )}
          </div>
        )}
      </div>

      {/* Built-in Synchronized Replay Bar (visible when active in Execution Cockpit) */}
      {!isControlledReplay && effectiveReplayIdx !== null && allNormalized.length > 0 && (
        <div
          className="mono"
          style={{
            padding: '6px 12px',
            borderTop: '1px solid rgba(168,85,247,0.35)',
            background: 'rgba(168,85,247,0.08)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11,
          }}
        >
          <span style={{ color: 'var(--accent-purple)', fontWeight: 700 }}>REPLAY</span>
          <button
            className="icon-btn"
            onClick={() => {
              setIsPlaying(false);
              setEffectiveReplayIdx(Math.max(0, (effectiveReplayIdx ?? 0) - 1));
            }}
            title="Step Back 1 Bar"
          >
            <SkipBack size={13} />
          </button>
          <button
            className="tf-btn active"
            style={{ background: 'rgba(168,85,247,0.22)', borderColor: 'var(--accent-purple)', color: '#f3e8ff' }}
            onClick={() => setIsPlaying((p) => !p)}
          >
            {isPlaying ? <Pause size={12} /> : <Play size={12} />}
          </button>
          <button
            className="icon-btn"
            onClick={() => {
              setIsPlaying(false);
              setEffectiveReplayIdx(Math.min(allNormalized.length - 1, (effectiveReplayIdx ?? 0) + 1));
            }}
            title="Step Forward 1 Bar"
          >
            <SkipForward size={13} />
          </button>
          <input
            type="range"
            min={0}
            max={Math.max(0, allNormalized.length - 1)}
            value={effectiveReplayIdx}
            onChange={(e) => {
              setIsPlaying(false);
              setEffectiveReplayIdx(Number(e.target.value));
            }}
            style={{ flex: 1, accentColor: 'var(--accent-purple)', cursor: 'pointer' }}
          />
          <span style={{ color: 'var(--text-main)' }}>
            Bar {(effectiveReplayIdx ?? 0) + 1}/{allNormalized.length} (
            {currentReplayBar
              ? new Date((currentReplayBar.ts + 19800) * 1000).toISOString().slice(11, 16)
              : '--'}
            )
          </span>
          <button
            className="tf-btn"
            onClick={() => setPlaySpeedMs((s) => (s === 450 ? 200 : s === 200 ? 90 : 450))}
            title="Cycle Playback Speed"
          >
            {playSpeedMs === 450 ? '1x' : playSpeedMs === 200 ? '2x' : '5x'}
          </button>
          <button
            className="icon-btn"
            title="Exit Replay (Return to Full Session)"
            onClick={() => {
              setIsPlaying(false);
              setEffectiveReplayIdx(null);
            }}
          >
            <RotateCcw size={13} />
          </button>
        </div>
      )}
    </div>
  );
};
