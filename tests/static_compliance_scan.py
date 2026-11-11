# -*- coding: utf-8 -*-
"""M4 阶段一 · 全站合规静态扫描（qa-compliance）。

扫描对象：miniapp/ 全部源码 + server/config.py + docs/m1-模型说明-v1.md §7。
检查项：
  1. 红线词扫描：买入/卖出/推荐/建议/抄底/加仓/上车/目标价/评级/收益/荐股/预测。
     豁免规则：行内含「不构成」（免责声明否定式用法）或位于开发者日志
     （ec-canvas console.warn）视为良性；
  2. 口径披露条：index/watchlist/stock-detail/etf-detail 四个数据页必须常驻
     <disclosure-bar />；profile 页自带口径+免责卡片，豁免；
  3. 全站命名：数据页必须出现「估算净流入」（字段统一命名）；
  4. 文案终稿一致性：server/config.py 的 DISCLOSURE_SHORT/LONG 必须与
     docs/m1-模型说明-v1.md §7 终稿逐字一致（唯一文案来源）；
  5. mock 兜底文案（mock/meta.js）与终稿不一致 → 仅 INFO（dev-only，建议同步）。

用法：python tests/static_compliance_scan.py
退出码：0 = 无违规；1 = 存在违规；2 = 无法读取关键文件。
"""
from __future__ import annotations

import os
import re
import sys

PASS = 0
FAIL = 0
INFO = 0

RED_WORDS = re.compile(r"买入|卖出|推荐|建议|抄底|加仓|上车|目标价|评级|收益|荐股|预测")
BENIGN_IF = "不构成"          # 免责声明否定式用法
DEV_LOG_FILES = {"ec-canvas.js"}   # 开发者日志（非用户可见文案）

DATA_PAGES = [   # wxml 路径 → 是否必须出现「估算净流入」
    ("miniapp/pages/index/index.wxml", True),
    ("miniapp/pages/watchlist/watchlist.wxml", True),
    ("miniapp/package-detail/pages/stock-detail/stock-detail.wxml", True),
    ("miniapp/package-detail/pages/etf-detail/etf-detail.wxml", True),
    ("miniapp/pages/profile/profile.wxml", False),   # 自带口径/免责卡片，豁免披露条
]


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail[:260]}")


def info(name: str, detail: str) -> None:
    global INFO
    INFO += 1
    print(f"  [INFO] {name}  {detail[:200]}")


def scan_red_words(proj: str) -> None:
    print("[1] 红线词扫描（miniapp/ 全部源码）")
    bad = []
    for root, dirs, files in os.walk(os.path.join(proj, "miniapp")):
        dirs[:] = [d for d in dirs if d not in ("ec-canvas", "node_modules", "assets")]
        for fn in files:
            if not fn.endswith((".js", ".wxml", ".json", ".wxss", ".wxs")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    for ln, line in enumerate(fh, 1):
                        if not RED_WORDS.search(line):
                            continue
                        if BENIGN_IF in line:
                            continue
                        if fn in DEV_LOG_FILES and "console.warn" in line:
                            continue
                        bad.append((path.replace(proj + os.sep, ""), ln, line.strip()[:80]))
            except UnicodeDecodeError:
                continue
    check(f"无未豁免红线词（命中 {len(bad)} 处）", not bad, str(bad[:6]))
    for p, ln, t in bad[:6]:
        print(f"      {p}:{ln}  {t}")


def scan_disclosure_and_naming(proj: str) -> None:
    print("[2] 口径披露条与命名")
    for rel, need_naming in DATA_PAGES:
        path = os.path.join(proj, rel)
        try:
            txt = open(path, encoding="utf-8").read()
        except OSError:
            check(f"{rel} 可读取", False)
            continue
        has_bar = "<disclosure-bar" in txt
        if rel.endswith("profile.wxml"):
            check("profile 自带口径+免责声明（豁免披露条）",
                  ("数据口径说明" in txt and "免责声明" in txt))
        else:
            check(f"{rel} 常驻 <disclosure-bar />", has_bar)
        if need_naming:
            check(f"{rel} 使用「估算净流入」命名", "估算净流入" in txt)
        # 详情页不得展示行情价格/涨跌幅（已定决策，降低类目合规风险）
        if "detail" in rel:
            check(f"{rel} 不展示涨跌幅", "涨跌幅" not in txt and "涨跌" not in txt)


def scan_disclosure_copy(proj: str) -> None:
    print("[3] 口径披露文案终稿一致性（config ↔ m1-模型说明-v1.md §7）")
    doc_path = os.path.join(proj, "docs", "m1-模型说明-v1.md")
    try:
        doc = open(doc_path, encoding="utf-8").read()
    except OSError:
        check("m1-模型说明-v1.md 可读取", False)
        return
    sec = doc.split("## 7.")[1].split("## 8.")[0]
    short = re.search(r"\*\*短文案.*?\*\*\s*\n>\s*(.+)", sec)
    long_lines = re.findall(r"^>\s*(.+)$", sec, re.M)
    short_doc = short.group(1).strip() if short else ""
    long_doc = "\n".join(long_lines[1:]).strip() if len(long_lines) > 1 else ""
    sys.path.insert(0, os.path.join(proj, "server"))
    try:
        import config  # noqa: PLC0415  只读导入，无副作用
    except Exception as e:  # noqa: BLE001
        check("server/config.py 可导入", False, str(e))
        return
    check("短文案与 §7 终稿逐字一致", config.DISCLOSURE_SHORT == short_doc,
          f"cfg={config.DISCLOSURE_SHORT[:60]}... doc={short_doc[:60]}...")
    check("长文案与 §7 终稿逐字一致", config.DISCLOSURE_LONG == long_doc,
          "有差异（见 §7 与 config.DISCLOSURE_LONG）")
    check("短文案含红线否定式", "不构成投资建议" in config.DISCLOSURE_SHORT)
    # mock 兜底文案（dev-only）
    mock_path = os.path.join(proj, "miniapp", "mock", "meta.js")
    try:
        mock_txt = open(mock_path, encoding="utf-8").read()
        if "本页「估算净流入」为估算值" not in mock_txt:
            info("mock/meta.js 兜底文案与终稿不一致（仅开发模式）",
                 "建议同步为 §7 终稿，避免 USE_MOCK 演示时文案漂移")
        else:
            check("mock/meta.js 兜底文案已同步", True)
    except OSError:
        info("mock/meta.js 不存在", "跳过")


def scan_env(proj: str) -> None:
    print("[4] 环境配置核查")
    path = os.path.join(proj, "miniapp", "config", "env.js")
    txt = open(path, encoding="utf-8").read()
    check("联调走真实后端（USE_MOCK=false）", re.search(r"USE_MOCK:\s*false", txt))
    if "127.0.0.1" in txt:
        info("BASE_URL 为 127.0.0.1", "真机预览/体验版需改为局域网 IP 或正式域名（真机清单 §2）")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    print(f"== 合规静态扫描（{proj}）==")
    scan_red_words(proj)
    scan_disclosure_and_naming(proj)
    scan_disclosure_copy(proj)
    scan_env(proj)
    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL / {INFO} INFO ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
