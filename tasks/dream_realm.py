"""
任务：迷梦之域（v1.9.66 实装，用户 17:49 需求 + 4 张截图实测）。

────────────────────────────────────────────
【入口路径】
  玩法目录·常规玩法 → 「迷梦之域」卡片（OCR 0.99）
  → 迷梦主界面（沼泽之主 挑战 + 次数 N/10）
  → 点「挑战」→ 备战页（战斗 + 阵容推荐）→ F 开战
  → 战斗中（跳过按钮模板快进）→ 结算「战斗结束」→ 点空白关闭
  → 回主界面 → 循环直到次数耗尽 → done

【页面判据】（MaaOCR 实测 2026-09-10 截图，1280x1020）
  dream.main   ：「挑战」0.99 + 「次数N/10」1.00（rx0.54 ry0.93/0.96）
  dream.ready  ：「战斗」0.98（rx0.58 ry0.92）+「阵容推荐」0.99
  dream.result ：「战斗结束」0.96 +「点击空白处关闭」0.99

【流程状态机】
  ① 结算页（战斗结束/点击空白处关闭）→ 点空白关闭
  ② 主界面（挑战+次数）→ 次数 0 → done；否则点「挑战」
  ③ 备战页（战斗+阵容推荐）→ press_f 开战
  ④ 战斗中 → 跳过按钮模板命中 → 点击快进
  ⑤ 玩法目录（迷梦之域卡片可见）→ 点卡片进入
  ⑥ 其他 → 等待（转场），stall 超限 → failed

【架构】preflight/_stop_result/checklist 齐全（v1.9.39 铁律）。
────────────────────────────────────────────
"""

import re
from typing import List, Optional, Tuple

from task_registry import TaskRegistry, TaskMeta
from tasks.base import BaseTask
from tasks.labyrinth import (  # noqa: E402  复用跳过按钮模板
    _SKIP_BTN_SCALE_RANGE, _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD)

task_id = "dream_realm"
label = "迷梦之域"
description = "挑战迷梦之域（F 开战，可循环至次数耗尽）"

_STALL_MAX: int = 20        # 未知画面连续上限（× 2s）
_ROUND_MAX: int = 30        # 单次运行最大轮数（次数上限 10，留余量）
_SKIP_REGION: Tuple[int, int, int, int] = (0, 0, 1280, 1024)


class DreamRealmTask(BaseTask):
    """迷梦之域循环挑战。"""

    task_id = task_id
    label = label

    options_schema = {
        # v1.9.66（用户 17:52）：第 5 步「重复 1-4」为可勾选——
        # 不勾选（默认）只打一次，勾选则循环至次数耗尽
        'loop': {
            'type': 'bool',
            'label': '重复挑战至次数耗尽',
            'default': False,
            'desc': '不勾选则只打一次',
        },
    }

    def preflight(self) -> Tuple[bool, str]:
        """窗口就绪检查（v1.9.39 铁律）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _stop_result(self) -> str:
        """手动停止结果（BaseTask 无此方法，v1.9.39 铁律）。"""
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def summary(self) -> str:
        return "挑战迷梦之域（可勾选重复）"

    # ── 工具 ────────────────────────────────────────────────

    def _read_attempts(self, items: List[Tuple]) -> Optional[int]:
        """主界面「次数N/10」→ N（0.99 实测）。"""
        for it in items:
            if len(it) < 4 or it[3] < 0.8:
                continue
            m = re.search(r"次数\s*(\d{1,2})\s*/\s*10", it[2].strip())
            if m:
                return int(m.group(1))
        return None

    # ── 启动探查 ────────────────────────────────────────────

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        texts = self._texts(items, 0.6)   # v2.1.27：本任务沿用 0.6 阈值（意图显式化）
        if any('战斗结束' in t or '点击空白处关闭' in t for t in texts):
            self.log("[信息] 启动即在结算页，先关闭")
            self._close_result(items)
            items = self.bot._ocr_screen()
            texts = self._texts(items, 0.6)
        if any('挑战' in t for t in texts) and any('次数' in t for t in texts):
            self.log("[信息] 启动即在迷梦主界面")
            return 'ready'
        if any('战斗' in t for t in texts) and any('阵容推荐' in t
                                                  for t in texts):
            self.log("[信息] 启动即在备战页")
            return 'ready'
        return 'clear'

    # ── 主循环 ──────────────────────────────────────────────

    def run(self) -> str:
        self.log("启动（玩法目录 → 迷梦之域 → 循环挑战）")
        probe = self.startup_probe()
        if probe != 'ready':
            if not self._goto_dream_page():
                if self.check_stop():
                    return self._stop_result()
                self.log_nav_failed("迷梦之域")
                return 'failed'
        return self._play_loop()

    # ── 入口导航 ────────────────────────────────────────────

    def _goto_dream_page(self) -> bool:
        """玩法目录 → 点「迷梦之域」卡片。"""
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(
            self.bot, logger=self.log, stop_check=self.check_stop,
        )
        if not nav.navigate_to('playbook'):
            self._nav_last_screen = getattr(nav, 'last_screen', '')   # v2.1.27：留现场（诊断用，取值失败不得中断）
            return False
        for i in range(4):
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            texts = self._texts(items, 0.6)
            if any('挑战' in t for t in texts) and any('次数' in t
                                                      for t in texts):
                self.log("[成功] 已进入迷梦之域主界面")
                return True
            pos = next(((it[1], it[0]) for it in items
                        if len(it) >= 4 and it[3] >= 0.8
                        and '迷梦之域' in it[2]), None)
            if pos:
                self.log(f"[点击] 「迷梦之域」卡片 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '迷梦_卡片')
                self._sleep(2.5)
                continue
            # 卡片不在首屏 → 滚动找（玩法目录可滚动区中心上滑）
            self.log(f"[信息] 未见「迷梦之域」卡片，滚动查找（{i + 1}/4）")
            self.bot.drag_abs(640, 666, 640, 358, duration=0.4)
            self._sleep(1.5)
        items = self.bot._ocr_screen()
        texts = self._texts(items, 0.6)
        return any('挑战' in t for t in texts) and any('次数' in t
                                                      for t in texts)

    # ── 战斗循环 ────────────────────────────────────────────

    def _close_result(self, items: List[Tuple]) -> None:
        """结算页点空白关闭（「点击空白处关闭」实测 ry 0.919）。"""
        self.log("[点击] 结算页点空白关闭")
        self.bot.click_ratio(0.5, 0.72, label='迷梦_结算关闭')
        self._sleep(2.0)

    def _play_loop(self) -> str:
        self._loop = bool(self.options.get('loop', False))
        if self._loop:
            self.log("[信息] 已勾选「重复挑战至次数耗尽」")
        else:
            self.log("[信息] 未勾选重复挑战，本次只打一次")
        stall = 0
        rounds = 0
        self._f_pressed = False   # v1.9.66b：本轮是否已按 F
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            texts = self._texts(items, 0.6)

            # ① 结算页 → 点空白关闭
            if any('战斗结束' in t or '点击空白处关闭' in t for t in texts):
                self._close_result(items)
                # v1.9.66（用户 17:52）：未勾选「重复挑战」→ 只打一次
                if not self._loop:
                    self.log(
                        f"[成功] 已完成 {max(rounds, 1)} 轮挑战"
                        "（未勾选重复挑战），任务完成")
                    return 'done'
                stall = 0
                continue

            # ② 主界面（挑战+次数）
            if any('挑战' in t for t in texts) and any('次数' in t
                                                      for t in texts):
                n = self._read_attempts(items)
                if n is not None and n <= 0:
                    self.log(f"[成功] 挑战次数耗尽（0/10），"
                             f"共完成 {rounds} 轮，任务完成")
                    return 'done'
                if rounds >= _ROUND_MAX:
                    self.log(f"[警告] 达 {_ROUND_MAX} 轮上限，停止")
                    return 'done'
                pos = next(((it[1], it[0]) for it in items
                            if len(it) >= 4 and it[3] >= 0.8
                            and it[2].strip() == '挑战'), None)
                if pos:
                    self.log(f"[点击] 「挑战」（剩余 {n if n is not None else '?'}"
                             f" 次） | 图像={pos}")
                    self.bot.click_at(pos[0], pos[1], '迷梦_挑战')
                    self._f_pressed = False   # v1.9.66b：新轮次重置 F 标记
                    self._sleep(2.5)
                stall = 0
                continue

            # ③ 备战页（战斗+阵容推荐）
            #    v2.1.2（用户 20:54）：**「跳过战斗」优先**——当天已挑战过
            #    一次后，备战页会出现「跳过战斗」按钮（实测 rx≈0.40
            #    ry≈0.93，与「战斗」按钮并列），点击即直接到结算界面。
            #    无该按钮（首次挑战）→ 按 F 正常开战。
            if any('战斗' in t for t in texts) and any('阵容推荐' in t
                                                      for t in texts):
                _skip_btn = next(((it[1], it[0]) for it in items
                                  if len(it) >= 4 and it[3] >= 0.7
                                  and '跳过战斗' in it[2]), None)
                if _skip_btn and not self._f_pressed:
                    self.log(f"[点击] 「跳过战斗」（当天已挑战过）"
                             f" | 图像={_skip_btn}")
                    self.bot.click_at(_skip_btn[0], _skip_btn[1], '迷梦_跳过战斗')
                    rounds += 1
                    self._f_pressed = True
                    self._sleep(3.0)
                    stall = 0
                    continue
                if not self._f_pressed:
                    self.log("[信息] 备战页，输入 F 开战")
                    self.bot.press_f(label='迷梦_备战开战')
                    rounds += 1
                    self._f_pressed = True
                    self._sleep(3.0)
                else:
                    self._sleep(2.0)   # 等待开战转场
                stall = 0
                continue

            # ④ 战斗中 → 跳过
            # v2.0.8（真机 19:08：迷梦有跳过但未点击）——迷宫模板在迷梦
            # UI 上失配，改 **OCR「跳过」文字优先**（与按钮位置无关），
            # 复用本帧 OCR（零额外开销），模板作兜底
            _sp = next(((it[1], it[0]) for it in items
                       if len(it) >= 4 and it[3] >= 0.7
                       and '跳过' in it[2]), None)
            if _sp:
                self.log(f"[点击] 战斗「跳过」（OCR） | 图像={_sp}")
                self.bot.click_at(_sp[0], _sp[1], '迷梦_跳过')
                self._sleep(1.5)
                stall = 0
                continue
            skip_pos = self.bot.template_match(
                _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD, silent=True,
                scale_range=_SKIP_BTN_SCALE_RANGE, region=_SKIP_REGION,
            )
            if skip_pos:
                self.log("[信息] 战斗中出现「跳过」（模板），点击快进")
                self.bot._click_abs(*skip_pos)
                self._sleep(1.5)
                stall = 0
                continue

            # ⑤ 玩法目录（迷梦之域卡片可见）→ 点卡片
            pos = next(((it[1], it[0]) for it in items
                        if len(it) >= 4 and it[3] >= 0.8
                        and '迷梦之域' in it[2]), None)
            if pos:
                self.log(f"[点击] 「迷梦之域」卡片 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '迷梦_卡片')
                self._sleep(2.5)
                stall = 0
                continue

            # ⑥ 其他：转场等待
            stall += 1
            if stall % 5 == 1:
                self.log("[信息] 等待画面就绪…")
            if stall >= _STALL_MAX * 6:
                self.log("[错误] 长时间无已知画面，任务失败")
                return 'failed'
            self._sleep(2.0)


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="💠",
    group="游戏任务",
    status="ready",
    options_schema=DreamRealmTask.options_schema,
    nav_target='playbook',   # v1.8.31：入口=玩法目录（1 层）
    oneshot=False,
))
