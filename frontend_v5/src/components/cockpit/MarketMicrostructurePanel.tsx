import React from 'react';
import { BarChart3, BookOpen, Droplets, Scale, Waves } from 'lucide-react';
import { MicrostructureData } from '../../types/api';

interface MarketMicrostructurePanelProps {
  symbol: string;
  micro?: MicrostructureData | null;
}

function fmtVol(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`;
  return String(v);
}

export const MarketMicrostructurePanel: React.FC<MarketMicrostructurePanelProps> = ({
  symbol,
  micro,
}) => {
  // Strictly omit panel if backend reports no reliable microstructure data
  if (!micro || !micro.has_any_reliable) {
    return null;
  }

  const showImbalance = Boolean(micro.imbalance_reliable && micro.bid_ask_imbalance);
  const showDepth = Boolean(micro.depth_reliable && micro.order_book_depth);
  const showDelta = Boolean(micro.volume_delta_reliable && micro.volume_delta);
  const showRvol = Boolean(micro.rvol_reliable && micro.relative_volume);
  const showLiquidity = Boolean(micro.liquidity_reliable && micro.liquidity_conditions);

  if (!showImbalance && !showDepth && !showDelta && !showRvol && !showLiquidity) {
    return null;
  }

  const buyRatio =
    showDelta && micro.volume_delta && micro.volume_delta.total_volume > 0
      ? Math.round((micro.volume_delta.buy_volume_est / micro.volume_delta.total_volume) * 100)
      : 50;

  const bidImbPct =
    showImbalance && micro.bid_ask_imbalance
      ? Math.round(
          (micro.bid_ask_imbalance.buy_qty /
            Math.max(1, micro.bid_ask_imbalance.buy_qty + micro.bid_ask_imbalance.sell_qty)) *
            100
        )
      : 50;

  return (
    <div className="panel-card" style={{ padding: '8px 12px', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Waves size={13} color="var(--accent-cyan)" />
          Market Microstructure ({symbol})
        </span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
          Verified Empirical Telemetry Only
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
          gap: 8,
        }}
      >
        {/* 1. Bid/Ask Imbalance (Only when live L2 book active on both sides) */}
        {showImbalance && micro.bid_ask_imbalance && (
          <div style={{ background: '#060a11', borderRadius: 7, padding: '8px 10px', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-muted)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <Scale size={12} color="var(--accent-cyan)" />
                Bid / Ask Imbalance
              </span>
              <strong
                className="mono"
                style={{
                  color: micro.bid_ask_imbalance.imbalance_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                }}
              >
                {micro.bid_ask_imbalance.imbalance_pct >= 0 ? '+' : ''}
                {micro.bid_ask_imbalance.imbalance_pct.toFixed(1)}%
              </strong>
            </div>
            <div style={{ height: 6, borderRadius: 4, overflow: 'hidden', display: 'flex', marginTop: 6, background: 'rgba(255,255,255,0.06)' }}>
              <div style={{ width: `${bidImbPct}%`, background: 'var(--accent-green)' }} />
              <div style={{ width: `${100 - bidImbPct}%`, background: 'var(--accent-red)' }} />
            </div>
            <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
              <span>Bid {fmtVol(micro.bid_ask_imbalance.buy_qty)}</span>
              <span>Ask {fmtVol(micro.bid_ask_imbalance.sell_qty)}</span>
            </div>
          </div>
        )}

        {/* 2. Order-Book Depth (Only when live L2 5-level quotes exist) */}
        {showDepth && micro.order_book_depth && (
          <div style={{ background: '#060a11', borderRadius: 7, padding: '8px 10px', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-muted)', marginBottom: 4 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <BookOpen size={12} color="var(--accent-cyan)" />
                L2 Order-Book Depth
              </span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--accent-cyan)' }}>
                {micro.order_book_depth.best_bid?.toFixed(2)} / {micro.order_book_depth.best_ask?.toFixed(2)}
              </span>
            </div>
            <div className="mono" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 10 }}>
              <div>
                {micro.order_book_depth.bid_levels.slice(0, 3).map((b, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--accent-green)' }}>
                    <span>{b.quantity.toLocaleString()}</span>
                    <span>₹{b.price.toFixed(2)}</span>
                  </div>
                ))}
              </div>
              <div>
                {micro.order_book_depth.ask_levels.slice(0, 3).map((a, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--accent-red)' }}>
                    <span>₹{a.price.toFixed(2)}</span>
                    <span>{a.quantity.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 3. Volume Delta */}
        {showDelta && micro.volume_delta && (
          <div style={{ background: '#060a11', borderRadius: 7, padding: '8px 10px', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-muted)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <BarChart3 size={12} color="var(--accent-cyan)" />
                Volume Delta
              </span>
              <strong
                className="mono"
                style={{
                  color: micro.volume_delta.cumulative_delta >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                }}
              >
                {micro.volume_delta.cumulative_delta >= 0 ? '+' : ''}
                {fmtVol(micro.volume_delta.cumulative_delta)} ({micro.volume_delta.delta_pct >= 0 ? '+' : ''}
                {micro.volume_delta.delta_pct.toFixed(1)}%)
              </strong>
            </div>
            <div style={{ height: 6, borderRadius: 4, overflow: 'hidden', display: 'flex', marginTop: 6, background: 'rgba(255,255,255,0.06)' }}>
              <div style={{ width: `${buyRatio}%`, background: 'var(--accent-green)' }} />
              <div style={{ width: `${100 - buyRatio}%`, background: 'var(--accent-red)' }} />
            </div>
            <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
              <span>Buy {fmtVol(micro.volume_delta.buy_volume_est)}</span>
              <span>5-Bar: {micro.volume_delta.recent_5bar_delta >= 0 ? '+' : ''}{fmtVol(micro.volume_delta.recent_5bar_delta)}</span>
              <span>Sell {fmtVol(micro.volume_delta.sell_volume_est)}</span>
            </div>
          </div>
        )}

        {/* 4. Relative Volume */}
        {showRvol && micro.relative_volume && (
          <div style={{ background: '#060a11', borderRadius: 7, padding: '8px 10px', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-muted)' }}>
              <span>Relative Volume (RVOL)</span>
              <strong
                className="mono"
                style={{
                  color:
                    micro.relative_volume.rvol >= 1.5
                      ? 'var(--accent-green)'
                      : micro.relative_volume.rvol >= 0.9
                      ? 'var(--accent-cyan)'
                      : 'var(--accent-amber)',
                }}
              >
                {micro.relative_volume.rvol.toFixed(2)}x
              </strong>
            </div>
            <div className="mono" style={{ fontSize: 11, fontWeight: 700, marginTop: 5, color: 'var(--text-main)' }}>
              {micro.relative_volume.regime}
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>
              Vol {fmtVol(micro.relative_volume.session_volume)} vs 5D Avg {fmtVol(micro.relative_volume.avg_5d_volume)}
            </div>
          </div>
        )}

        {/* 5. Liquidity Conditions */}
        {showLiquidity && micro.liquidity_conditions && (
          <div style={{ background: '#060a11', borderRadius: 7, padding: '8px 10px', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-muted)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <Droplets size={12} color="var(--accent-cyan)" />
                Liquidity Conditions
              </span>
              <strong className="mono" style={{ color: 'var(--accent-green)' }}>
                {micro.liquidity_conditions.tier.replace('_', ' ')}
              </strong>
            </div>
            <div className="mono" style={{ fontSize: 12, fontWeight: 700, marginTop: 5, color: '#fff' }}>
              Turnover: ₹{micro.liquidity_conditions.turnover_cr.toLocaleString('en-IN')} Cr
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>
              {micro.spread_reliable && micro.liquidity_conditions.spread_bps !== null
                ? `Top-of-Book Spread: ${micro.liquidity_conditions.spread_bps.toFixed(2)} bps`
                : `Session Shares Traded: ${fmtVol(micro.liquidity_conditions.volume)}`}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
