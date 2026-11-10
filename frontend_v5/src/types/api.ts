export interface MarketStatus {
  is_open: boolean;
  is_holiday: boolean;
  holiday_name?: string;
  status_label: string;
  active_phase: string;
  last_active_date: string;
  next_session: string;
}

export interface MarketQuote {
  ltp: number;
  open: number;
  day_high: number;
  day_low: number;
  volume: number;
  l2_imbalance_pct?: number;
  buy_depth?: number;
  sell_depth?: number;
  last_trade_time?: string;
}

export interface IndexQuote {
  symbol: string;
  ticker: string;
  ltp: number;
  prev_close: number;
  change_pts: number;
  change_pct: number;
  trend: string;
}

export interface ActiveTrade {
  symbol: string;
  direction: string;
  entry_time: string;
  entry_price: number;
  stop_loss: number;
  total_qty: number;
  notional: number;
  target_1_price: number;
  target_2_est_price: number;
  fee_guard_price: number;
  active_floor: number;
  peak_favorable_r: number;
  status: string;
  current_ltp: number;
  unrealized_pnl: number;
}

export interface LiveEngineStatus {
  engine_status: string;
  last_updated: string | null;
  account: {
    ucc: string;
    clear_cash: number;
    available_margin: number;
    mis_margin_used: number;
    position_status: string;
  };
  today_net_pnl: number;
  strategy: {
    name: string;
    active_phase: string;
    execution_mode: string;
    macro_regime: string;
  };
  safety_guards: {
    latency_watchdog: {
      rolling_latency_ms: number;
      route_mode?: string;
      status?: string;
    };
    token_sentinel: {
      status: string;
      valid: boolean;
    };
    margin_validator: {
      status?: string;
      approved?: boolean;
      free_buffer_rs?: number;
    };
  };
  market_data: Record<string, MarketQuote>;
  indices?: Record<string, IndexQuote>;
  macro_live?: Record<string, IndexQuote>;
  lunch_bases: Record<string, { base_high: number; base_low: number }>;
  active_trade: ActiveTrade | null;
  market_status: MarketStatus;
}

export interface CandleBar {
  time: string | number;
  timestamp?: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface VolumeProfileNode {
  price: number;
  volume: number;
  is_poc: boolean;
  is_value_area: boolean;
}

export interface CockpitSymbolData {
  status: string;
  symbol: string;
  company_name: string;
  ltp: number;
  day_open: number;
  day_high: number;
  day_low: number;
  change_pts: number;
  change_pct: number;
  vwap: number;
  rsi_14: number;
  rvol: number;
  atr_15m: number;
  last_trade_time: string;
  ai_metrics: {
    drift_20d_pct: number;
    volatility_pct: number;
    crash_prob_pct: number;
    alpha_score: number;
    tail_safety: number;
    composite_score: number;
    members: Array<{
      model: string;
      direction: string;
      prob: number;
      weight: number;
      pred_ret_pct: number;
    }>;
  };
  factor_scores: Array<{ name: string; score: number }>;
  execution_brief: {
    recommendation: string;
    setup_mode: string;
    entry_zone: string;
    target_1: number;
    target_2: number;
    stop_loss: number;
    risk_per_trade_rs: number;
  };
  dalton_base: { base_high: number; base_low: number } | null;
  volume_profile: {
    success: boolean;
    poc_price: number;
    vah_price: number;
    val_price: number;
    total_volume: number;
    profile: VolumeProfileNode[];
  } | null;
}

export interface DaemonLogEntry {
  id: number;
  time_str: string;
  timestamp: number;
  level: string;
  subsystem: string;
  message: string;
  is_historical: boolean;
}

export interface DaemonLogsResponse {
  status: string;
  is_market_open: boolean;
  market_session?: MarketStatus;
  badge: string;
  mode: string;
  logs: DaemonLogEntry[];
}

export interface PremarketMacroResponse {
  status: string;
  timestamp: string;
  token_countdown: {
    seconds_remaining: number;
    formatted_remaining: string;
    auth_guard_status: string;
    token_expiry_timestamp: number;
  };
  global_macro: {
    gift_nifty: { last: number; change_pts: number; change_pct: number; trend?: string };
    banknifty?: { last: number; change_pts: number; change_pct: number; trend?: string };
    india_vix?: { last: number; change_pct: number; trend?: string };
    usdinr?: { last: number; change_pct: number; trend?: string };
    sp500: { last: number; change_pct: number; trend?: string };
    nasdaq: { last: number; change_pct: number; trend?: string };
    crude_oil_brent: { last: number; change_pct: number };
    us_10y_yield: { last: number; change_pts: number; change_pct?: number };
    global_risk?: string;
  };
  projected_openings: Array<{
    symbol: string;
    close: number;
    projected_open: number;
    expected_gap_pct: number;
    bias: string;
  }>;
  eod_reconciliation: {
    ledger_date?: string;
    starting_cash: number;
    today_net_alpha: number;
    ending_clear_cash: number;
    statutory_friction_paid: number;
    buying_power_3_5x: number;
    hard_reserve_floor: number;
    unencumbered_risk_budget: number;
  };
}

export interface AuditTrade {
  symbol: string;
  strategy: string;
  entry_time: string;
  entry_price: number;
  stop_loss: number;
  risk_per_share: number;
  total_qty: number;
  notional: number;
  target_1: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  peak_r: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  r_multiple: number;
}

export interface AuditLedgerResponse {
  date?: string;
  trades: AuditTrade[];
  summary?: {
    starting_capital: number;
    total_trades: number;
    wins: number;
    losses: number;
    win_rate_pct: number;
    total_net_pnl_rs: number;
    ending_capital: number;
    return_pct: number;
  };
  comparative_matrix?: Record<string, { trades: number; net_pnl_rs: number; ending_balance: number; assessment: string }>;
}

export interface PremarketAnalysisItem {
  symbol: string;
  predicted_return_pct: number;
  aleatoric_vol_pct: number;
  epistemic_uncertainty: number;
  drawdown_crash_prob: number;
  sharpe_score: number;
  verdict: string;
  badge: string;
  rationale: string;
}

export interface PremarketAnalysisResponse {
  scan_time: string;
  scan_date: string;
  stocks_analyzed: number;
  top_pick: string;
  items: PremarketAnalysisItem[];
}
