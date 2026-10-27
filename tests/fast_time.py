# -*- coding: utf-8 -*-
"""测试态时间加速（v1.9.2 测试流程优化 · 措施 1）。

背景：干跑测试极慢（全量 25~30 分钟），根因是**被测代码里的 sleep 被真实
执行**——测试脚本自身几乎没有 sleep。典型常量：`_TEXT_TIMEOUT=8s`、
`_DOOR_TRANSITION_WAIT=3s`、`GlobalNavigator.poll=1.2s×14`、
`_BATTLE_WAIT=180s`。

方案：**虚拟时钟**——`time.sleep(x)` 不真睡，只把 x 累加进虚拟偏移；
`time.time()` 返回"真实时间 + 虚拟偏移"。于是：
  - 真实耗时 ≈ 0（sleep 全部跳过）
  - 时序语义完全一致（deadline / max_time / 轮询超时都按虚拟时间正确推进）

用法（各测试脚本顶部）：
    from tests import fast_time
    fast_time.install()

关闭加速（发布级跑真实时间）：
    set AFK_TEST_FAST=0

需要与真实时间对齐的片段（罕见）用上下文管理器：
    with fast_time.real_time():
        ...
"""
from __future__ import annotations

import os
import time as _time

_LOCK = object()


class _FastClock:
    """虚拟时钟：sleep 只推进偏移，不真实等待。"""

    def __init__(self) -> None:
        self.offset: float = 0.0
        self._installed: bool = False
        self._orig_sleep = _time.sleep
        self._orig_time = _time.time

    # ── 加速版实现 ──
    def sleep(self, sec: float) -> None:
        try:
            self.offset += max(0.0, float(sec))
        except (TypeError, ValueError):
            pass

    def now(self) -> float:
        return self._orig_time() + self.offset

    # ── 安装 / 卸载 ──
    def install(self) -> None:
        if self._installed:
            return
        # 保存真实实现（供 real_time 与卸载使用）
        self._orig_sleep = _time.sleep
        self._orig_time = _time.time
        _time.sleep = self.sleep          # type: ignore[assignment]
        _time.time = self.now             # type: ignore[assignment]
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        _time.sleep = self._orig_sleep    # type: ignore[assignment]
        _time.time = self._orig_time      # type: ignore[assignment]
        self._installed = False

    def reset(self) -> None:
        self.offset = 0.0


CLOCK = _FastClock()


class real_time:
    """上下文：临时恢复真实 sleep/time（给必须真等的片段）。"""

    def __enter__(self) -> "real_time":
        CLOCK.uninstall()
        return self

    def __exit__(self, *exc) -> bool:
        CLOCK.install()
        return False


def install(force: bool = False) -> bool:
    """安装加速时钟。返回是否真的安装了。

    Args:
        force: True 时忽略环境变量强制加速（用于明确要快的场景）
    """
    if not force and os.environ.get('AFK_TEST_FAST', '1') == '0':
        return False
    CLOCK.install()
    return True


def uninstall() -> None:
    CLOCK.uninstall()


def elapsed() -> float:
    """当前虚拟时钟推进量（秒），便于测试自检。"""
    return CLOCK.offset
