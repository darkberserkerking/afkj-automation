"""
任务：竞技场（v1.9.31 实装，用户 2026-09-10 需求 + 真机截图 OCR 实测）。

────────────────────────────────────────────
【入口路径】
  0 层（世界/家园）→ 玩法目录 → 滚动找「竞技场」卡片 → 点 → 竞技场主界面

【页面判据】（MaaOCR 实测，素材=用户 2026-09-10 截图）
  竞技场主界面：「开始挑战」0.99 +「防守阵容」0.97 双词
  选择对手页  ：「选择对」0.99（标题 OCR 常截断"对手"→"对"）
  备战页      ：「战斗」大按钮 0.99 +「编队」0.97 双词
  结算页      ：「战斗胜利/失败」+「点击空白处关闭」（dismiss_blink_popup 通用）

【对手选择规则】（用户定案）
  三列候选从左到右（列中心 rx≈0.36/0.505/0.65）：
  - 含「公会成员」或「好友」标签的列排除（OCR 实测「本公会成员」含子串）
  - 合格列点双剑按钮（arena_challenge_icon.png maskedNCC）
  - 全部被排除 → 点「重置」（右下 rx0.68 ry0.95，最多 7 次）后重选
  - 重置用尽仍无 → 任务完成

【挑战次数】
  主界面「开始挑战」下方票数「3/1」（ry≈0.958）→ 分子 0 → 任务完成。

【战斗】
  备战页输入 F 开始（send_key_f）；战斗中无判据词 → 等结算页出现。

【架构原则】
  内部状态全部自 OCR 判定，不动 detector（goddess/labyrinth 同款隔离）。
────────────────────────────────────────────
"""

import os
import re
import time
from typing import List, Optional, Tuple

from tasks.base import BaseTask
from tasks.labyrinth import (  # noqa: E402  v1.9.46 复用跳过按钮模板
    _SKIP_BTN_SCALE_RANGE, _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD)
from task_registry import TaskRegistry, TaskMeta

task_id = "arena"
label = "竞技场"
description = "自动挑战：选非好友非公会对手，循环战斗至票数耗尽"

# ── 判据常量 ────────────────────────────────────────────────
_STALL_MAX: int = 10            # 未知画面连续帧兜底
_RESET_MAX: int = 7             # 对手重置上限（界面 7/7）
_OPPONENT_COLS: Tuple[float, ...] = (0.36, 0.505, 0.65)   # 三列中心 rx
_CHALLENGE_TPL: str = 'arena_challenge_icon.png'          # 双剑按钮
_CHALLENGE_THRESHOLD: float = 0.72


class ArenaTask(BaseTask):
    task_id = task_id      # v1.9.33：类属性（TASK_CLASSES 映射键）
    label = label
    """竞技场自动挑战。"""

    options_schema = {
        # v1.9.45（用户定案换回）：黑名单=永不挑战；白名单=优先挑战
        'blacklist': {
            'type': 'str',
            'label': '挑战黑名单',
            'default': '',
            'desc': '角色名用;或；分隔，永不作为挑战对象',
            'note': '黑名单=永不挑战；白名单=优先挑战',
        },
        'whitelist': {
            'type': 'str',
            'label': '挑战白名单',
            'default': '',
            'desc': '角色名用;或；分隔，优先挑战目标',
            # v2.2.0（用户 01:19 指定文案与位置）：白名单行右侧灰字提示。
            # 此前该行右侧为空，而 desc 只作**占位符**（输入框有值就不显示）
            # → 用户会把名字用逗号连写（如 `a,b`），实际 `re.split(r'[;；]')`
            #   不拆分，整串被当成一个名字、静默失效。故常显此提示。
            'note': '角色名用 ; 或 ； 分隔',
        },
    }

    def __init__(self, ctx: dict):
        super().__init__(ctx)

    def preflight(self) -> Tuple[bool, str]:
        """启动前窗口就绪检查（v1.9.39：与其他任务对齐——此前缺失
        导致竞技场排第一时游戏窗口从未被激活，坐标缓存 0×0 →
        截图空 → 整轮未识别画面）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _stop_result(self) -> str:
        """手动停止时的任务结果（v1.9.38：BaseTask 无此方法）。"""
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    # ── 工具 ────────────────────────────────────────────────

    def _find_word(self, items: List[Tuple], word: str,
                   min_conf: float = 0.6) -> Optional[Tuple[int, int]]:
        """子串匹配找词，返回图像坐标中心。"""
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and word in it[2]:
                return (it[1], it[0])   # (x, y)
        return None

    def _blacklist(self) -> List[str]:
        """挑战黑名单（;或；分隔，永不挑战，v1.9.45 换回）。"""
        raw = str(self.options.get('blacklist') or '')
        return [s.strip() for s in re.split(r'[;；]', raw)
                if s.strip()]

    def _whitelist(self) -> List[str]:
        """挑战白名单（;或；分隔，优先挑战，v1.9.45 换回）。"""
        raw = str(self.options.get('whitelist') or '')
        return [s.strip() for s in re.split(r'[;；]', raw)
                if s.strip()]

    def _rx(self, it: Tuple) -> float:
        """OCR 项的相对 x（W=1280 归一，跨度大足够区分三列）。"""
        return it[1] / 1280.0

    def _ry(self, it: Tuple) -> float:
        return it[0] / 1024.0

    # ── 启动探查 ────────────────────────────────────────────

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        texts = self._texts(items)
        if ('开始挑战' in texts or '继续挑战' in texts
                or any('选择对' in t for t in texts)):
            self.log("[信息] 启动即在竞技场界面，直接续跑")
            return 'ready'
        return 'clear'

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> str:
        self.log("启动（导航 + 竞技场循环挑战）")
        probe: str = self.startup_probe()
        if probe != 'ready':
            ok = self._navigate_to_arena()
            if self.check_stop():
                return self._stop_result()
            if not ok:
                self.log_nav_failed("竞技场")
                return 'failed'
        return self._play_loop()

    def summary(self) -> str:
        return "按名单选对手，循环挑战至票尽"

    # ── 导航 ────────────────────────────────────────────────

    def _navigate_to_arena(self) -> bool:
        """0 层 → 玩法目录 → 滚动找「竞技场」卡片并点击。"""
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(
            self.bot, logger=self.log, stop_check=self.check_stop,
        )
        if not nav.navigate_to('playbook'):
            self._nav_last_screen = getattr(nav, 'last_screen', '')   # v2.1.27：留现场（诊断用，取值失败不得中断）
            return False
        # 玩法目录内滚动找「竞技场」卡片（goddess 同款自愈滚动）
        prev_texts: Optional[List[str]] = None
        while True:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            texts = self._texts(items)
            pos = self._find_word(items, '竞技场', min_conf=0.85)
            if pos:
                self.bot.click_at(pos[0], pos[1], '玩法目录_竞技场卡片')
                self._sleep(2.5)
                return True
            if texts == prev_texts:
                self.log("[警告] 玩法目录已滚到底，未找到「竞技场」卡片")
                return False
            prev_texts = texts
            # 滚轮向下（goddess 同款：激活窗口 + 移到滚动区中心）
            self.bot.scroll_wheel(
                -30, rx=0.61, ry=0.36, label='玩法目录_向下')
            self._sleep(0.3)

    # ── 玩法主循环（状态机）────────────────────────────────

    def _play_loop(self) -> str:
        stall: int = 0
        resets_used: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            texts = self._texts(items)

            # ① 结算页（战斗胜利/失败 + 点击空白处关闭）→ 关闭
            if (any('战斗胜利' in t or '战斗失败' in t for t in texts)
                    and any('点击空白处关闭' in t for t in texts)):
                self.log("[信息] 战斗结算，点击空白关闭")
                self.dismiss_blink_popup(items)
                self._sleep(2.0)     # 回主界面
                stall = 0
                continue

            # ② 选择对手页
            if any('选择对' in t for t in texts):
                r = self._pick_opponent(items, resets_used)
                if r == 'clicked':
                    resets_used = 0
                    stall = 0
                    # v1.9.40（用户 11:08）：双剑后必进备战页 → 直接
                    # 输入 F 开战（「战斗/编队」双词判定在真机不稳，
                    # 不再依赖；③ 分支保留作兜底）
                    self._sleep(2.0)   # 等备战页渲染
                    self.bot.press_f(label='竞技场_备战开战')
                    self._sleep(3.0)
                    continue
                if r == 'reset':
                    resets_used += 1
                    if resets_used > _RESET_MAX:
                        self.log("[成功] 重置用尽仍无合格对手，任务完成")
                        return 'done'
                    stall = 0
                    continue
                stall += 1
                if stall >= _STALL_MAX * 3:
                    self.log("[错误] 选择对手页无进展，任务失败")
                    return 'failed'
                self._sleep(1.5)
                continue

            # ③ 备战页（战斗按钮 + 编队）→ 输入 F 开始战斗
            if '战斗' in texts and '编队' in texts:
                self.log("[信息] 备战页，输入 F 开始战斗")
                self.bot.press_f(label='竞技场_战斗')
                self._sleep(3.0)
                stall = 0
                continue

            # ④ 竞技场主界面（开始挑战/继续挑战 + 防守阵容）
            # v1.9.41：上次场次未打完退出后重进，按钮=「继续挑战」
            # （下方是倒计时非票数）→ 点它回到场次中
            if '防守阵容' in texts and ('开始挑战' in texts
                                         or '继续挑战' in texts):
                if '开始挑战' in texts:
                    # 票数检查：开始挑战下方分式（ry 0.93~0.99）
                    tickets = self._read_tickets(items)
                    if tickets is not None and tickets[0] <= 0:
                        self.log("[成功] 挑战票数耗尽，任务完成")
                        return 'done'
                    kw = '开始挑战'
                else:
                    kw = '继续挑战'
                pos = self._find_word(items, kw)
                if pos:
                    self.log(f"[点击] {kw}")
                    self.bot.click_at(pos[0], pos[1], f'竞技场_{kw}')
                    self._sleep(2.5)
                stall = 0
                continue

            # ⑤ 玩法目录（意外退回）→ 重新点竞技场卡片
            if ('常规玩法' in texts and '活动' in texts):
                pos = self._find_word(items, '竞技场', min_conf=0.85)
                if pos:
                    self.bot.click_at(pos[0], pos[1], '玩法目录_竞技场卡片')
                    self._sleep(2.5)
                    stall = 0
                    continue

            # ⑥ 战斗中（无判据词）→ 等结算出现
            # v1.9.46（用户）：战斗中可点「跳过」快进（迷宫同款模板）
            skip_pos = self.bot.template_match(
                _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD, silent=True,
                scale_range=_SKIP_BTN_SCALE_RANGE,
            )
            if skip_pos:
                self.log("[信息] 战斗中出现「跳过」，点击快进")
                self.bot._click_abs(*skip_pos)
                # v2.1.15（用户 12:28）：点「跳过」后有一小段动画 →
                # 在原有 1.5s 基础上再等 2s，避免动画期误判/误操作
                self._sleep(3.5)
                continue
            stall += 1
            if stall % 5 == 1:
                self.log("[信息] 战斗进行中，等待结算…")
            if stall >= _STALL_MAX * 6:
                self.log("[错误] 长时间无已知画面，任务失败")
                return 'failed'
            self._sleep(2.0)

    # ── 选择对手 ────────────────────────────────────────────

    def _pick_opponent(self, items: List[Tuple],
                       resets_used: int) -> str:
        """选择对手页：排除好友/公会列 → 点合格列双剑；无 → 重置。

        Returns:
            'clicked' 已点双剑进入备战
            'reset'   已点重置（刷新候选）
            'wait'    界面未就绪/处理中
        """
        blocked: set = set()
        for it in items:
            if len(it) < 4 or it[3] < 0.6:
                continue
            t = it[2]
            rx = self._rx(it)
            ry = self._ry(it)
            if not (0.28 < rx < 0.75 and 0.40 < ry < 0.68):
                continue   # 只认对手卡片区内的标签
            col = min(range(3),
                      key=lambda i: abs(_OPPONENT_COLS[i] - rx))
            if '公会成员' in t:
                blocked.add(col)
            elif t.strip() == '好友':
                blocked.add(col)
            else:
                # v1.9.45：黑名单=永不挑战（双向子串——OCR 可能截断名字）
                for b in self._blacklist():
                    if b in t or t in b:
                        blocked.add(col)
                        break
        free = [c for c in range(3) if c not in blocked]
        # v1.9.45：白名单最高优先——命中的列无视标签/黑名单直接选
        # （黑名单=永不挑战，其命中列已在上面 blocked 排除）
        white_cols: List[int] = []
        wl = self._whitelist()
        if wl:
            for it in items:
                if len(it) < 4 or it[3] < 0.6:
                    continue
                rx = self._rx(it)
                ry = self._ry(it)
                if not (0.28 < rx < 0.75 and 0.40 < ry < 0.68):
                    continue
                t = it[2]
                col = min(range(3),
                          key=lambda i: abs(_OPPONENT_COLS[i] - rx))
                if any(w in t or t in w for w in wl) and col not in white_cols:
                    white_cols.append(col)
        if white_cols:
            free = white_cols
            self.log(f"[信息] 白名单命中第 "
                     f"{[c + 1 for c in white_cols]} 列，优先挑战")
        self.log(f"[信息] 候选列状态：排除={sorted(blocked)}，"
                 f"合格={free}（已重置 {resets_used}/7）")
        if free:
            # v1.9.32：按列区域匹配双剑（单点全局匹配可能落在排除列，
            # 三列按钮同图形分数接近——限定各列区域逐一匹配更可靠）
            try:
                img, cl, ct, cw, chh = self.bot.screen_image()
            except Exception as e:
                self.log(f"[警告] screen_image 异常（{e}），跳过本轮选对手")
                return 'wait'
            # v1.9.43（用户）：多列合格时优先右边的（free 倒序）
            for col in sorted(free, reverse=True):
                try:
                    cx_img = int(_OPPONENT_COLS[col] * cw)
                    # v1.9.38（真机 10:45）：双剑按钮实际 ry≈0.70~0.75
                    # （三列高度不一），y 区域放宽到 0.55~0.87H，
                    # 阈值 0.72→0.65，缩放 0.7~1.4（源图分辨率差异）
                    y0 = int(0.55 * chh)
                    region = (max(0, cl + cx_img - 130),
                              max(0, ct + y0), 260,
                              int(0.32 * chh))
                    pos = self.bot.template_match(
                        _CHALLENGE_TPL, threshold=0.65,
                        silent=True, scale_range=(0.7, 1.4, 0.1),
                        region=region,
                    )
                except Exception as e:
                    # v1.9.36：单列匹配异常不崩任务（region 越界等），试下一列
                    self.log(f"[警告] 第 {col + 1} 列模板匹配异常（{e}），试下一列")
                    continue
                if pos:
                    self.log(f"[点击] 第 {col + 1} 列对手双剑按钮")
                    self.bot._click_abs(pos[0], pos[1])
                    self._sleep(2.5)
                    return 'clicked'
                self.log(f"[信息] 第 {col + 1} 列双剑未命中，试下一列")
        # 无合格对手 / 合格列无双剑命中 → 点重置
        pos = self._find_word(items, '重置', min_conf=0.6)
        if pos:
            self.log("[点击] 重置对手（候选均为好友/公会或无可点）")
            self.bot.click_at(pos[0], pos[1], '竞技场_重置')
            self._sleep(2.0)
            return 'reset'
        return 'wait'

    def _read_tickets(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """读主界面挑战票数（开始挑战下方分式，ry 0.92~0.99）。"""
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            ry = self._ry(it)
            if not (0.92 < ry < 0.99):
                continue
            t = it[2].strip().replace('O', '0')
            m = re.fullmatch(r"(\d{1,3})\s*/\s*(\d{1,3})", t)
            if m:
                return (int(m.group(1)), int(m.group(2)))
        return None


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="⚔",
    group="游戏任务",
    status="ready",
    options_schema=ArenaTask.options_schema,
    nav_target='playbook',   # 玩法目录入口卡片
    oneshot=True,   # v1.9.32：循环到票尽自然结束，隐藏时间/轮次
))
