"""
Apify cooldown + monthly spending cap.

LinkedIn data is the one source in this app that costs real money (about
$0.10 per 1,000 results), because it comes from an Apify actor rather than
a free API. Everything in this file exists to make sure we never
accidentally blow past the free $5/month Apify credit — see spec §8.

Two independent checks gate every Apify run:
  1. Cooldown: has it been at least `apify_cooldown_minutes` since the last
     run? (Stops us hammering Apify on every refresh click.)
  2. Budget: would this run push this month's estimated spend over
     `apify_monthly_budget_usd`? (The hard money cap — never skipped, even
     for a "Force LinkedIn refresh".)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from jobboard import db


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str  # human-readable explanation, shown in the UI when skipped
    spend_so_far_usd: float
    monthly_budget_usd: float
    estimated_run_cost_usd: float


def estimate_run_cost(
    max_results: int,
    price_per_1000_usd: float,
    num_searches: int = 1,
    actor_start_fee_usd: float = 0.0,
) -> float:
    """
    What a run would cost in the worst case: the per-result price for the
    maximum number of results, plus a flat start fee for every separate
    actor invocation (we run the LinkedIn actor once per search keyword,
    since it only accepts one keyword/location per call — see
    linkedin_apify.py).
    """
    return round(
        max_results * price_per_1000_usd / 1000 + num_searches * actor_start_fee_usd, 4
    )


def minutes_since_last_run(conn: sqlite3.Connection) -> Optional[float]:
    """How long ago Apify last ran, in minutes, or None if it's never run."""
    last_run_at = db.get_last_apify_run(conn)
    if last_run_at is None:
        return None
    last_dt = datetime.fromisoformat(last_run_at)
    now = datetime.now(timezone.utc)
    return (now - last_dt).total_seconds() / 60


def check_apify_budget(
    conn: sqlite3.Connection, settings: dict, force: bool = False
) -> BudgetDecision:
    """
    Decide whether an Apify/LinkedIn run is allowed right now.

    `force` corresponds to the "Force LinkedIn refresh" button: it skips
    the cooldown check, but the budget check always applies — there's no
    way to force-spend past the cap.
    """
    apify_settings = settings["apify"]
    monthly_budget = apify_settings["monthly_budget_usd"]
    price_per_1000 = apify_settings["price_per_1000_usd"]
    max_results = apify_settings["max_results_per_run"]
    cooldown_minutes = apify_settings["cooldown_minutes"]
    actor_start_fee = apify_settings.get("actor_start_fee_usd", 0.0)
    # One actor run per search keyword (the actor takes a single
    # keyword/location per call) — see linkedin_apify.py.
    num_searches = len(settings["linkedin_search_keywords"])

    spend_so_far = db.get_apify_spend_this_month(conn)
    estimated_cost = estimate_run_cost(max_results, price_per_1000, num_searches, actor_start_fee)

    # The budget check always applies, force or not.
    if spend_so_far + estimated_cost > monthly_budget:
        return BudgetDecision(
            allowed=False,
            reason=(
                f"would exceed monthly budget: ${spend_so_far:.2f} spent + "
                f"~${estimated_cost:.2f} for this run > ${monthly_budget:.2f} cap"
            ),
            spend_so_far_usd=spend_so_far,
            monthly_budget_usd=monthly_budget,
            estimated_run_cost_usd=estimated_cost,
        )

    if not force:
        since_last = minutes_since_last_run(conn)
        if since_last is not None and since_last < cooldown_minutes:
            remaining = cooldown_minutes - since_last
            return BudgetDecision(
                allowed=False,
                reason=f"cooldown active, {remaining:.0f} more minutes until next allowed run",
                spend_so_far_usd=spend_so_far,
                monthly_budget_usd=monthly_budget,
                estimated_run_cost_usd=estimated_cost,
            )

    return BudgetDecision(
        allowed=True,
        reason="ok",
        spend_so_far_usd=spend_so_far,
        monthly_budget_usd=monthly_budget,
        estimated_run_cost_usd=estimated_cost,
    )


def record_run(
    conn: sqlite3.Connection,
    results: int,
    price_per_1000_usd: float,
    num_searches: int = 1,
    actor_start_fee_usd: float = 0.0,
) -> None:
    """Log an Apify run's actual cost so future budget checks account for it."""
    cost = estimate_run_cost(results, price_per_1000_usd, num_searches, actor_start_fee_usd)
    db.log_apify_run(conn, results, cost)


def get_status(conn: sqlite3.Connection, settings: dict) -> dict:
    """
    A snapshot of Apify's current budget/cooldown state, for the frontend's
    "when was LinkedIn last refreshed / $x of $y used" display.
    """
    apify_settings = settings["apify"]
    last_run_at = db.get_last_apify_run(conn)
    since_last = minutes_since_last_run(conn)
    cooldown_minutes = apify_settings["cooldown_minutes"]
    next_allowed_in_minutes = (
        max(0.0, cooldown_minutes - since_last) if since_last is not None else 0.0
    )

    return {
        "last_run_at": last_run_at,
        "next_allowed_in_minutes": round(next_allowed_in_minutes, 1),
        "spend_so_far_usd": round(db.get_apify_spend_this_month(conn), 2),
        "monthly_budget_usd": apify_settings["monthly_budget_usd"],
    }
