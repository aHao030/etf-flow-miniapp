# -*- coding: utf-8 -*-
"""申万一级行业静态映射表（M2 自采，task-3 裁决 ②：选最省事的免费行业字段来源）。

来源与口径（写死于此，供「关于/口径」引用）：
  - 行业列表：申万宏源研究官网（akshare `sw_index_first_info`，31 个一级行业，
    代码 801010.SI ~ 8019xx.SI）
  - 行业成分：申万指数成分股（akshare `index_component_sw`，乐咕乐股聚合的申万口径，
    含最新权重与计入日期）
  - 口径：申万一级（2021 版行业分类标准），映射为静态表，代码以 6 位行业代码存
    （去 .SI 后缀）
  - 采集时间即 version；覆盖池外 / 未分类个股 industry_code=''、industry_name='未分类'

产物：server/data/industry_sw_l1.csv
  industry_code,industry_name,stock_code,stock_name,weight,join_date,version,built_at
"""
from __future__ import annotations

import csv
import os
import sys
import time

import config

COLUMNS = ["industry_code", "industry_name", "stock_code", "stock_name",
           "weight", "join_date", "version", "built_at"]

_STOCK_CODE_RE = __import__("re").compile(r"^\d{6}$")


def build(output: str = config.INDUSTRY_CSV) -> dict:
    """拉取申万一级行业与成分，写静态 CSV。返回 {version, n_rows, n_industries}。"""
    import akshare as ak  # 延迟导入（仅构建时用）

    first = ak.sw_index_first_info()
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    version = f"sw_l1_{time.strftime('%Y%m%d')}"
    rows: list[dict] = []
    n_ind = 0
    for _, r in first.iterrows():
        code_full = str(r.get("行业代码", "")).strip()
        if not code_full:
            continue
        code = code_full.replace(".SI", "").strip()
        name = str(r.get("行业名称", "")).strip()
        n_ind += 1
        try:
            cons = ak.index_component_sw(symbol=code)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 行业 {code} {name} 成分获取失败: {e}", file=sys.stderr)
            continue
        for _, c in cons.iterrows():
            sc = str(c.get("证券代码", "")).strip()
            if not _STOCK_CODE_RE.match(sc):
                continue
            rows.append({
                "industry_code": code, "industry_name": name,
                "stock_code": sc, "stock_name": str(c.get("证券名称", "")).strip(),
                "weight": c.get("最新权重"), "join_date": c.get("计入日期"),
                "version": version, "built_at": built_at,
            })
        time.sleep(0.2)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] 申万一级行业映射：{len(rows)} 行（{n_ind} 个行业）→ {output}")
    return {"version": version, "n_rows": len(rows), "n_industries": n_ind}


def load(path: str = config.INDUSTRY_CSV) -> dict[str, dict]:
    """读静态表 → {stock_code: {industry_code, industry_name}}。"""
    if not os.path.exists(path):
        return {}
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sc = r["stock_code"].strip()
            out[sc] = {"industry_code": r["industry_code"].strip(),
                       "industry_name": r["industry_name"].strip()}
    return out


def version_of(path: str = config.INDUSTRY_CSV) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            return r.get("version") or None
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
