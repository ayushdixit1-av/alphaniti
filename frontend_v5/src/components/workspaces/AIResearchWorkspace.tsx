import React from 'react';
import { Cpu, Globe2 } from 'lucide-react';
import { PremarketAnalysisResponse, PremarketMacroResponse } from '../../types/api';

interface AIResearchWorkspaceProps {
  premarket: PremarketAnalysisResponse | null;
  macro: PremarketMacroResponse | null;
  onInspectSymbol: (sym: string) => void;
}

export const AIResearchWorkspace: React.FC<AIResearchWorkspaceProps> = ({
  premarket,
  macro,
  onInspectSymbol,
}) => {
  const items = premarket?.items || [];
  const projected = macro?.projected_openings || [];

  // Scatter dimensions
  const w = 520;
  const h = 220;
  const pad = 32;
  const xScale = (p: number) => pad + Math.min(1, Math.max(0, p / 0.55)) * (w - pad * 2);
  const yScale = (ret: number) => h - pad - Math.min(1, Math.max(0, ret / 15)) * (h - pad * 2);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1.35fr 1fr', gap: 8 }}>
        {/* Left: Risk-vs-Alpha Evidential Scatter */}
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Cpu size={14} color="var(--accent-cyan)" />
              Evidential GRU Ensemble — Crash Hazard vs Expected Drift ({items.length} Securities)
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--accent-green)' }}>
              Top Pick: {premarket?.top_pick || '--'}
            </span>
          </div>

          <div style={{ padding: 10 }}>
            <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 220, background: '#05080f', borderRadius: 8 }}>
              {/* 0.40 Crash Hazard Veto Line */}
              <line
                x1={xScale(0.4)}
                x2={xScale(0.4)}
                y1={pad - 10}
                y2={h - pad}
                stroke="#f43f5e"
                strokeDasharray="4 4"
                strokeWidth="1.5"
              />
              <text x={xScale(0.4) + 5} y={pad} fill="#f43f5e" fontSize="9.5" fontFamily="JetBrains Mono">
                0.40 VETO WALL
              </text>

              {/* Axes */}
              <line x1={pad} x2={w - pad} y1={h - pad} y2={h - pad} stroke="rgba(148,163,184,0.25)" />
              <line x1={pad} x2={pad} y1={pad - 10} y2={h - pad} stroke="rgba(148,163,184,0.25)" />
              <text x={w / 2} y={h - 8} textAnchor="middle" fill="#64748b" fontSize="9.5" fontFamily="JetBrains Mono">
                Drawdown Crash Probability (p_crash)
              </text>

              {items.map((it) => {
                const cx = xScale(it.drawdown_crash_prob);
                const cy = yScale(it.predicted_return_pct);
                const col =
                  it.verdict === 'QUALIFIED_ALPHA'
                    ? '#10b981'
                    : it.verdict === 'WATCHLIST'
                    ? '#f59e0b'
                    : '#f43f5e';
                return (
                  <g
                    key={it.symbol}
                    style={{ cursor: 'pointer' }}
                    onClick={() => onInspectSymbol(it.symbol)}
                  >
                    <circle cx={cx} cy={cy} r={6.5} fill={col} fillOpacity={0.78} stroke="#fff" strokeWidth={0.8} />
                    <text
                      x={cx + 8}
                      y={cy + 3}
                      fill="#e2e8f0"
                      fontSize="9"
                      fontFamily="JetBrains Mono"
                      fontWeight="700"
                    >
                      {it.symbol}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>

        {/* Right: Projected Pre-Market Opening Gaps */}
        <div className="panel-card">
          <div className="panel-head">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Globe2 size={14} color="var(--accent-purple)" />
              Next Session Projected Openings (9 Core Stocks)
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              Global Risk: {macro?.global_macro?.global_risk || 'Neutral'}
            </span>
          </div>
          <div style={{ overflowY: 'auto', maxHeight: 245 }}>
            <table className="watchlist-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th style={{ textAlign: 'right' }}>Last Close</th>
                  <th style={{ textAlign: 'right' }}>Proj. Open</th>
                  <th style={{ textAlign: 'right' }}>Gap %</th>
                  <th>Bias</th>
                </tr>
              </thead>
              <tbody>
                {projected.map((p) => (
                  <tr key={p.symbol} className="watchlist-row" onClick={() => onInspectSymbol(p.symbol)}>
                    <td style={{ fontWeight: 700 }}>{p.symbol}</td>
                    <td style={{ textAlign: 'right' }}>₹{p.close.toFixed(2)}</td>
                    <td style={{ textAlign: 'right', color: 'var(--accent-cyan)' }}>₹{p.projected_open.toFixed(2)}</td>
                    <td
                      style={{
                        textAlign: 'right',
                        color: p.expected_gap_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                      }}
                    >
                      {p.expected_gap_pct >= 0 ? '+' : ''}
                      {p.expected_gap_pct.toFixed(2)}%
                    </td>
                    <td style={{ fontSize: 10, color: 'var(--text-muted)' }}>{p.bias}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Full Ranked 12-Stock Evidential Screener */}
      <div className="panel-card">
        <div className="panel-head">
          <span>Evidential Deep Ensemble Alpha Ranking ({premarket?.scan_date || 'Active'})</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            Click any row to open SuperChart
          </span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="watchlist-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Verdict</th>
                <th style={{ textAlign: 'right' }}>Pred. Drift</th>
                <th style={{ textAlign: 'right' }}>Aleatoric Vol</th>
                <th style={{ textAlign: 'right' }}>Crash Prob</th>
                <th style={{ textAlign: 'right' }}>Sharpe Score</th>
                <th>Ensemble Rationale</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.symbol} className="watchlist-row" onClick={() => onInspectSymbol(it.symbol)}>
                  <td style={{ fontWeight: 800 }}>{it.symbol}</td>
                  <td>
                    <span
                      className="lvl-pill"
                      style={{
                        background:
                          it.verdict === 'QUALIFIED_ALPHA'
                            ? 'rgba(16,185,129,0.16)'
                            : it.verdict === 'WATCHLIST'
                            ? 'rgba(245,158,11,0.16)'
                            : 'rgba(244,63,94,0.16)',
                        color:
                          it.verdict === 'QUALIFIED_ALPHA'
                            ? 'var(--accent-green)'
                            : it.verdict === 'WATCHLIST'
                            ? 'var(--accent-amber)'
                            : 'var(--accent-red)',
                      }}
                    >
                      {it.badge}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right', color: 'var(--accent-green)' }}>+{it.predicted_return_pct.toFixed(2)}%</td>
                  <td style={{ textAlign: 'right' }}>{it.aleatoric_vol_pct.toFixed(2)}%</td>
                  <td
                    style={{
                      textAlign: 'right',
                      color: it.drawdown_crash_prob >= 0.4 ? 'var(--accent-red)' : 'var(--text-main)',
                    }}
                  >
                    {(it.drawdown_crash_prob * 100).toFixed(1)}%
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent-cyan)' }}>
                    {it.sharpe_score.toFixed(3)}
                  </td>
                  <td style={{ fontFamily: 'var(--sans)', color: 'var(--text-muted)' }}>{it.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
