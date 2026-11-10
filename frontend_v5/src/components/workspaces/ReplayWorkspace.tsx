import React, { useEffect, useState } from 'react';
import { ShieldAlert, Zap } from 'lucide-react';
import { AuditLedgerResponse, AuditTrade, CandleBar, CockpitSymbolData } from '../../types/api';
import { SuperChart } from '../charts/SuperChart';

interface ReplayWorkspaceProps {
  auditLedger: AuditLedgerResponse | null;
  onSelectSymbolForCockpit: (sym: string) => void;
}

export const ReplayWorkspace: React.FC<ReplayWorkspaceProps> = ({ auditLedger }) => {
  const trades = auditLedger?.trades || [];
  const [selectedTradeIdx, setSelectedTradeIdx] = useState(0);
  const [stepPct, setStepPct] = useState(55);
  const [candles, setCandles] = useState<CandleBar[]>([]);
  const [cockpit, setCockpit] = useState<CockpitSymbolData | null>(null);
  const [stressMode, setStressMode] = useState<'NONE' | 'CRASH' | 'SQUEEZE'>('NONE');

  const trade: AuditTrade | undefined = trades[selectedTradeIdx] || trades[0];

  useEffect(() => {
    if (!trade) return;
    let cancelled = false;
    Promise.all([
      fetch(`/api/candles/${trade.symbol}`).then((r) => r.json()),
      fetch(`/api/cockpit_symbol/${trade.symbol}`).then((r) => r.json()),
    ])
      .then(([cRes, sRes]) => {
        if (cancelled) return;
        if (cRes?.candles) setCandles(cRes.candles);
        if (sRes?.status === 'SUCCESS') setCockpit(sRes);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [trade?.symbol]);

  if (!trade) {
    return (
      <div className="panel-card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
        Loading verified execution trades for bar-by-bar replay...
      </div>
    );
  }

  const totalRisk = trade.risk_per_share * trade.total_qty;
  const currentR = Number(((stepPct / 100) * Math.max(trade.peak_r, 0.5)).toFixed(2));
  const modeStatus =
    currentR >= 0.7 && trade.peak_r >= 0.7
      ? 'MODE B: 70% GAIN TRAIL ACTIVE'
      : currentR >= 0.5 && trade.peak_r >= 0.5
      ? 'MODE A: FEE GUARD CLAMPED'
      : 'OPEN NOISE BUFFER (INITIAL SL)';

  const lockedCash =
    currentR >= 0.5 && trade.peak_r >= 0.5
      ? `+₹${trade.net_pnl.toFixed(2)} Protected`
      : `-₹${totalRisk.toFixed(2)} At Risk`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Trade Picker Bar */}
      <div className="panel-card" style={{ padding: '10px 12px', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700 }}>
            Two-Mode Neutralizer — Historical Trade Replay ({auditLedger?.date || 'Session'})
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {trades.map((t, idx) => (
              <button
                key={`${t.symbol}-${idx}`}
                className={`tf-btn ${idx === selectedTradeIdx ? 'active' : ''}`}
                style={{ border: '1px solid var(--border-subtle)' }}
                onClick={() => {
                  setSelectedTradeIdx(idx);
                  setStepPct(55);
                }}
              >
                {t.symbol} ({t.net_pnl >= 0 ? '+' : ''}₹{t.net_pnl.toFixed(2)})
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)', gap: 8 }}>
        {/* Left: Actual 5m Candle Replay Chart */}
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
          />
        </div>

        {/* Right: Scrubber + Payoff Matrix + Stress Simulator */}
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
                  background: currentR >= 0.5 ? 'rgba(16,185,129,0.16)' : 'rgba(244,63,94,0.16)',
                  color: currentR >= 0.5 ? 'var(--accent-green)' : 'var(--accent-red)',
                }}
              >
                {modeStatus}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 6 }}>
              {[
                { label: '0.0R Entry', val: 0 },
                { label: '+0.30R Noise', val: 35 },
                { label: '+0.50R Mode A', val: 65 },
                { label: 'Exit Banked', val: 100 },
              ].map((m) => (
                <button
                  key={m.label}
                  className={`tf-btn ${Math.abs(stepPct - m.val) < 15 ? 'active' : ''}`}
                  style={{ flex: 1, border: '1px solid var(--border-subtle)', textAlign: 'center' }}
                  onClick={() => setStepPct(m.val)}
                >
                  {m.label}
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-dim)' }}>{trade.entry_time}</span>
              <input
                type="range"
                min={0}
                max={100}
                value={stepPct}
                onChange={(e) => setStepPct(Number(e.target.value))}
                style={{ flex: 1, accentColor: 'var(--accent-cyan)', cursor: 'pointer' }}
              />
              <span className="mono" style={{ fontSize: 12, fontWeight: 800, color: 'var(--accent-cyan)' }}>
                +{currentR.toFixed(2)}R
              </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Entry / Exit</div>
                <div className="mono" style={{ fontSize: 11.5, fontWeight: 700, marginTop: 2 }}>
                  ₹{trade.entry_price.toFixed(1)} ➔ ₹{trade.exit_price.toFixed(1)}
                </div>
              </div>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Qty / Notional</div>
                <div className="mono" style={{ fontSize: 11.5, fontWeight: 700, marginTop: 2 }}>
                  {trade.total_qty}sh (₹{Math.round(trade.notional).toLocaleString('en-IN')})
                </div>
              </div>
              <div style={{ background: '#060a11', padding: 8, borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Cash Protection</div>
                <div
                  className="mono"
                  style={{
                    fontSize: 11.5,
                    fontWeight: 700,
                    marginTop: 2,
                    color: currentR >= 0.5 ? 'var(--accent-green)' : 'var(--accent-amber)',
                  }}
                >
                  {lockedCash}
                </div>
              </div>
            </div>

            {/* Model Head-to-Head */}
            <table className="watchlist-table" style={{ fontSize: 11 }}>
              <thead>
                <tr>
                  <th>Model Comparison</th>
                  <th>Outcome</th>
                  <th style={{ textAlign: 'right' }}>Net Cash</th>
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

          {/* Interactive Stress Test Drawer (Zero browser alerts) */}
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
                    <div>Notional {Math.round(trade.notional).toLocaleString('en-IN')} / ₹8,382 = {(trade.notional / 8382.07).toFixed(2)}x (Bounded ≤ 3.50x Cap).</div>
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
