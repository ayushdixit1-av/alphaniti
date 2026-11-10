import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Copy, Download } from 'lucide-react';
import { DaemonLogsResponse } from '../../types/api';

interface TelemetryConsoleProps {
  telemetry: DaemonLogsResponse | null;
}

const FILTERS = ['ALL', 'WATCHDOG', 'TOKEN', 'MARGIN', 'DALTON', 'EXEC'] as const;

export const TelemetryConsole: React.FC<TelemetryConsoleProps> = ({ telemetry }) => {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const logs = (telemetry?.logs || []).filter((l) => {
    if (filter === 'ALL') return true;
    const sub = (l.subsystem || '').toUpperCase();
    return sub.includes(filter);
  });

  useEffect(() => {
    if (autoScroll && !collapsed && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [logs.length, autoScroll, collapsed]);

  const handleCopy = () => {
    const txt = logs.map((l) => `${l.time_str} [${l.level}] ${l.subsystem}: ${l.message}`).join('\n');
    navigator.clipboard?.writeText(txt);
  };

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(logs, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `alphaniti_telemetry_${Date.now()}.json`;
    a.click();
  };

  const levelBadgeStyle = (lvl: string): React.CSSProperties => {
    const u = (lvl || 'INFO').toUpperCase();
    if (u === 'WARN') return { background: 'rgba(245,158,11,0.18)', color: 'var(--accent-amber)' };
    if (u === 'EXEC') return { background: 'rgba(168,85,247,0.2)', color: 'var(--accent-purple)' };
    if (u === 'GUARD' || u === 'SUCCESS') return { background: 'rgba(16,185,129,0.18)', color: 'var(--accent-green)' };
    return { background: 'rgba(56,189,248,0.16)', color: 'var(--accent-cyan)' };
  };

  return (
    <div className="panel-card">
      <div className="panel-head" style={{ padding: '6px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <button
            className="icon-btn"
            onClick={() => setCollapsed((c) => !c)}
            style={{ padding: 2 }}
            title={collapsed ? 'Expand Console' : 'Collapse Console'}
          >
            {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          <span>Telemetry Console</span>
          <span
            className="mono"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              fontSize: 10,
              padding: '2px 8px',
              borderRadius: 999,
              background: telemetry?.is_market_open ? 'rgba(16,185,129,0.14)' : 'rgba(245,158,11,0.12)',
              color: telemetry?.is_market_open ? 'var(--accent-green)' : 'var(--accent-amber)',
              border: `1px solid ${telemetry?.is_market_open ? 'rgba(16,185,129,0.35)' : 'rgba(245,158,11,0.3)'}`,
            }}
          >
            <span className={`dot ${telemetry?.is_market_open ? 'dot-green' : 'dot-amber'}`} />
            {telemetry?.is_market_open ? 'LIVE' : telemetry?.mode === 'HOLIDAY_SURVEILLANCE' ? 'HOLIDAY' : 'REPLAY'}
          </span>

          <div style={{ display: 'flex', gap: 4 }}>
            {FILTERS.map((f) => (
              <button
                key={f}
                className={`tf-btn ${filter === f ? 'active' : ''}`}
                style={{ fontSize: 10, padding: '2px 8px' }}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            Auto-scroll
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              style={{ accentColor: 'var(--accent-cyan)', cursor: 'pointer' }}
            />
          </label>
          <button className="icon-btn" onClick={handleCopy} title="Copy Visible Logs">
            <Copy size={13} />
          </button>
          <button className="icon-btn" onClick={handleExport} title="Export Telemetry JSON">
            <Download size={13} />
          </button>
        </div>
      </div>

      {!collapsed && (
        <div ref={bodyRef} style={{ maxHeight: 165, overflowY: 'auto', background: '#05080d' }}>
          <table className="telemetry-table">
            <thead>
              <tr>
                <th style={{ width: 95 }}>Time (IST)</th>
                <th style={{ width: 72 }}>Level</th>
                <th style={{ width: 120 }}>Module</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {[...logs].reverse().slice(0, 40).map((log, i) => (
                <tr key={`${log.id}-${i}`}>
                  <td style={{ color: 'var(--text-dim)' }}>{log.time_str}</td>
                  <td>
                    <span className="lvl-pill" style={levelBadgeStyle(log.level)}>
                      {log.level || 'INFO'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-main)', fontWeight: 700 }}>{log.subsystem}</td>
                  <td style={{ color: '#cbd5e1' }}>{log.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
