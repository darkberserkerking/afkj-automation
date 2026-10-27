"""v1.8.5：异界迷宫「整轮 1→15 层」干跑（dry-run）逻辑测试。
（v1.8.6 更新：迭代上限已取消 while True，断言改为固定防死循环上界）

目标：验证用户定义的完整流程「进入 → 打通 1-15 层 → 结束探索退出」
在迭代上限取消（v1.8.6 起 while True）下能正常跑完并返回 done。
防死循环保障：texts_by_call 耗尽后 MockBot 返回兜底文本 → _detect_play_state
判 unknown → 累积达 _UNKNOWN_GIVE_UP(10) → failed；测试另加固定迭代上界
断言（若状态机 bug 致死循环，迭代数会远超正常 ~60-71 次）。

不依赖真机：MockBot 按脚本序列返回 OCR 文本（机制同
test_labyrinth_nav_dryrun.py——texts_by_call 每次 _ocr_screen() 弹 1 条）。

OCR 消耗模型（每轮状态判定 = 1 条 texts；战斗等待在子方法内部轮询，
不耗主循环迭代）：
  普通层(1-14)：door(1) → battle_prep(1) → 战斗轮询命中胜利(1)
               → 主循环 victory 关弹层(1) → 烙印判定(1) → 烙印确认钮(1) = 6 条
  Boss 层(15)： door(1) → battle_prep(1) → 轮询命中「难度通关」(1)
               → 主循环 boss_cleared(1) → _click_text(结束探索)(1) → 确认探测(1) = 6 条
  前导(导航+进入)：3 条（同 nav 用例 13）

用例 A：14 普通层 + 1 Boss 层净流转（93 条）→ 断言 done 与各动作计数
        （门卡×15 / press_f×15 / 烙印点卡×14 / 结束探索×1 / 无 ESC）。
用例 B：层间插入 unknown 转场 + 第 8 层改菲茨小店（不购物退出）→
        断言 done、ESC 0 次（unknown 未误触发）、主循环迭代数 < 200（防死循环）。

运行约 90-110s（含状态轮询 sleep），请给足超时：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_labyrinth_full_round_dryrun.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.labyrinth import (            # noqa: E402
    LabyrinthTask,
)

# 迭代上限取消后的防死循环断言上界（正常整轮 59/71 次，留足裕量；
# 真机 unknown 转场多轮时 200 也被突破过 → 此值仅作测试回归防死循环用）
_ITER_CEIL: int = 200

# ── 结果收集 ────────────────────────────────────────────────

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


# ── Mock bot（同 nav 干跑，裁剪至本文件所需）──────────────

class MockBot:
    """按脚本返回 phase / OCR 结果，记录全部调用与点击。"""

    def __init__(self, phases, texts_by_call=None, template_hit=True):
        self._phases = list(phases)
        self._texts_by_call = list(texts_by_call or [])
        self._template_hit = template_hit
        self.calls: list = []
        self.clicked: list = []

    def detect_phase(self):
        self.calls.append('detect_phase')
        return self._phases.pop(0) if self._phases else 'labyrinth'

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            texts = self._texts_by_call.pop(0)
        else:
            texts = ['玩法目录', '常规玩法', '活动']   # 兜底（正常不应被消费）
        # 6 元组 (cy, cx, text, conf, x_min, x_max)
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, keyword, min_conf=0.5):
        for it in items:
            if keyword in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicked.append(label)

    def click_ratio(self, rx, ry, label=''):
        self.calls.append(f'click_ratio:{label}@{rx},{ry}')
        self.clicked.append(label)

    def press_f(self, label=''):
        self.calls.append(f'press_f:{label}')

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def template_match(self, tpl_path, threshold=0.8, scale_range=(0.5, 2.0, 0.1),
                       silent=False, region=None):
        name = os.path.basename(tpl_path)
        self.calls.append(f'template_match:{name}')
        return (500, 500) if self._template_hit else None

    def _click_abs(self, cx, cy):
        self.calls.append(f'_click_abs:({cx},{cy})')
        self.clicked.append(f'模板@{cx},{cy}')

    def screen_to_image(self, screen_x, screen_y):
        return (screen_x, screen_y)

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append(f'scroll_wheel:{clicks}')

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append('click_back_button')
        return (500, 500)

    def find_and_focus(self):
        return 12345

    def _handle_common_phase(self, phase):
        self.calls.append(f'common:{phase}')
        return phase


def _make_task(mock_bot, logs):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()                 # 未暂停
    ctx = {
        'bot': mock_bot,
        'logger': logs.append,
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': {},
        'max_time': None,          # 不受时间上限干扰
        
        'inst_id': 'labyrinth#full-round',
    }
    return LabyrinthTask(ctx)


# ── 15 层序列构造 ───────────────────────────────────────────

_PRE_TEXTS = [                            # 前导（v1.8.32 全局导航版）
    ['难度15', '探索阵容(5/12)'],         # 阶段0 启动检查：无残留弹窗
    ['难度15', '探索阵容(5/12)'],         # 全局导航判定帧：迷宫主页 → 到达
    ['进入'],                             # _enter_labyrinth 点「进入」
    ['难度15', '探索阵容(12/12)'],        # 点进入后弹窗探测：无
]

_DOOR = ['难度15', '结束探索', '遗物之门']
_DOOR_SHOP = ['难度15', '结束探索', '菲茨小店']


def _normal_layer():
    """普通战斗层 6 条：选门→F开战→胜利弹层→烙印（判定+确认）。"""
    return [
        list(_DOOR),                  # door → 按优先级点遗物之门按钮
        ['难度15', '4/15', '战斗', 'F'],                # battle_prep → 按 F
        ['战斗胜利'],                 # 战斗轮询命中胜利弹层（子方法返回）
        ['战斗胜利'],                 # 主循环：点空白关闭弹层
        # v2.2.10：卡名写进**弹层帧**（真实帧卡名一出现即可读）——
        # 旧脚本把名字放在点后帧，与"先读名再选择"的新时序不符
        ['选择烙印', '稀有', '藏品柜', '确认'],  # 烙印判定 → 读名 → 点第一个稀有度卡
        ['选择烙印', '寒风警告', '确认'],   # 点卡后 OCR → 点「确认」钮
                                      # （v1.9.34 卡名读取源帧）
    ]


def _shop_layer():
    """菲茨小店层 3 条：选小店门 → 判定 shop → 点「继续前进」退出（不购物）。"""
    return [
        list(_DOOR_SHOP),             # door → 优先级命中菲茨小店
        ['商品列表', '菲茨小店', '继续前进'],   # shop 判定
        ['商品列表', '菲茨小店', '继续前进'],   # _click_text('继续前进') 内部 OCR
    ]


def _boss_layer():
    """Boss 层 6 条：打完 Boss 直接弹「难度通关」结算（无 victory 弹层）。"""
    return [
        list(_DOOR),
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],     # 战斗轮询命中 boss_cleared（子方法返回）
        ['难度通关', '结束探索'],     # 主循环 boss_cleared → 点「结束探索」
        ['难度通关', '结束探索'],     # _click_text('结束探索') 内部 OCR → 点击
        [],                           # 确认弹窗探测：无
    ]


def _count(bot, label):
    return sum(1 for c in bot.clicked if c == label)


def _count_ratio(bot, label):
    return sum(1 for c in bot.calls if c.startswith(f'click_ratio:{label}'))


def _count_at(bot, label):
    """v1.8.10：count click_at 标签（烙印首卡改用 OCR 实时坐标点击）。"""
    return sum(1 for c in bot.calls if c.startswith(f'click_at:{label}'))


def _iter_count(task):
    """主循环迭代次数 = 任务显式计数器 `_loop_iters`。

    v2.1.27：原实现用「玩法循环 |」**日志条数**当迭代数 —— 日志一旦做
    状态降噪（同状态不再每帧打印）该口径即失效，属于"把日志当 API"。
    改为读 `BaseTask.count_iter()` 维护的真实计数器。
    """
    return getattr(task, '_loop_iters', 0)


# ═══════════════════════════════════════════════════════════
#  用例 A：整轮净流转（14 普通层 + 1 Boss 层）
# ═══════════════════════════════════════════════════════════
print('── A. 整轮 1→15 层净流转 ──')
seq_a = list(_PRE_TEXTS)
for _ in range(14):
    seq_a += _normal_layer()
seq_a += _boss_layer()

logs_a: list = []
bot_a = MockBot(phases=['labyrinth'], texts_by_call=seq_a)
t_a = _make_task(bot_a, logs_a)
r_a = t_a._run_once()

check('整轮 15 层 run() 返回 done', r_a == 'done', f'result={r_a}')
check('点了 15 次门卡按钮（1-15 层各一）',
      _count(bot_a, '门卡_遗物之门_按钮') == 15,
      f'实际 {_count(bot_a, "门卡_遗物之门_按钮")}')
check('按了 15 次 F 开战',
      sum(1 for c in bot_a.calls if c.startswith('press_f')) == 15,
      str([c for c in bot_a.calls if 'press_f' in c]))
check('烙印稀有度卡点了 14 次（前 14 层，v1.8.20 优先级选卡）',
      _count_at(bot_a, '烙印_选卡') == 14,
      f'实际 {_count_at(bot_a, "烙印_选卡")}')
check('烙印「确认」点了 14 次', _count(bot_a, '烙印_确认') == 14,
      f'实际 {_count(bot_a, "烙印_确认")}')
check('胜利弹层关空白 14 次（前 14 层）',
      _count_ratio(bot_a, '胜利弹层_点空白关闭') == 14,
      f'实际 {_count_ratio(bot_a, "胜利弹层_点空白关闭")}')
check('「结束探索」只点 1 次（Boss 通关后）',
      _count(bot_a, '结束探索') == 1, f'实际 {_count(bot_a, "结束探索")}')
check('全程未按 ESC（无 unknown 干扰）',
      not any('press_esc' in c for c in bot_a.calls),
      str([c for c in bot_a.calls if 'esc' in c]))
n_iter_a = _iter_count(t_a)
check(f'主循环迭代 {n_iter_a} 次 < 防死循环上界 {_ITER_CEIL}',
      n_iter_a < _ITER_CEIL, f'实际迭代={n_iter_a}')
print(f'    [明细] 主循环迭代 {n_iter_a} 次（15层 × ~4 状态 + 转场）\n')

# ═══════════════════════════════════════════════════════════
#  用例 B：混入层间 unknown 转场 + 第 8 层菲茨小店
# ═══════════════════════════════════════════════════════════
print('── B. 15 层混入 unknown 转场 + 第 8 层菲茨小店 ──')
seq_b = list(_PRE_TEXTS)
for floor in range(1, 15):
    if floor == 8:
        seq_b += _shop_layer()
    else:
        seq_b += _normal_layer()
    seq_b += [['难度15']]          # 层间转场：仅层数标题，无状态特征词 → unknown
seq_b += _boss_layer()

logs_b: list = []
bot_b = MockBot(phases=['labyrinth'], texts_by_call=seq_b)
t_b = _make_task(bot_b, logs_b)
r_b = t_b._run_once()

check('混转场+小店 run() 返回 done', r_b == 'done', f'result={r_b}')
check('小店门按钮点了 1 次（第 8 层）',
      _count(bot_b, '门卡_菲茨小店_按钮') == 1,
      f'实际 {_count(bot_b, "门卡_菲茨小店_按钮")}')
check('「继续前进」点了 1 次（不购物退出小店）',
      _count(bot_b, '继续前进') == 1, f'实际 {_count(bot_b, "继续前进")}')
check('遗物之门按钮点了 14 次（13 普通层 + Boss 层）',
      _count(bot_b, '门卡_遗物之门_按钮') == 14,
      f'实际 {_count(bot_b, "门卡_遗物之门_按钮")}')
check('按 F 14 次（小店层无战斗）',
      sum(1 for c in bot_b.calls if c.startswith('press_f')) == 14,
      str([c for c in bot_b.calls if 'press_f' in c]))
check('全程未按 ESC（孤立 unknown 未触发 ESC 阈值 3）',
      not any('press_esc' in c for c in bot_b.calls),
      str([c for c in bot_b.calls if 'esc' in c]))
n_iter_b = _iter_count(t_b)
check(f'主循环迭代 {n_iter_b} 次 < 防死循环上界 {_ITER_CEIL}',
      n_iter_b < _ITER_CEIL, f'实际迭代={n_iter_b}')
print(f'    [明细] 主循环迭代 {n_iter_b} 次（含 14 条 unknown 转场 + 小店 3 条）\n')

# ── 汇总 ────────────────────────────────────────────────────
print(f'══ 整轮 1→15 层干跑测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f in _FAIL:
        print(f'  ❌ {f}')
sys.exit(0 if not _FAIL else 1)
