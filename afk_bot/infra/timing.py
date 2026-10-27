"""分段可中断休眠（v2.1.27 · P1-3）。

背景：引擎层散布 60+ 处裸 `time.sleep`，**绕过停止信号** —— 用户点「停止」
后仍要等完当前等待（实测最坏数百毫秒~数秒）。任务层此前已改为分段可中断
（`BaseTask._sleep`），引擎层未跟进，导致「停止要即时」的要求只覆盖了一半。

本模块提供**唯一实现**，避免各文件再各写一份（`battle.py` 原先已有一份，
现改为委托到这里）。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

#: 分段步长（秒）——停止信号的最坏响应延迟 ≈ 本值
SLEEP_STEP: float = 0.1


def sleep_interruptible(
    sec: float,
    should_stop: Optional[Callable[[], bool]] = None,
    step: float = SLEEP_STEP,
) -> bool:
    """分段休眠，每 `step` 秒检查一次停止信号。

    未传 `should_stop` 时退化为普通休眠（供无停止上下文的场景使用）。

    Args:
        sec: 需要等待的总秒数
        should_stop: 停止判定回调；返回 True 表示需要停止
        step: 分段步长

    Returns:
        bool: True = 睡满；False = 中途检测到停止（调用方应尽快返回）
    """
    if sec <= 0:
        return True
    if should_stop is None:
        time.sleep(sec)
        return True
    elapsed: float = 0.0
    while elapsed < sec:
        if should_stop():
            return False
        chunk: float = min(step, sec - elapsed)
        time.sleep(chunk)
        elapsed += chunk
    return True
