import React, { useEffect, useRef, useState } from 'react';
import {
  createChart,
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
import { Camera, Maximize2, Crosshair, SlidersHorizontal, Layers } from 'lucide-react';
import { AuditTrade, CandleBar, CockpitSymbolData } from '../../types/api';

interface SuperChartProps {
  symbol: string;
  timeframe: string;
  onTimeframeChange: (tf: string) => void;
  candles: CandleBar[];
  sessionDate?: string;
  isLiveToday?: boolean;
  cockpit: CockpitSymbolData | null;
  trades: AuditTrade[];
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
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  const [showIndicators, setShowIndicators] = useState(true);
  const [showVP, setShowVP] = useState(true);
  const [showDB, setShowDB] = useState(true);
  const [crosshairBar, setCrosshairBar] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    vol: number;
  } | null>(null);

  // Initialize chart instance once
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
        mode: 0,
        vertLine: { color: 'rgba(56, 189, 248, 0.4)', width: 1, style: 2 },
        horzLine: { color: 'rgba(56, 189, 248, 0.4)', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)',
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      timeScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
      },
      localization: {
        timeFormatter: (t: number) => {
          const d = new Date((t + 19800) * 1000);
          return timeframe === 'D'
            ? d.toISOString().slice(0, 10)
            : d.toISOString().slice(11, 16);
        },
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    const volSeries = chart.addHistogramSeries({
      color: 'rgba(56, 189, 248, 0.25)',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    chart.priceScale('').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const emaSeries = chart.addLineSeries({
      color: '#22d3ee',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const vwapSeries = chart.addLineSeries({
      color: '#e879f9',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volSeriesRef.current = volSeries;
    emaSeriesRef.current = emaSeries;
    vwapSeriesRef.current = vwapSeries;

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
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // Update Time formatter when timeframe switches
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.applyOptions({
      localization: {
        timeFormatter: (t: number) => {
          const d = new Date((t + 19800) * 1000);
          return timeframe === 'D'
            ? d.toISOString().slice(0, 10)
            : d.toISOString().slice(11, 16);
        },
      },
    });
  }, [timeframe]);

  // Populate series + markers + price lines whenever candles / cockpit change
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const vs = volSeriesRef.current;
    const es = emaSeriesRef.current;
    const vws = vwapSeriesRef.current;
    const chart = chartRef.current;
    if (!cs || !vs || !es || !vws || !chart) return;

    // Normalize candle timestamps strictly increasing UTC seconds
    const seen = new Set<number>();
    const formatted: CandlestickData<Time>[] = [];
    const volData: HistogramData<Time>[] = [];
    const emaData: LineData<Time>[] = [];
    const vwapData: LineData<Time>[] = [];

    let emaPrev: number | null = null;
    const k = 2 / (9 + 1);
    let cumTPV = 0;
    let cumVol = 0;

    const sortedCandles = [...candles]
      .map((c, idx) => {
        const ts =
          typeof c.timestamp === 'number'
            ? c.timestamp
            : typeof c.time === 'number'
            ? c.time
            : Math.floor(Date.now() / 1000) - (candles.length - idx) * 300;
        return { ...c, _ts: ts };
      })
      .sort((a, b) => a._ts - b._ts);

    for (const c of sortedCandles) {
      if (seen.has(c._ts) || c.close <= 0) continue;
      seen.add(c._ts);
      const t = c._ts as UTCTimestamp;
      formatted.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close });
      const isUp = c.close >= c.open;
      volData.push({
        time: t,
        value: c.volume || 0,
        color: isUp ? 'rgba(16, 185, 129, 0.32)' : 'rgba(244, 63, 94, 0.32)',
      });
      emaPrev = emaPrev === null ? c.close : c.close * k + emaPrev * (1 - k);
      emaData.push({ time: t, value: Number(emaPrev.toFixed(2)) });

      const tp = (c.high + c.low + c.close) / 3;
      const v = Math.max(1, c.volume || 1);
      cumTPV += tp * v;
      cumVol += v;
      vwapData.push({ time: t, value: Number((cumTPV / cumVol).toFixed(2)) });
    }

    cs.setData(formatted);
    vs.setData(volData);
    es.setData(showIndicators ? emaData : []);
    vws.setData(showIndicators ? vwapData : []);

    // Execution Trade Markers (B / S) for selected symbol
    const markers: SeriesMarker<Time>[] = [];
    const symTrades = trades.filter((t) => t.symbol.toUpperCase() === symbol.toUpperCase());
    if (symTrades.length > 0 && sortedCandles.length > 0) {
      const findNearestTs = (hhmm: string): number => {
        let bestTs = sortedCandles[0]._ts;
        let bestDiff = Infinity;
        const [hh, mm] = hhmm.split(':').map((n) => parseInt(n, 10) || 0);
        const targetMins = hh * 60 + mm;
        for (const bar of sortedCandles) {
          const ist = new Date((bar._ts + 19800) * 1000);
          const barMins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
          const diff = Math.abs(barMins - targetMins);
          if (diff < bestDiff) {
            bestDiff = diff;
            bestTs = bar._ts;
          }
        }
        return bestTs;
      };

      for (const tr of symTrades) {
        const isShort = tr.strategy.includes('SHORT');
        const entryTs = findNearestTs(tr.entry_time) as UTCTimestamp;
        const exitTs = findNearestTs(tr.exit_time) as UTCTimestamp;
        markers.push({
          time: entryTs,
          position: isShort ? 'aboveBar' : 'belowBar',
          color: isShort ? '#f43f5e' : '#10b981',
          shape: isShort ? 'arrowDown' : 'arrowUp',
          text: isShort ? 'S' : 'B',
        });
        markers.push({
          time: exitTs,
          position: isShort ? 'belowBar' : 'aboveBar',
          color: '#38bdf8',
          shape: isShort ? 'arrowUp' : 'arrowDown',
          text: `${tr.net_pnl >= 0 ? '+' : ''}₹${tr.net_pnl.toFixed(0)}`,
        });
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

    // Add VAH, POC, VAL and Risk Bracket lines
    if (showVP && cockpit?.volume_profile?.success) {
      const vp = cockpit.volume_profile;
      priceLinesRef.current.push(
        cs.createPriceLine({
          price: vp.vah_price,
          color: '#94a3b8',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `VAH ${vp.vah_price.toFixed(2)}`,
        }),
        cs.createPriceLine({
          price: vp.poc_price,
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `POC ${vp.poc_price.toFixed(2)}`,
        }),
        cs.createPriceLine({
          price: vp.val_price,
          color: '#94a3b8',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `VAL ${vp.val_price.toFixed(2)}`,
        })
      );
    }

    if (cockpit?.execution_brief) {
      const eb = cockpit.execution_brief;
      priceLinesRef.current.push(
        cs.createPriceLine({
          price: eb.target_1,
          color: '#10b981',
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: false,
          title: 'T1',
        }),
        cs.createPriceLine({
          price: eb.stop_loss,
          color: '#f43f5e',
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: false,
          title: 'SL',
        })
      );
    }

    chart.timeScale().fitContent();

    // Crosshair listener for OHLCV HUD
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setCrosshairBar(null);
        return;
      }
      const d = param.seriesData.get(cs) as CandlestickData<Time> | undefined;
      const v = param.seriesData.get(vs) as HistogramData<Time> | undefined;
      if (d) {
        const ist = new Date(((param.time as number) + 19800) * 1000);
        setCrosshairBar({
          time: timeframe === 'D' ? ist.toISOString().slice(0, 10) : ist.toISOString().slice(11, 16),
          open: d.open,
          high: d.high,
          low: d.low,
          close: d.close,
          vol: v?.value ?? 0,
        });
      }
    });
  }, [candles, symbol, showIndicators, showVP, cockpit, trades, timeframe]);

  // Draw right-edge Volume Profile Histogram & Dalton Base synced to PriceScale coordinates
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

      // 1. Dalton Base Box
      if (showDB && cockpit?.dalton_base) {
        const yHigh = cs.priceToCoordinate(cockpit.dalton_base.base_high);
        const yLow = cs.priceToCoordinate(cockpit.dalton_base.base_low);
        if (yHigh !== null && yLow !== null) {
          const top = Math.min(yHigh, yLow);
          const boxH = Math.max(4, Math.abs(yLow - yHigh));
          const left = w * 0.25;
          const boxW = w * 0.45;
          ctx.fillStyle = 'rgba(56, 189, 248, 0.04)';
          ctx.fillRect(left, top, boxW, boxH);
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.25)';
          ctx.setLineDash([4, 4]);
          ctx.strokeRect(left, top, boxW, boxH);
          ctx.setLineDash([]);
          ctx.fillStyle = 'rgba(56, 189, 248, 0.7)';
          ctx.font = "600 9.5px 'JetBrains Mono'";
          ctx.fillText(
            `DALTON BASE ${cockpit.dalton_base.base_low.toFixed(1)}–${cockpit.dalton_base.base_high.toFixed(1)}`,
            left + 6,
            top + 12
          );
        }
      }

      // 2. Right-Edge Volume Profile Histogram
      if (showVP && cockpit?.volume_profile?.profile?.length) {
        const nodes = cockpit.volume_profile.profile;
        const maxVol = Math.max(...nodes.map((n) => n.volume), 1);
        const maxBarW = Math.min(130, w * 0.16);
        const rightEdge = w - 68; // inside right price axis

        for (let i = 0; i < nodes.length; i++) {
          const node = nodes[i];
          const y = cs.priceToCoordinate(node.price);
          if (y === null || y < 10 || y > h - 25) continue;
          const barW = (node.volume / maxVol) * maxBarW;
          const barH = Math.max(2, (h * 0.65) / nodes.length);
          ctx.fillStyle = node.is_poc
            ? 'rgba(245, 158, 11, 0.75)'
            : node.is_value_area
            ? 'rgba(56, 189, 248, 0.36)'
            : 'rgba(148, 163, 184, 0.22)';
          ctx.fillRect(rightEdge - barW, y - barH / 2, barW, barH);
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
  }, [cockpit, showVP, showDB, candles]);

  const handleScreenshot = () => {
    const chart = chartRef.current;
    if (!chart) return;
    const sc = chart.takeScreenshot();
    const link = document.createElement('a');
    link.download = `${symbol}_${timeframe}_AlphaNiti.png`;
    link.href = sc.toDataURL('image/png');
    link.click();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 330 }}>
      <div className="chart-toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {['1m', '5m', '15m', '1h', 'D'].map((tf) => (
            <button
              key={tf}
              className={`tf-btn ${timeframe === tf ? 'active' : ''}`}
              onClick={() => onTimeframeChange(tf)}
            >
              {tf}
            </button>
          ))}
          <span style={{ color: 'rgba(148,163,184,0.2)', margin: '0 4px' }}>|</span>
          <button
            className={`tf-btn ${showIndicators ? 'active' : ''}`}
            onClick={() => setShowIndicators((s) => !s)}
            title="Toggle EMA(9) & VWAP"
          >
            <SlidersHorizontal size={12} style={{ marginRight: 4, verticalAlign: '-2px' }} />
            Indicators
          </button>
          <button
            className={`tf-btn ${showVP ? 'active' : ''}`}
            onClick={() => setShowVP((s) => !s)}
            title="Volume Profile (VAH / POC / VAL)"
          >
            VP
          </button>
          <button
            className={`tf-btn ${showDB ? 'active' : ''}`}
            onClick={() => setShowDB((s) => !s)}
            title="Dalton Base Zone"
          >
            DB
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
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
            className="icon-btn"
            title="Fit Chart Scale"
            onClick={() => chartRef.current?.timeScale().fitContent()}
          >
            <Maximize2 size={13} />
          </button>
          <button className="icon-btn" title="Save Chart PNG" onClick={handleScreenshot}>
            <Camera size={13} />
          </button>
        </div>
      </div>

      <div style={{ position: 'relative', flex: 1, minHeight: 295 }}>
        <div
          ref={containerRef}
          style={{ position: 'absolute', inset: 0 }}
        />
        <canvas
          ref={overlayCanvasRef}
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 4 }}
        />

        {/* OHLCV Live Crosshair HUD */}
        <div
          className="mono"
          style={{
            position: 'absolute',
            top: 8,
            left: 10,
            zIndex: 6,
            display: 'flex',
            gap: 10,
            fontSize: 10.5,
            padding: '3px 8px',
            borderRadius: 5,
            background: 'rgba(6, 10, 17, 0.82)',
            border: '1px solid rgba(148,163,184,0.12)',
            pointerEvents: 'none',
          }}
        >
          {crosshairBar ? (
            <>
              <span style={{ color: 'var(--text-muted)' }}>{crosshairBar.time}</span>
              <span>O <strong style={{ color: 'var(--text-main)' }}>{crosshairBar.open.toFixed(2)}</strong></span>
              <span>H <strong style={{ color: 'var(--accent-green)' }}>{crosshairBar.high.toFixed(2)}</strong></span>
              <span>L <strong style={{ color: 'var(--accent-red)' }}>{crosshairBar.low.toFixed(2)}</strong></span>
              <span>C <strong style={{ color: 'var(--accent-cyan)' }}>{crosshairBar.close.toFixed(2)}</strong></span>
              <span>V <strong style={{ color: 'var(--text-muted)' }}>{crosshairBar.vol.toLocaleString()}</strong></span>
            </>
          ) : (
            <span style={{ color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Crosshair size={11} />
              <span>EMA(9) Cyan • VWAP Magenta • <Layers size={11} style={{ verticalAlign: '-2px' }} /> VP + Dalton Base</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
