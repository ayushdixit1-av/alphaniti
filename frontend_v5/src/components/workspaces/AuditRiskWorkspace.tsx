import React, { useState } from 'react';
import { Calendar, FileSpreadsheet, TrendingUp } from 'lucide-react';
import { AuditLedgerResponse, LiveEngineStatus, PremarketMacroResponse } from '../../types/api';

interface AuditRiskWorkspaceProps {
  auditLedger: AuditLedgerResponse | null;
  engineStatus: LiveEngineStatus | null;
  macro: PremarketMacroResponse | null;
  onInspectSymbol: (sym: string) => void;
}

export const AuditRiskWorkspace: React.FC<AuditRiskWorkspaceProps> = ({
  auditLedger,
  onInspectSymbol,
}) => {
  const [auditDate, setAuditDate] = useState(auditLedger?.date || '2026-11-09');
  const [historicalResult, setHistoricalResult] = useState<any>(null);
  const [loadingDate, setLoadingDate] = useState(false);
  const [hoverPointIdx, setHoverPointIdx] = useState<number | null>(null);

  const trades = auditLedger?.trades || [];
  const perf = auditLedger?.performance_analytics;
  const startCap = perf?.starting_capital_rs ?? auditLedger?.summary?.starting_capital ?? 8382.07;
  const totalGross = perf?.gross_pnl_rs ?? trades.reduce((acc, t) => acc + t.gross_pnl, 0);
  const totalFees = perf?.statutory_charges_rs ?? trades.reduce((acc, t) => acc + t.fees, 0);
  const totalNet = perf?.net_pnl_after_charges_rs ?? trades.reduce((acc, t) => acc + t.net_pnl, 0);
  const returnPct = perf?.return_pct ?? (startCap > 0 ? (totalNet / startCap) * 100 : 0);
  const winsCount = perf?.wins ?? trades.filter((t) => t.net_pnl > 0).length;
  const lossesCount = perf?.losses ?? trades.filter((t) => t.net_pnl < 0).length;
  const winRatePct = perf?.win_rate_pct ?? (trades.length > 0 ? (winsCount / trades.length) * 100 : 0);
  const avgWinRs = perf?.avg_win_rs ?? 0;
  const avgLossRs = perf?.avg_loss_rs ?? 0;
  const profitFactor = perf?.profit_factor;
  const expectancyRs = perf?.expectancy_rs ?? (trades.length > 0 ? totalNet / trades.length : 0);
  const expectancyR = perf?.expectancy_r ?? 0;
  const maxDdRs = perf?.max_drawdown_rs ?? 0;
  const maxDdPct = perf?.max_drawdown_pct ?? 0;
  const eqCurve = perf?.equity_curve || [];
  const compMatrix = auditLedger?.comparative_matrix
    ? Object.entries(auditLedger.comparative_matrix)
    : [];

  const runDateAudit = async (dateStr: string) => {
    setLoadingDate(true);
    try {
      const resp = await fetch(`/api/audit_date?date=${encodeURIComponent(dateStr)}`);
      if (resp.ok) {
        setHistoricalResult(await resp.json());
      }
    } finally {
      setLoadingDate(false);
    }
  };

  // Equity curve SVG geometry
  const w = 680;
  const h = 190;
  const padL = 54;
  const padR = 18;
  const padT = 18;
  const padB = 24;
  const eqVals = eqCurve.map((p) => p.equity);
  const minEq = eqVals.length ? Math.min(...eqVals) : startCap;
  const maxEq = eqVals.length ? Math.max(...eqVals) : startCap + 100;
  const span = maxEq - minEq || 10;

  const xPos = (i: number) => padL + (i / Math.max(1, eqCurve.length - 1)) * (w - padL - padR);
  const yPos = (eq: number) => padT + (1 - (eq - minEq) / span) * (h - padT - padB);

  const linePath = eqCurve
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xPos(i).toFixed(1)} ${yPos(p.equity).toFixed(1)}`)
    .join(' ');
  const areaPath =
    eqCurve.length > 0
      ? `${linePath} L ${xPos(eqCurve.length - 1).toFixed(1)} ${h - padB} L ${xPos(0).toFixed(1)} ${h - padB} Z`
      : '';

  const hovered = hoverPointIdx !== null ? eqCurve[hoverPointIdx] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* TOP: 7 Performance Analytics KPI Strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(175px, 1fr))',
          gap: 8,
        }}
      >
        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>NET P&amp;L (AFTER CHARGES)</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: totalNet >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
            {totalNet >= 0 ? '+' : ''}₹{totalNet.toFixed(2)}{' '}
            <span style={{ fontSize: 11.5 }}>({returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Gross +₹{totalGross.toFixed(2)} − Fees ₹{totalFees.toFixed(2)}
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>PROFIT FACTOR</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: 'var(--accent-green)' }}>
            {profitFactor != null ? `${profitFactor.toFixed(2)}x` : '--'}
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Payoff Ratio: {perf?.payoff_ratio != null ? `${perf.payoff_ratio.toFixed(2)}x` : '--'}
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>EXPECTANCY / TRADE</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: expectancyRs >= 0 ? 'var(--accent-cyan)' : 'var(--accent-red)' }}>
            {expectancyRs >= 0 ? '+' : ''}₹{expectancyRs.toFixed(2)}
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Mean R-Multiple: {expectancyR >= 0 ? '+' : ''}{expectancyR.toFixed(2)}R / trade
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>WIN RATE</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: '#fff' }}>
            {winRatePct.toFixed(1)}%
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            <strong style={{ color: 'var(--accent-green)' }}>{winsCount} Wins</strong> •{' '}
            <strong style={{ color: 'var(--accent-red)' }}>{lossesCount} Loss</strong> ({trades.length} Total)
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>AVERAGE WIN / LOSS</div>
          <div className="mono" style={{ fontSize: 15, fontWeight: 800, marginTop: 2 }}>
            <span style={{ color: 'var(--accent-green)' }}>+₹{avgWinRs.toFixed(2)}</span>{' '}
            <span style={{ color: 'var(--text-dim)' }}>/</span>{' '}
            <span style={{ color: 'var(--accent-red)' }}>-₹{Math.abs(avgLossRs).toFixed(2)}</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Net realization per win/loss leg
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>MAXIMUM DRAWDOWN</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: maxDdRs < -100 ? 'var(--accent-red)' : 'var(--accent-amber)' }}>
            ₹{maxDdRs.toFixed(2)}{' '}
            <span style={{ fontSize: 11.5 }}>({maxDdPct.toFixed(2)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Start ₹{startCap.toFixed(2)} ➔ End ₹{(startCap + totalNet).toFixed(2)}
          </div>
        </div>
      </div>

      {/* MIDDLE: Interactive Equity Curve Chart + Strategy Comparison & 5Y Date Auditor */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.35fr 1fr', gap: 8 }}>
        {/* Left: Full Equity Curve */}
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <TrendingUp size={14} color="var(--accent-green)" />
              Intraday Equity Curve After Statutory Charges ({auditLedger?.date || 'Session'})
            </span>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent-green)' }}>
              {hovered
                ? `${hovered.time} ${hovered.symbol}: ₹${hovered.equity.toFixed(2)} (${hovered.trade_net >= 0 ? '+' : ''}₹${hovered.trade_net.toFixed(2)})`
                : `Peak ₹${maxEq.toFixed(2)}`}
            </span>
          </div>

          <div style={{ padding: 10 }}>
            <svg
              viewBox={`0 0 ${w} ${h}`}
              style={{ width: '100%', height: 195, background: '#05080f', borderRadius: 8 }}
              onMouseLeave={() => setHoverPointIdx(null)}
            >
              <defs>
                <linearGradient id="auditEqFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity="0.34" />
                  <stop offset="100%" stopColor="#10b981" stopOpacity="0.0" />
                </linearGradient>
              </defs>

              {/* Starting capital baseline */}
              <line
                x1={padL}
                x2={w - padR}
                y1={yPos(startCap)}
                y2={yPos(startCap)}
                stroke="rgba(148,163,184,0.28)"
                strokeDasharray="4 4"
              />
              <text x={8} y={yPos(startCap) + 3} fill="#64748b" fontSize="9" fontFamily="JetBrains Mono">
                ₹{Math.round(startCap)}
              </text>
              <text x={8} y={padT + 6} fill="#10b981" fontSize="9" fontFamily="JetBrains Mono">
                ₹{Math.round(maxEq)}
              </text>

              <path d={areaPath} fill="url(#auditEqFill)" />
              <path d={linePath} fill="none" stroke="#10b981" strokeWidth="2.2" />

              {eqCurve.map((pt, i) => (
                <g key={i}>
                  <circle
                    cx={xPos(i)}
                    cy={yPos(pt.equity)}
                    r={hoverPointIdx === i ? 5.5 : 3.5}
                    fill={pt.trade_net < 0 ? '#f43f5e' : '#10b981'}
                    stroke="#06090e"
                    strokeWidth={1.2}
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={() => setHoverPointIdx(i)}
                  />
                  <text
                    x={xPos(i)}
                    y={h - 6}
                    textAnchor="middle"
                    fill="#64748b"
                    fontSize="8.5"
                    fontFamily="JetBrains Mono"
                  >
                    {pt.time}
                  </text>
                </g>
              ))}
            </svg>
          </div>
        </div>

        {/* Right: Comparative Matrix + 5Y Historical Date Auditor */}
        <div className="panel-card" style={{ padding: 12, gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
              <FileSpreadsheet size={14} color="var(--accent-cyan)" />
              Architecture Attribution &amp; 5Y Date Auditor
            </span>
          </div>

          {compMatrix.length > 0 && (
            <table className="watchlist-table" style={{ fontSize: 10.5 }}>
              <thead>
                <tr>
                  <th>Architecture Mode</th>
                  <th style={{ textAlign: 'right' }}>Trades</th>
                  <th style={{ textAlign: 'right' }}>Net Cash</th>
                </tr>
              </thead>
              <tbody>
                {compMatrix.map(([name, m]) => (
                  <tr key={name} style={{ background: name.includes('All-Weather') ? 'rgba(16,185,129,0.08)' : undefined }}>
                    <td style={{ fontWeight: name.includes('All-Weather') ? 700 : 500 }}>{name}</td>
                    <td style={{ textAlign: 'right' }}>{m.trades}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, color: m.net_pnl_rs > 0 ? 'var(--accent-green)' : m.net_pnl_rs < 0 ? 'var(--accent-red)' : 'var(--text-muted)' }}>
                      {m.net_pnl_rs >= 0 ? '+' : ''}₹{m.net_pnl_rs.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
            <Calendar size={14} color="var(--accent-cyan)" />
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Historical Audit:</span>
            <input
              type="date"
              value={auditDate}
              onChange={(e) => setAuditDate(e.target.value)}
              style={{
                background: '#060a11',
                color: 'var(--text-main)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 5,
                padding: '3px 8px',
                fontFamily: 'var(--mono)',
                fontSize: 11,
              }}
            />
            <button
              className="tf-btn active"
              onClick={() => runDateAudit(auditDate)}
              disabled={loadingDate}
            >
              {loadingDate ? 'Auditing...' : 'Run Date Audit'}
            </button>
          </div>

          {historicalResult && (
            <div
              className="mono"
              style={{
                background: '#05080f',
                padding: 8,
                borderRadius: 6,
                border: '1px solid var(--border-subtle)',
                fontSize: 10.5,
                maxHeight: 82,
                overflowY: 'auto',
              }}
            >
              {JSON.stringify(historicalResult)}
            </div>
          )}
        </div>
      </div>

      {/* Full Verified Execution Ledger */}
      <div className="panel-card">
        <div className="panel-head">
          <span>Verified Execution Audit Ledger ({trades.length} Trades • {winRatePct.toFixed(1)}% Win Rate • Net +₹{totalNet.toFixed(2)})</span>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent-green)' }}>
            Capital: ₹{startCap.toFixed(2)} ➔ ₹{(startCap + totalNet).toFixed(2)}
          </span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="watchlist-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Exit Reason</th>
                <th style={{ textAlign: 'right' }}>Peak R</th>
                <th style={{ textAlign: 'right' }}>Gross</th>
                <th style={{ textAlign: 'right' }}>Charges</th>
                <th style={{ textAlign: 'right' }}>Net Cash</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={`${t.symbol}-${i}`} className="watchlist-row" onClick={() => onInspectSymbol(t.symbol)}>
                  <td style={{ fontWeight: 800 }}>{t.symbol}</td>
                  <td style={{ fontSize: 10, color: 'var(--accent-cyan)' }}>{t.strategy}</td>
                  <td>
                    {t.entry_time} @ ₹{t.entry_price.toFixed(2)}
                  </td>
                  <td>
                    {t.exit_time} @ ₹{t.exit_price.toFixed(2)}
                  </td>
                  <td style={{ fontSize: 10, color: 'var(--text-muted)' }}>{t.exit_reason}</td>
                  <td style={{ textAlign: 'right', color: 'var(--accent-cyan)' }}>+{t.peak_r.toFixed(2)}R</td>
                  <td style={{ textAlign: 'right' }}>₹{t.gross_pnl.toFixed(2)}</td>
                  <td style={{ textAlign: 'right', color: 'var(--accent-amber)' }}>-₹{t.fees.toFixed(2)}</td>
                  <td
                    style={{
                      textAlign: 'right',
                      fontWeight: 800,
                      color: t.net_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                    }}
                  >
                    {t.net_pnl >= 0 ? '+' : ''}₹{t.net_pnl.toFixed(2)} ({t.r_multiple}R)
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
