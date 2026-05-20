#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Walk-forward backtest of the seasonal-adjusted EWMA prediction model.

Replicates the exact algorithm from index.html renderPredictCard():
  getDayType() → day-type averages → multipliers → deseasonalize → EWMA → re-seasonalize

Usage:
  python backtest_prediction.py              # run backtest, print report
  python backtest_prediction.py --json       # output JSON for CI monitoring
"""
import os
import sys
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

import requests

# ---- Config ----
MOVIE_ID = 1516982
HOLIDAY_END = "2026-05-05"
ALPHA = 0.3
MIN_TRAIN_DAYS = 3

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ebmncqnzammtplpwlveb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd")


def get_day_type(date_str):
    """Replicate frontend getDayType() exactly."""
    if not date_str:
        return "weekday"
    if "2026-05-01" <= date_str <= HOLIDAY_END:
        return "holiday"
    dow = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    return "weekend" if dow >= 5 else "weekday"


def fetch_daily_data():
    """Fetch daily box office data for the target movie from Supabase."""
    url = (f"{SUPABASE_URL}/rest/v1/mayday_daily_stats"
           f"?movie_id=eq.{MOVIE_ID}&order=stat_date.asc&limit=200")
    resp = requests.get(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    resp.raise_for_status()
    return resp.json()


def avg(lst):
    """Simple mean, replicates frontend avg()."""
    return sum(lst) / len(lst) if lst else 0


def predict_one_step(sorted_data):
    """
    Given sorted daily records [0..i], predict the NEXT day (i+1).
    Replicates renderPredictCard() logic step by step.

    Returns: (predicted_value, multipliers_dict) or (None, None)
    """
    n = len(sorted_data)
    if n < 2:
        return None, None

    # 1. Classify days & collect by type
    by_type = defaultdict(list)
    for r in sorted_data:
        t = get_day_type(r["stat_date"])
        by_type[t].append(r["daily_box"] or 0)

    avgs = {t: avg(vals) for t, vals in by_type.items()}

    # 2. Baseline & multipliers (with defaults matching frontend)
    baseline = avgs.get("weekday") or avgs.get("weekend") or avgs.get("holiday") or 1
    multipliers = {
        "holiday": (avgs["holiday"] / baseline) if avgs.get("holiday", 0) > 0 else 2.0,
        "weekend": (avgs["weekend"] / baseline) if avgs.get("weekend", 0) > 0 else 1.3,
        "weekday": 1.0,
    }

    # 3. Deseasonalize
    adjusted = [
        (r["daily_box"] or 0) / multipliers.get(get_day_type(r["stat_date"]), 1)
        for r in sorted_data
    ]

    # 4. EWMA on adjusted
    ewma = [adjusted[0]]
    for i in range(1, len(adjusted)):
        ewma.append(ALPHA * adjusted[i] + (1 - ALPHA) * ewma[i - 1])

    # 5. Predict tomorrow: S_{t+1} = alpha*X_t + (1-alpha)*S_t  (deseasonalized)
    #    Then re-seasonalize with tomorrow's type coefficient
    last_adj = adjusted[-1]
    last_ewma = ewma[-1]
    tomorrow_adj_pred = ALPHA * last_adj + (1 - ALPHA) * last_ewma

    last_date = sorted_data[-1]["stat_date"]
    next_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_type = get_day_type(next_date)
    prediction = tomorrow_adj_pred * multipliers.get(tomorrow_type, 1)

    return prediction, multipliers


def run_backtest(data):
    """Walk-forward validation: at each step i (>= MIN_TRAIN_DAYS), predict for day i+1."""
    sorted_data = sorted(data, key=lambda r: r["stat_date"])
    results = []

    for i in range(MIN_TRAIN_DAYS - 1, len(sorted_data) - 1):
        train = sorted_data[: i + 1]
        actual_rec = sorted_data[i + 1]
        actual_val = actual_rec["daily_box"] or 0
        if actual_val == 0:
            continue

        pred, mults = predict_one_step(train)
        if pred is None:
            continue

        ape = abs(actual_val - pred) / actual_val
        results.append({
            "predict_date": actual_rec["stat_date"],
            "train_days": len(train),
            "predicted": round(pred, 2),
            "actual": actual_val,
            "absolute_error": round(abs(actual_val - pred), 2),
            "ape": round(ape, 4),
            "multipliers": {k: round(v, 3) for k, v in (mults or {}).items()},
        })

    return results


def main():
    print("Fetching data from Supabase...")
    try:
        data = fetch_daily_data()
    except Exception as e:
        print(f"ERROR: Failed to fetch data: {e}")
        sys.exit(1)

    if not data:
        print("ERROR: No data returned")
        sys.exit(1)

    print(f"Fetched {len(data)} daily records for movie {MOVIE_ID}")

    results = run_backtest(data)
    if not results:
        print(f"ERROR: insufficient data for backtest (need at least {MIN_TRAIN_DAYS + 1} days)")
        sys.exit(1)

    apes = [r["ape"] for r in results]
    mape = sum(apes) / len(apes)
    rmspe = math.sqrt(sum(a * a for a in apes) / len(apes))
    sorted_apes = sorted(apes)
    median_ape = sorted_apes[len(sorted_apes) // 2]
    within_10pct = sum(1 for a in apes if a < 0.10)
    within_25pct = sum(1 for a in apes if a < 0.25)

    if "--json" in sys.argv:
        print(json.dumps({
            "mape": round(mape, 4),
            "rmspe": round(rmspe, 4),
            "median_ape": round(median_ape, 4),
            "n_predictions": len(results),
            "within_10pct": within_10pct,
            "within_25pct": within_25pct,
            "total_days": len(data),
            "details": results,
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n{'=' * 70}")
    print(f"  回测结果 — 给阿嬷的情书 (Seasonal EWMA, alpha={ALPHA})")
    print(f"{'=' * 70}")
    print(f"  训练方式: walk-forward (从第 {MIN_TRAIN_DAYS} 天起向前滚动)")
    print(f"  预测点数: {len(results)}")
    print(f"  MAPE:     {mape * 100:.2f}%")
    print(f"  RMSPE:    {rmspe * 100:.2f}%")
    print(f"  Median APE: {median_ape * 100:.2f}%")
    print(f"  < 10% 误差: {within_10pct}/{len(results)} ({within_10pct/len(results)*100:.0f}%)")
    print(f"  < 25% 误差: {within_25pct}/{len(results)} ({within_25pct/len(results)*100:.0f}%)")
    print(f"  最低误差: {min(apes) * 100:.2f}%")
    print(f"  最高误差: {max(apes) * 100:.2f}%")
    print(f"{'=' * 70}")
    print(f"\n{'日期':>12} {'训练天数':>7} {'预测(万)':>10} {'实际(万)':>10} {'APE':>7}")
    print("-" * 55)
    for r in results:
        print(f"{r['predict_date']:>12}  {r['train_days']:>4}d  "
              f"{r['predicted']:>9.1f}  {r['actual']:>9.1f}  "
              f"{r['ape']*100:>6.1f}%")

    if results:
        last = results[-1]
        m = last["multipliers"]
        print(f"\n  最终季节系数: holiday={m['holiday']}x  "
              f"weekend={m['weekend']}x  weekday=1.0x")


if __name__ == "__main__":
    main()
