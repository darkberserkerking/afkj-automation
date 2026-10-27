"""v1.8.13 赛季挂机关卡页识别回归测试（用户真机截图 OCR 实测词）。

背景：赛季挂机关卡页（已完成全部挑战 + 赛季挂机关卡 + 英雄厅堂）无独立
「挑战/幻灵挑战」按钮 → 旧 idle_stage 判据落空；「赛季挂机关卡」含
playbook 子串「挂机关卡」→ 误判 playbook → 导航反复点卡片标题死循环。
修复：idle_stage 判据加赛季页正向特征词（已完成全部挑战/赛季挂机关卡）。

运行：python tests/test_detector_season_idle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afk_bot.services.detector import PhaseDetector

_PASS = []
_FAIL = []


def check(name, cond, detail=''):
    if cond:
        _PASS.append(name)
        print(f'✅ {name}' + (f' — {detail}' if detail else ''))
    else:
        _FAIL.append(name)
        print(f'❌ {name} — {detail}')


def _items(texts):
    """(cy, cx, text, conf, x0, x1) 6 元组，坐标固定。"""
    return [(100.0, 200.0, t, 0.9, 150, 250) for t in texts]


def _detector():
    d = PhaseDetector(None, None, log_callback=None)
    d._cache_has_formation = False
    d._cache_has_prep = False
    return d


# 用户真机截图（clipboard-2026-09-04T06-54-32）MaaOCR 实测词（conf≥0.3）
SEASON_IDLE_TEXTS = _items([
    '英雄厅堂', '已完成全部挑战', '00:00:38', '50',
    '先锋之炎', '先锋关卡', '阶段3', '赛季挂机关卡',
    '1800', '1799', '1786', '13', '阶段3增益等级：11级',
    '赛季阶段3结算：18天17小时', 'Janon', '第1名',
])

d1 = _detector()
r1 = d1._detect_navigation_phase(None, SEASON_IDLE_TEXTS, 1280, 1024)
check('赛季挂机关卡页 → idle_stage（不再误判 playbook）', r1 == 'idle_stage',
      f'判定={r1}')

# 仅「已完成全部挑战」（标题 OCR 偶发漏检时仍能兜底）
d2 = _detector()
r2 = d2._detect_navigation_phase(None, _items(
    ['已完成全部挑战', '英雄厅堂', '00:00:38']), 1280, 1024)
check('仅「已完成全部挑战」→ idle_stage', r2 == 'idle_stage', f'判定={r2}')

# 普通挂机页（独立挑战按钮）不回归
d3 = _detector()
r3 = d3._detect_navigation_phase(None, _items(
    ['挂机关卡', '幻灵挑战', '挑战', '阶段3']), 1280, 1024)
check('普通挂机页（幻灵挑战按钮）→ idle_stage（回归）', r3 == 'idle_stage',
      f'判定={r3}')

# 真玩法目录仍判 playbook（回归；v1.8.14 计数式判据——真实首屏卡片词）
d4 = _detector()
r4 = d4._detect_navigation_phase(None, _items(
    ['常规玩法', '活动', '挂机关卡', '女神试炼', '迷梦之域',
     '竞技场', '巅峰竞技场', '进入']), 1280, 1024)
check('真玩法目录首屏（卡片A词表 6 命中）仍判 playbook（回归）',
      r4 == 'playbook', f'判定={r4}')

# 赛季备战界面（赛季先锋关卡 + 编队特征）不误判 idle_stage：
# 真实流程中 detect_phase 先跑战斗检测置缓存标志，这里手动模拟。
# 注：未命中任何早期返回时函数会继续走 0 层评估（需真实 screen），
# 故捕获后续异常——断言点仅为「未返回 idle_stage」。
d5 = _detector()
d5._cache_has_formation = True
d5._cache_has_prep = True
try:
    r5 = d5._detect_navigation_phase(None, _items(
        ['赛季先锋关卡588', '先锋之炎', '编队', '出战阵容', '战斗']),
        1280, 1024)
except AttributeError:
    r5 = 'fell_through'  # 未命中 idle_stage 早退（走到深层评估才报错）
check('赛季备战界面（有编队特征）不误判 idle_stage', r5 != 'idle_stage',
      f'判定={r5}')

# v1.8.14 计数式 playbook 判据（用户设计：卡片A/B/C + UI栏词表，命中 >4）
# 首屏（卡片A 词表 6 命中）→ playbook
d6 = _detector()
r6 = d6._detect_navigation_phase(None, _items(
    ['常规玩法', '活动', '挂机关卡', '女神试炼', '迷梦之域',
     '竞技场', '巅峰竞技场', '进入']), 1280, 1024)
check('v1.8.14 玩法目录首屏（卡片A 6命中）→ playbook', r6 == 'playbook',
      f'判定={r6}')

# 滚动到 B 区（挂机关卡已滚出屏幕，旧单子串判据会失效）→ 仍判 playbook
d7 = _detector()
r7 = d7._detect_navigation_phase(None, _items(
    ['常规玩法', '活动', '异界迷宫', '公会征锋', '天穹试炼',
     '沙盘演兵', '荣誉对决', '公平竞技']), 1280, 1024)
check('v1.8.14 滚动到卡片B区（8命中）→ playbook（修复滚出失效）',
      r7 == 'playbook', f'判定={r7}')

# 0 层底栏常驻 chrome（神秘屋/玩法目录/英雄厅堂/公会）→ 0 命中，不判 playbook
d10 = _detector()
try:
    r10 = d10._detect_navigation_phase(None, _items(
        ['神秘屋', '玩法目录', '英雄厅堂', '公会']), 1280, 1024)
except AttributeError:
    r10 = 'fell_through'
check('v1.8.14 0层底栏 chrome（0命中）→ 不判 playbook',
      r10 != 'playbook', f'判定={r10}')

# 迷宫主页（异界迷宫卡 1 命中 + 探索阵容）→ labyrinth 不变（回归）
d11 = _detector()
r11 = d11._detect_navigation_phase(None, _items(
    ['异界迷宫', '探索阵容(5/12)', '进入', '难度15']), 1280, 1024)
check('v1.8.14 迷宫主页仍判 labyrinth（回归）', r11 == 'labyrinth',
      f'判定={r11}')

# 兜底排除：卡片词命中但含 2 级页特征词（结束探索）→ 不判 playbook
d8 = _detector()
try:
    r8 = d8._detect_navigation_phase(None, _items(
        ['挂机关卡', '结束探索', '难度15']), 1280, 1024)
except AttributeError:
    r8 = 'fell_through'
check('v1.8.14 卡片词+2级特征词（结束探索）→ 不判 playbook',
      r8 != 'playbook', f'判定={r8}')

# 双保险第一层（顺序拦截）：「赛季挂机关卡」页 → idle_stage 优先返回，
# 根本落不到 playbook 判据；赛季页仅「挂机关卡」子串 1 命中也远低于阈值
d9 = _detector()
r9 = d9._detect_navigation_phase(None, _items(
    ['赛季挂机关卡', '英雄厅堂']), 1280, 1024)
check('v1.8.14 「赛季挂机关卡」页 → idle_stage（顺序拦截，不落 playbook）',
      r9 == 'idle_stage', f'判定={r9}')

print('\n' + '=' * 54)
print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print(f'  ❌ 失败项：{_FAIL}')
else:
    print('  ✅ 赛季挂机关卡识别测试全部通过')
print('=' * 54)

sys.exit(0 if not _FAIL else 1)
