# -*- coding: utf-8 -*-
"""全局导航系统 · 全场景矩阵验证（v1.8.38）

思路：不是预排 OCR 帧，而是用「虚拟游戏状态机」——GlobalNavigator 每执行
一个动作（ESC/点玩法目录/点卡片/滚动/点进入），模拟器按游戏规则转移当前
画面，OCR 返回转移后的真实画面。这样验证的是导航逻辑能否真正收敛。

矩阵：起点画面（19 个已录入界面）× 目标（5 个）= 95 条组合。
断言：每条组合最终到达目标（返回 True）且迭代数不超限。
"""
import sys
import threading

sys.path.insert(0, r'C:\Users\x9502\WorkBuddy\20260320211537')

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.global_nav import GlobalNavigator  # noqa: E402
from tasks.screen_registry import (  # noqa: E402
    GLOBAL_SCREEN_REGISTRY, zh_screen,
)

# ── 虚拟游戏：每个画面的 OCR 词表 ──────────────────────────────
FRAMES = {
    'nav.world_home': ['神秘屋', '玩法目录', '英雄厅堂', '公会'],
    'nav.guild': ['庇护之地', '商店', '公会挑战', '公会管理',
                  '玩法目录', '英雄厅堂'],   # 底栏常驻（底部直达模型）
    'nav.playbook': ['玩法目录', '挂机关卡', '女神试炼', '迷梦之域',
                     '竞技场', '巅峰联赛', '常规玩法', '活动',
                     '异界迷宫'],
    'nav.activity': ['活动', '常规玩法', '玩法目录'],
    # v1.8.83：玩法目录·活动页签（真机 2026-09-08 17:58 截图词表；
    # 点「常规玩法」切页签 → nav.playbook）
    'nav.playbook_activity': ['热点活动', '灾厄征伐', '当前活动',
                              '常规玩法', '活动', '玩法目录',
                              '神秘屋', '英雄厅堂', '公会'],
    'nav.idle_stage': ['赛季挂机关卡', '已完成全部挑战', '英雄厅堂'],
    'nav.labyrinth_main': ['异界迷宫', '探索阵容(12/12)', '进入'],
    'goddess.select_page': ['魔力秘藏', '已达今日可挑战层数上限',
                            '扫荡', '魔饰总览', '详情'],
    'goddess.battle_prep': ['强攻秘藏-第56层', '自动挑战', '战斗', '秘藏'],
    'goddess.victory_result': ['战斗胜利', '首次通关', '再次挑战'],
    'goddess.sweep_dialog': ['扫荡次数', '奖励预览', '扫荡'],
    'goddess.auto_battle': ['自动挑战中'],
    'labyrinth.door': ['难度15', '结束探索', '遗物之门'],
    'labyrinth.battle_prep': ['难度15', '4/15', '战斗', '守护水晶'],
    'labyrinth.imprint': ['选择烙印', '稀有', '史诗', '确认'],
    'labyrinth.shop': ['商品列表', '进去看看', '继续前进'],
    'labyrinth.boss_cleared': ['难度通关', '结束探索'],
    'labyrinth.final_settle': ['探索完成', '点击屏幕跳过', '到达层数: 20'],
    # v1.8.87：离开迷宫荣誉结算页（战斗失败收尾后的结算，真机 21:09 截图）
    'labyrinth.leave_settle': ['难度 15', '离开迷宫', '到达层数:17',
                               '荣誉点数', '点击屏幕跳过'],
    'labyrinth.push_map': ['赛季先锋关卡588', '自动挑战', '战斗', '通关阵容'],
    'unknown': ['乱码画面'],
}

# 卡片点击 → 目标画面（玩法目录内）
CARD_TO_SCREEN = {
    '挂机关卡': 'nav.idle_stage',
    '异界迷宫': 'nav.labyrinth_main',
    '女神试炼': 'goddess.select_page',
}

# 画面的所属层（与 global_nav._SCREEN_LAYER 一致）
LAYER = {
    'nav.world_home': 0, 'nav.guild': 1,   # v1.8.48：公会=1 层
    'nav.playbook': 1, 'nav.activity': 1,
    'nav.playbook_activity': 1,   # v1.8.83
    'nav.idle_stage': 2, 'nav.labyrinth_main': 2,
    'goddess.select_page': 2,
}


class SimBot:
    """虚拟游戏 bot：状态机 + 动作转移。"""

    def __init__(self, start: str):
        self.state = start
        self.calls: list = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append(f'ocr:{self.state}')
        texts = FRAMES.get(self.state, ['乱码画面'])
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, kw, min_conf=0.5):
        for it in items:
            if kw in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click:{label}')
        # v1.8.83：玩法目录·活动页签点「常规玩法」→ 切到常规页签
        if label == '常规玩法' and self.state == 'nav.playbook_activity':
            self.state = 'nav.playbook'
            return
        # 底部 UI 直达：0/1 层点「玩法目录/公会」→ 对应 1 层页
        # （注意：nav.playbook_activity 点底部「玩法目录」无效——真机
        # 2026-09-08 实测，故不入转移表，误走此路会超时暴露）
        if label == '玩法目录' and self.state in (
                'nav.world_home', 'nav.guild', 'nav.activity'):
            self.state = 'nav.playbook'
            return
        if label == '公会' and self.state in (
                'nav.world_home', 'nav.playbook', 'nav.activity'):
            self.state = 'nav.guild'
            return
        # 玩法目录点卡片 → 进入对应 2 层页
        if label in CARD_TO_SCREEN and self.state == 'nav.playbook':
            self.state = CARD_TO_SCREEN[label]

    def press_esc(self, label=''):
        self.calls.append('esc')
        layer = LAYER.get(self.state, 3)
        # 层级下降：3→2（回到本玩法的入口页）、2→1、1→0
        if layer >= 3:
            if self.state.startswith('labyrinth.'):
                self.state = 'nav.labyrinth_main'
            elif self.state.startswith('goddess.'):
                self.state = 'goddess.select_page'
            else:
                self.state = 'nav.playbook'
        elif layer == 2:
            self.state = 'nav.playbook'
        elif layer == 1:
            self.state = 'nav.world_home'
        # guild 已改 1 层：layer==1 分支自动处理（ESC → 世界）

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append('back')
        self.press_esc('back')
        return (500, 500)

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append('scroll')   # 玩法目录滚动：画面不变（模拟已到底）


# ── 矩阵执行 ──────────────────────────────────────────────────
STARTS = [
    'nav.world_home', 'nav.guild', 'nav.playbook', 'nav.activity',
    'nav.playbook_activity',   # v1.8.83：活动页签
    'nav.idle_stage', 'nav.labyrinth_main', 'goddess.select_page',
    'goddess.battle_prep', 'goddess.victory_result', 'goddess.sweep_dialog',
    'goddess.auto_battle', 'labyrinth.door', 'labyrinth.battle_prep',
    'labyrinth.imprint', 'labyrinth.shop', 'labyrinth.boss_cleared',
    'labyrinth.final_settle', 'labyrinth.leave_settle',   # v1.8.87
    'labyrinth.push_map', 'unknown',
]
TARGETS = ['playbook', 'idle_stage', 'labyrinth', 'goddess_trial',
           'world_home']

# v1.8.45：点卡片后的过场动画场景（unknown 若干帧 → 目标页）
TRANSITION_CASES = [
    ('nav.playbook', 'labyrinth'),
    ('nav.playbook', 'goddess_trial'),
]

passed, failed, rows = 0, [], []
for start in STARTS:
    for target in TARGETS:
        bot = SimBot(start)
        nav = GlobalNavigator(
            bot, logger=lambda m: None, stop_check=lambda: False,
            max_iter=14, poll=0.0,
        )
        try:
            ok = nav.navigate_to(target)
        except Exception as e:
            ok = False
            bot.calls.append(f'EXC:{e}')
        final = bot.state
        # 到达判定与生产 _arrived 一致
        tgt_layer, card = {
            'playbook': (1, ''), 'world_home': (0, ''),
            'idle_stage': (2, '挂机关卡'), 'labyrinth': (2, '异界迷宫'),
            'goddess_trial': (2, '女神试炼'),
        }[target]
        arrived = (
            LAYER.get(bot.state) == tgt_layer
            and (not card or bot.state in
                 ('nav.idle_stage', 'nav.labyrinth_main',
                  'goddess.select_page'))
        )
        status = ok and (arrived or final == target)
        if status:
            passed += 1
            rows.append(f'  ✅ {start} → {target}（{len(bot.calls)} 动作）')
        else:
            failed.append(f'{start} → {target}')
            rows.append(
                f'  ❌ {start} → {target} ok={ok} 终态={bot.state} '
                f'动作={bot.calls[:8]}'
            )

# 过场动画专项：unknown×3 帧（转场中）→ 目标页（应等待而非打断）
for start, target in TRANSITION_CASES:
    bot = SimBot(start)
    bot.t = [['乱码画面']] * 3 + [FRAMES[CARD_TO_SCREEN[
        {'labyrinth': '异界迷宫', 'goddess_trial': '女神试炼'}[target]]]]
    orig_ocr = bot._ocr_screen
    def ocr(img=None, _b=bot):
        if _b.t:
            t = _b.t.pop(0)
            return [(100, 200, x, 0.9, 150, 250) for x in t]
        return orig_ocr()
    bot._ocr_screen = ocr
    nav = GlobalNavigator(
        bot, logger=lambda m: None, stop_check=lambda: False,
        max_iter=14, poll=0.0,
    )
    ok = nav.navigate_to(target)
    status = ok and not any('esc' in c for c in bot.calls)
    if status:
        passed += 1
        rows.append(f'  ✅ 过场动画 {start} → {target}（等待未打断）')
    else:
        failed.append(f'过场动画 {start} → {target}')
        rows.append(f'  ❌ 过场动画 {start} → {target} 动作={bot.calls}')

print(f'══ 全局导航矩阵：通过 {passed}/{len(STARTS) * len(TARGETS)} ══')
for r in rows:
    if r.strip().startswith('❌'):
        print(r)
if failed:
    print('失败组合：', failed)
sys.exit(0 if not failed else 1)
