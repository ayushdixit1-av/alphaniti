import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  Bell,
  Cpu,
  History,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Wallet,
  KeyRound,
  Gauge,
  CandlestickChart,
} from 'lucide-react';
import {
  AuditLedgerResponse,
  CandleBar,
  CockpitSymbolData,
  DaemonLogsResponse,
  LiveEngineStatus,
  PremarketAnalysisResponse,
  PremarketMacroResponse,
  RiskCommandResponse,
} from './types/api';
import { ExecutionWorkspace } from './components/cockpit/ExecutionWorkspace';
import { ReplayWorkspace } from './components/workspaces/ReplayWorkspace';
import { AIResearchWorkspace } from './components/workspaces/AIResearchWorkspace';
import { AuditRiskWorkspace } from './components/workspaces/AuditRiskWorkspace';
import { RiskCommandCenter } from './components/workspaces/RiskCommandCenter';
import { StrategyChartWorkspace } from './components/workspaces/StrategyChartWorkspace';
import { TelemetryConsole } from './components/layout/TelemetryConsole';
import { CommandPalette } from './components/layout/CommandPalette';

const ALL_SYMBOLS = [
  'SBIN',
  'HDFCBANK',
  'ICICIBANK',
  'AXISBANK',
  'KOTAKBANK',
  'RELIANCE',
  'TCS',
  'INFY',
  'BAJFINANCE',
  'NIFTY',
  'BANKNIFTY',
];

type WorkspaceId = 'execution' | 'chart' | 'replay' | 'ai' | 'risk' | 'audit';

function getInitialWorkspace(): WorkspaceId {
  const valid: WorkspaceId[] = ['execution', 'chart', 'replay', 'ai', 'risk', 'audit'];
  const hash = (window.location.hash || '').replace('#', '').toLowerCase() as WorkspaceId;
  if (valid.includes(hash)) return hash;
  const qp = new URLSearchParams(window.location.search).get('ws')?.toLowerCase() as WorkspaceId | undefined;
  if (qp && valid.includes(qp)) return qp;
  return 'execution';
}

export function App() {
  const [workspace, setWorkspaceState] = useState<WorkspaceId>(getInitialWorkspace);
  const setWorkspace = useCallback((ws: WorkspaceId) => {
    setWorkspaceState(ws);
    if (window.location.hash !== `#${ws}`) {
      window.history.replaceState(null, '', `#${ws}`);
    }
  }, []);
  const [selectedSymbol, setSelectedSymbol] = useState<string>('SBIN');
  const [timeframe, setTimeframe] = useState<string>('5m');
  const [searchOpen, setSearchOpen] = useState<boolean>(false);

  const [engineStatus, setEngineStatus] = useState<LiveEngineStatus | null>(null);
  const [cockpit, setCockpit] = useState<CockpitSymbolData | null>(null);
  const [candles, setCandles] = useState<CandleBar[]>([]);
  const [sessionDate, setSessionDate] = useState<string>('2026-11-09');
  const [isLiveToday, setIsLiveToday] = useState<boolean>(false);
  const [telemetry, setTelemetry] = useState<DaemonLogsResponse | null>(null);
  const [macro, setMacro] = useState<PremarketMacroResponse | null>(null);
  const [auditLedger, setAuditLedger] = useState<AuditLedgerResponse | null>(null);
  const [premarket, setPremarket] = useState<PremarketAnalysisResponse | null>(null);
  const [riskCommand, setRiskCommand] = useState<RiskCommandResponse | null>(null);

  const [istClock, setIstClock] = useState<string>('--:--:-- IST');
  const [tokenSecondsLeft, setTokenSecondsLeft] = useState<number>(0);

  // Live IST Clock & smooth 1-second Token TTL countdown
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const utc = now.getTime() + now.getTimezoneOffset() * 60000;
      const ist = new Date(utc + 3600000 * 5.5);
      const hh = String(ist.getHours()).padStart(2, '0');
      const mm = String(ist.getMinutes()).padStart(2, '0');
      const ss = String(ist.getSeconds()).padStart(2, '0');
      setIstClock(`${hh}:${mm}:${ss} IST`);
      setTokenSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // Keyboard shortcuts: Ctrl+K Command Palette + Esc close + Up/Down ticker switch
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen((o) => !o);
      } else if (e.key === 'Escape') {
        setSearchOpen(false);
      } else if (!searchOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
        e.preventDefault();
        setSelectedSymbol((cur) => {
          const idx = ALL_SYMBOLS.indexOf(cur);
          if (idx === -1) return ALL_SYMBOLS[0];
          const next =
            e.key === 'ArrowDown'
              ? (idx + 1) % ALL_SYMBOLS.length
              : (idx - 1 + ALL_SYMBOLS.length) % ALL_SYMBOLS.length;
          return ALL_SYMBOLS[next];
        });
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [searchOpen]);

  // Fetch core live feeds (Engine Status, Telemetry, Macro, Ledger, Premarket AI)
  const fetchLiveFeeds = useCallback(async () => {
    if (document.hidden) return;
    try {
      const [engRes, telRes, macRes, riskRes] = await Promise.all([
        fetch('/api/live_engine_status').then((r) => (r.ok ? r.json() : null)),
        fetch('/api/daemon_logs?limit=50').then((r) => (r.ok ? r.json() : null)),
        fetch('/api/premarket_macro').then((r) => (r.ok ? r.json() : null)),
        fetch('/api/risk_command').then((r) => (r.ok ? r.json() : null)),
      ]);
      if (engRes) setEngineStatus(engRes);
      if (telRes) setTelemetry(telRes);
      if (riskRes && riskRes.status === 'SUCCESS') setRiskCommand(riskRes);
      if (macRes) {
        setMacro(macRes);
        if (macRes.token_countdown?.seconds_remaining !== undefined) {
          setTokenSecondsLeft(macRes.token_countdown.seconds_remaining);
        }
      }
    } catch {
      // transient network hiccup ignored safely
    }
  }, []);

  useEffect(() => {
    fetchLiveFeeds();
    const id = setInterval(fetchLiveFeeds, 3000);
    return () => clearInterval(id);
  }, [fetchLiveFeeds]);

  // Fetch Audit Ledger & Premarket AI Analysis once + every 30s
  useEffect(() => {
    const loadStaticFeeds = async () => {
      try {
        const [audRes, pmRes] = await Promise.all([
          fetch('/api/audit_ledger').then((r) => (r.ok ? r.json() : null)),
          fetch('/api/premarket_analysis').then((r) => (r.ok ? r.json() : null)),
        ]);
        if (audRes) setAuditLedger(audRes);
        if (pmRes) setPremarket(pmRes);
      } catch {
        // ignore
      }
    };
    loadStaticFeeds();
    const id = setInterval(loadStaticFeeds, 30000);
    return () => clearInterval(id);
  }, []);

  // Fetch selected symbol Cockpit & Candles whenever symbol or timeframe changes
  const fetchSymbolData = useCallback(async () => {
    try {
      const cockpitPromise = fetch(`/api/cockpit_symbol/${selectedSymbol}`).then((r) =>
        r.ok ? r.json() : null
      );

      let candlesPromise: Promise<any>;
      if (timeframe === '5m') {
        candlesPromise = fetch(`/api/candles/${selectedSymbol}`).then((r) =>
          r.ok ? r.json() : null
        );
      } else {
        const ovSym = selectedSymbol === 'NIFTY' ? 'NIFTY50' : selectedSymbol;
        const periodMap: Record<string, string> = {
          '1m': '1d',
          '15m': '5d',
          '1h': '1mo',
          D: '6mo',
        };
        const ovInterval = timeframe === 'D' ? '1d' : timeframe;
        const period = periodMap[timeframe] || '5d';
        candlesPromise = fetch(
          `/api/openview/history?symbol=${encodeURIComponent(ovSym)}&interval=${ovInterval}&period=${period}`
        ).then((r) => (r.ok ? r.json() : null));
      }

      const [cData, candleData] = await Promise.all([cockpitPromise, candlesPromise]);
      if (cData && cData.status === 'SUCCESS') {
        setCockpit(cData);
      }
      if (candleData && candleData.candles && candleData.candles.length > 0) {
        setCandles(candleData.candles);
        setSessionDate(candleData.session_date || engineStatus?.market_status?.last_active_date || '2026-11-09');
        setIsLiveToday(Boolean(candleData.is_live_today));
      } else if (timeframe !== '5m') {
        // Fallback cleanly to Groww 5m if OpenView interval empty
        const fb = await fetch(`/api/candles/${selectedSymbol}`).then((r) => (r.ok ? r.json() : null));
        if (fb?.candles) {
          setCandles(fb.candles);
          setSessionDate(fb.session_date || '2026-11-09');
          setIsLiveToday(Boolean(fb.is_live_today));
        }
      }
    } catch {
      // ignore
    }
  }, [selectedSymbol, timeframe, engineStatus?.market_status?.last_active_date]);

  useEffect(() => {
    fetchSymbolData();
    const id = setInterval(() => {
      if (engineStatus?.market_status?.is_open && !document.hidden) {
        fetchSymbolData();
      }
    }, 10000);
    return () => clearInterval(id);
  }, [fetchSymbolData, engineStatus?.market_status?.is_open]);

  const ms = engineStatus?.market_status;
  const clearCash = engineStatus?.account?.clear_cash ?? 8382.07;
  const riskCap = Math.round(clearCash * 0.016);
  const latencyMs = engineStatus?.safety_guards?.latency_watchdog?.rolling_latency_ms ?? 64;
  const ttlH = Math.floor(tokenSecondsLeft / 3600);
  const ttlM = Math.floor((tokenSecondsLeft % 3600) / 60);
  const ttlFormatted = tokenSecondsLeft > 0 ? `${ttlH}h${String(ttlM).padStart(2, '0')}m` : macro?.token_countdown?.formatted_remaining || '--';

  const navItems: Array<{ id: WorkspaceId; short: string; label: string; icon: React.ReactNode }> = [
    { id: 'execution', short: 'Exec', label: 'Execution', icon: <Activity size={17} /> },
    { id: 'chart', short: 'Chart', label: '1m A8 Chart', icon: <CandlestickChart size={17} /> },
    { id: 'replay', short: 'Rply', label: 'Replay', icon: <History size={17} /> },
    { id: 'ai', short: 'AI', label: 'AI Research', icon: <Cpu size={17} /> },
    { id: 'risk', short: 'Risk', label: 'Risk Command', icon: <ShieldAlert size={17} /> },
    { id: 'audit', short: 'Audit', label: 'Audit & Ledger', icon: <ShieldCheck size={17} /> },
  ];

  return (
    <div className="cockpit-shell">
      {/* TOP COMMAND RAIL */}
      <header className="top-rail">
        <div className="brand-wrap">
          <div className="brand-icon">A</div>
          <div className="brand-title">
            <span className="brand-name">AlphaNiti v5.0</span>
            <span className="brand-sub">Quantitative Execution Cockpit</span>
          </div>
        </div>

        <div className="top-pills">
          <div className={`pill ${ms?.is_open ? 'pill-live' : 'pill-holiday'}`}>
            <span className={`dot ${ms?.is_open ? 'dot-green' : 'dot-amber'}`} />
            <span style={{ color: ms?.is_open ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
              {ms?.is_open ? 'LIVE MARKET' : ms?.is_holiday ? 'NSE HOLIDAY' : 'STANDBY'}
            </span>
            <span style={{ color: 'var(--text-dim)' }}>|</span>
            <span>NSE</span>
            <span style={{ color: 'var(--text-dim)' }}>|</span>
            <span style={{ color: 'var(--text-muted)' }}>
              {ms?.is_holiday ? (ms?.holiday_name?.split('(')[0].trim().slice(0, 14) || 'Holiday') : ms?.last_active_date || sessionDate}
            </span>
          </div>

          <div className="pill">
            <Gauge size={13} color="var(--accent-green)" />
            <span>Groww</span>
            <strong style={{ color: 'var(--accent-green)' }}>{Math.round(latencyMs)} ms</strong>
          </div>

          <div className="pill">
            <Wallet size={13} color="var(--accent-cyan)" />
            <span style={{ color: 'var(--text-muted)' }}>Cash</span>
            <strong>₹{Math.round(clearCash).toLocaleString('en-IN')}</strong>
          </div>

          <button
            className="pill"
            style={{ cursor: 'pointer', borderColor: workspace === 'risk' ? 'var(--accent-cyan)' : undefined }}
            onClick={() => setWorkspace('risk')}
            title="Open Dedicated Risk Command Center"
          >
            <ShieldAlert size={13} color={riskCommand?.tripped_count ? 'var(--accent-red)' : 'var(--accent-amber)'} />
            <span style={{ color: 'var(--text-muted)' }}>Risk Cap</span>
            <strong>₹{riskCap} (1.6%)</strong>
          </button>

          <div className="pill pill-purple">
            <KeyRound size={13} color="var(--accent-purple)" />
            <span style={{ color: 'var(--text-muted)' }}>SEBI Token TTL</span>
            <strong style={{ color: '#e9d5ff' }}>{ttlFormatted}</strong>
          </div>
        </div>

        <div className="top-right">
          <button className="search-trigger" onClick={() => setSearchOpen(true)}>
            <Search size={13} />
            <span>Ctrl + K Search...</span>
          </button>
          <span className="clock-box">{istClock}</span>
          <button
            className="icon-btn"
            title="Telemetry & Alerts"
            onClick={() => setWorkspace('audit')}
          >
            <Bell size={15} />
          </button>
          <button
            className="icon-btn"
            title="Replay & Stress Lab"
            onClick={() => setWorkspace('replay')}
          >
            <Settings size={15} />
          </button>
        </div>
      </header>

      {/* BODY: Left Navigation Rail + Active Workspace + Bottom Telemetry */}
      <div className="shell-body">
        <aside className="left-rail">
          {navItems.map((it) => (
            <button
              key={it.id}
              className={`rail-btn ${workspace === it.id ? 'active' : ''}`}
              onClick={() => setWorkspace(it.id)}
              title={it.label}
            >
              {it.icon}
              <span>{it.short}</span>
            </button>
          ))}
        </aside>

        <main className="main-area">
          {/* Workspace Sub-Tab Header */}
          <div className="workspace-bar">
            <div className="workspace-tabs">
              <span className="ws-label">Workspace:</span>
              {navItems.map((it) => (
                <button
                  key={it.id}
                  className={`ws-tab ${workspace === it.id ? 'active' : ''}`}
                  onClick={() => setWorkspace(it.id)}
                >
                  {it.label}
                </button>
              ))}
              <a
                href="/#chart"
                target="_blank"
                rel="noopener noreferrer"
                title="Open 1-Minute Strategy Chart Explorer in a dedicated browser tab"
                style={{
                  marginLeft: 8,
                  padding: '3px 9px',
                  borderRadius: 6,
                  border: '1px solid rgba(56, 189, 248, 0.35)',
                  background: 'rgba(56, 189, 248, 0.1)',
                  color: 'var(--accent-cyan)',
                  fontSize: 11,
                  fontFamily: 'var(--mono)',
                  textDecoration: 'none',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                Open #chart ↗
              </a>
            </div>

            {ms?.is_holiday && (
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--accent-amber)' }}>
                {ms.holiday_name} • Next Open: {ms.next_session}
              </div>
            )}
          </div>

          {/* Workspace Viewport */}
          {workspace === 'execution' && (
            <ExecutionWorkspace
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
              timeframe={timeframe}
              onTimeframeChange={setTimeframe}
              engineStatus={engineStatus}
              cockpit={cockpit}
              candles={candles}
              sessionDate={sessionDate}
              isLiveToday={isLiveToday}
              macro={macro}
              auditLedger={auditLedger}
              onOpenSearch={() => setSearchOpen(true)}
              onRefreshSymbol={fetchSymbolData}
            />
          )}

          {workspace === 'chart' && (
            <StrategyChartWorkspace
              initialSymbol={selectedSymbol}
              defaultCapital={clearCash > 0 ? clearCash : 10000}
            />
          )}

          {workspace === 'replay' && (
            <ReplayWorkspace
              auditLedger={auditLedger}
              onSelectSymbolForCockpit={(sym) => {
                setSelectedSymbol(sym);
                setWorkspace('execution');
              }}
            />
          )}

          {workspace === 'ai' && (
            <AIResearchWorkspace
              premarket={premarket}
              macro={macro}
              onInspectSymbol={(sym) => {
                setSelectedSymbol(sym);
                setWorkspace('execution');
              }}
            />
          )}

          {workspace === 'risk' && (
            <RiskCommandCenter
              riskData={riskCommand}
              onInspectSymbol={(sym) => {
                setSelectedSymbol(sym);
                setWorkspace('execution');
              }}
            />
          )}

          {workspace === 'audit' && (
            <AuditRiskWorkspace
              auditLedger={auditLedger}
              engineStatus={engineStatus}
              macro={macro}
              onInspectSymbol={(sym) => {
                setSelectedSymbol(sym);
                setWorkspace('replay');
              }}
            />
          )}

          {/* Persistent Collapsible Telemetry Console */}
          <TelemetryConsole telemetry={telemetry} />
        </main>
      </div>

      <CommandPalette
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        symbols={ALL_SYMBOLS}
        onSelectSymbol={setSelectedSymbol}
        onSelectWorkspace={setWorkspace}
      />
    </div>
  );
}

export default App;
