#!/usr/bin/env python3
"""Estimate spend from cached plans by summing exact token counts.

Token counts are exact (from the API usage field); prices are ESTIMATES —
verify against current OpenAI pricing. Prices are per 1M tokens (input,
output). Update PRICES if the real numbers differ; the token totals stay valid.

Usage: python scripts/cost_report.py [experiments_root]
"""

import json
import sys
from pathlib import Path

# ESTIMATED USD per 1M tokens (input, output). Verify before trusting the $ column.
PRICES = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5.5": (1.25, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def price_for(model: str) -> tuple[float, float]:
    for key, price in PRICES.items():
        if model == key:
            return price
    for key, price in PRICES.items():  # prefix fallback (dated ids)
        if model.startswith(key):
            return price
    return (0.0, 0.0)


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "experiments")
    caches = sorted(root.glob("**/plans/*/*.jsonl"))
    if not caches:
        print(f"no plan caches under {root}")
        return

    grand_in = grand_out = grand_cost = 0.0
    print(f"{'model/variant':<40}{'calls':>7}{'tok_in':>12}{'tok_out':>12}{'est_usd':>10}")
    print("-" * 81)
    for cache in caches:
        model = cache.parent.name
        variant = cache.stem
        calls = tok_in = tok_out = 0
        with cache.open() as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                calls += 1
                planner = rec.get("planner", {})
                tok_in += planner.get("tokens_in", 0)
                tok_out += planner.get("tokens_out", 0)
        pin, pout = price_for(model)
        cost = tok_in / 1e6 * pin + tok_out / 1e6 * pout
        grand_in += tok_in
        grand_out += tok_out
        grand_cost += cost
        print(f"{model + '/' + variant:<40}{calls:>7}{tok_in:>12,}{tok_out:>12,}{cost:>10.3f}")
    print("-" * 81)
    print(f"{'TOTAL':<40}{'':>7}{grand_in:>12,.0f}{grand_out:>12,.0f}{grand_cost:>10.3f}")
    print("\n(prices are estimates; token counts are exact)")


if __name__ == "__main__":
    main()
