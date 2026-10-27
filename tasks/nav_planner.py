"""全局导航·最短路径规划器（v1.9.58）。

在既有「界面注册表 + NAV_TARGETS + 动作表」之上，建立**画面节点图**，
用 BFS 自主探索并比较所有候选路线，输出步数最少的最优路径。

────────────────────────────────────────────
【图模型】
  节点 = 画面 id（screen_registry 的 nav.* / labyrinth.* 等）
  边   = 一个可执行动作（标签=动作描述、cost=1 步）

【等价最优路径的选择策略】（BFS 同层按边表声明顺序扩展）
  1. 底部直达按钮 > ESC 退回 > 卡片滚动查找（边表顺序即优先级）
     —— 少一次转场、少一次滚动风险，同为 N 步时优先更稳的动作；
  2. 仍等价 → 取边表声明顺序靠前者（动作序列的字典序最小）；
  3. 规划结果写入日志（路径 + 总步数 + 选择说明），便于人工复核。

【鲁棒性】规划是"一次性计算 + 反应式执行"：执行途中每帧重新探测，
若实际画面偏离预期路径（弹窗/转场/识别抖动），以当前画面为新起点**重规划**。
────────────────────────────────────────────
"""

from collections import deque
from typing import Dict, List, Optional, Tuple

# 边表：from → [(to, action_key, action_label)]
# action_key 供执行器识别动作类型；顺序 = 同层优先级（先声明优先扩展）
NAV_EDGES: Dict[str, List[Tuple[str, str, str]]] = {
    # 0 层主世界/家园：底部按钮直达 1 层；右下按钮场景互切
    'nav.world_scene': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.main_menu', 'menu.open', '主菜单按钮'),
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
        ('nav.home_scene', 'scene.home', '右下「家园」按钮'),
    ],
    'nav.home_scene': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.main_menu', 'menu.open', '主菜单按钮'),
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
        ('nav.world_scene', 'scene.world', '右下「世界」按钮'),
    ],
    # 兼容旧识别（0 层未细分时的兜底 id）
    'nav.world_home': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.main_menu', 'menu.open', '主菜单按钮'),
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
    ],
    # 1 层玩法目录：点卡片进 2 层；ESC 退 0 层；公会互达
    'nav.playbook': [
        ('nav.idle_stage', 'card.idle_stage', '卡片「挂机关卡」'),
        # v2.1.16（用户 12:57）：1 层底部 UI 栏互通
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.labyrinth', 'card.labyrinth', '卡片「异界迷宫」'),
        ('nav.goddess_trial', 'card.goddess_trial', '卡片「女神试炼」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
        ('nav.world_scene', 'esc', 'ESC 退回 0 层'),
        ('nav.home_scene', 'esc', 'ESC 退回 0 层'),
    ],
    # 活动页签 → 常规玩法页签
    'nav.playbook_activity': [
        ('nav.playbook', 'tab.normal', '页签「常规玩法」'),
    ],
    # 1 层公会：底部玩法目录直达；ESC 退 0 层
    'nav.guild': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.world_scene', 'esc', 'ESC 退回 0 层'),
        ('nav.home_scene', 'esc', 'ESC 退回 0 层'),
    ],
    # 2 层玩法：ESC 退 1 层
    'nav.idle_stage': [('nav.playbook', 'esc', 'ESC 退回玩法目录')],
    'nav.labyrinth': [('nav.playbook', 'esc', 'ESC 退回玩法目录')],
    'nav.goddess_trial': [('nav.playbook', 'esc', 'ESC 退回玩法目录')],

    # ── v1.9.58 补全：注册表 35 画面中此前无出边的 22 个 ──────────────
    #    （完备性断言见 tests/test_nav_edges_coverage.py）
    #    设计原则：层级递减优先——玩法内部/子页面主出边 = ESC 退回上一层；
    #    次优先 ESC 退回玩法目录兜底（上一层识别失败时仍可收敛）。
    # ────────────────────────────────────────────────────────────────

    # 2 层·异界迷宫主页：ESC 退回 1 层玩法目录（主，最稳）；「进入」进迷宫内部
    'nav.labyrinth_main': [
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
        ('labyrinth.door', 'click.enter', '点「进入」进迷宫内部'),
    ],
    # 2 层·女神选关页：ESC 退回 1 层玩法目录
    'goddess.select_page': [
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    # 2 层·推图副本页（本域无内部节点）：ESC 退回 1 层玩法目录
    'labyrinth.push_map': [
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    # 1 层·玩法目录·活动页：点「常规玩法」页签回常规目录
    'nav.activity': [
        ('nav.playbook', 'tab.normal', '页签「常规玩法」'),
    ],

    # 3 层·迷宫内部（13 个画面）：ESC 退回 2 层迷宫主页（主）；
    #      次优先 ESC 退回玩法目录兜底（迷宫主页识别失败时用）
    'labyrinth.door': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.battle_prep': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.imprint': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.imprint_fallback': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.shop': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.shop_fallback': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.door_fallback': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.victory': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.boss_cleared': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.final_settle': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.leave_settle': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.failure': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'labyrinth.end_confirm': [
        ('nav.labyrinth_main', 'esc', 'ESC 退回异界迷宫主页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],

    # 3 层·家园子页面（愿望单）：ESC 退回家园场景（主）；次优先退回世界场景
    'home.wishlist': [
        ('nav.home_scene', 'esc', 'ESC 退回家园'),
        ('nav.world_scene', 'esc', 'ESC 退回世界'),
    ],

    # 3 层·女神内部（4 个画面）：ESC 退回 2 层女神选关页（主）；
    #      次优先 ESC 退回玩法目录兜底
    'goddess.victory_result': [
        ('goddess.select_page', 'esc', 'ESC 退回女神选关页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'goddess.sweep_dialog': [
        ('goddess.select_page', 'esc', 'ESC 退回女神选关页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'goddess.battle_prep': [
        ('goddess.select_page', 'esc', 'ESC 退回女神选关页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'goddess.auto_battle': [
        ('goddess.select_page', 'esc', 'ESC 退回女神选关页'),
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],

    # ── v2.1.16（用户 12:57 规范）：1 层底部 UI 栏四入口 + 主菜单 ──
    #   规则：1 层可一步前往其他 1 层或世界；
    #   主菜单无底部 UI 栏 → 只能退回 0 层
    'nav.mystery_house': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.hero_hall', 'bottom.hero_hall', '底部「英雄厅堂」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
        ('nav.world_scene', 'esc', 'ESC 退回 0 层'),
        ('nav.home_scene', 'esc', 'ESC 退回 0 层'),
    ],
    'nav.hero_hall': [
        ('nav.playbook', 'bottom.playbook', '底部「玩法目录」'),
        ('nav.mystery_house', 'bottom.mystery', '底部「神秘屋」'),
        ('nav.guild', 'bottom.guild', '底部「公会」'),
        ('nav.world_scene', 'esc', 'ESC 退回 0 层'),
        ('nav.home_scene', 'esc', 'ESC 退回 0 层'),
    ],
    # 主菜单面板：**无底部 UI 栏** → 先回 0 层才能去其他 1 层（用户定案）
    'nav.main_menu': [
        ('nav.friend', 'menu.friend', '菜单项「社交」'),
        ('nav.world_scene', 'esc', 'ESC/返回箭头 退回 0 层'),
        ('nav.home_scene', 'esc', 'ESC/返回箭头 退回 0 层'),
    ],

    # 主菜单子页：好友界面（收赠友情点执行域）——**有稳定返回路径**，
    # 故建出边（v2.1.9 复审修正：原先误列入"系统弹层无出边"）
    'nav.friend': [
        # v2.1.16（用户真机确认 12:57）：好友界面按一次 ESC → 回到「主菜单面板」
        ('nav.main_menu', 'esc', 'ESC 回主菜单面板'),
        ('nav.world_scene', 'esc', 'ESC 退回世界'),
        ('nav.home_scene', 'esc', 'ESC 退回家园'),
    ],
    # v2.1.26（用户 18:57 真机）：任务界面（主菜单·任务 进入的子页）——
    # 此前**完全未注册** → 停在非「每日任务」标签时全局导航判 unknown，
    # 每日任务任务反复「未识别超限→脱离→回玩法目录」两轮后失败。
    # 与 nav.friend 同构（同为 2 层主菜单子页）：ESC 回主菜单面板。
    'nav.task_page': [
        ('nav.main_menu', 'esc', 'ESC 回主菜单面板'),
        ('nav.world_scene', 'esc', 'ESC 退回世界'),
        ('nav.home_scene', 'esc', 'ESC 退回家园'),
    ],
    # v2.1.15：家园工坊内部（3 层）→ ESC 退回家园/世界
    'home.workshop': [
        ('nav.home_scene', 'esc', 'ESC 退回家园'),
        ('nav.world_scene', 'esc', 'ESC 退回世界'),
    ],

    # v2.1.15：竞技场（2 层）→ ESC 回玩法目录
    'arena.main': [
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],
    'arena.select': [
        ('nav.playbook', 'esc', 'ESC 退回玩法目录'),
    ],

    # ── 系统弹层：不入路径图，由统一清理逻辑处理（无需出边）────────────
    #    以下 5 个注册画面为覆盖层/过渡态，无稳定的"上一层"语义，
    #    统一由清理逻辑（点 X / 等待 / 领奖）消除，故不为其建立出边：
    #      nav.quit_confirm   退出确认弹窗（ESC 陷阱，点 X 取消）
    #      nav.loading        加载界面（动作=等待）
    #      nav.login          游戏登录界面（重启/掉线后停此页）
    #      nav.monthly_reward 伊索米亚月刊弹窗（动作=领取奖励）
    #      home.res_short     一键家园·资源不足弹窗（材料耗尽提示）
    # ────────────────────────────────────────────────────────────────
}

# 导航目标 → 目标画面 id
TARGET_SCREENS: Dict[str, str] = {
    'world_home': 'nav.world_scene',   # 0 层任一场景（细分前兼容）
    'world_scene': 'nav.world_scene',
    'home_scene': 'nav.home_scene',
    'playbook': 'nav.playbook',
    'guild': 'nav.guild',
    'idle_stage': 'nav.idle_stage',
    'labyrinth': 'nav.labyrinth',
    'goddess_trial': 'nav.goddess_trial',
}

# 目标画面的等价集合（到达任一即算到达）
TARGET_ALIASES: Dict[str, Tuple[str, ...]] = {
    'world_home': ('nav.world_scene', 'nav.home_scene', 'nav.world_home'),
}


def plan_path(start: str, goal: str) -> Optional[Tuple[List[Tuple[str, str, str]], int]]:
    """BFS 求 start → goal 的最少步数路径。

    Args:
        start: 当前画面 id
        goal:  目标画面 id（或 TARGET_SCREENS 的目标别名）

    Returns:
        (path, steps)：path = [(to_screen, action_key, action_label), ...]
        起点即目标 → ([], 0)；不可达 → None
    """
    goal_screen = TARGET_SCREENS.get(goal, goal)
    goals = set(TARGET_ALIASES.get(goal, (goal_screen,)))
    goals.add(goal_screen)
    if start in goals:
        return [], 0

    # BFS：同层按边表顺序扩展（= 优先级），首次到达即「最少步数 +
    # 同层优先级最高」的路径 → 等价路径选择策略见模块 docstring
    prev: Dict[str, Tuple[str, str, str]] = {}
    seen = {start}
    q: deque = deque([start])
    while q:
        cur = q.popleft()
        for to, act, label in NAV_EDGES.get(cur, ()):
            if to in seen:
                continue
            seen.add(to)
            prev[to] = (cur, act, label)
            if to in goals:
                # 回溯路径
                path: List[Tuple[str, str, str]] = []
                node = to
                while node != start:
                    p, a, lb = prev[node]
                    path.append((node, a, lb))
                    node = p
                path.reverse()
                return path, len(path)
            q.append(to)
    return None


def describe_plan(start: str, goal: str) -> str:
    """规划结果的可读日志（路径 + 总步数 + 策略说明）。"""
    r = plan_path(start, goal)
    if r is None:
        return f"[规划] {start} → {goal}：无可达路径（图未覆盖）"
    path, steps = r
    if not path:
        return f"[规划] 已在目标画面（{start}），0 步"
    chain = ' → '.join([start] + [f'{to}({label})' for to, _, label in path])
    return (f"[规划] 最优路径 {steps} 步：{chain}"
            f"（同层等价路径优先底部直达 > ESC > 卡片）")
