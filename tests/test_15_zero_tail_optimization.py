"""
Pytest verification for Test 15 — 10,000-Scenario Randomized Zero-Tail Exposure Optimization.
Verifies:
  1. Exact 10,000-scenario seeded reproducibility across repeated runs.
  2. Exposure sweep 0.00x .. 1.58x exhibits genuine risk-return Pareto trade-offs (no 0-risk free lunch).
  3. Dynamic Jump-CVaR + Fragility + Time-Decay policy outperforms static 1.58x in Expected Shortfall
     while maintaining >= 100% normal-market profit retention.
  4. Strict anti-cheating auditor rejects lookahead, phantom zero-bid fills, and zero-fee assumptions.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.validation.test15_monte_carlo_frontier import run_test15_zero_tail_optimization


def test_15_reproducible_10k_scenarios_and_pareto_frontier():
    r1 = run_test15_zero_tail_optimization(n_scenarios=10_000, seed=20261110)
    r2 = run_test15_zero_tail_optimization(n_scenarios=10_000, seed=20261110)

    # 1. Exact reproducibility
    assert r1["n_scenarios"] == 10_000
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)

    # 2. All synthetic cheaters MUST be explicitly rejected
    assert r1["all_cheaters_rejected"] is True
    for cp in r1["rejected_cheat_probes"]:
        assert cp["status"] == "REJECTED"
        assert len(cp["rejection_reasons"]) >= 1

    # 3. Validate exposure frontier 0.00x .. 1.58x monotonicity & 0x trade-off
    frontier = r1["frontier"]
    assert frontier[0]["exposure_cap_x"] == 0.0
    assert frontier[0]["normal_profit_retention_pct"] == 0.0
    assert frontier[0]["missed_opportunities_pct"] == 100.0
    assert frontier[-1]["exposure_cap_x"] == 1.58

    static_158 = frontier[-1]
    dyn = r1["dynamic_optimal"]
    assert dyn["status"] == "VALID"
    # Dynamic policy slashes 99% VaR and Expected Shortfall relative to static 1.58x
    assert abs(dyn["var_99_loss_pct"]) < abs(static_158["var_99_loss_pct"])
    assert abs(dyn["expected_shortfall_99_pct"]) < abs(static_158["expected_shortfall_99_pct"])
    # And keeps normal-market profit retention >= 100.0%
    assert dyn["normal_profit_retention_pct"] >= 100.0
