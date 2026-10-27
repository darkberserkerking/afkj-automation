"""
任务：赠送收取友情点（v1.9.62 实装，用户 17:29 需求 + 双截图实测）。

────────────────────────────────────────────
【入口路径】
  主菜单按钮（0 层 btn_menu_l0 / 1 层 btn_menu_l1，与每日任务同款）
  → 侧滑菜单 → 「社交」（双人头像图标，btn_social.png 模板）
  → 好友界面 → 「一键赠礼并收取」（底部大按钮）

【页面判据】（MaaOCR 实测 2026-09-10 好友界面截图）
  好友界面：「收礼进度N/10」0.98（顶部进度条）或
            「一键赠礼并收取」（底部大按钮）+「好友」页签 0.98

【流程】
  startup_probe：已在好友界面 → 直通；
  主菜单：按当前所在层选按钮模板（0/1 层都支持，未命中试另一款）；
  「社交」：模板（用户裁图）优先，OCR 全等「社交」兜底；
  「一键赠礼并收取」：OCR 点击 → 关弹层 → 复核一轮（≤2 次）→ done。

【架构】preflight/_stop_result/checklist 齐全（v1.9.39 铁律）。
────────────────────────────────────────────
"""

from typing import List, Tuple

from task_registry import TaskRegistry, TaskMeta
from tasks.base import BaseTask

task_id = "friend_gift"
label = "收赠友情点"
description = "一键赠礼并收取友情点（收礼进度满即完成）"

_SOCIAL_TPL: str = 'btn_social.png'
_SOCIAL_THRESHOLD: float = 0.72
_GIFT_MAX: int = 2               # 一键赠礼并收取点击上限


class FriendGiftTask(BaseTask):
    """赠送收取友情点。"""

    task_id = task_id
    label = label

    # v2.1.9（用户 22:35 规范）循环语义声明——好友界面内即可就地续跑
    loop_screens = frozenset({'nav.friend'})

    options_schema = {}

    def preflight(self) -> Tuple[bool, str]:
        """窗口就绪检查（v1.9.39 铁律：新任务必写）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _gift_done_today(self, items: List[Tuple]) -> bool:
        """收礼进度 10/10 → 当日已完成（v1.9.63，用户 17:34）。

        真机实测「收礼进度10/10」（合并文本 conf 0.98）；兼容
        「收礼进度」与「10/10」分列的 OCR 形态。
        """
        import re
        for it in items:
            if len(it) < 4 or it[3] < 0.8:
                continue
            m = re.search(r"收礼进度\s*(\d{1,2})\s*/\s*10", it[2].strip())
            if m:
                return int(m.group(1)) >= 10
        if any('收礼进度' in it[2] for it in items if len(it) >= 4):
            for it in items:
                if (len(it) >= 4 and it[3] >= 0.8
                        and re.fullmatch(r"10\s*/\s*10", it[2].strip())):
                    return True
        return False

    def _on_friend_page(self, items: List[Tuple]) -> bool:
        """好友界面判定：收礼进度 / 一键赠礼并收取 / 好友页签。"""
        texts = self._texts(items)
        return (any('收礼进度' in t for t in texts)
                or any('一键赠礼' in t for t in texts))

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        if self._dismiss_reward_popup(items):
            items = self.bot._ocr_screen()
        if self._on_friend_page(items):
            if self._gift_done_today(items):
                self.log("[成功] 收礼进度 10/10，当日已完成，无需操作")
                return 'done'
            self.log("[信息] 启动即在好友界面，直接赠礼收取")
            return 'ready'
        return 'clear'

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> str:
        self.log("启动（主菜单 + 社交 + 一键赠礼并收取）")
        probe = self.startup_probe()
        if probe == 'done':
            return 'done'
        if probe != 'ready':
            if not self._goto_friend_page():
                if self.check_stop():
                    return self._stop_result()
                self.log_nav_failed("好友界面")
                return 'failed'
        return self._gift_flow()

    def summary(self) -> str:
        return "一键赠礼并收取友情点"

    # ── 入口导航 ────────────────────────────────────────────

    def _goto_friend_page(self) -> bool:
        """按当前所在层打开主菜单 → 「社交」（公共逻辑见 menu_nav）。

        v1.9.69：入口导航抽到 tasks/menu_nav.open_from_main_menu
        （与 daily_quest 共用）。
        """
        from tasks.menu_nav import open_from_main_menu
        return open_from_main_menu(
            self,
            on_page=self._on_friend_page,
            item_tpl=_SOCIAL_TPL,
            item_threshold=_SOCIAL_THRESHOLD,
            item_kw='社交',
            label='友情点',
        )
    # ── 赠礼收取 ────────────────────────────────────────────

    def _gift_flow(self) -> str:
        """好友界面：「一键赠礼并收取」点击 ≤2 次。"""
        clicked = 0
        items0 = self.bot._ocr_screen()
        if self._gift_done_today(items0):
            self.log("[成功] 收礼进度 10/10，当日已完成，无需操作")
            return 'done'
        while clicked < _GIFT_MAX:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            pos = self.bot.find_text(items, '一键赠礼', min_conf=0.8)
            if not pos:
                if self._dismiss_reward_popup(items):
                    continue
                self.log(f"[成功] 友情点赠送收取完毕（{clicked} 次）")
                return 'done'
            if clicked > 0 and self._dismiss_reward_popup(items):
                # 已点过一轮 → 弹层残留先关掉再复核
                continue
            self.log(f"[点击] 「一键赠礼并收取」（第 {clicked + 1} 次） | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '友情点_一键赠礼并收取')
            clicked += 1
            self._sleep(2.5)
            it_r = self.bot._ocr_screen()
            self._dismiss_reward_popup(it_r)
        self.log("[成功] 友情点赠送收取完毕（达 2 次上限）")
        return 'done'


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🎁",
    group="游戏任务",
    status="ready",
    options_schema=FriendGiftTask.options_schema,
    nav_target='',   # 主菜单在 0/1 层均有按钮
    oneshot=True,
))
