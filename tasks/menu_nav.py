"""主菜单入口导航公共模块（v1.9.69，代码收敛）。

daily_quest 与 friend_gift 的入口导航此前约 90% 逐字重复（按当前所在层
选主菜单按钮模板 → 点主菜单 → 点菜单项 → 判目标页）。抽为本模块复用，
后续任何「主菜单 → 菜单项 → 目标页」形态的任务直接调用。

【约束】GlobalNavigator 必须在函数体内延迟 import——两个任务的 dryrun
测试通过 monkeypatch 替换 `tasks.global_nav.GlobalNavigator` 注入假导航，
模块顶层导入会让 patch 失效（测试会真的走全局导航）。
"""

from typing import Callable, List, Tuple

# 主菜单按钮按层选款式（0 层 / 1 层款式不同，未命中自动互换重试）
# v2.1.15（用户 12:28）：值为 '' 表示**主菜单面板已在该页下层**（如好友界面
# 是从主菜单点「社交」进入的子页）→ 直接点菜单项，不点主菜单按钮、更不导航。
MENU_BY_SCREEN = {
    'nav.world_scene': 'btn_menu_l0.png',
    'nav.home_scene': 'btn_menu_l0.png',
    'nav.world_home': 'btn_menu_l0.png',
    'nav.playbook': 'btn_menu_l1.png',
    'nav.guild': 'btn_menu_l1.png',
    'nav.friend': '',          # 主菜单子页 → 先「返回上一层」露出主菜单面板
    # v2.1.26：任务界面同为「主菜单子页」（v2.1.25 页面判据已放宽到"任务界面"，
    # 正常情况下 on_page 直接命中、不会走到这里；登记以防判据抖动时绕路）
    'nav.task_page': '',
    # v2.1.16：主菜单面板**本身就是菜单** → 直接点菜单项（不点按钮、不导航）
    'nav.main_menu': '__open__',
}
MENU_THRESHOLD: float = 0.75
SCALE_RANGE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
_ROUNDS: int = 4           # 外层轮次（含导航兜底）
_ITEM_ROUNDS: int = 3      # 菜单项子循环


def open_from_main_menu(
    task,
    on_page: Callable[[List[Tuple]], bool],
    item_tpl: str,
    item_threshold: float,
    item_kw: str,
    label: str = '菜单项',
) -> bool:
    """打开主菜单并点击菜单项，直到 `on_page(items)` 为真。

    Args:
        task: BaseTask 实例（使用其 bot / log / check_stop / _sleep）
        on_page: 目标页判据（接收 OCR items）
        item_tpl: 菜单项模板文件名（如 btn_quest.png）
        item_threshold: 菜单项模板阈值
        item_kw: 菜单项 OCR 全等兜底词（如「任务」/「社交」）
        label: 点击标签前缀（如「每日任务」/「友情点」）

    Returns:
        bool: 是否已到达目标页
    """
    from tasks.global_nav import GlobalNavigator    # 延迟导入（保测试 patch）
    from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY

    for _round in range(_ROUNDS):
        if task.check_stop():
            return False
        items = task.bot._ocr_screen()
        if on_page(items):
            return True
        screen = GLOBAL_SCREEN_REGISTRY.detect(items)
        tpl = MENU_BY_SCREEN.get(screen, None)
        mpos = None
        if tpl:
            # 已知层 → 当前层款式优先
            mpos = task.bot.template_match(
                tpl, threshold=MENU_THRESHOLD, silent=True,
                scale_range=SCALE_RANGE)
            if not mpos:
                other = ('btn_menu_l1.png' if tpl.endswith('l0.png')
                         else 'btn_menu_l0.png')
                mpos = task.bot.template_match(
                    other, threshold=MENU_THRESHOLD, silent=True,
                    scale_range=SCALE_RANGE)
        elif tpl is None:
            # v2.1.15（用户 12:28）：**未登记画面**先试两款主菜单模板（0/1 层），
            # 命中即点；两款都不命中才退回全局导航。旧逻辑直接导航到玩法目录，
            # 真机白花 10~15 秒（例：家园工坊内部、竞技场收尾后）
            for _t in ('btn_menu_l0.png', 'btn_menu_l1.png'):
                mpos = task.bot.template_match(
                    _t, threshold=MENU_THRESHOLD, silent=True,
                    scale_range=SCALE_RANGE)
                if mpos:
                    break
        elif tpl == '__open__':
            # 主菜单面板已开 → 无需点主菜单按钮，直接进菜单项循环
            pass
        else:
            # tpl == ''：该页是主菜单**子页**（如好友界面）→
            # 按用户定案：「①返回上一层（主菜单界面）②点击菜单项」——
            # 即先返回露出主菜单面板，**不点主菜单按钮、更不导航绕路**
            # （真机 12:18:26~12:18:40 旧逻辑白花 14 秒）
            task.log(f"[信息] 菜单子页（{screen}）→ 先返回上一层")
            task.bot.press_esc(label=f'{label}_返回上层')
            task._sleep(1.5)
            items = task.bot._ocr_screen()
            if on_page(items):
                return True
        # 已按上表决定是否点主菜单按钮（tpl=='' 的子页已在上面返回上层）
        if mpos:
            task.log(f"[点击] 主菜单按钮（{screen}） | 屏幕={mpos}")
            task.bot._click_abs(*mpos)
            task._sleep(1.5)
        elif tpl is None:
            # 两款模板都没命中 → 全局导航兜底（退回 1 层）
            task.log("[信息] 主菜单按钮未命中，走全局导航兜底")
            nav = GlobalNavigator(
                task.bot, logger=task.log, stop_check=task.check_stop,
            )
            if not nav.navigate_to('playbook'):
                task._nav_last_screen = getattr(nav, 'last_screen', '')   # v2.1.27：留现场（诊断用，取值失败不得中断）
                return False
            continue
        # 菜单面板 → 菜单项（模板优先，OCR 全等兜底）
        for _ in range(_ITEM_ROUNDS):
            if task.check_stop():
                return False
            items = task.bot._ocr_screen()
            if on_page(items):
                return True
            ipos = task.bot.template_match(
                item_tpl, threshold=item_threshold, silent=True,
                scale_range=SCALE_RANGE)
            if ipos:
                task.log(f"[点击] 「{item_kw}」按钮 | 屏幕={ipos}")
                task.bot._click_abs(*ipos)
                task._sleep(2.0)
                continue
            tpos = next(((it[1], it[0]) for it in items
                         if len(it) >= 4 and it[3] >= 0.8
                         and it[2].strip() == item_kw), None)
            if tpos:
                task.log(f"[点击] 「{item_kw}」按钮（OCR 兜底） | 图像={tpos}")
                task.bot.click_at(tpos[0], tpos[1], f'{label}_菜单项')
                task._sleep(2.0)
                continue
            break
        items = task.bot._ocr_screen()
        if on_page(items):
            return True
    return False
