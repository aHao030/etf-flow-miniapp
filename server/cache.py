# -*- coding: utf-8 -*-
"""进程内缓存（M2 MVP，零依赖）。

契约 §4 的 Redis key 仅作行为约定；本实现为进程内 TTL 字典，
接口签名与 Redis 语义对齐（get(builder) / invalidate），将来换 Redis
只需替换本类实现，调用方不变。
"""
from __future__ import annotations

import threading
import time

import config


class Cache:
    """线程安全的进程内 TTL 缓存。"""

    def __init__(self, ttl: int = config.CACHE_TTL_SECONDS):
        self._ttl = ttl
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, builder, ttl: int | None = None):
        """命中返回缓存值；未命中调用 builder() 构建、写入并返回。"""
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item and item[0] > now:
                self.hits += 1
                return item[1]
        value = builder()
        if value is not None:
            with self._lock:
                self._data[key] = (now + (ttl or self._ttl), value)
                self.misses += 1
        return value

    def set(self, key: str, value, ttl: int | None = None) -> None:
        with self._lock:
            self._data[key] = (time.time() + (ttl or self._ttl), value)

    def invalidate(self) -> None:
        """每日任务成功后调用：整批失效（latest_date 原子切换语义）。"""
        with self._lock:
            self._data.clear()


cache = Cache()
