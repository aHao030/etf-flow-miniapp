"""Prepare an auditable ETF candidate list; never changes the production universe."""
import argparse
import csv
import re

EXCLUDE = ("债", "货币", "QDII", "联接", "黄金", "商品", "原油", "港股", "纳指", "标普", "日经")

def classify(row):
    code = row.get("fund_code", "").strip()
    name = row.get("fund_name", "").strip()
    description = " ".join((name, row.get("fund_type", ""))).upper()
    if any(term.upper() in description for term in EXCLUDE):
        return "excluded", "名称或类型提示非境内股票 ETF"
    if not re.fullmatch(r"\d{6}", code) or "ETF" not in name.upper():
        return "pending_review", "代码或 ETF 身份需核实"
    # Name alone cannot establish underlying exposure, legal form or eligibility.
    return "pending_review", "需核实投资范围、上市状态和数据授权"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="本地 CSV，需包含 fund_code,fund_name，可含 fund_type")
    parser.add_argument("output", help="候选审核 CSV；不会更改生产基金池")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"fund_code", "fund_name"}.issubset(reader.fieldnames or []):
            parser.error("输入缺少 fund_code 或 fund_name")
        rows = list(reader)
        fields = list(reader.fieldnames)
    for field in ("review_status", "review_reason"):
        if field not in fields:
            fields.append(field)
    for row in rows:
        row["review_status"], row["review_reason"] = classify(row)
    with open(args.output, "x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"候选 {len(rows)} 条；输出需人工审核，不自动激活。")

if __name__ == "__main__":
    main()
