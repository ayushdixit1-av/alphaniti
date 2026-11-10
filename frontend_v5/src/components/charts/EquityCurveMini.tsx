import React, { useState } from 'react';

interface Point {
  label: string;
  symbol?: string;
  tradeNet?: number;
  cumPnl: number;
}

interface EquityCurveMiniProps {
  points: Point[];
}

export const EquityCurveMini: React.FC<EquityCurveMiniProps> = ({ points }) => {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const width = 320;
  const height = 88;
  const padX = 8;
  const padY = 10;

  if (!points || points.length === 0) {
    return (
      <div
        className="mono"
        style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 11,
          color: 'var(--text-dim)',
        }}
      >
        Awaiting session executions...
      </div>
    );
  }

  const allPts: Point[] =
    points[0]?.label === '09:15' && points[0]?.cumPnl === 0
      ? points
      : [{ label: '09:15', symbol: 'START', tradeNet: 0, cumPnl: 0 }, ...points];

  const values = allPts.map((p) => p.cumPnl);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;

  const coords = allPts.map((p, idx) => {
    const x = padX + (idx / Math.max(1, allPts.length - 1)) * (width - padX * 2);
    const y = padY + (1 - (p.cumPnl - minVal) / range) * (height - padY * 2);
    return { x, y, ...p };
  });

  const linePath = coords.map((c, i) => `${i === 0 ? 'M' : 'L'} ${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(' ');
  const areaPath = `${linePath} L ${coords[coords.length - 1].x.toFixed(1)} ${height - padY} L ${coords[0].x.toFixed(1)} ${height - padY} Z`;
  const lastPositive = values[values.length - 1] >= 0;
  const stroke = lastPositive ? '#10b981' : '#f43f5e';
  const hovered = hoverIdx !== null ? coords[hoverIdx] : null;

  return (
    <div style={{ position: 'relative' }}>
      {hovered && (
        <div
          className="mono"
          style={{
            position: 'absolute',
            top: 2,
            left: 6,
            fontSize: 9.5,
            background: 'rgba(6, 10, 17, 0.9)',
            padding: '2px 6px',
            borderRadius: 4,
            border: '1px solid var(--border-subtle)',
            pointerEvents: 'none',
          }}
        >
          {hovered.label} {hovered.symbol || ''} •{' '}
          <strong style={{ color: hovered.cumPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
            {hovered.cumPnl >= 0 ? '+' : ''}₹{hovered.cumPnl.toFixed(2)}
          </strong>
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', height: 88, display: 'block' }}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <defs>
          <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.32" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.0" />
          </linearGradient>
        </defs>
        {minVal < 0 && maxVal > 0 && (
          <line
            x1={padX}
            x2={width - padX}
            y1={padY + (1 - (0 - minVal) / range) * (height - padY * 2)}
            y2={padY + (1 - (0 - minVal) / range) * (height - padY * 2)}
            stroke="rgba(148,163,184,0.22)"
            strokeDasharray="3 3"
          />
        )}
        <path d={areaPath} fill="url(#eqFill)" />
        <path d={linePath} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        {coords.map((c, idx) => (
          <circle
            key={idx}
            cx={c.x}
            cy={c.y}
            r={hoverIdx === idx ? 4.5 : idx === coords.length - 1 ? 3.5 : 2.5}
            fill={c.tradeNet !== undefined && c.tradeNet < 0 ? '#f43f5e' : stroke}
            style={{ cursor: 'pointer' }}
            onMouseEnter={() => setHoverIdx(idx)}
          />
        ))}
      </svg>
    </div>
  );
};
