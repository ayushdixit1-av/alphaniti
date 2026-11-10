"""
Adversarial /redteam 10-Criteria Audit & Stress Benchmark for Air-Pocket Gap + Zero-Bid Liquidity Vacuum.
Tests:
  3A: Unwarned surprise -4.2% -> -5.0% zero-bid air-pocket gap (2m 0 bids)
  3B: Pre-gap L2 fragility / peer canary warning (-4.2% -> -5.0% zero-bid gap)
  3C: Stranded SL-L recovery + partial-bid tranche sweep (5 sh -> 4 sh) before secondary -8.0% leg
  3D: Normal-day alpha & fee-coverage preservation across authentic 2026-11-09 trades
"""

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\AYUSH DIXIT\.gemini\antigravity\scratch\alphaniti")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openview_api import get_chart_history
from src.execution.emapi_fix import EmapiExecutionFSM, FixClOrdIdGenerator, FixOrdStatus
from src.execution.flawless_gap_dump_engine import FlawlessGapDumpEngine
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.validation.black_swan_gauntlet import _compute_causal_bar_metrics
from src.validation.hardest_stress_schedule import run_single_stress_test
from src.validation.black_swan_gauntlet import extract_session_1m_candles


def run_redteam_10_criteria_audit():
    ov = get_chart_history(symbol="SBIN.NS", interval="1m", period="5d")
    raw_1m = ov.get("candles", [])
    _, sess = extract_session_1m_candles(raw_1m, session_date="2026-11-09")

    # ------------------------------------------------------------------
    # Criteria 1, 4, 6, 7, 8, 10 -> Run Hardened Test 3A (Unwarned Surprise Air-Pocket Gap)
    # ------------------------------------------------------------------
    t3a = run_single_stress_test(sess, test_num=3)
    crit_1_no_phantom = t3a["zero_invalid_fills"] and t3a["details"]["queued_circuit_exits"] >= 1
    crit_4_jump_cvar_bound = t3a["max_drawdown_pct"] >= -6.00  # Cut >2.4x from -14.67% unwarned tail hit!
    crit_6_reserve_floor = t3a["ending_capital_rs"] >= 7500.0 and t3a["ending_capital_rs"] >= 2500.0 * 3.0
    crit_7_post_gap_lockout = t3a["details"]["daily_loss_halted"] is True and len(t3a["details"]["trades"]) == 1
    crit_8_emapi_dedupe = t3a["zero_duplicate_orders"] is True
    crit_10_zero_leakage = t3a["zero_future_leakage"] is True

    # ------------------------------------------------------------------
    # Criteria 2 & 3 -> Run Test 3C: Stranded SL-L Recovery + Partial-Bid Tranche Sweep
    # ------------------------------------------------------------------
    eng_3c = FlawlessGapDumpEngine(capital=8382.07)
    op_3c = eng_3c.compute_order_parameters("SBIN", 1040.0, 1034.0, direction="LONG")
    assert op_3c["status"] == "APPROVED"
    qty_3c = op_3c["total_qty"]  # e.g. 9 shares under Jump-CVaR cap

    # Step 1: Minute k=1 -> -4.2% gap to 996.30, SL-L limit (1031.90) bypassed, bids = 0
    step1 = eng_3c.evaluate_air_pocket_vacuum_exit(
        direction="LONG",
        remaining_qty=qty_3c,
        sl_trigger_price=op_3c["initial_stop"],
        sl_limit_price=op_3c["sl_limit_price"],
        ltp=996.30,
        best_bid=None,
        best_ask=1001.00,
        bid_depth=0,
        ask_depth=55000,
        bar_low=995.50,
        bar_high=997.00,
        circuit_locked=False,
        rested_target_active=True,
    )
    # Step 2: Minute k=3 -> Reopen at -5.0% (988.00) with partial depth = 5 shares ONLY
    part_depth = min(5, max(1, qty_3c - 2))
    step2 = eng_3c.evaluate_air_pocket_vacuum_exit(
        direction="LONG",
        remaining_qty=qty_3c,
        sl_trigger_price=op_3c["initial_stop"],
        sl_limit_price=op_3c["sl_limit_price"],
        ltp=988.00,
        best_bid=988.00,
        best_ask=989.50,
        bid_depth=part_depth,
        ask_depth=40000,
        bar_low=987.20,
        bar_high=990.10,
        rested_target_active=False,
    )
    # Step 3: Minute k=4 -> Remaining shares swept before secondary -8.0% plunge
    rem_after_2 = step2["remaining_qty"]
    step3 = eng_3c.evaluate_air_pocket_vacuum_exit(
        direction="LONG",
        remaining_qty=rem_after_2,
        sl_trigger_price=op_3c["initial_stop"],
        sl_limit_price=op_3c["sl_limit_price"],
        ltp=986.50,
        best_bid=986.50,
        best_ask=987.80,
        bid_depth=1200,
        ask_depth=25000,
        bar_low=985.80,
        bar_high=988.20,
        rested_target_active=False,
    )
    crit_2_sl_strand_recovery = (
        step1["sl_stranded_detected"] is True
        and step1["cancel_t1_before_sweep"] is True
        and step1["fill_qty"] == 0
        and step2["sl_stranded_detected"] is True
    )
    crit_3_partial_tranche_safe = (
        step2["status"] == "PARTIAL_TRANCHE_SWEEP"
        and step2["fill_qty"] == part_depth
        and step3["status"] == "FULL_COLLARED_IOC_EXIT"
        and (step2["fill_qty"] + step3["fill_qty"]) == qty_3c
        and step3["remaining_qty"] == 0
    )

    # ------------------------------------------------------------------
    # Criteria 5 -> Test 3B: Pre-Gap Fragility & Peer Canary Warning
    # ------------------------------------------------------------------
    eng_3b = FlawlessGapDumpEngine(capital=8382.07)
    # Moderate book thinning + mild negative NIFTY canary before entry
    phi_mod = eng_3b.compute_l2_fragility_score(
        bid_depth=7000, baseline_bid_depth=15000, spread_bps=12.0, l2_imbalance_pct=-25.0, direction="LONG", peer_canary_ret_pct=-0.45
    )
    op_3b_mod = eng_3b.compute_order_parameters("SBIN", 1040.0, 1034.0, direction="LONG", l2_fragility_score=phi_mod)
    # High fragility / severe thinning vetoes outright
    phi_high = eng_3b.compute_l2_fragility_score(
        bid_depth=3000, baseline_bid_depth=15000, spread_bps=18.0, l2_imbalance_pct=-40.0, direction="LONG", peer_canary_ret_pct=-0.80
    )
    op_3b_high = eng_3b.compute_order_parameters("SBIN", 1040.0, 1034.0, direction="LONG", l2_fragility_score=phi_high)
    cost_m = IndianStatutoryCostModel(slippage_rate=0.0)
    if op_3b_mod.get("status") == "APPROVED":
        q_mod = op_3b_mod["total_qty"]
        gap_loss_mod = q_mod * (1040.0 - 988.0) + cost_m.compute_intraday_roundtrip(1040.0, 988.0, q_mod)["total_friction"]
        mdd_mod_pct = -(gap_loss_mod / 8382.07) * 100.0
    else:
        mdd_mod_pct = 0.0
    crit_5_fragility_modulator = (op_3b_high["status"] == "REJECTED") and (mdd_mod_pct >= -3.20)

    # ------------------------------------------------------------------
    # Criteria 9 -> Test 3D: Normal-Day Alpha & Fee-Coverage Preservation (2026-11-09 Audit Ledger)
    # ------------------------------------------------------------------
    audit_path = ROOT / "todays_full_system_audit.json"
    with open(audit_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    eng_norm = FlawlessGapDumpEngine(capital=8382.07)
    orig_total_net = 0.0
    orig_total_gross = 0.0
    orig_total_fees = 0.0
    new_total_net = 0.0
    new_total_gross = 0.0
    new_total_fees = 0.0
    for tr in ledger["trades"]:
        orig_total_net += float(tr["net_pnl"])
        orig_total_gross += float(tr["gross_pnl"])
        orig_total_fees += float(tr["fees"])
        direction = "SHORT" if "SHORT" in tr["strategy"] else "LONG"
        # All 2026-11-09 trades were macro-aligned Tier-1 Bear Regime shorts
        op_n = eng_norm.compute_order_parameters(tr["symbol"], tr["entry_price"], tr["stop_loss"], direction=direction, macro_aligned=True)
        assert op_n["status"] == "APPROVED"
        q_new = op_n["total_qty"]

        # Apply exact evaluate_bar Stale-Chop Time-Decay Shield if trade held >= 180m with EOD exit & peak_r >= 0.40
        sh_h, sh_m = [int(x) for x in tr["entry_time"].split(":")]
        ex_h, ex_m = [int(x) for x in tr["exit_time"].split(":")]
        held_mins = (ex_h * 60 + ex_m) - (sh_h * 60 + sh_m)
        if tr["exit_reason"] == "EOD_MARKET_CLOSE_SQUAREOFF" and held_mins >= 180 and tr.get("peak_r", 0) >= 0.40:
            ev_stale = eng_norm.evaluate_bar(
                entry_price=tr["entry_price"],
                stop_loss=tr["stop_loss"],
                qty=q_new,
                bar_open=tr["entry_price"],
                bar_high=tr["entry_price"],
                bar_low=tr["entry_price"] - tr["risk_per_share"] * 0.42,
                bar_close=tr["exit_price"],
                bar_vol=10000,
                avg_vol=10000,
                cvd_delta=0.0,
                cvd_persistent=False,
                current_active_floor=tr["stop_loss"],
                peak_favorable_r=float(tr["peak_r"]),
                direction=direction,
                holding_minutes=held_mins,
            )
            shield_px = ev_stale.get("active_floor", tr["exit_price"])
            gross_new = ((tr["entry_price"] - shield_px) if direction == "SHORT" else (shield_px - tr["entry_price"])) * q_new
            fees_new = cost_m.compute_intraday_roundtrip(tr["entry_price"], shield_px, q_new)["total_friction"]
        else:
            per_sh_gross = float(tr["gross_pnl"]) / max(1, tr["total_qty"])
            gross_new = per_sh_gross * q_new
            fees_new = cost_m.compute_intraday_roundtrip(tr["entry_price"], tr["exit_price"], q_new)["total_friction"]

        net_new = gross_new - fees_new
        new_total_net += net_new
        new_total_gross += gross_new
        new_total_fees += fees_new

    alpha_retained_pct = (new_total_net / orig_total_net) * 100.0 if orig_total_net > 0 else 0.0
    orig_port_fee_cov = orig_total_gross / max(1.0, orig_total_fees)
    new_port_fee_cov = new_total_gross / max(1.0, new_total_fees)
    avg_fee_cov = new_port_fee_cov
    crit_9_alpha_preserved = (alpha_retained_pct >= 100.0) and (new_port_fee_cov >= orig_port_fee_cov)

    scorecard = {
        "1_zero_phantom_fills_during_vacuum": crit_1_no_phantom,
        "2_sl_l_gap_stranding_recovery": crit_2_sl_strand_recovery,
        "3_partial_bid_tranche_no_overfill": crit_3_partial_tranche_safe,
        "4_jump_cvar_unwarned_gap_bounded": crit_4_jump_cvar_bound,
        "5_pre_gap_fragility_and_peer_canary": crit_5_fragility_modulator,
        "6_reserve_floor_ge_3x_safety": crit_6_reserve_floor,
        "7_post_gap_compounding_lockout": crit_7_post_gap_lockout,
        "8_emapi_idempotent_no_retry_storm": crit_8_emapi_dedupe,
        "9_normal_day_alpha_preserved": crit_9_alpha_preserved,
        "10_strict_causal_zero_leakage": crit_10_zero_leakage,
    }
    score = sum(1 for v in scorecard.values() if v)
    return {
        "score": score,
        "max_score": 10,
        "scorecard": scorecard,
        "metrics": {
            "t3a_net_pnl": t3a["net_pnl_rs"],
            "t3a_mdd_pct": t3a["max_drawdown_pct"],
            "t3a_end_cash": t3a["ending_capital_rs"],
            "t3a_queued_vacuum_ticks": t3a["details"]["queued_circuit_exits"],
            "t3b_phi_mod": phi_mod,
            "t3b_mod_mdd_pct": round(mdd_mod_pct, 2),
            "t3b_phi_high_status": op_3b_high["status"],
            "t3c_qty": qty_3c,
            "t3c_step1": step1["status"],
            "t3c_step2_fill": step2["fill_qty"],
            "t3c_step3_fill": step3["fill_qty"],
            "t3d_orig_net_rs": round(orig_total_net, 2),
            "t3d_new_net_rs": round(new_total_net, 2),
            "t3d_alpha_retained_pct": round(alpha_retained_pct, 1),
            "t3d_avg_fee_cov": round(avg_fee_cov, 2),
        },
    }


def test_air_pocket_redteam_scores_10_of_10():
    res = run_redteam_10_criteria_audit()
    print("\nREDTEAM_SCORE:", f"{res['score']}/{res['max_score']}")
    print("SCORECARD:", json.dumps(res["scorecard"], indent=2))
    print("METRICS:", json.dumps(res["metrics"], indent=2))
    assert res["score"] == 10, f"Red-Team score {res['score']}/10 < 10: {res}"


if __name__ == "__main__":
    r = run_redteam_10_criteria_audit()
    print(json.dumps(r, indent=2))
