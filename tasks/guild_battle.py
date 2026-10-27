"""结社战线（工会玩法）自动挑战。

【入口】玩法目录 → 「工会玩法」→「结社战线」
【流程】（用户 2026-10-27 指定）
  1. 扫荡：参考女神试炼的写法 —— 点「扫荡」（**全等匹配**，避免命中
     「扫荡掉落」等标签）；无次数/无可扫荡 → 跳过，直接进挑战循环
  2. 挑战循环：
     挑战 →（备战）→ 通关阵容 →（阵容弹窗）→ 一键采用 → 回备战 → 按 F
     → 战斗中 →（有「跳过」就点）→ 结算 → 再点「挑战」→ 循环…
  3. 失败处理：**原阵容重试一次**；再次失败则计数后继续下一轮
  4. 停止条件：没有挑战次数 / 轮次上限 / 时间上限 / 用户手动停止

【限制】（用户 2026-10-27 要求"挑战次数/时间限制"）
  - 本任务选项 max_battles（0=不限）、max_minutes（0=不限）
  - 同时兼容 BaseTask 的 max_turns / max_time（调度器全局设置）

【判据】全部走 **OCR 文本**（不写死坐标）；找不到关键按钮时打印候选，
        便于真机按日志微调（本文件不依赖任何新模板）。
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from tasks.base import BaseTask
from tasks.labyrinth import (      # noqa: E402  复用"跳过"按钮模板
    _SKIP_BTN_SCALE_RANGE, _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD)
from task_registry import TaskMeta, TaskRegistry

task_id = "guild_battle"
label = "结社战线"
description = "扫荡 + 循环挑战（通关阵容/一键采用/F）；失败原阵容重试一次"

# ── 判据 ────────────────────────────────────────────────────
_STALL_MAX: int = 12          # 未知画面连续帧兜底
_MAIN_WORDS: Tuple[str, ...] = ('结社战线',)          # 主界面标题
_BATTLE_WORDS: Tuple[str, ...] = ('挑战',)            # 主界面大按钮
_SWEEP_WORD: str = '扫荡'                             # **全等**匹配
_READY_WORDS: Tuple[str, ...] = ('通关阵容', '编队', '一键采用')
_RESULT_WORDS: Tuple[str, ...] = ('战斗胜利', '战斗失败')


class GuildBattleTask(BaseTask):
    """结社战线自动挑战。"""

    task_id = task_id
    label = label

    options_schema = {
        'max_battles': {
            'type': 'int',
            'label': '挑战次数上限',
            'default': 0,
            'desc': '0 = 不限（打到没次数或手动停止）',
            'note': '统计"实际开打的局数"',
        },
        'max_minutes': {
            'type': 'int',
            'label': '时间上限(分钟)',
            'default': 0,
            'desc': '0 = 不限；到点自动收手',
        },
    }

    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self.battles: int = 0          # 实际开打局数
        self.wins: int = 0
        self.losses: int = 0
        self.retried: bool = False     # 上一次失败是否已重试过（原阵容重试一次）
        self._t0: float = time.time()

    # ── 基类契约 ────────────────────────────────────────────

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _stop_result(self) -> str:
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        texts = self._texts(items)
        if any(w in t for w in _MAIN_WORDS for t in texts):
            self.log("[信息] 启动即在结社战线界面，直接续跑")
            return 'ready'
        if any(w in t for w in _READY_WORDS for t in texts):
            self.log("[信息] 启动即在备战界面，直接续跑")
            return 'ready'
        return 'clear'

    def run(self) -> str:
        self.log("启动（导航 + 扫荡 + 循环挑战）")
        if self.startup_probe() != 'ready':
            if not self._navigate_in():
                self.log_nav_failed("结社战线")
                return 'failed'
        if self.check_stop():
            return self._stop_result()
        # ① 扫荡（一次；无次数则跳过）
        self._sweep()
        if self.check_stop():
            return self._stop_result()
        # ② 挑战循环
        return self._play_loop()

    def summary(self) -> str:
        return (f"结社战线：开打 {self.battles} 局"
                f"（胜 {self.wins} / 负 {self.losses}）")

    # ── 工具 ────────────────────────────────────────────────

    def _find_exact(self, items: List[Tuple], word: str,
                    min_conf: float = 0.6
                    ) -> Optional[Tuple[int, int]]:
        """**全等**匹配（避免「扫荡掉落」这类标签被当成按钮）。"""
        for it in items:
            if (len(it) >= 4 and it[3] >= min_conf
                    and str(it[2]).strip() == word):
                return (it[1], it[0])
        return None

    def _find_any(self, items: List[Tuple], words: Tuple[str, ...],
                  min_conf: float = 0.6
                  ) -> Optional[Tuple[int, int]]:
        for w in words:
            pos = self._find_exact(items, w, min_conf)
            if pos:
                return pos
        for it in items:                       # 再退一步：子串
            if (len(it) >= 4 and it[3] >= min_conf
                    and any(w in str(it[2]) for w in words)):
                return (it[1], it[0])
        return None

    def _cands(self, items: List[Tuple]) -> str:
        """诊断：打印范围内候选项（找不到按钮时附带证据）。"""
        out = [f'{it[2]}({int(it[1])},{int(it[0])},{it[3]:.2f})'
               for it in items if len(it) >= 4 and it[3] >= 0.5]
        return '; '.join(out[:18]) if out else '无'

    def _over_limit(self) -> Optional[str]:
        """到达任一上限则返回原因（None = 未到）。"""
        mb = int(self.options.get('max_battles') or 0)
        if mb > 0 and self.battles >= mb:
            return f"已达挑战次数上限 {mb}"
        mm = int(self.options.get('max_minutes') or 0)
        if mm > 0 and (time.time() - self._t0) >= mm * 60:
            return f"已达时间上限 {mm} 分钟"
        if self.max_turns is not None and self.turns >= self.max_turns:
            return f"已达轮次上限 {self.max_turns}"
        if self.max_time is not None and self.elapsed >= self.max_time:
            return f"已达时间上限 {self.max_time}s"
        return None

    # ── 导航 ────────────────────────────────────────────────

    def _click_card(self, word: str, tag: str) -> bool:
        """在卡片页里滚动查找并点击（玩法目录/工会玩法通用）。"""
        prev: Optional[List[str]] = None
        for _ in range(12):
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            texts = self._texts(items)
            pos = self._find_any(items, (word,), min_conf=0.85)
            if pos:
                self.bot.click_at(pos[0], pos[1], tag)
                self._sleep(2.5)
                return True
            if prev is not None and texts == prev:
                self.log(f"[警告] 已滚到底仍未找到「{word}」｜候选={self._cands(items)}")
                return False
            prev = texts
            self.bot.scroll_wheel(-30, rx=0.61, ry=0.36, label=f'{tag}_向下')
            self._sleep(0.3)
        self.log(f"[警告] 滚动次数用尽仍未找到「{word}」")
        return False

    def _navigate_in(self) -> bool:
        """玩法目录 → 工会玩法 → 结社战线。"""
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(self.bot, logger=self.log,
                              stop_check=self.check_stop)
        if not nav.navigate_to('playbook'):
            self._nav_last_screen = getattr(nav, 'last_screen', '')
            return False
        if not self._click_card('工会玩法', '玩法目录_工会玩法'):
            return False
        if not self._click_card('结社战线', '工会玩法_结社战线'):
            return False
        self.log("[成功] 已进入结社战线界面")
        return True

    # ── 扫荡（参考女神试炼：全等匹配 + 无次数即跳过）─────────

    def _sweep(self) -> None:
        for _ in range(3):
            if self.check_stop():
                return
            items = self.bot._ocr_screen()
            pos = self._find_exact(items, _SWEEP_WORD, min_conf=0.7)
            if not pos:
                self.log("[信息] 主界面无「扫荡」按钮（可能无次数）→ 跳过扫荡")
                return
            self.log(f"[点击] 「扫荡」 | 图像=({pos[0]},{pos[1]})")
            self.bot.click_at(pos[0], pos[1], '结社战线_扫荡')
            self._sleep(1.5)
            items2 = self.bot._ocr_screen()
            texts2 = self._texts(items2)
            # 常见回执：确认弹窗 / 次数不足提示 / 结算页 → 一律关掉
            if any('次数不足' in t or '无可扫荡' in t or '没有' in t
                   for t in texts2):
                self.log("[信息] 扫荡无次数（提示已出）→ 跳过扫荡")
                self.dismiss_blink_popup(items2)
                self._sleep(1.0)
                return
            if any(w in t for t in texts2 for w in _RESULT_WORDS) or \
                    any('点击空白处关闭' in t for t in texts2):
                self.dismiss_blink_popup(items2)
                self._sleep(1.2)
            self.log("[成功] 扫荡已执行")
            return
        self.log("[信息] 扫荡未确认成功，继续走挑战流程")

    # ── 备战（每局都走：通关阵容 → 一键采用 → F）────────────

    def _ready_and_fight(self) -> bool:
        """在当前备战界面完成 '通关阵容 → 一键采用 → F'。

        Returns:
            True = 已按 F 进入战斗；False = 未能在备战界面完成（交由调用方处理）
        """
        # ① 通关阵容
        pos = self._find_exact(self.bot._ocr_screen(), '通关阵容', 0.7)
        if pos:
            self.log(f"[点击] 「通关阵容」 | 图像=({pos[0]},{pos[1]})")
            self.bot.click_at(pos[0], pos[1], '备战_通关阵容')
            self._sleep(1.5)
        # ② 一键采用（阵容选择弹窗内）
        pos2 = self._find_exact(self.bot._ocr_screen(), '一键采用', 0.7)
        if pos2:
            self.log(f"[点击] 「一键采用」 | 图像=({pos2[0]},{pos2[1]})")
            self.bot.click_at(pos2[0], pos2[1], '备战_一键采用')
            self._sleep(1.5)
        # ③ 按 F 开战
        self.log("[信息] 按 F 开始战斗")
        self.bot.send_key_f()
        self._sleep(2.0)
        # turns 是 BaseTask 的**只读属性**（由 stats['total_battles'] 推算）→
        # 必须递增统计值，不能直接给 turns 赋值
        try:
            self.stats['total_battles'] = int(
                self.stats.get('total_battles', 0)) + 1
        except Exception:
            pass
        self.battles += 1
        return True

    # ── 主循环（状态机）────────────────────────────────────

    def _play_loop(self) -> str:
        stall: int = 0
        in_battle: bool = False
        while True:
            if self.check_stop():
                return self._stop_result()

            reason = self._over_limit()
            if reason:
                self.log(f"[成功] {reason} → 结束结社战线")
                return 'done'

            items = self.bot._ocr_screen()
            texts = self._texts(items)

            # ① 战斗结算页：胜利/失败 + 点空白关闭（有「跳过」先点跳过）
            if any(w in t for t in texts for w in _RESULT_WORDS):
                win = any('战斗胜利' in t for t in texts)
                if win:
                    self.wins += 1
                    self.retried = False
                else:
                    self.losses += 1
                    # 用户口径（2026-10-27）：失败 → **原阵容重试一次**；
                    # 连续两次失败即收手（不换阵容、不无限重试）
                    if self.retried:
                        self.log("[警告] 连续两次失败（原阵容重试已用完）"
                                 "→ 结束结社战线")
                        return 'failed'
                    self.retried = True
                    self.log("[信息] 战斗失败 → 原阵容重试一次")
                self.log(f"[结果] 战斗{'胜利' if win else '失败'}"
                         f"（累计 {self.wins}胜/{self.losses}负）")
                skip = self.bot.template_match(
                    _SKIP_BTN_TPL, threshold=_SKIP_BTN_THRESHOLD,
                    silent=True, scale_range=_SKIP_BTN_SCALE_RANGE)
                if skip:
                    self.log(f"[点击] 跳过（模板） | 屏幕=({skip[0]},{skip[1]})")
                    self.bot._click_abs(skip[0], skip[1])
                    self._sleep(1.2)
                self.dismiss_blink_popup(items)
                self._sleep(1.2)
                in_battle = False
                stall = 0
                continue

            # ② 备战界面：每局都走 通关阵容 → 一键采用 → F
            if any(w in t for t in texts for w in _READY_WORDS) or \
                    any('战斗' == t.strip() for t in texts):
                if in_battle:
                    # 刚按过 F 还在备战页 → 等一等（加载/动画）
                    self._sleep(1.0)
                    stall += 1
                    if stall >= _STALL_MAX:
                        self.log(f"[警告] 战斗未开始（停在备战）｜候选={self._cands(items)}")
                        return 'failed'
                    continue
                self.log("[信息] 备战界面（结社战线）")
                self._ready_and_fight()
                in_battle = True
                stall = 0
                continue

            # ③ 结社战线主界面：点「挑战」
            if any(w in t for t in texts for w in _MAIN_WORDS):
                pos = self._find_any(items, _BATTLE_WORDS, min_conf=0.7)
                if pos:
                    self.log(f"[点击] 「挑战」 | 图像=({pos[0]},{pos[1]})")
                    self.bot.click_at(pos[0], pos[1], '结社战线_挑战')
                    self._sleep(2.0)
                    stall = 0
                    continue
                # 主界面但无「挑战」→ 次数用尽
                self.log("[成功] 主界面已无「挑战」按钮（次数用尽）→ 结束")
                return 'done'

            # ④ 其它弹窗（奖励/提示）→ 关掉
            if any('点击空白处关闭' in t or '确定' == t.strip()
                   or '取消' == t.strip() for t in texts):
                self.log("[点击] 关闭弹窗（点空白）")
                self.dismiss_blink_popup(items)
                self._sleep(1.0)
                stall = 0
                continue

            # ⑤ 未知画面
            stall += 1
            self.log(f"[信息] 结社战线：未知画面（等待 {stall}/{_STALL_MAX}）")
            if stall >= _STALL_MAX:
                self.log(f"[错误] 连续 {stall} 帧未知画面，终止"
                         f"｜候选={self._cands(items)}")
                return 'failed'
            self._sleep(1.2)


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🏯",
    group="游戏任务",
    status="ready",
    options_schema=GuildBattleTask.options_schema,
    nav_target='playbook',   # 入口在玩法目录
    oneshot=True,            # 自带走次/时限选项 → 隐藏全局"时间/轮次"
))
