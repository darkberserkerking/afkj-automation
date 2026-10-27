"""
afk_bot 统计追踪模块

StatsTracker 封装战斗统计数据（胜场数、耗时、关卡进度等），
并提供推送和查询接口。从 afk_bot.py 提取，去除对 AFKBot 的直接依赖。

设计原则：
- 纯数据容器 + 简单操作，无截图/OCR/点击依赖
- push() 回调 status_callback 推送到 GUI，含关卡推进去重逻辑
- get/set/increment 提供线程安全的字典操作
"""

import threading
from typing import Any, Callable, Dict, List, Optional


class StatsTracker:
    """战斗统计追踪器。

    维护 stats 字典（胜场数、耗时记录、关卡进度等），
    并通过 push() 方法将数据推送到 GUI。

    Attributes:
        stats: 统计字典，键值对形式
        _status_callback: GUI 状态推送回调
        _log_callback: 日志回调
        _last_pushed_stage: 上次推送时的关卡进度（用于去重）
        _lock: 线程锁
    """

    def __init__(
        self,
        status_callback: Optional[Callable[..., None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        """初始化 StatsTracker。

        Args:
            status_callback: GUI 状态推送回调，接收 **kw 参数
            log_callback: 日志回调，接收消息字符串
        """
        self._status_callback: Callable[..., None] = (
            status_callback or (lambda **kw: None)
        )
        self._log_callback: Callable[[str], None] = (
            log_callback or (lambda msg: None)
        )
        self._last_pushed_stage: Optional[int] = None
        self._lock: threading.Lock = threading.Lock()

        self.stats: Dict[str, Any] = {
            'huanling_wins': 0,
            'normal_wins': 0,
            'huanling_times': [],
            'normal_times': [],
            'total_battles': 0,
            'recovery_count': 0,
            'stage_progress': 0,       # 总推进关卡数
            '本轮进度': 0,              # 本轮推进关卡数
            'current_stage': 0,        # 当前关卡（挂机关卡页红底白字）
            'huanling_stage': 0,       # 赛季幻灵关卡
            'normal_stage': 0,         # 赛季挂机关卡
            '_prev_huanling': 0,       # 进入战斗前的赛季幻灵关卡（临时）
            '_prev_normal': 0,         # 进入战斗前的赛季挂机关卡（临时）
        }

    # ── 推送 ────────────────────────────────────────────────────

    def push(self) -> None:
        """推送统计数据到 GUI（含关卡推进去重逻辑）。

        当 stage_progress 变化时输出推进日志，
        每次调用都将完整 stats 推送到 status_callback。
        """
        stage_progress: int = self.stats.get('stage_progress', 0)
        # v1.8.42：「推进: 0关」无信息量且会出现在非推图任务日志里——
        # 仅在有关卡推进（>0）时输出
        if stage_progress and stage_progress != self._last_pushed_stage:
            self._log_callback(f'[数据] 推进: {stage_progress}关')
            self._last_pushed_stage = stage_progress
        # 推送完整 stats 快照
        self._status_callback(stats=dict(self.stats))

    # ── 读取 ────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """线程安全地获取统计值。

        Args:
            key: 统计键名
            default: 键不存在时的默认返回值

        Returns:
            统计值或 default
        """
        with self._lock:
            return self.stats.get(key, default)

    # ── 写入 ────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """线程安全地设置统计值。

        Args:
            key: 统计键名
            value: 新值
        """
        with self._lock:
            self.stats[key] = value

    # ── 递增 ────────────────────────────────────────────────────

    def increment(self, key: str, delta: int = 1) -> int:
        """线程安全地递增统计值。

        Args:
            key: 统计键名
            delta: 递增量（默认 1）

        Returns:
            递增后的值
        """
        with self._lock:
            current: int = self.stats.get(key, 0)
            new_val: int = current + delta
            self.stats[key] = new_val
            return new_val

    # ── 战斗结果记录 ────────────────────────────────────────────

    def record_battle_result(
        self,
        result: str,
        mode: str,
        elapsed: float = 0.0,
    ) -> None:
        """记录一次战斗结果。

        Args:
            result: 战斗结果 — 'victory' 或 'defeat' 或 'done'
            mode: 战斗模式 — 'huanling' 或 'normal'
            elapsed: 战斗耗时（秒）
        """
        with self._lock:
            self.stats['total_battles'] = self.stats.get('total_battles', 0) + 1
            if mode == 'huanling':
                if result in ('victory', 'done'):
                    self.stats['huanling_wins'] = \
                        self.stats.get('huanling_wins', 0) + 1
                if elapsed > 0:
                    times: List[float] = self.stats.get('huanling_times', [])
                    times.append(elapsed)
                    self.stats['huanling_times'] = times
            elif mode == 'normal':
                if result in ('victory', 'done'):
                    self.stats['normal_wins'] = \
                        self.stats.get('normal_wins', 0) + 1
                if elapsed > 0:
                    times: List[float] = self.stats.get('normal_times', [])
                    times.append(elapsed)
                    self.stats['normal_times'] = times
