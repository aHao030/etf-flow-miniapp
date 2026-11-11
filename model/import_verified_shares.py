"""Validate manually verified historical share observations into model CSV format.

No database or existing input is modified. The caller must merge after review.
"""
import argparse
import csv
import math
import re
from datetime import date

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input")
    p.add_argument("output")
    args = p.parse_args()
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"stat_date", "market", "fund_code", "fund_name", "shares", "source", "verified_by"}
        if not required.issubset(reader.fieldnames or []):
            p.error("缺少必填列: " + ", ".join(sorted(required)))
        rows = list(reader)
    seen = set()
    for n, row in enumerate(rows, 2):
        try:
            date.fromisoformat(row["stat_date"])
            value = float(row["shares"])
            assert math.isfinite(value) and value >= 0
            assert row["market"] in ("SH", "SZ")
            assert re.fullmatch(r"\d{6}", row["fund_code"])
            assert row["fund_name"].strip() and row["source"].strip() and row["verified_by"].strip()
            key = (row["stat_date"], row["market"], row["fund_code"])
            assert key not in seen
            seen.add(key)
        except (ValueError, AssertionError) as e:
            p.error(f"第 {n} 行无效: {e}")
        row["shares_wan"] = str(value / 10000)
    # Preserve provenance as extra columns for review; model ignores them.
    fields = ["stat_date", "market", "fund_code", "fund_name", "shares_wan", "shares", "source", "verified_by"]
    with open(args.output, "x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"已生成 {len(rows)} 行审核输入；未合并、未激活或写入数据库。")

if __name__ == "__main__":
    main()
