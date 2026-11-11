# -*- coding: utf-8 -*-
"""A6 补验：中证指数官网成分权重免费可得性探测（task-2, quant-analyst）。

结论（2026-10-30 实测）：akshare index_stock_cons_weight_csindex 直连中证指数官网
（csindex.com.cn），免费、无 token，返回全成分权重（如 000300 全 300 只，权重合计 ~100%）。
→ V1 宽基 ETF 权重可用指数官网成分权重补全季报前 100 缺口（回退链第 4 级激活）。

用法: python check_index_weights.py [index_code ...]   （默认探测 6 个宽基指数）
输出: 打印 shape/权重合计；CSV 落盘 ../cache/csindex_weights/{code}.csv
"""
import os
import sys

import pandas as pd
import akshare as ak

DEFAULT_CODES = ["000016", "000300", "000905", "000852", "000688", "000510"]
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "csindex_weights")

COLS = ["date", "index_code", "index_name", "index_name_en", "stock_code",
        "stock_name", "stock_name_en", "exchange", "exchange_en", "weight_pct"]


def fetch(code: str) -> pd.DataFrame:
    df = ak.index_stock_cons_weight_csindex(symbol=code)
    df.columns = COLS[: len(df.columns)]
    return df


def main() -> None:
    codes = sys.argv[1:] or DEFAULT_CODES
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for code in codes:
        try:
            df = fetch(code)
            w = pd.to_numeric(df["weight_pct"], errors="coerce")
            df.to_csv(os.path.join(OUT_DIR, f"{code}.csv"), index=False, encoding="utf-8-sig")
            print(f"[OK] {code} date={df['date'].iloc[0]} n={len(df)} wsum={w.sum():.2f}%")
            rows.append({"index_code": code, "date": df["date"].iloc[0],
                         "n_const": len(df), "weight_sum_pct": round(float(w.sum()), 2)})
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {code}: {type(exc).__name__}: {exc}")
            rows.append({"index_code": code, "date": "", "n_const": 0, "weight_sum_pct": None})
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "_summary.csv"),
                              index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
