# -*- coding: utf-8 -*-
"""V1 测算模型驱动脚本（task-2, quant-analyst）。

用法: python compute_flow.py
输入: data/samples/*.csv + model/cache/csindex_weights/
输出: model/outputs/{flow_daily,flow_derived,top_contributors,flow_by_etf,
                  quality_marks,weights_used,weight_decisions}.csv + summary.txt
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v1_flow as V  # noqa: E402


def main() -> None:
    universe = V.load_universe()
    shares, nav, hold, floatcap = V.load_inputs()
    weights, decisions = V.build_weights(universe, hold)
    contrib, marks = V.compute_flow(universe, weights, shares, nav)
    daily, derived, top5 = V.aggregate(contrib, floatcap, V.trading_dates(shares, nav))
    paths = V.write_outputs(daily, derived, top5, contrib, marks, weights, decisions)

    lines = []
    n_active = int((universe["status"] == "active").sum())
    lines.append(f"active_funds={n_active} (snapshot_only={len(universe) - n_active})")
    lines.append(f"flow_days={daily['date'].nunique() if not daily.empty else 0}")
    lines.append(f"stocks={daily['stock_code'].nunique() if not daily.empty else 0}")
    lines.append(f"contrib_rows={len(contrib)}")
    if not marks.empty:
        fc = marks["flags"].value_counts(dropna=False).to_dict()
        lines.append(f"quality_flag_counts={ {k: int(v) for k, v in fc.items()} }")
    lines.append("")
    lines.append("== weight decisions ==")
    lines.append(decisions[["fund_code", "policy", "weight_source", "weight_quarter",
                            "coverage_pct", "n_stocks"]].to_string(index=False))
    if not daily.empty:
        last = str(daily["date"].max().date())
        last_day = daily[daily["date"] == daily["date"].max()].sort_values(
            "flow_yuan", ascending=False)
        lines.append("")
        lines.append(f"== top10 inflow by stock on {last} ==")
        lines.append(last_day.head(10)[["stock_code", "stock_name", "flow_yuan", "n_etf"]]
                     .to_string(index=False))
        lines.append("")
        lines.append(f"== top10 outflow by stock on {last} ==")
        lines.append(last_day.tail(10)[["stock_code", "stock_name", "flow_yuan", "n_etf"]]
                     .to_string(index=False))
    summary = "\n".join(lines)
    with open(os.path.join(V.OUT_DIR, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(summary)
    print(summary)
    print("\n[OK] outputs written to:", os.path.abspath(V.OUT_DIR))
    for k in sorted(paths):
        print("  -", os.path.basename(paths[k]))


if __name__ == "__main__":
    main()
