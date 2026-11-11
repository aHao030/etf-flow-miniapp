# -*- coding: utf-8 -*-
"""M2 服务入口：APScheduler 定时任务 + FastAPI（本地部署，体验版期）。

用法:
  python main.py                # 启动调度器 + HTTP 服务（默认 127.0.0.1:8000）
  python main.py --no-scheduler # 仅 HTTP（自测用）
  python main.py --port 8001
"""
from __future__ import annotations

import argparse
import sys

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import db
import pipeline

TZ = config.TZ
JOBS = [
    # 盘后尝试；数据尚未披露时晚间重试。
    (pipeline.job_pipeline_retry, "15:30", "pipeline_daily"),
    (pipeline.job_pipeline_retry, "19:30", "pipeline_evening_retry"),
    (pipeline.job_pipeline_retry, "22:30", "pipeline_retry"),
    (pipeline.job_pipeline_morning, "8:10", "pipeline_morning"),
    # 深市份额：20:30 落库 / 8:00 核对补拉（契约 §4-1）
    (pipeline.job_szse_evening, "20:30", "szse_evening"),
    (pipeline.job_szse_morning, "8:00", "szse_morning"),
]


def _cron(hhmm: str) -> CronTrigger:
    h, m = hhmm.split(":")
    return CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=TZ)


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TZ)
    for fn, hhmm, jid in JOBS:
        sched.add_job(fn, _cron(hhmm), id=jid, misfire_grace_time=3600,
                      coalesce=True, max_instances=1)
    sched.start()
    return sched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-scheduler", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    db.init()
    sched = None
    if not args.no_scheduler:
        sched = start_scheduler()
        print("[scheduler] 已启动（工作日 15:30/19:30/22:30/8:10 + 深市 20:30/8:00）")
    try:
        uvicorn.run("app:app", host=args.host, port=args.port, log_level="info")
    finally:
        if sched:
            sched.shutdown(wait=False)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
