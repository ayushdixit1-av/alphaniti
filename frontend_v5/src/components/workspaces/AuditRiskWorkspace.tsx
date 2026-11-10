import React, { useMemo, useState } from 'react';
import { Calendar, CheckCircle2, Clock, FileSpreadsheet, TrendingUp, XCircle } from 'lucide-react';
import { AuditLedgerResponse, LiveEngineStatus, PremarketMacroResponse } from '../../types/api';

interface AuditRiskWorkspaceProps {
  auditLedger: AuditLedgerResponse | null;
  engineStatus: LiveEngineStatus | null;
  macro: PremarketMacroResponse | null;
  onInspectSymbol: (sym: string) => void;
}

function parseRs(val: any): number {
  if (typeof val === 'number') return val;
  if (!val) return 0;
  const cleaned = String(val).replace(/[₹,+\s]/g, '').replace(/\(.*\)/, '');
  const n = parseFloat(cleaned);
  return Number.isFinite(n) ? n : 0;
}

export const AuditRiskWorkspace: React.FC<AuditRiskWorkspaceProps> = ({
  auditLedger,
  onInspectSymbol,
}) => {
  const [auditDate, setAuditDate] = useState('2026-11-04');
  const [historicalResult, setHistoricalResult] = useState<any>(null);
  const [viewMode, setViewMode] = useState<'SESSION' | 'HISTORICAL'>('SESSION');
  const [loadingDate, setLoadingDate] = useState(false);
  const [dateError, setDateError] = useState<string | null>(null);
  const [hoverPointIdx, setHoverPointIdx] = useState<number | null>(null);

  const runDateAudit = async (dateStr: string) => {
    setLoadingDate(true);
    setDateError(null);
    try {
      const resp = await fetch(`/api/audit_date?date=${encodeURIComponent(dateStr)}`);
      if (!resp.ok) {
        setDateError(`HTTP ${resp.status} while querying ${dateStr}`);
        return;
      }
      const data = await resp.json();
      if (!data.success) {
        setDateError(data.error || `No trading session data available for ${dateStr}`);
        return;
      }
      setHistoricalResult(data);
      setViewMode('HISTORICAL');
      setHoverPointIdx(null);
    } catch (err: any) {
      setDateError(err?.message || 'Network error querying historical audit');
    } finally {
      setLoadingDate(false);
    }
  };

  // Convert historicalResult into structured Performance Analytics + Equity Curve + Trades
  const historicalAnalytics = useMemo(() => {
    if (!historicalResult || !historicalResult.success) return null;
    const cap = Number(historicalResult.capital || 50000);
    const cross: any[] = historicalResult.cross_sectional || [];
    const lifecycle: any[] = historicalResult.lifecycle_execution || [];

    const findParam = (kw: string) =>
      lifecycle.find((r) => String(r.param || '').toLowerCase().includes(kw))?.value;

    const primaryGross = parseRs(findParam('gross trading'));
    const primaryFees = parseRs(findParam('statutory fees'));
    const primaryNet = Number(historicalResult.net_pnl ?? parseRs(findParam('net realized')));

    const legPnls = cross.map((c) => parseRs(c.net_pnl));
    const legsToAnalyze = legPnls.length > 0 ? legPnls : [primaryNet];
    const winsArr = legsToAnalyze.filter((v) => v > 0);
    const lossArr = legsToAnalyze.filter((v) => v < 0);
    const netSum = cross.length > 0 ? legsToAnalyze.reduce((a, b) => a + b, 0) : primaryNet;
    const winSum = winsArr.reduce((a, b) => a + b, 0);
    const lossAbs = Math.abs(lossArr.reduce((a, b) => a + b, 0));

    let cum = 0;
    let peak = 0;
    let maxDdRs = 0;
    let maxDdPct = 0;
    const curve = [
      { time: '09:15', symbol: 'START', trade_net: 0, cum_net: 0, equity: cap, drawdown_rs: 0, drawdown_pct: 0 },
    ];

    if (cross.length > 0) {
      cross.forEach((c) => {
        const netV = parseRs(c.net_pnl);
        cum = Number((cum + netV).toFixed(2));
        if (cum > peak) peak = cum;
        const dd = Number((cum - peak).toFixed(2));
        const ddPct = cap + peak > 0 ? Number(((dd / (cap + peak)) * 100).toFixed(2)) : 0;
        if (dd < maxDdRs) {
          maxDdRs = dd;
          maxDdPct = ddPct;
        }
        curve.push({
          time: String(c.exit_time || '10:30'),
          symbol: String(c.candidate || '').split(' ')[0],
          trade_net: netV,
          cum_net: cum,
          equity: Number((cap + cum).toFixed(2)),
          drawdown_rs: dd,
          drawdown_pct: ddPct,
        });
      });
    } else {
      curve.push({
        time: '15:15',
        symbol: historicalResult.top_pick?.symbol || 'TRADE',
        trade_net: primaryNet,
        cum_net: primaryNet,
        equity: Number((cap + primaryNet).toFixed(2)),
        drawdown_rs: Math.min(0, primaryNet),
        drawdown_pct: cap > 0 ? Number(((Math.min(0, primaryNet) / cap) * 100).toFixed(2)) : 0,
      });
    }

    const totalN = legsToAnalyze.length;
    const avgW = winsArr.length > 0 ? winSum / winsArr.length : 0;
    const avgL = lossArr.length > 0 ? -lossAbs / lossArr.length : 0;

    return {
      date: historicalResult.date,
      startCap: cap,
      totalGross: cross.length > 0 ? netSum + primaryFees * cross.length : primaryGross,
      totalFees: cross.length > 0 ? primaryFees * cross.length : primaryFees,
      totalNet: cross.length > 0 ? netSum : primaryNet,
      returnPct: cap > 0 ? ((cross.length > 0 ? netSum : primaryNet) / cap) * 100 : 0,
      winsCount: winsArr.length,
      lossesCount: lossArr.length,
      totalTrades: totalN,
      winRatePct: totalN > 0 ? (winsArr.length / totalN) * 100 : 0,
      avgWinRs: avgW,
      avgLossRs: avgL,
      profitFactor: lossAbs > 0 ? winSum / lossAbs : winsArr.length > 0 ? 99.99 : null,
      payoffRatio: avgL < 0 ? avgW / Math.abs(avgL) : winsArr.length > 0 ? 99.99 : null,
      expectancyRs: totalN > 0 ? netSum / totalN : 0,
      maxDdRs,
      maxDdPct,
      eqCurve: curve,
    };
  }, [historicalResult]);

  // Select active metrics source (Session vs Audited Historical Date)
  const trades = auditLedger?.trades || [];
  const perf = auditLedger?.performance_analytics;
  const useHist = viewMode === 'HISTORICAL' && historicalAnalytics !== null;

  const activeLabel = useHist ? `Historical ${historicalAnalytics!.date}` : auditLedger?.date || 'Session';
  const startCap = useHist ? historicalAnalytics!.startCap : perf?.starting_capital_rs ?? auditLedger?.summary?.starting_capital ?? 8382.07;
  const totalGross = useHist ? historicalAnalytics!.totalGross : perf?.gross_pnl_rs ?? trades.reduce((acc, t) => acc + t.gross_pnl, 0);
  const totalFees = useHist ? historicalAnalytics!.totalFees : perf?.statutory_charges_rs ?? trades.reduce((acc, t) => acc + t.fees, 0);
  const totalNet = useHist ? historicalAnalytics!.totalNet : perf?.net_pnl_after_charges_rs ?? trades.reduce((acc, t) => acc + t.net_pnl, 0);
  const returnPct = useHist ? historicalAnalytics!.returnPct : perf?.return_pct ?? (startCap > 0 ? (totalNet / startCap) * 100 : 0);
  const winsCount = useHist ? historicalAnalytics!.winsCount : perf?.wins ?? trades.filter((t) => t.net_pnl > 0).length;
  const lossesCount = useHist ? historicalAnalytics!.lossesCount : perf?.losses ?? trades.filter((t) => t.net_pnl < 0).length;
  const totalTrades = useHist ? historicalAnalytics!.totalTrades : trades.length;
  const winRatePct = useHist ? historicalAnalytics!.winRatePct : perf?.win_rate_pct ?? (trades.length > 0 ? (winsCount / trades.length) * 100 : 0);
  const avgWinRs = useHist ? historicalAnalytics!.avgWinRs : perf?.avg_win_rs ?? 0;
  const avgLossRs = useHist ? historicalAnalytics!.avgLossRs : perf?.avg_loss_rs ?? 0;
  const profitFactor = useHist ? historicalAnalytics!.profitFactor : perf?.profit_factor;
  const payoffRatio = useHist ? historicalAnalytics!.payoffRatio : perf?.payoff_ratio;
  const expectancyRs = useHist ? historicalAnalytics!.expectancyRs : perf?.expectancy_rs ?? (trades.length > 0 ? totalNet / trades.length : 0);
  const expectancyR = useHist ? (expectancyRs / Math.max(1, startCap * 0.015)) : perf?.expectancy_r ?? 0;
  const maxDdRs = useHist ? historicalAnalytics!.maxDdRs : perf?.max_drawdown_rs ?? 0;
  const maxDdPct = useHist ? historicalAnalytics!.maxDdPct : perf?.max_drawdown_pct ?? 0;
  const eqCurve = useHist ? historicalAnalytics!.eqCurve : perf?.equity_curve || [];
  const compMatrix = auditLedger?.comparative_matrix ? Object.entries(auditLedger.comparative_matrix) : [];

  // Equity curve SVG geometry
  const w = 680;
  const h = 190;
  const padL = 56;
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
      {/* TOP BAR: Session vs Historical Mode Switch + 5Y Date Auditor Controls */}
      <div className="panel-card" style={{ padding: '8px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)', fontWeight: 700 }}>Audit Source:</span>
            <button
              className={`tf-btn ${viewMode === 'SESSION' ? 'active' : ''}`}
              style={{ border: '1px solid var(--border-subtle)' }}
              onClick={() => setViewMode('SESSION')}
            >
              Active Session ({auditLedger?.date || '2026-11-09'})
            </button>
            {historicalAnalytics && (
              <button
                className={`tf-btn ${viewMode === 'HISTORICAL' ? 'active' : ''}`}
                style={{ border: '1px solid rgba(56,189,248,0.4)' }}
                onClick={() => setViewMode('HISTORICAL')}
              >
                Historical Audit ({historicalAnalytics.date})
              </button>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Calendar size={14} color="var(--accent-cyan)" />
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>5-Year Historical Auditor:</span>
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
        </div>
        {dateError && (
          <div className="mono" style={{ marginTop: 6, fontSize: 11, color: 'var(--accent-amber)' }}>
            {dateError}
          </div>
        )}
      </div>

      {/* 6 PERFORMANCE ANALYTICS KPI CARDS (Dynamically tracks Session OR Historical Audit) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(175px, 1fr))', gap: 8 }}>
        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>NET P&amp;L (AFTER CHARGES)</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: totalNet >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
            {totalNet >= 0 ? '+' : ''}₹{totalNet.toFixed(2)}{' '}
            <span style={{ fontSize: 11.5 }}>({returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Gross +₹{totalGross.toFixed(2)} − Charges ₹{totalFees.toFixed(2)}
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>PROFIT FACTOR</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: 'var(--accent-green)' }}>
            {profitFactor != null ? `${profitFactor.toFixed(2)}x` : '--'}
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Payoff Ratio: {payoffRatio != null ? `${payoffRatio.toFixed(2)}x` : '--'}
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>EXPECTANCY / TRADE</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: expectancyRs >= 0 ? 'var(--accent-cyan)' : 'var(--accent-red)' }}>
            {expectancyRs >= 0 ? '+' : ''}₹{expectancyRs.toFixed(2)}
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Mean R-Multiple: {expectancyR >= 0 ? '+' : ''}{expectancyR.toFixed(2)}R / leg
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>WIN RATE</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: '#fff' }}>
            {winRatePct.toFixed(1)}%
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            <strong style={{ color: 'var(--accent-green)' }}>{winsCount} Wins</strong> •{' '}
            <strong style={{ color: 'var(--accent-red)' }}>{lossesCount} Loss</strong> ({totalTrades} Legs)
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
            Net realization per win/loss
          </div>
        </div>

        <div className="panel-card" style={{ padding: '9px 12px', gap: 3 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>MAXIMUM DRAWDOWN</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 800, color: maxDdRs < -100 ? 'var(--accent-red)' : 'var(--accent-amber)' }}>
            ₹{maxDdRs.toFixed(2)}{' '}
            <span style={{ fontSize: 11.5 }}>({maxDdPct.toFixed(2)}%)</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--text-dim)' }}>
            Start ₹{startCap.toFixed(0)} ➔ End ₹{(startCap + totalNet).toFixed(0)}
          </div>
        </div>
      </div>

      {/* EQUITY CURVE + RIGHT ATTRIBUTION OR HISTORICAL GATES */}
      <div className="split-2-col">
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <TrendingUp size={14} color="var(--accent-green)" />
              Equity Curve After Statutory Charges ({activeLabel})
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
                    {pt.symbol === 'START' ? pt.time : pt.symbol}
                  </text>
                </g>
              ))}
            </svg>
          </div>
        </div>

        {/* Right Panel: Historical Gates Breakdown (when Historical) OR Architecture Comparison (when Session) */}
        {useHist && historicalResult ? (
          <div className="panel-card" style={{ padding: 12, gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
                {historicalResult.gates?.all_passed ? (
                  <CheckCircle2 size={14} color="var(--accent-green)" />
                ) : (
                  <XCircle size={14} color="var(--accent-amber)" />
                )}
                Microstructure Gates — {historicalResult.date}
              </span>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent-cyan)' }}>
                Top Pick: {historicalResult.top_pick?.symbol || '--'}
              </span>
            </div>

            <div style={{ fontSize: 11, color: '#cbd5e1', background: '#060a11', padding: '7px 9px', borderRadius: 6, border: '1px solid var(--border-subtle)' }}>
              {historicalResult.crucial_observation}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 10.5 }} className="mono">
              {Object.entries(historicalResult.gates || {})
                .filter(([k]) => k !== 'all_passed')
                .map(([gateKey, gateMsg]) => {
                  const passed = String(gateMsg).includes('PASS');
                  return (
                    <div key={gateKey} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, borderBottom: '1px solid rgba(148,163,184,0.06)', paddingBottom: 3 }}>
                      <span style={{ color: 'var(--text-dim)', textTransform: 'uppercase' }}>{gateKey.replace(/_/g, ' ')}</span>
                      <span style={{ color: passed ? 'var(--accent-green)' : 'var(--accent-amber)', textAlign: 'right' }}>
                        {String(gateMsg)}
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        ) : (
          <div className="panel-card" style={{ padding: 12, gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
                <FileSpreadsheet size={14} color="var(--accent-cyan)" />
                Architecture Attribution Matrix
              </span>
            </div>

            {compMatrix.length > 0 && (
              <table className="watchlist-table" style={{ fontSize: 11 }}>
                <thead>
                  <tr>
                    <th>Architecture Mode</th>
                    <th style={{ textAlign: 'right' }}>Trades</th>
                    <th style={{ textAlign: 'right' }}>Net Cash</th>
                    <th style={{ textAlign: 'right' }}>Balance</th>
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
                      <td style={{ textAlign: 'right' }}>₹{m.ending_balance.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {/* BOTTOM TABLE + TIMELINE */}
      {useHist && historicalResult ? (
        <div className="split-2-col">
          {/* Left: Structured Cross-Sectional Portfolio & Universe Table */}
          <div className="panel-card">
            <div className="panel-head">
              <span>Cross-Sectional Audited Executions ({historicalResult.date})</span>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent-green)' }}>
                Primary #1 Pick Net: ₹{Number(historicalResult.net_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="watchlist-table">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Time</th>
                    <th>Reason</th>
                    <th style={{ textAlign: 'right' }}>Day Ret</th>
                    <th style={{ textAlign: 'right' }}>Net PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {(historicalResult.cross_sectional || []).map((c: any, idx: number) => {
                    const pnlVal = parseRs(c.net_pnl);
                    const rawSym = String(c.candidate || '').split(' ')[0];
                    return (
                      <tr key={idx} className="watchlist-row" onClick={() => onInspectSymbol(rawSym)}>
                        <td style={{ fontWeight: 700 }}>{c.candidate}</td>
                        <td>{c.entry_price}</td>
                        <td>{c.exit_price}</td>
                        <td>{c.exit_time}</td>
                        <td style={{ fontSize: 10, color: 'var(--text-muted)' }}>{c.exit_reason}</td>
                        <td style={{ textAlign: 'right', color: String(c.current_return).includes('-') ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                          {c.current_return}
                        </td>
                        <td style={{ textAlign: 'right', fontWeight: 700, color: pnlVal >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                          {c.net_pnl}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Right: Structured Forensic Intraday Timeline */}
          <div className="panel-card">
            <div className="panel-head">
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Clock size={13} color="var(--accent-cyan)" />
                Intraday Forensic Timeline ({historicalResult.date})
              </span>
            </div>
            <div style={{ maxHeight: 270, overflowY: 'auto', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(historicalResult.timeline || []).map((ev: any, i: number) => (
                <div key={i} style={{ borderLeft: '2px solid var(--accent-cyan)', paddingLeft: 10 }}>
                  <div className="mono" style={{ fontSize: 10, color: 'var(--accent-cyan)', fontWeight: 700 }}>
                    {ev.time} • {ev.title}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{ev.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
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
                    <td>{t.entry_time} @ ₹{t.entry_price.toFixed(2)}</td>
                    <td>{t.exit_time} @ ₹{t.exit_price.toFixed(2)}</td>
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
      )}
    </div>
  );
};
