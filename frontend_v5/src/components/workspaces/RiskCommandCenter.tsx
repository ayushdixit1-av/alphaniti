import React, { useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Gauge,
  Lock,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  Wallet,
} from 'lucide-react';
import { RiskCommandResponse } from '../../types/api';

interface RiskCommandCenterProps {
  riskData: RiskCommandResponse | null;
  onInspectSymbol: (sym: string) => void;
}

export const RiskCommandCenter: React.FC<RiskCommandCenterProps> = ({
  riskData,
  onInspectSymbol,
}) => {
  const [calcSymbol, setCalcSymbol] = useState('RELIANCE');
  const [customRiskPts, setCustomRiskPts] = useState<number>(12.5);

  if (!riskData) {
    return (
      <div className="panel-card" style={{ padding: 28, textAlign: 'center', color: 'var(--text-muted)' }}>
        Connecting to Risk Command Telemetry (/api/risk_command)...
      </div>
    );
  }

  const mdd = riskData.live_max_drawdown;
  const rpt = riskData.risk_per_trade;
  const margin = riskData.available_margin;
  const dll = riskData.daily_loss_limit;
  const exp = riskData.exposure_utilization;
  const series = riskData.equity_drawdown_series || [];
  const breakers = riskData.circuit_breakers || [];
  const proximity = riskData.constituent_circuit_proximity || [];

  // Pre-flight Live Position Sizer Calculation
  const selectedConstituent = proximity.find((p) => p.symbol === calcSymbol) || proximity[0];
  const calcLtp = selectedConstituent?.ltp && selectedConstituent.ltp > 0 ? selectedConstituent.ltp : 1324.4;
  const safeRiskDist = Math.max(1.0, customRiskPts);
  const rawSharesByRisk = Math.floor(rpt.risk_rs / safeRiskDist);
  const maxSharesByCap = Math.floor(exp.max_notional_cap_rs / calcLtp);
  const approvedShares = Math.max(0, Math.min(rawSharesByRisk, maxSharesByCap));
  const simNotional = approvedShares * calcLtp;
  const simNotionalMult = margin.clear_cash_rs > 0 ? simNotional / margin.clear_cash_rs : 0;
  const simRiskRs = approvedShares * safeRiskDist;
  const simFloorPass = margin.clear_cash_rs >= margin.hard_reserve_floor_rs && simNotional <= exp.max_notional_cap_rs;

  // Dual-panel Equity + Underwater Drawdown SVG geometry
  const w = 640;
  const h = 210;
  const padX = 46;
  const eqTop = 14;
  const eqH = 92;
  const ddTop = 122;
  const ddH = 68;

  const eqVals = series.map((s) => s.equity);
  const minEq = eqVals.length ? Math.min(...eqVals) : 8300;
  const maxEq = eqVals.length ? Math.max(...eqVals) : 8600;
  const eqSpan = maxEq - minEq || 10;

  const ddVals = series.map((s) => s.drawdown_pct);
  const minDd = Math.min(-dll.limit_pct, ...(ddVals.length ? ddVals : [-1.0]));

  const xPos = (idx: number) =>
    padX + (idx / Math.max(1, series.length - 1)) * (w - padX - 18);
  const yEq = (v: number) => eqTop + (1 - (v - minEq) / eqSpan) * eqH;
  const yDd = (pct: number) => ddTop + (Math.min(0, pct) / minDd) * ddH;

  const eqLine = series
    .map((pt, i) => `${i === 0 ? 'M' : 'L'} ${xPos(i).toFixed(1)} ${yEq(pt.equity).toFixed(1)}`)
    .join(' ');
  const eqArea =
    series.length > 0
      ? `${eqLine} L ${xPos(series.length - 1).toFixed(1)} ${eqTop + eqH} L ${xPos(0).toFixed(1)} ${eqTop + eqH} Z`
      : '';

  const ddLine = series
    .map((pt, i) => `${i === 0 ? 'M' : 'L'} ${xPos(i).toFixed(1)} ${yDd(pt.drawdown_pct).toFixed(1)}`)
    .join(' ');
  const ddArea =
    series.length > 0
      ? `M ${xPos(0).toFixed(1)} ${ddTop} ${series
          .map((pt, i) => `L ${xPos(i).toFixed(1)} ${yDd(pt.drawdown_pct).toFixed(1)}`)
          .join(' ')} L ${xPos(series.length - 1).toFixed(1)} ${ddTop} Z`
      : '';

  const statusColor = (sev: string) =>
    sev === 'CRITICAL' ? 'var(--accent-red)' : sev === 'WARN' ? 'var(--accent-amber)' : 'var(--accent-green)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* TOP ROW: 6 Primary Institutional Risk KPIs */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(195px, 1fr))',
          gap: 8,
        }}
      >
        {/* 1. Live Maximum Drawdown */}
        <div className="panel-card" style={{ padding: '10px 12px', gap: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>LIVE MAX DRAWDOWN</span>
            <TrendingDown size={13} color={mdd.drawdown_rs < 0 ? 'var(--accent-amber)' : 'var(--accent-green)'} />
          </div>
          <div className="mono" style={{ fontSize: 19, fontWeight: 800, color: mdd.drawdown_rs < -150 ? 'var(--accent-red)' : 'var(--accent-green)' }}>
            ₹{mdd.drawdown_rs.toFixed(2)}{' '}
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>({mdd.drawdown_pct.toFixed(2)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            HWM: ₹{mdd.peak_equity_rs.toLocaleString('en-IN')} • {mdd.session_label}
          </div>
        </div>

        {/* 2. Risk Per Trade */}
        <div className="panel-card" style={{ padding: '10px 12px', gap: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>RISK PER TRADE (1R)</span>
            <ShieldCheck size={13} color="var(--accent-cyan)" />
          </div>
          <div className="mono" style={{ fontSize: 19, fontWeight: 800, color: 'var(--accent-cyan)' }}>
            ₹{rpt.risk_rs.toFixed(2)}{' '}
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>({rpt.risk_pct.toFixed(1)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Active Open Risk: <strong style={{ color: rpt.active_trade_risk_rs > 0 ? 'var(--accent-amber)' : 'var(--accent-green)' }}>₹{rpt.active_trade_risk_rs.toFixed(2)}</strong>
          </div>
        </div>

        {/* 3. Available Margin */}
        <div className="panel-card" style={{ padding: '10px 12px', gap: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>AVAILABLE MARGIN</span>
            <Wallet size={13} color="var(--accent-green)" />
          </div>
          <div className="mono" style={{ fontSize: 19, fontWeight: 800, color: '#fff' }}>
            ₹{margin.available_margin_rs.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Deployable (&gt;₹2.5k Floor): <strong style={{ color: 'var(--accent-green)' }}>₹{margin.deployable_margin_rs.toLocaleString('en-IN')}</strong>
          </div>
        </div>

        {/* 4. Daily Loss Limit */}
        <div className="panel-card" style={{ padding: '10px 12px', gap: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>DAILY LOSS LIMIT</span>
            <ShieldAlert size={13} color={dll.consumed_pct > 65 ? 'var(--accent-red)' : 'var(--accent-amber)'} />
          </div>
          <div className="mono" style={{ fontSize: 19, fontWeight: 800, color: 'var(--accent-amber)' }}>
            -₹{dll.limit_rs.toFixed(2)}{' '}
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>({dll.limit_pct.toFixed(1)}%)</span>
          </div>
          <div className="factor-track" style={{ height: 5 }}>
            <div
              style={{
                width: `${dll.consumed_pct}%`,
                height: '100%',
                background: dll.consumed_pct > 70 ? 'var(--accent-red)' : 'var(--accent-amber)',
              }}
            />
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Remaining Headroom: <strong style={{ color: 'var(--accent-green)' }}>₹{dll.remaining_rs.toFixed(2)}</strong>
          </div>
        </div>

        {/* 5. Exposure Utilization */}
        <div className="panel-card" style={{ padding: '10px 12px', gap: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>EXPOSURE UTILIZATION</span>
            <Gauge size={13} color="var(--accent-cyan)" />
          </div>
          <div className="mono" style={{ fontSize: 19, fontWeight: 800 }}>
            {exp.utilization_pct.toFixed(1)}%{' '}
            <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>of {exp.max_notional_multiple.toFixed(1)}x</span>
          </div>
          <div className="factor-track" style={{ height: 5 }}>
            <div
              style={{
                width: `${Math.min(100, exp.utilization_pct || exp.session_peak_utilization_pct)}%`,
                height: '100%',
                background: exp.utilization_pct > 0 ? 'var(--accent-cyan)' : 'rgba(56,189,248,0.45)',
              }}
            />
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Cap ₹{Math.round(exp.max_notional_cap_rs).toLocaleString('en-IN')} • Peak {exp.session_peak_utilization_pct.toFixed(1)}%
          </div>
        </div>

        {/* 6. Circuit-Breaker Status */}
        <div
          className="panel-card"
          style={{
            padding: '10px 12px',
            gap: 5,
            borderColor: riskData.tripped_count > 0 ? 'rgba(244,63,94,0.5)' : 'rgba(16,185,129,0.35)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10.5, color: 'var(--text-muted)' }}>
            <span>CIRCUIT BREAKERS</span>
            <Lock size={13} color={riskData.tripped_count > 0 ? 'var(--accent-red)' : 'var(--accent-green)'} />
          </div>
          <div
            className="mono"
            style={{
              fontSize: 15.5,
              fontWeight: 800,
              color: riskData.tripped_count > 0 ? 'var(--accent-red)' : 'var(--accent-amber)',
              marginTop: 2,
            }}
          >
            {riskData.overall_breaker_status}
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            {breakers.length} Guards Active • <strong style={{ color: 'var(--accent-green)' }}>{riskData.tripped_count} Tripped</strong>
          </div>
        </div>
      </div>

      {/* MIDDLE ROW: Live Equity & Underwater Drawdown Chart + Margin & Pre-Flight Position Sizer */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 8 }}>
        {/* Left: Equity + Underwater Drawdown Chart */}
        <div className="panel-card">
          <div className="panel-head">
            <span>Live Equity &amp; Underwater Drawdown Curve ({mdd.session_label})</span>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
              Peak ₹{mdd.peak_equity_rs.toFixed(2)} • Current ₹{mdd.current_equity_rs.toFixed(2)}
            </span>
          </div>
          <div style={{ padding: 10 }}>
            <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 215, background: '#05080f', borderRadius: 8 }}>
              <defs>
                <linearGradient id="riskEqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity="0.32" />
                  <stop offset="100%" stopColor="#10b981" stopOpacity="0.0" />
                </linearGradient>
              </defs>

              {/* Top Panel Label */}
              <text x={8} y={eqTop + 10} fill="#64748b" fontSize="9.5" fontFamily="JetBrains Mono">
                EQUITY ₹
              </text>
              <path d={eqArea} fill="url(#riskEqGrad)" />
              <path d={eqLine} fill="none" stroke="#10b981" strokeWidth="2" />
              {series.map((pt, i) => (
                <circle key={`eq-${i}`} cx={xPos(i)} cy={yEq(pt.equity)} r={2.5} fill="#10b981">
                  <title>{`${pt.time} ${pt.symbol || ''}: ₹${pt.equity.toFixed(2)}`}</title>
                </circle>
              ))}

              {/* Separator & Bottom Underwater Drawdown Panel */}
              <line x1={padX} x2={w - 18} y1={ddTop} y2={ddTop} stroke="rgba(148,163,184,0.22)" />
              <text x={8} y={ddTop + 10} fill="#64748b" fontSize="9.5" fontFamily="JetBrains Mono">
                DD %
              </text>
              {/* Daily Loss Limit Ceiling Line */}
              <line
                x1={padX}
                x2={w - 18}
                y1={yDd(-dll.limit_pct)}
                y2={yDd(-dll.limit_pct)}
                stroke="#f43f5e"
                strokeDasharray="4 4"
                strokeWidth="1.2"
              />
              <text x={w - 145} y={yDd(-dll.limit_pct) - 4} fill="#f43f5e" fontSize="9" fontFamily="JetBrains Mono">
                DAILY LIMIT -{dll.limit_pct.toFixed(1)}%
              </text>

              <path d={ddArea} fill="rgba(244, 63, 94, 0.28)" />
              <path d={ddLine} fill="none" stroke="#f43f5e" strokeWidth="1.8" />
              {series.map((pt, i) => (
                <text
                  key={`lbl-${i}`}
                  x={xPos(i)}
                  y={h - 4}
                  textAnchor="middle"
                  fill="#64748b"
                  fontSize="8.5"
                  fontFamily="JetBrains Mono"
                >
                  {pt.time}
                </text>
              ))}
            </svg>
          </div>
        </div>

        {/* Right: Margin Allocation & Pre-Flight Position Size Simulator */}
        <div className="panel-card" style={{ padding: 12, gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>Margin Allocator &amp; Pre-Flight Sizer</span>
            <span className="mono" style={{ fontSize: 10, color: simFloorPass ? 'var(--accent-green)' : 'var(--accent-red)' }}>
              {simFloorPass ? 'INVARIANT PASS' : 'CAP VIOLATION'}
            </span>
          </div>

          {/* Margin Stacked Bar */}
          <div>
            <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
              <span>Hard Floor ₹{margin.hard_reserve_floor_rs.toLocaleString('en-IN')}</span>
              <span>Deployable ₹{margin.deployable_margin_rs.toLocaleString('en-IN')}</span>
              <span>MIS Used ₹{margin.mis_margin_used_rs.toFixed(0)}</span>
            </div>
            <div style={{ height: 12, borderRadius: 6, overflow: 'hidden', display: 'flex', background: '#060a11', border: '1px solid var(--border-subtle)' }}>
              <div
                style={{
                  width: `${(margin.hard_reserve_floor_rs / Math.max(1, margin.clear_cash_rs)) * 100}%`,
                  background: 'var(--accent-amber)',
                }}
                title="Mandatory ₹2,500 Reserve Floor"
              />
              <div
                style={{
                  width: `${(margin.deployable_margin_rs / Math.max(1, margin.clear_cash_rs)) * 100}%`,
                  background: 'rgba(16,185,129,0.55)',
                }}
                title="Deployable Equity Buffer"
              />
            </div>
          </div>

          {/* Interactive Position Sizer */}
          <div
            style={{
              background: '#060a11',
              borderRadius: 8,
              border: '1px solid var(--border-subtle)',
              padding: 10,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                value={calcSymbol}
                onChange={(e) => setCalcSymbol(e.target.value)}
                style={{
                  background: 'var(--bg-surface)',
                  color: 'var(--text-main)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 5,
                  padding: '4px 8px',
                  fontFamily: 'var(--mono)',
                  fontSize: 11.5,
                }}
              >
                {proximity.map((p) => (
                  <option key={p.symbol} value={p.symbol}>
                    {p.symbol} (₹{p.ltp.toFixed(1)})
                  </option>
                ))}
              </select>
              <label className="mono" style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
                SL Distance ₹:
                <input
                  type="number"
                  min={1}
                  step={0.5}
                  value={customRiskPts}
                  onChange={(e) => setCustomRiskPts(Number(e.target.value) || 1)}
                  style={{
                    width: 68,
                    background: 'var(--bg-surface)',
                    color: 'var(--accent-cyan)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 5,
                    padding: '3px 6px',
                    fontFamily: 'var(--mono)',
                    fontSize: 11.5,
                  }}
                />
              </label>
            </div>

            <div className="mono" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, fontSize: 11 }}>
              <div>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Approved Qty</div>
                <strong style={{ fontSize: 14, color: 'var(--accent-cyan)' }}>{approvedShares} sh</strong>
              </div>
              <div>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Notional (≤ 3.5x)</div>
                <strong style={{ fontSize: 14, color: simNotionalMult <= 3.5 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                  ₹{Math.round(simNotional).toLocaleString('en-IN')} ({simNotionalMult.toFixed(2)}x)
                </strong>
              </div>
              <div>
                <div style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>Exact ₹ Risk</div>
                <strong style={{ fontSize: 14, color: 'var(--text-main)' }}>₹{simRiskRs.toFixed(2)}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* BOTTOM ROW: Circuit Breakers Grid + 9-Stock NSE Circuit Band Proximity */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 8 }}>
        {/* 6 Live Circuit Breakers */}
        <div className="panel-card" style={{ padding: 12, gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
              <CheckCircle2 size={14} color="var(--accent-green)" />
              Hardware &amp; Protocol Circuit-Breaker Matrix
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              Real-Time Daemon Interlocks
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
            {breakers.map((b) => (
              <div
                key={b.id}
                style={{
                  background: '#060a11',
                  border: `1px solid ${b.severity === 'CRITICAL' ? 'rgba(244,63,94,0.45)' : b.severity === 'WARN' ? 'rgba(245,158,11,0.35)' : 'var(--border-subtle)'}`,
                  borderRadius: 7,
                  padding: '8px 10px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 3,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontSize: 11.5 }}>{b.name}</strong>
                  <span
                    className="lvl-pill mono"
                    style={{
                      background:
                        b.severity === 'CRITICAL'
                          ? 'rgba(244,63,94,0.18)'
                          : b.severity === 'WARN'
                          ? 'rgba(245,158,11,0.18)'
                          : 'rgba(16,185,129,0.18)',
                      color: statusColor(b.severity),
                    }}
                  >
                    {b.status}
                  </span>
                </div>
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--accent-cyan)' }}>{b.live_value}</div>
                <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                  Rule: {b.threshold}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 9-Stock NSE Price Band Circuit Proximity */}
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <AlertTriangle size={13} color="var(--accent-amber)" />
              Constituent Circuit Proximity (±20% Band)
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              Click to Inspect
            </span>
          </div>
          <div style={{ overflowY: 'auto', maxHeight: 255 }}>
            <table className="watchlist-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th style={{ textAlign: 'right' }}>LTP</th>
                  <th style={{ textAlign: 'right' }}>Day Δ%</th>
                  <th style={{ textAlign: 'right' }}>LC Buffer</th>
                  <th style={{ textAlign: 'right' }}>UC Buffer</th>
                  <th style={{ textAlign: 'right' }}>Gate</th>
                </tr>
              </thead>
              <tbody>
                {proximity.map((p) => (
                  <tr key={p.symbol} className="watchlist-row" onClick={() => onInspectSymbol(p.symbol)}>
                    <td style={{ fontWeight: 700 }}>{p.symbol}</td>
                    <td style={{ textAlign: 'right' }}>₹{p.ltp.toFixed(2)}</td>
                    <td style={{ textAlign: 'right', color: p.day_chg_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                      {p.day_chg_pct >= 0 ? '+' : ''}{p.day_chg_pct.toFixed(2)}%
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>-{p.dist_to_lower_circuit_pct.toFixed(1)}%</td>
                    <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>+{p.dist_to_upper_circuit_pct.toFixed(1)}%</td>
                    <td style={{ textAlign: 'right' }}>
                      <span
                        className="lvl-pill"
                        style={{
                          background: p.status === 'SAFE' ? 'rgba(16,185,129,0.16)' : 'rgba(244,63,94,0.18)',
                          color: p.status === 'SAFE' ? 'var(--accent-green)' : 'var(--accent-red)',
                        }}
                      >
                        {p.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};
