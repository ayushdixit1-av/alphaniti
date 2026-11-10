import React, { useEffect, useState } from 'react';
import { Search, Command } from 'lucide-react';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  symbols: string[];
  onSelectSymbol: (sym: string) => void;
  onSelectWorkspace: (ws: 'execution' | 'chart' | 'replay' | 'ai' | 'risk' | 'audit') => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  open,
  onClose,
  symbols,
  onSelectSymbol,
  onSelectWorkspace,
}) => {
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (open) setQuery('');
  }, [open]);

  if (!open) return null;

  const q = query.trim().toUpperCase();
  const matchedSyms = symbols.filter((s) => s.includes(q));
  const workspaces: Array<{ id: 'execution' | 'chart' | 'replay' | 'ai' | 'risk' | 'audit'; label: string }> = [
    { id: 'execution', label: 'Workspace: Execution Cockpit' },
    { id: 'chart', label: 'Workspace: 1m A8 Strategy Chart (Any NSE Stock)' },
    { id: 'replay', label: 'Workspace: Two-Mode Replay & Stress' },
    { id: 'ai', label: 'Workspace: AI Research & Macro Gaps' },
    { id: 'risk', label: 'Workspace: Risk Command Center' },
    { id: 'audit', label: 'Workspace: Historical Audit & Friction Ledger' },
  ];

  return (
    <div className="cmd-backdrop" onClick={onClose}>
      <div className="cmd-modal" onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '12px 14px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <Search size={15} color="var(--accent-cyan)" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Jump to ticker (RELIANCE, SBIN, NIFTY) or workspace..."
            style={{
              flex: 1,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: 'var(--text-main)',
              fontFamily: 'var(--mono)',
              fontSize: 13,
            }}
          />
          <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            ESC
          </span>
        </div>

        <div style={{ maxHeight: 320, overflowY: 'auto', padding: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', padding: '4px 8px', fontWeight: 700 }}>
            TICKERS
          </div>
          {matchedSyms.map((s) => (
            <button
              key={s}
              onClick={() => {
                onSelectSymbol(s);
                onSelectWorkspace('execution');
                onClose();
              }}
              style={{
                width: '100%',
                textAlign: 'left',
                padding: '7px 10px',
                borderRadius: 6,
                border: 'none',
                background: 'transparent',
                color: 'var(--text-main)',
                fontFamily: 'var(--mono)',
                fontSize: 12,
                cursor: 'pointer',
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>{s}</span>
              <span style={{ color: 'var(--text-dim)', fontSize: 10.5 }}>Inspect in Cockpit</span>
            </button>
          ))}

          <div style={{ fontSize: 10, color: 'var(--text-dim)', padding: '8px 8px 4px', fontWeight: 700 }}>
            WORKSPACES
          </div>
          {workspaces.map((w) => (
            <button
              key={w.id}
              onClick={() => {
                onSelectWorkspace(w.id);
                onClose();
              }}
              style={{
                width: '100%',
                textAlign: 'left',
                padding: '7px 10px',
                borderRadius: 6,
                border: 'none',
                background: 'transparent',
                color: 'var(--accent-cyan)',
                fontFamily: 'var(--sans)',
                fontSize: 12,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <Command size={12} />
              <span>{w.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
