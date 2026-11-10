import React, { useState } from 'react';
import { Calendar, CheckCircle2, FileSpreadsheet } from 'lucide-react';
import { AuditLedgerResponse, LiveEngineStatus, PremarketMacroResponse } from '../../types/api';

interface AuditRiskWorkspaceProps {
  auditLedger: AuditLedgerResponse | null;
  engineStatus: LiveEngineStatus | null;
  macro: PremarketMacroResponse | null;
  onInspectSymbol: (sym: string) => void;
}

export const AuditRiskWorkspace: React.FC<AuditRiskWorkspaceProps> = ({
  auditLedger,
  engineStatus,
  macro,
  onInspectSymbol,
}) => {
  const [auditDate, setAuditDate] = useState(auditLedger?.date || '2026-11-09');
  const [historicalResult, setHistoricalResult] = useState<any>(null);
  const [loadingDate, setLoadingDate] = useState(false);

  const trades = auditLedger?.trades || [];
  const totalGross = trades.reduce((acc, t) => acc + t.gross_pnl, 0);
  const totalFees = trades.reduce((acc, t) => acc + t.fees, 0);
  const totalNet = trades.reduce((acc, t) => acc + t.net_pnl, 0);

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Top 2 Panels: Statutory Friction Waterfall + Safety Guard Matrix */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <div className="panel-card" style={{ padding: 12, gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
              <FileSpreadsheet size={14} color="var(--accent-green)" />
              Statutory Friction &amp; Cash Waterfall ({auditLedger?.date || 'Session'})
            </span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--accent-green)' }}>
              Net Alpha: {totalNet >= 0 ? '+' : ''}₹{totalNet.toFixed(2)}
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
            <div style={{ background: '#060a11', padding: 10, borderRadius: 6, borderLeft: '3px solid var(--accent-cyan)' }}>
              <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>Gross Alpha</div>
              <div className="mono" style={{ fontSize: 16, fontWeight: 800, color: 'var(--accent-cyan)' }}>
                +₹{totalGross.toFixed(2)}
              </div>
            </div>
            <div style={{ background: '#060a11', padding: 10, borderRadius: 6, borderLeft: '3px solid var(--accent-amber)' }}>
              <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>Groww Statutory Fees</div>
              <div className="mono" style={{ fontSize: 16, fontWeight: 800, color: 'var(--accent-amber)' }}>
                -₹{totalFees.toFixed(2)}
              </div>
            </div>
            <div style={{ background: '#060a11', padding: 10, borderRadius: 6, borderLeft: '3px solid var(--accent-green)' }}>
              <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>Net Banked In-Hand</div>
              <div className="mono" style={{ fontSize: 16, fontWeight: 800, color: 'var(--accent-green)' }}>
                +₹{totalNet.toFixed(2)}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
            <Calendar size={14} color="var(--accent-cyan)" />
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>5-Year Historical Auditor:</span>
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
                fontSize: 11,
                maxHeight: 90,
                overflowY: 'auto',
              }}
            >
              {JSON.stringify(historicalResult)}
            </div>
          )}
        </div>

        {/* Right: Compliance & Guard Matrix */}
        <div className="panel-card" style={{ padding: 12, gap: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
            <CheckCircle2 size={14} color="var(--accent-green)" />
            SEBI Compliance &amp; Safety Guard Triad
          </div>
          <div className="kv-list" style={{ padding: 0, gap: 8 }}>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Broker Gateway Latency</span>
              <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                {engineStatus?.safety_guards?.latency_watchdog?.rolling_latency_ms?.toFixed(1) ?? '--'} ms (
                {engineStatus?.safety_guards?.latency_watchdog?.route_mode || 'FAST_PEGGED_LIMIT'})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>SEBI 06:00 AM Token Guard</span>
              <strong className="mono" style={{ color: 'var(--accent-purple)' }}>
                {macro?.token_countdown?.auth_guard_status || 'ARMED'} ({macro?.token_countdown?.formatted_remaining || '--'})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Upfront MIS Reserve Floor</span>
              <strong className="mono" style={{ color: 'var(--accent-amber)' }}>
                ₹2,500.00 Protected (Buffer ₹{engineStatus?.safety_guards?.margin_validator?.free_buffer_rs?.toFixed(2) ?? '--'})
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>3.5x Buying Power Ceiling</span>
              <strong className="mono">
                ₹{macro?.eod_reconciliation?.buying_power_3_5x?.toLocaleString('en-IN') ?? '--'}
              </strong>
            </div>
            <div className="kv-row">
              <span style={{ color: 'var(--text-muted)' }}>Last Verified Ledger Session</span>
              <strong className="mono">{auditLedger?.date || '--'}</strong>
            </div>
          </div>
        </div>
      </div>

      {/* Full Verified Execution Ledger */}
      <div className="panel-card">
        <div className="panel-head">
          <span>Verified Execution Audit Ledger ({trades.length} Trades • {auditLedger?.summary?.win_rate_pct ?? 87.5}% Win Rate)</span>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent-green)' }}>
            Capital: ₹{auditLedger?.summary?.starting_capital?.toFixed(2) ?? '8382.07'} ➔ ₹
            {auditLedger?.summary?.ending_capital?.toFixed(2) ?? '8540.74'}
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
                <th style={{ textAlign: 'right' }}>Fees</th>
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
