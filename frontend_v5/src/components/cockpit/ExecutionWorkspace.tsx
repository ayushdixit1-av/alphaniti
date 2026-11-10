import React from 'react';
import { RefreshCw, Plus, MoreVertical, ShieldCheck, Activity, ArrowUp, ArrowDown, Minus } from 'lucide-react';
import {
  AuditLedgerResponse,
  CandleBar,
  CockpitSymbolData,
  LiveEngineStatus,
  PremarketMacroResponse,
} from '../../types/api';
import { SuperChart } from '../charts/SuperChart';
import { EquityCurveMini } from '../charts/EquityCurveMini';
import { MarketMicrostructurePanel } from './MarketMicrostructurePanel';

const EQUITY_SYMBOLS = [
  'SBIN',
  'HDFCBANK',
  'ICICIBANK',
  'AXISBANK',
  'KOTAKBANK',
  'RELIANCE',
  'TCS',
  'INFY',
  'BAJFINANCE',
];

interface ExecutionWorkspaceProps {
  selectedSymbol: string;
  onSelectSymbol: (sym: string) => void;
  timeframe: string;
  onTimeframeChange: (tf: string) => void;
  engineStatus: LiveEngineStatus | null;
  cockpit: CockpitSymbolData | null;
  candles: CandleBar[];
  sessionDate?: string;
  isLiveToday?: boolean;
  macro: PremarketMacroResponse | null;
  auditLedger: AuditLedgerResponse | null;
  onOpenSearch: () => void;
  onRefreshSymbol: () => void;
}

export const ExecutionWorkspace: React.FC<ExecutionWorkspaceProps> = ({
  selectedSymbol,
  onSelectSymbol,
  timeframe,
  onTimeframeChange,
  engineStatus,
  cockpit,
  candles,
  sessionDate,
  isLiveToday,
  macro,
  auditLedger,
  onOpenSearch,
  onRefreshSymbol,
}) => {
  const trades = auditLedger?.trades || [];
  const perf = auditLedger?.performance_analytics;
  const summary = auditLedger?.summary;
  const clearCash = engineStatus?.account?.clear_cash ?? 8382.07;
  const riskCap = Number((clearCash * 0.016).toFixed(2));
  const openPosCount = engineStatus?.active_trade?.status === 'OPEN' ? 1 : 0;

  // Use backend performance_analytics with safe local fallback
  let cum = 0;
  let peak = 0;
  let maxDDLocal = 0;
  const sortedTrades = [...trades].sort((a, b) => a.exit_time.localeCompare(b.exit_time));
  const eqPoints = perf?.equity_curve
    ? perf.equity_curve.map((pt) => ({ label: pt.time, symbol: pt.symbol, tradeNet: pt.trade_net, cumPnl: pt.cum_net }))
    : sortedTrades.map((t) => {
        cum = Number((cum + t.net_pnl).toFixed(2));
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDDLocal) maxDDLocal = dd;
        return { label: t.exit_time, symbol: t.symbol, tradeNet: t.net_pnl, cumPnl: cum };
      });

  const realizedPnl =
    engineStatus?.market_status?.is_open && (engineStatus?.today_net_pnl ?? 0) !== 0
      ? engineStatus.today_net_pnl
      : perf?.net_pnl_after_charges_rs ?? summary?.total_net_pnl_rs ?? cum;
  const wins = perf?.wins ?? summary?.wins ?? trades.filter((t) => t.net_pnl > 0).length;
  const totalTr = perf?.total_trades ?? summary?.total_trades ?? trades.length;
  const winRatePct = perf?.win_rate_pct ?? (totalTr > 0 ? Number(((wins / totalTr) * 100).toFixed(1)) : 0);
  const realizedPct = perf?.return_pct ?? (clearCash > 0 ? (realizedPnl / clearCash) * 100 : 0);
  const maxDD = perf?.max_drawdown_rs ?? maxDDLocal;
  const maxDDPct = perf?.max_drawdown_pct ?? (clearCash > 0 ? (maxDD / clearCash) * 100 : 0);
  const profitFactor = perf?.profit_factor;
  const expectancyRs = perf?.expectancy_rs ?? (totalTr > 0 ? realizedPnl / totalTr : 0);
  const avgWinRs = perf?.avg_win_rs ?? 0;
  const avgLossRs = perf?.avg_loss_rs ?? 0;
  const statutoryFees = perf?.statutory_charges_rs ?? trades.reduce((acc, t) => acc + t.fees, 0);

  // Watchlist rows: 9 equities + NIFTY + BANKNIFTY
  const watchlistRows = [
    ...EQUITY_SYMBOLS.map((sym) => {
      const q = engineStatus?.market_data?.[sym];
      const ltp = q?.ltp ?? 0;
      const open = q?.open ?? ltp;
      const pct = open > 0 ? ((ltp - open) / open) * 100 : 0;
      return {
        sym,
        label: `${sym}.NS`,
        ltp,
        pct,
        ltt: q?.last_trade_time || '--',
      };
    }),
    ...['NIFTY', 'BANKNIFTY'].map((idxSym) => {
      const idx = engineStatus?.indices?.[idxSym];
      return {
        sym: idxSym,
        label: idxSym,
        ltp: idx?.ltp ?? 0,
        pct: idx?.change_pct ?? 0,
        ltt: 'INDEX',
      };
    }),
  ];

  const brief = cockpit?.execution_brief;
  const ai = cockpit?.ai_metrics;
  const gm = macro?.global_macro;
  const macroRegime = engineStatus?.strategy?.macro_regime || 'ROTATIONAL_CHOP';
  const shortRegimeBadge = macroRegime.split(' ')[0];

  const formatSignedPct = (v?: number) => {
    const num = v ?? 0;
    return `${num >= 0 ? '+' : ''}${num.toFixed(2)}%`;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* ROW 1: Watchlist | Chart Hero | Execution Brief + Ensemble */}
      <div className="cockpit-row1">
        {/* LEFT: Watchlist (Intraday) */}
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Activity size={13} color="var(--accent-cyan)" />
              Watchlist (Intraday)
            </span>
            <div style={{ display: 'flex', gap: 2 }}>
              <button className="icon-btn" title="Quick Search (Ctrl+K)" onClick={onOpenSearch}>
                <Plus size={13} />
              </button>
              <button className="icon-btn" title="Refresh Watchlist" onClick={onRefreshSymbol}>
                <MoreVertical size={13} />
              </button>
            </div>
          </div>

          <div style={{ overflowY: 'auto', flex: 1 }}>
            <table className="watchlist-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th style={{ textAlign: 'right' }}>LTP</th>
                  <th style={{ textAlign: 'right' }}>Δ%</th>
                </tr>
              </thead>
              <tbody>
                {watchlistRows.map((row) => {
                  const isActive = row.sym === selectedSymbol;
                  const isUp = row.pct >= 0;
                  return (
                    <tr
                      key={row.sym}
                      className={`watchlist-row ${isActive ? 'active' : ''}`}
                      onClick={() => onSelectSymbol(row.sym)}
                      title={`Last Tick: ${row.ltt}`}
                    >
                      <td style={{ fontWeight: isActive ? 800 : 600, color: isActive ? '#fff' : 'var(--text-main)' }}>
                        {row.label}
                      </td>
                      <td style={{ textAlign: 'right', fontWeight: 700 }}>
                        {row.ltp > 0 ? row.ltp.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--'}
                      </td>
                      <td
                        style={{
                          textAlign: 'right',
                          fontWeight: 700,
                          color: isUp ? 'var(--accent-green)' : 'var(--accent-red)',
                        }}
                      >
                        {row.ltp > 0 ? formatSignedPct(row.pct) : '--'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* CENTER: Hero Chart Card */}
        <div className="panel-card">
          <div className="ticker-hero-bar">
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span className="mono" style={{ fontSize: 15, fontWeight: 800 }}>
                    {selectedSymbol === 'NIFTY' || selectedSymbol === 'BANKNIFTY'
                      ? selectedSymbol
                      : `${selectedSymbol}.NS`}
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                    {cockpit?.company_name || 'NSE Equity'}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 2 }}>
                  <span className="mono" style={{ fontSize: 20, fontWeight: 800, color: 'var(--accent-green)' }}>
                    {cockpit?.ltp ? cockpit.ltp.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--'}
                  </span>
                  <span
                    className="mono"
                    style={{
                      fontSize: 11.5,
                      fontWeight: 700,
                      color: (cockpit?.change_pts ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                    }}
                  >
                    {cockpit
                      ? `${cockpit.change_pts >= 0 ? '+' : ''}${cockpit.change_pts.toFixed(2)} (${formatSignedPct(cockpit.change_pct)})`
                      : '--'}
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span
                  className="mono"
                  style={{
                    padding: '4px 12px',
                    borderRadius: 6,
                    fontSize: 11.5,
                    fontWeight: 800,
                    background: brief?.recommendation.includes('SHORT')
                      ? 'rgba(244,63,94,0.16)'
                      : brief?.recommendation.includes('LONG')
                      ? 'rgba(16,185,129,0.16)'
                      : 'rgba(16,185,129,0.12)',
                    color: brief?.recommendation.includes('SHORT')
                      ? 'var(--accent-red)'
                      : brief?.recommendation.includes('HOLIDAY')
                      ? 'var(--accent-amber)'
                      : 'var(--accent-green)',
                    border: `1px solid ${
                      brief?.recommendation.includes('SHORT')
                        ? 'rgba(244,63,94,0.4)'
                        : brief?.recommendation.includes('HOLIDAY')
                        ? 'rgba(245,158,11,0.4)'
                        : 'rgba(16,185,129,0.4)'
                    }`,
                  }}
                >
                  {brief?.recommendation || 'HOLD'}
                </span>
                <span
                  className="mono"
                  style={{
                    padding: '4px 9px',
                    borderRadius: 6,
                    fontSize: 11,
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-muted)',
                  }}
                >
                  Conf. <strong style={{ color: '#fff' }}>{ai ? `${ai.composite_score.toFixed(1)}%` : '--'}</strong>
                </span>
              </div>
            </div>

            {/* 5 Right-Aligned Quantitative Metrics */}
            <div className="hero-metrics-row">
              <div className="hero-metric">
                <span className="hero-metric-lbl">20D Drift</span>
                <span
                  className="hero-metric-val"
                  style={{ color: (ai?.drift_20d_pct ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}
                >
                  {ai ? `${ai.drift_20d_pct >= 0 ? '+' : ''}${ai.drift_20d_pct.toFixed(2)}%` : '--'}
                </span>
              </div>
              <div className="hero-metric">
                <span className="hero-metric-lbl">Volatility</span>
                <span className="hero-metric-val">{ai ? `${ai.volatility_pct.toFixed(1)}%` : '--'}</span>
              </div>
              <div className="hero-metric">
                <span className="hero-metric-lbl">Crash Prob</span>
                <span
                  className="hero-metric-val"
                  style={{ color: (ai?.crash_prob_pct ?? 0) >= 40 ? 'var(--accent-red)' : 'var(--text-main)' }}
                >
                  {ai ? `${ai.crash_prob_pct.toFixed(1)}%` : '--'}
                </span>
              </div>
              <div className="hero-metric">
                <span className="hero-metric-lbl">Alpha Score</span>
                <span
                  className="hero-metric-val"
                  style={{ color: (ai?.alpha_score ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-amber)' }}
                >
                  {ai ? `${ai.alpha_score >= 0 ? '+' : ''}${ai.alpha_score.toFixed(2)}` : '--'}
                </span>
              </div>
              <div className="hero-metric">
                <span className="hero-metric-lbl">Tail Safety</span>
                <span className="hero-metric-val" style={{ color: 'var(--accent-green)' }}>
                  {ai ? ai.tail_safety.toFixed(2) : '--'}
                </span>
              </div>
            </div>
          </div>

          <SuperChart
            symbol={selectedSymbol}
            timeframe={timeframe}
            onTimeframeChange={onTimeframeChange}
            candles={candles}
            sessionDate={sessionDate}
            isLiveToday={isLiveToday}
            cockpit={cockpit}
            trades={trades}
          />
        </div>

        {/* RIGHT: Execution Brief + Model Ensemble (3) */}
        <div className="right-col-stack">
          <div className="panel-card" style={{ flex: 1 }}>
            <div className="panel-head">
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <ShieldCheck size={13} color="var(--accent-cyan)" />
                Execution Brief ({selectedSymbol})
              </span>
              <button className="icon-btn" onClick={onRefreshSymbol} title="Refresh Signal">
                <RefreshCw size={12} />
              </button>
            </div>

            <div className="rec-banner">
              <div style={{ fontSize: 9.5, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.5px' }}>
                RECOMMENDATION
              </div>
              <div
                className="mono"
                style={{
                  fontSize: 18,
                  fontWeight: 800,
                  marginTop: 2,
                  color: brief?.recommendation.includes('SHORT')
                    ? 'var(--accent-red)'
                    : brief?.recommendation.includes('HOLIDAY')
                    ? 'var(--accent-amber)'
                    : 'var(--accent-cyan)',
                }}
              >
                {brief?.recommendation || 'HOLD'}
              </div>
            </div>

            <div className="kv-list">
              <div className="kv-row">
                <span style={{ color: 'var(--text-muted)' }}>Setup Mode</span>
                <strong className="mono">{brief?.setup_mode || '--'}</strong>
              </div>
              <div className="kv-row">
                <span style={{ color: 'var(--text-muted)' }}>Entry Zone</span>
                <strong className="mono" style={{ color: 'var(--accent-cyan)' }}>{brief?.entry_zone || '--'}</strong>
              </div>
              <div className="kv-row">
                <span style={{ color: 'var(--text-muted)' }}>Target 1 (1.0R)</span>
                <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                  {brief ? brief.target_1.toFixed(2) : '--'}
                </strong>
              </div>
              <div className="kv-row">
                <span style={{ color: 'var(--text-muted)' }}>Target 2 (1.8R)</span>
                <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                  {brief ? brief.target_2.toFixed(2) : '--'}
                </strong>
              </div>
              <div className="kv-row">
                <span style={{ color: 'var(--text-muted)' }}>Stop Loss (-1.0R)</span>
                <strong className="mono" style={{ color: 'var(--accent-red)' }}>
                  {brief ? brief.stop_loss.toFixed(2) : '--'}
                </strong>
              </div>
            </div>
          </div>

          <div className="panel-card" style={{ flex: 1 }}>
            <div className="panel-head">
              <span>Model Ensemble (3)</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                evidential_v26
              </span>
            </div>

            <div style={{ padding: '4px 10px 8px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <table className="watchlist-table" style={{ fontSize: 11 }}>
                <thead>
                  <tr>
                    <th style={{ padding: '4px 4px' }}>Model</th>
                    <th style={{ padding: '4px 4px' }}>Direction</th>
                    <th style={{ padding: '4px 4px', textAlign: 'right' }}>Prob</th>
                    <th style={{ padding: '4px 4px', textAlign: 'right' }}>Weight</th>
                  </tr>
                </thead>
                <tbody>
                  {(ai?.members || []).map((m) => {
                    const dirColor =
                      m.direction === 'Long'
                        ? 'var(--accent-green)'
                        : m.direction === 'Short'
                        ? 'var(--accent-red)'
                        : 'var(--text-muted)';
                    return (
                      <tr key={m.model}>
                        <td style={{ padding: '5px 4px', cursor: 'default' }}>{m.model}</td>
                        <td style={{ padding: '5px 4px', color: dirColor, fontWeight: 700, cursor: 'default' }}>
                          {m.direction === 'Long' && <ArrowUp size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />}
                          {m.direction === 'Short' && <ArrowDown size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />}
                          {m.direction === 'Neutral' && <Minus size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />}
                          {m.direction}
                        </td>
                        <td style={{ padding: '5px 4px', textAlign: 'right', cursor: 'default' }}>
                          {m.prob.toFixed(2)}
                        </td>
                        <td style={{ padding: '5px 4px', textAlign: 'right', cursor: 'default' }}>
                          {m.weight.toFixed(2)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              <div
                className="kv-row"
                style={{
                  borderTop: '1px solid rgba(148,163,184,0.1)',
                  paddingTop: 6,
                  marginTop: 2,
                  fontSize: 11.5,
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>Composite Decision Score</span>
                <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                  {ai ? `${ai.composite_score.toFixed(1)} / 100` : '--'}
                </strong>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Market Microstructure (Only rendered when backend confirms reliable data) */}
      <MarketMicrostructurePanel symbol={selectedSymbol} micro={cockpit?.microstructure} />

      {/* ROW 2: 4 Analytical Cards */}
      <div className="cockpit-row2">
        {/* Card 1: Account & Risk */}
        <div className="panel-card">
          <div className="panel-head">
            <span>Account &amp; Risk</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--accent-green)' }}>
              {engineStatus?.account?.position_status?.includes('FLAT') ? 'FLAT' : 'ACTIVE'}
            </span>
          </div>
          <div className="kv-list" style={{ padding: '8px 12px', gap: 6 }}>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Cash Balance</span>
              <strong className="mono">₹{clearCash.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Risk Per Trade (1.6%)</span>
              <strong className="mono">₹{riskCap.toFixed(2)}</strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Open Position</span>
              <strong className="mono">{openPosCount}</strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>
                Realized PnL ({engineStatus?.market_status?.is_open ? 'Today' : auditLedger?.date || 'Last'})
              </span>
              <strong className="mono" style={{ color: realizedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                {realizedPnl >= 0 ? '+' : ''}₹{realizedPnl.toFixed(2)}
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Win / Loss (Session)</span>
              <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                {wins} / {Math.max(0, totalTr - wins)} ({winRatePct}%)
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Max Drawdown (D)</span>
              <strong className="mono" style={{ color: maxDD < 0 ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                ₹{maxDD.toFixed(2)} ({maxDDPct.toFixed(2)}%)
              </strong>
            </div>
          </div>
        </div>

        {/* Card 2: Factor Scores */}
        <div className="panel-card">
          <div className="panel-head">
            <span>Factor Scores ({selectedSymbol})</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              RSI {cockpit?.rsi_14 ?? '--'}{cockpit?.rvol != null ? ` • RVOL ${cockpit.rvol}x` : ''}
            </span>
          </div>
          <div style={{ padding: '9px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(cockpit?.factor_scores || []).map((f) => (
              <div key={f.name} className="factor-row">
                <span style={{ color: 'var(--text-muted)' }}>{f.name}</span>
                <div className="factor-track">
                  <div className="factor-fill" style={{ width: `${f.score}%` }} />
                </div>
                <strong className="mono" style={{ textAlign: 'right' }}>{f.score}</strong>
              </div>
            ))}
          </div>
        </div>

        {/* Card 3: Macro & Regime */}
        <div className="panel-card">
          <div className="panel-head">
            <span>Macro &amp; Regime</span>
            <span
              className="mono"
              style={{
                fontSize: 9.5,
                padding: '2px 7px',
                borderRadius: 4,
                background: 'rgba(245,158,11,0.14)',
                color: 'var(--accent-amber)',
                border: '1px solid rgba(245,158,11,0.35)',
              }}
            >
              {shortRegimeBadge}
            </span>
          </div>
          <div className="kv-list" style={{ padding: '8px 12px', gap: 6 }}>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Nifty Trend</span>
              <strong
                className="mono"
                style={{ color: (gm?.gift_nifty?.change_pct ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}
              >
                {gm?.gift_nifty?.trend || 'Neutral'} ({formatSignedPct(gm?.gift_nifty?.change_pct)})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>BankNifty</span>
              <strong
                className="mono"
                style={{ color: (gm?.banknifty?.change_pct ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}
              >
                {gm?.banknifty?.trend || 'Neutral'} ({formatSignedPct(gm?.banknifty?.change_pct)})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>India VIX</span>
              <strong className="mono">
                {gm?.india_vix?.last?.toFixed(2) ?? '--'} ({formatSignedPct(gm?.india_vix?.change_pct)})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>USDINR</span>
              <strong className="mono">
                {gm?.usdinr?.last?.toFixed(2) ?? '--'} ({formatSignedPct(gm?.usdinr?.change_pct)})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>US 10Y Yield</span>
              <strong className="mono">
                {gm?.us_10y_yield?.last?.toFixed(2) ?? '--'}% ({gm?.us_10y_yield?.change_pts ?? 0 >= 0 ? '+' : ''}
                {gm?.us_10y_yield?.change_pts?.toFixed(2) ?? '0.00'})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Global Risk</span>
              <strong className="mono">{gm?.global_risk || 'Neutral'}</strong>
            </div>
          </div>
        </div>

        {/* Card 4: Day Performance */}
        <div className="panel-card">
          <div className="panel-head">
            <span>Day Performance</span>
            <span className="mono" style={{ fontSize: 11 }}>
              Net{' '}
              <strong style={{ color: realizedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                {realizedPnl >= 0 ? '+' : ''}₹{realizedPnl.toFixed(2)} ({realizedPct >= 0 ? '+' : ''}
                {realizedPct.toFixed(2)}%)
              </strong>
            </span>
          </div>
          <div style={{ padding: '4px 10px 8px', flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: 6 }}>
            <EquityCurveMini points={eqPoints} />
            <div
              className="mono"
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '4px 8px',
                fontSize: 9.8,
                color: 'var(--text-dim)',
                borderTop: '1px solid rgba(148,163,184,0.1)',
                paddingTop: 5,
              }}
            >
              <span>PF <strong style={{ color: 'var(--accent-green)' }}>{profitFactor != null ? `${profitFactor.toFixed(2)}x` : '--'}</strong></span>
              <span>WR <strong style={{ color: 'var(--text-main)' }}>{winRatePct}%</strong></span>
              <span>Exp <strong style={{ color: expectancyRs >= 0 ? 'var(--accent-cyan)' : 'var(--accent-red)' }}>{expectancyRs >= 0 ? '+' : ''}₹{expectancyRs.toFixed(1)}</strong></span>
              <span>Avg W/L <strong style={{ color: 'var(--text-main)' }}>{avgWinRs.toFixed(0)}/{Math.abs(avgLossRs).toFixed(0)}</strong></span>
              <span>DD <strong style={{ color: maxDD < 0 ? 'var(--accent-amber)' : 'var(--accent-green)' }}>{maxDDPct.toFixed(2)}%</strong></span>
              <span>Charges <strong style={{ color: 'var(--accent-amber)' }}>₹{statutoryFees.toFixed(0)}</strong></span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
