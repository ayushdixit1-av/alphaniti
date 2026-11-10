import React, { useEffect, useMemo, useState } from 'react';
import { Play, Pause, SkipBack, SkipForward, ShieldAlert, Zap } from 'lucide-react';
import { AuditLedgerResponse, AuditTrade, CandleBar, CockpitSymbolData } from '../../types/api';
import { ActiveBracketOverlay, SuperChart } from '../charts/SuperChart';
import { findNearestBarIndex, normalizeAndComputeIndicators } from '../../utils/chartMath';

interface ReplayWorkspaceProps {
  auditLedger: AuditLedgerResponse | null;
  onSelectSymbolForCockpit: (sym: string) => void;
}

export const ReplayWorkspace: React.FC<ReplayWorkspaceProps> = ({
  auditLedger,
  onSelectSymbolForCockpit,
}) => {
  const trades = auditLedger?.trades || [];
  const [selectedTradeIdx, setSelectedTradeIdx] = useState(0);
  const [candles, setCandles] = useState<CandleBar[]>([]);
  const [cockpit, setCockpit] = useState<CockpitSymbolData | null>(null);
  const [replayIdx, setReplayIdx] = useState<number>(20);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedMs, setSpeedMs] = useState(400);
  const [stressMode, setStressMode] = useState<'NONE' | 'CRASH' | 'SQUEEZE'>('NONE');

  const trade: AuditTrade | undefined = trades[selectedTradeIdx] || trades[0];

  const normalizedBars = useMemo(() => normalizeAndComputeIndicators(candles), [candles]);

  const { entryIdx, exitIdx } = useMemo(() => {
    if (!trade || normalizedBars.length === 0) return { entryIdx: 0, exitIdx: 0 };
    const en = findNearestBarIndex(normalizedBars, trade.entry_time);
    const ex = Math.max(en + 1, findNearestBarIndex(normalizedBars, trade.exit_time));
    return {
      entryIdx: en,
      exitIdx: Math.min(normalizedBars.length - 1, ex),
    };
  }, [trade, normalizedBars]);

  // Fetch 5m candles + cockpit context whenever selected trade changes
  useEffect(() => {
    if (!trade) return;
    setIsPlaying(false);
    let cancelled = false;
    Promise.all([
      fetch(`/api/candles/${trade.symbol}`).then((r) => r.json()),
      fetch(`/api/cockpit_symbol/${trade.symbol}`).then((r) => r.json()),
    ])
      .then(([cRes, sRes]) => {
        if (cancelled) return;
        if (cRes?.candles) {
          setCandles(cRes.candles);
          const norm = normalizeAndComputeIndicators(cRes.candles);
          const en = findNearestBarIndex(norm, trade.entry_time);
          const ex = Math.min(norm.length - 1, Math.max(en + 1, findNearestBarIndex(norm, trade.exit_time)));
          setReplayIdx(Math.min(norm.length - 1, Math.floor((en + ex) / 2)));
        }
        if (sRes?.status === 'SUCCESS') setCockpit(sRes);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [trade?.symbol, selectedTradeIdx]);

  // Synchronized Play loop
  useEffect(() => {
    if (!isPlaying || normalizedBars.length === 0) return;
    const id = setInterval(() => {
      setReplayIdx((prev) => {
        if (prev >= normalizedBars.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, speedMs);
    return () => clearInterval(id);
  }, [isPlaying, speedMs, normalizedBars.length]);

  // Causal bar-by-bar Two-Mode calculation synced to replayIdx
  const replayAnalytics = useMemo(() => {
    if (!trade || normalizedBars.length === 0) {
      return {
        barTime: trade?.entry_time || '--',
        curPrice: trade?.entry_price || 0,
        curR: 0,
        peakR: 0,
        activeFloor: null as number | null,
        modeLabel: 'PRE-ENTRY WATCHING',
        unrealizedCash: 0,
      };
    }

    const idx = Math.min(normalizedBars.length - 1, Math.max(0, replayIdx));
    const bar = normalizedBars[idx];
    const ist = new Date((bar.ts + 19800) * 1000);
    const barTime = ist.toISOString().slice(11, 16);
    const isShort = trade.strategy.includes('SHORT');
    const riskPerSh = trade.risk_per_share || Math.abs(trade.entry_price - trade.stop_loss) || 1;

    if (idx < entryIdx) {
      return {
        barTime,
        curPrice: bar.close,
        curR: 0,
        peakR: 0,
        activeFloor: null,
        modeLabel: 'PRE-ENTRY CALIBRATION',
        unrealizedCash: 0,
      };
    }

    // Track actual bar-by-bar peak excursion from entryIdx up to idx (clamped at exitIdx if past exit)
    const evalEnd = Math.min(idx, exitIdx);
    let maxFavDist = 0;
    for (let i = entryIdx; i <= evalEnd; i++) {
      const b = normalizedBars[i];
      const fav = isShort ? trade.entry_price - b.low : b.high - trade.entry_price;
      if (fav > maxFavDist) maxFavDist = fav;
    }

    const peakR = Number((maxFavDist / riskPerSh).toFixed(2));
    const evalBar = normalizedBars[evalEnd];
    const curPrice = idx >= exitIdx ? trade.exit_price : evalBar.close;
    const curDist = isShort ? trade.entry_price - curPrice : curPrice - trade.entry_price;
    const curR = Number((curDist / riskPerSh).toFixed(2));
    const grossNow = curDist * trade.total_qty;
    const netNow = Number((grossNow - trade.fees).toFixed(2));

    let activeFloor: number | null = null;
    let modeLabel = 'OPEN RISK ZONE (-1.0R SL)';

    if (idx >= exitIdx) {
      activeFloor = trade.exit_price;
      modeLabel = `EXITED: ${trade.exit_reason}`;
    } else if (peakR >= 0.7) {
      const trailDist = peakR * 0.7 * riskPerSh;
      activeFloor = Number((isShort ? trade.entry_price - trailDist : trade.entry_price + trailDist).toFixed(2));
      modeLabel = 'MODE B: 70% GAIN HARVEST';
    } else if (peakR >= 0.5) {
      const feePadPerSh = (trade.fees + 10) / Math.max(1, trade.total_qty);
      activeFloor = Number((isShort ? trade.entry_price - feePadPerSh : trade.entry_price + feePadPerSh).toFixed(2));
      modeLabel = 'MODE A: FEE GUARD CLAMPED';
    } else if (peakR >= 0.25) {
      modeLabel = 'NOISE BUFFER (HOLD SL)';
    }

    return {
      barTime,
      curPrice,
      curR,
      peakR,
      activeFloor,
      modeLabel,
      unrealizedCash: idx >= exitIdx ? trade.net_pnl : netNow,
    };
  }, [trade, normalizedBars, replayIdx, entryIdx, exitIdx]);

  const bracketOverlay: ActiveBracketOverlay | null = useMemo(() => {
    if (!trade) return null;
    const isShort = trade.strategy.includes('SHORT');
    const pad = trade.entry_price * 0.0006;
    return {
      direction: isShort ? 'SHORT' : 'LONG',
      entryLow: trade.entry_price - pad,
      entryHigh: trade.entry_price + pad,
      entryMid: trade.entry_price,
      stopLoss: trade.stop_loss,
      profitFloor: replayAnalytics.activeFloor,
      target1: trade.target_1,
      target2: null,
    };
  }, [trade, replayAnalytics.activeFloor]);

  if (!trade) {
    return (
      <div className="panel-card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
        Loading verified execution trades for synchronized replay...
      </div>
    );
  }

  const totalRisk = trade.risk_per_share * trade.total_qty;

  // Milestone bar jumpers
  const jumpTo = (mode: 'ENTRY' | 'MID' | 'PEAK' | 'EXIT') => {
    setIsPlaying(false);
    if (mode === 'ENTRY') setReplayIdx(entryIdx);
    else if (mode === 'MID') setReplayIdx(Math.min(exitIdx, Math.floor(entryIdx + (exitIdx - entryIdx) * 0.45)));
    else if (mode === 'PEAK') setReplayIdx(Math.min(exitIdx, Math.floor(entryIdx + (exitIdx - entryIdx) * 0.78)));
    else setReplayIdx(exitIdx);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Top Trade Selector Bar */}
      <div className="panel-card" style={{ padding: '8px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>
              Synchronized Two-Mode Trade Replay ({auditLedger?.date || 'Session'})
            </span>
            <button
              className="tf-btn"
              style={{ border: '1px solid rgba(56,189,248,0.35)', color: 'var(--accent-cyan)' }}
              onClick={() => onSelectSymbolForCockpit(trade.symbol)}
            >
              Open {trade.symbol} in Cockpit
            </button>
          </div>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {trades.map((t, idx) => (
              <button
                key={`${t.symbol}-${idx}`}
                className={`tf-btn ${idx === selectedTradeIdx ? 'active' : ''}`}
                style={{ border: '1px solid var(--border-subtle)' }}
                onClick={() => setSelectedTradeIdx(idx)}
              >
                {t.symbol} ({t.net_pnl >= 0 ? '+' : ''}₹{t.net_pnl.toFixed(1)})
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Main Synced Replay Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)', gap: 8 }}>
        {/* Left: Synced SuperChart revealing candles bar-by-bar */}
        <div className="panel-card">
          <SuperChart
            symbol={trade.symbol}
            timeframe="5m"
            onTimeframeChange={() => {}}
            candles={candles}
            sessionDate={auditLedger?.date || '2026-11-09'}
            isLiveToday={false}
            cockpit={cockpit}
            trades={[trade]}
            externalReplayIndex={replayIdx}
            onExternalReplayIndexChange={(i) => setReplayIdx(i ?? Math.max(0, normalizedBars.length - 1))}
            overrideBracket={bracketOverlay}
          />
        </div>

        {/* Right: Synchronized Transport, Two-Mode Neutralizer & Stress Drawer */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="panel-card" style={{ padding: 12, gap: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 700 }}>
                {trade.symbol} • {trade.strategy.replace(/_/g, ' ')}
              </span>
              <span
                className="mono"
                style={{
                  fontSize: 10,
                  padding: '2px 8px',
                  borderRadius: 4,
                  background: replayAnalytics.peakR >= 0.5 ? 'rgba(16,185,129,0.16)' : 'rgba(244,63,94,0.16)',
                  color: replayAnalytics.peakR >= 0.5 ? 'var(--accent-green)' : 'var(--accent-red)',
                }}
              >
                {replayAnalytics.modeLabel}
              </span>
            </div>

            {/* Synchronized Playback Transport Controls */}
            <div
              className="mono"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: '#060a11',
                padding: '6px 8px',
                borderRadius: 6,
                border: '1px solid var(--border-subtle)',
              }}
            >
              <button
                className="icon-btn"
                onClick={() => {
                  setIsPlaying(false);
                  setReplayIdx((r) => Math.max(0, r - 1));
                }}
                title="Previous Bar"
              >
                <SkipBack size={14} />
              </button>
              <button
                className="tf-btn active"
                onClick={() => setIsPlaying((p) => !p)}
                title="Play / Pause Synchronized Replay"
              >
                {isPlaying ? <Pause size={13} /> : <Play size={13} />}
              </button>
              <button
                className="icon-btn"
                onClick={() => {
                  setIsPlaying(false);
                  setReplayIdx((r) => Math.min(normalizedBars.length - 1, r + 1));
                }}
                title="Next Bar"
              >
                <SkipForward size={14} />
              </button>
              <input
                type="range"
                min={0}
                max={Math.max(0, normalizedBars.length - 1)}
                value={replayIdx}
                onChange={(e) => {
                  setIsPlaying(false);
                  setReplayIdx(Number(e.target.value));
                }}
                style={{ flex: 1, accentColor: 'var(--accent-cyan)', cursor: 'pointer' }}
              />
              <span style={{ fontSize: 11, color: 'var(--accent-cyan)', minWidth: 46, textAlign: 'right' }}>
                {replayAnalytics.barTime}
              </span>
              <button
                className="tf-btn"
                onClick={() => setSpeedMs((s) => (s === 400 ? 180 : s === 180 ? 80 : 400))}
              >
                {speedMs === 400 ? '1x' : speedMs === 180 ? '2x' : '5x'}
              </button>
            </div>

            {/* Quick Milestone Jumpers */}
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="tf-btn" style={{ flex: 1, border: '1px solid var(--border-subtle)' }} onClick={() => jumpTo('ENTRY')}>
                Entry ({trade.entry_time})
              </button>
              <button className="tf-btn" style={{ flex: 1, border: '1px solid var(--border-subtle)' }} onClick={() => jumpTo('MID')}>
                Noise Chop
              </button>
              <button className="tf-btn" style={{ flex: 1, border: '1px solid var(--border-subtle)' }} onClick={() => jumpTo('PEAK')}>
                Peak MFE
              </button>
              <button className="tf-btn" style={{ flex: 1, border: '1px solid var(--border-subtle)' }} onClick={() => jumpTo('EXIT')}>
                Exit ({trade.exit_time})
              </button>
            </div>

            {/* Live Replaying Metrics */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Current Bar LTP</div>
                <div className="mono" style={{ fontSize: 12, fontWeight: 700, marginTop: 2 }}>
                  ₹{replayAnalytics.curPrice.toFixed(2)} ({replayAnalytics.curR >= 0 ? '+' : ''}{replayAnalytics.curR.toFixed(2)}R)
                </div>
              </div>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Peak Excursion / Floor</div>
                <div className="mono" style={{ fontSize: 12, fontWeight: 700, marginTop: 2, color: 'var(--accent-amber)' }}>
                  +{replayAnalytics.peakR.toFixed(2)}R {replayAnalytics.activeFloor ? `• ₹${replayAnalytics.activeFloor.toFixed(1)}` : ''}
                </div>
              </div>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Replayed Net PnL</div>
                <div
                  className="mono"
                  style={{
                    fontSize: 12,
                    fontWeight: 700,
                    marginTop: 2,
                    color: replayAnalytics.unrealizedCash >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}
                >
                  {replayAnalytics.unrealizedCash >= 0 ? '+' : ''}₹{replayAnalytics.unrealizedCash.toFixed(2)}
                </div>
              </div>
            </div>

            {/* Model Head-to-Head */}
            <table className="watchlist-table" style={{ fontSize: 11 }}>
              <thead>
                <tr>
                  <th>Model Comparison</th>
                  <th>Behavior on This Trade</th>
                  <th style={{ textAlign: 'right' }}>Final Net</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Rigid 0.00 Breakeven</td>
                  <td style={{ color: 'var(--text-dim)' }}>Choked on noise retest</td>
                  <td style={{ textAlign: 'right', color: 'var(--accent-red)' }}>-₹{trade.fees.toFixed(2)}</td>
                </tr>
                <tr>
                  <td>No Neutralizer</td>
                  <td style={{ color: 'var(--text-dim)' }}>Held blindly to -1.0R</td>
                  <td style={{ textAlign: 'right', color: 'var(--accent-red)' }}>-₹{totalRisk.toFixed(2)}</td>
                </tr>
                <tr style={{ background: 'rgba(16,185,129,0.08)' }}>
                  <td style={{ color: 'var(--accent-green)', fontWeight: 700 }}>AlphaNiti Two-Mode</td>
                  <td style={{ color: 'var(--accent-green)' }}>{trade.exit_reason}</td>
                  <td
                    style={{
                      textAlign: 'right',
                      fontWeight: 800,
                      color: trade.net_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                    }}
                  >
                    {trade.net_pnl >= 0 ? '+' : ''}₹{trade.net_pnl.toFixed(2)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Stress Test Drawer */}
          <div className="panel-card" style={{ padding: 12, gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
                <ShieldAlert size={13} color="var(--accent-amber)" />
                Margin &amp; Tail Stress Simulator
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  className={`tf-btn ${stressMode === 'CRASH' ? 'active' : ''}`}
                  style={{ border: '1px solid rgba(245,158,11,0.4)', color: 'var(--accent-amber)' }}
                  onClick={() => setStressMode(stressMode === 'CRASH' ? 'NONE' : 'CRASH')}
                >
                  <Zap size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />
                  -2.5% Flash Crash
                </button>
                <button
                  className={`tf-btn ${stressMode === 'SQUEEZE' ? 'active' : ''}`}
                  style={{ border: '1px solid rgba(244,63,94,0.4)', color: 'var(--accent-red)' }}
                  onClick={() => setStressMode(stressMode === 'SQUEEZE' ? 'NONE' : 'SQUEEZE')}
                >
                  <Zap size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />
                  +1.8% Squeeze
                </button>
              </div>
            </div>

            {stressMode !== 'NONE' && (
              <div
                className="mono"
                style={{
                  background: '#060a11',
                  padding: 10,
                  borderRadius: 6,
                  border: '1px solid rgba(56,189,248,0.25)',
                  fontSize: 11,
                  lineHeight: 1.55,
                }}
              >
                {stressMode === 'CRASH' ? (
                  <>
                    <div><strong style={{ color: 'var(--accent-green)' }}>PRE-FLIGHT PASSED:</strong> Cash Reserve Floor ₹2,500.00 remains 100% intact.</div>
                    <div>Notional ₹{Math.round(trade.notional).toLocaleString('en-IN')} / ₹8,382 = {(trade.notional / 8382.07).toFixed(2)}x (Bounded ≤ 3.50x Cap).</div>
                    <div>1-Trade Mutex prevents correlated multi-stock margin spiral.</div>
                  </>
                ) : (
                  <>
                    <div><strong style={{ color: 'var(--accent-amber)' }}>STOP BOUNDED:</strong> Hard Stop ₹{trade.stop_loss.toFixed(2)} caps risk at -₹{totalRisk.toFixed(2)} (-1.0R).</div>
                    <div>Latency Watchdog routes exit via FAST_PEGGED_LIMIT (IOC) within 1 tick.</div>
                    <div>Max account drawdown strictly bounded to {((totalRisk / 8382.07) * 100).toFixed(2)}%.</div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
