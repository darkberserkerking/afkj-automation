# -*- coding: utf-8 -*-
"""天穹试炼任务 · 干跑（dry-run）逻辑测试。

不依赖真机与游戏窗口：MockBot 按帧返回真机 OCR 文本，验证
「塔列表 / 塔内楼层 / 备战」三态判据、「出发！」「挑战！」匹配、
塔列表 → 出发 → 挑战 → 备战 → F → 返回 → 换塔 → done 的完整链路。

判据来源：用户真机截图 OCR（1282×1027 / 1280×1026）——
  塔列表：「选择目的地」0.994 ·「出发！」0.957 ·「不在开放时间内」0.985+
          「赛季意志之**场**」0.885（误读，实为「意志之塔」）
  塔内  ：「赛季不朽之塔」0.975 ·「挑战！」0.988 ·「未解锁」0.989
  目录  ：「天穹试炼」0.989 与「女神试炼」0.983 同屏 → 必须完整四字匹配

用法：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_tianqiong_trial_dryrun.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 虚拟时钟：sleep 不真等，但 deadline/timeout 语义不变
from tests import fast_time  # noqa: E402
fast_time.install()

from tasks.tianqiong_trial import TianqiongTrialTask  # noqa: E402

# ── 结果收集 ────────────────────────────────────────────────
_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


def it(cy: int, cx: int, text: str, conf: float = 0.95) -> tuple:
    """构造一条 OCR item：(cy, cx, text, conf, x1, x2)。"""
    return (cy, cx, text, conf, cx - 40, cx + 40)


# ── 真机 OCR 帧 ─────────────────────────────────────────────
PLAYBOOK = [   # 玩法目录（滚动前的首屏，卡片可能还不可见）
    it(923, 340, '常规玩法', 0.998),
    it(500, 640, '活动', 0.99),
    it(490, 460, '女神试炼', 0.983),
    it(779, 458, '天穹试炼', 0.989),
]

TOWER_LIST = [                     # 塔列表：当天有可进的塔
    it(261, 639, '选择目的地', 0.994),
    it(75, 346, '季天穹试炼', 0.980),
    it(701, 853, '出发！', 0.957),
    it(419, 510, '不在开放时间内', 0.990),
    it(324, 490, '赛季光辉之塔', 0.970),
    it(472, 482, '赛季自然之塔', 0.960),
    it(618, 510, '赛季不朽之塔', 0.970),
    it(774, 496, '赛季意志之场', 0.885),   # 误读：实为「意志之塔」
    it(352, 520, '当前层数：3', 0.980),
]

TOWER_LIST_CLOSED = [              # 塔列表：四塔全部「不在开放时间内」
    it(261, 639, '选择目的地', 0.994),
    it(75, 346, '季天穹试炼', 0.980),
    it(419, 510, '不在开放时间内', 0.991),
    it(572, 508, '不在开放时间内', 0.985),
    it(867, 518, '不在开放时间内', 0.989),
    it(324, 490, '赛季光辉之塔', 0.970),
    it(618, 510, '赛季不朽之塔', 0.970),
    it(774, 496, '赛季意志之场', 0.885),
]

FLOOR = [                          # 塔内楼层页：可挑战
    it(50, 347, '赛季不朽之塔', 0.975),
    it(106, 358, '还差2层', 0.993),
    it(673, 747, '挑战！', 0.988),
    it(864, 472, '已通关', 0.960),
    it(191, 724, '未解锁', 0.989),
    it(408, 540, '未解锁', 0.989),
]

FLOOR_DONE = [                     # 塔内楼层页：本塔已无可挑战层
    it(50, 347, '赛季不朽之塔', 0.975),
    it(106, 358, '还差2层', 0.993),
    it(864, 472, '已通关', 0.960),
    it(191, 724, '未解锁', 0.989),
]

PREP = [                           # 备战界面
    it(285, 972, '通关阵容', 0.990),
    it(942, 945, '战斗', 0.970),
    it(940, 971, 'F', 0.950),
]

PREP_ADOPT = [                     # 阵容弹窗
    it(694, 852, '一键采用', 0.960),
    it(639, 965, '点击空白处关闭', 0.950),
]

CHALLENGE_REWARD = [               # 「挑战奖励：」不得被当成挑战按钮
    it(500, 640, '挑战奖励：', 0.960),
    it(864, 472, '已通关', 0.960),
    it(191, 724, '未解锁', 0.989),
]


# ── Mock bot ────────────────────────────────────────────────
class MockBot:
    """按帧返回 OCR 结果，记录点击/按键（最后一帧循环复用）。"""

    def __init__(self, frames, stop_event=None, stop_on_click=None):
        self.frames = list(frames)
        self.i = 0
        self.clicks: list = []
        self.keys: list = []
        self.scrolls: int = 0
        self.backs: int = 0
        self._stop_event = stop_event
        self._stop_on_click = stop_on_click

    def _ocr_screen(self, img=None, no_cache=False):
        idx = min(self.i, len(self.frames) - 1)
        self.i += 1
        return list(self.frames[idx])

    def find_hwnd(self):
        """返回 0 → 视为无窗口，跳过退出确认弹窗扫描（不额外消耗帧）。"""
        return 0

    def find_and_focus(self):
        return 12345

    def find_text(self, items, keyword, min_conf=0.5, exact=False):
        for it_ in items:
            if len(it_) >= 4 and it_[3] >= min_conf and keyword in str(it_[2]):
                return (it_[1], it_[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.clicks.append((cx, cy, label))
        if self._stop_on_click and label == self._stop_on_click:
            if self._stop_event is not None:
                self._stop_event.set()
        return True

    def press_f(self, label=''):
        self.keys.append('F')

    def press_esc(self, label=''):
        self.keys.append('ESC')

    def scroll_wheel(self, clicks, rx=0.5, ry=0.5, label=''):
        self.scrolls += 1

    def click_back_button(self, tpls=None, threshold=0.75):
        self.backs += 1
        return (500, 500)

    def template_match(self, tpl, threshold=0.8, silent=True,
                       scale_range=None, region=None):
        return None

    def _click_abs(self, cx, cy, label=''):
        self.clicks.append((cx, cy, label))


def _make_task(frames, stop=False, stop_on_click=None, options=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    if stop:
        stop_ev.set()
    bot = MockBot(frames, stop_event=stop_ev, stop_on_click=stop_on_click)
    logs: list = []
    ctx = {
        'bot': bot,
        'logger': logs.append,
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': options or {},
        'max_time': None,
        'max_turns': None,
        'inst_id': 'tianqiong_trial#dry',
    }
    task = TianqiongTrialTask(ctx)
    task._logs = logs
    return task, bot


print('=' * 64)
print('天穹试炼 干跑验证（判据来自真机截图 OCR）')
print('=' * 64)

# ═══════════════════════════════════════════════════════════
#  1. 三态判据
# ═══════════════════════════════════════════════════════════
print('\n── 1. 三态判据 ──')
t1, _ = _make_task([TOWER_LIST])
check('1a 塔列表（选择目的地）→ True', t1._is_tower_list(TOWER_LIST))
check('1b 塔内楼层（未解锁+挑战）→ True', t1._is_floor_page(FLOOR))
check('1c 备战（通关阵容）→ True', t1._is_battle_prep(PREP))
check('1d 玩法目录不算塔列表', not t1._is_tower_list(PLAYBOOK),
      str([x[2] for x in PLAYBOOK]))
check('1e 玩法目录不算塔内/备战',
      not t1._is_floor_page(PLAYBOOK) and not t1._is_battle_prep(PLAYBOOK))
check('1f 塔内楼层不算塔列表（只有 1 个「之塔」）',
      not t1._is_tower_list(FLOOR))
# 「赛季意志之场」误读 → 「之场」也算塔名
TOWERS_ONLY = [it(324, 490, '赛季光辉之塔'), it(472, 482, '赛季自然之塔'),
               it(774, 496, '赛季意志之场', 0.885)]
check('1g 无「选择目的地」但 ≥2 塔名 → 仍判塔列表',
      t1._is_tower_list(TOWERS_ONLY))

# ═══════════════════════════════════════════════════════════
#  2. 「出发！」匹配
# ═══════════════════════════════════════════════════════════
print('\n── 2. _find_go ──')
t2, _ = _make_task([TOWER_LIST])
pos_go = t2._find_go(TOWER_LIST)
check('2a 命中「出发！」→ 返回 (cx, cy)', pos_go == (853, 701), str(pos_go))
check('2b 「不在开放时间内」不算可进入',
      t2._find_go(TOWER_LIST_CLOSED) is None)
# v2.3.23：「出发」仅两字、短文本 OCR 置信度波动大 → 阈值放宽到 0.6。
# 真机 19:38 就是卡 0.85 漏检后误判「今日无开放塔」直接结束。
check('2c 低置信 0.80 仍能识别（阈值已放宽到 0.6）',
      t2._find_go([it(701, 853, '出发！', 0.80)]) == (853, 701),
      str(t2._find_go([it(701, 853, '出发！', 0.80)])))
check('2d 置信度 <0.6 不点', t2._find_go(
    [it(701, 853, '出发！', 0.50)]) is None)
check('2e 非「出发」文本不点（如「出发时间」）',
      t2._find_go([it(701, 853, '出发时间', 0.95)]) is None)

# ═══════════════════════════════════════════════════════════
#  3. 「挑战！」匹配（排除「挑战奖励」「继续挑战」）
# ═══════════════════════════════════════════════════════════
print('\n── 3. _find_challenge ──')
t3, _ = _make_task([FLOOR])
pos_ch = t3._find_challenge(FLOOR)
check('3a 命中「挑战！」→ 返回 (cx, cy)', pos_ch == (747, 673), str(pos_ch))
check('3b 不误命中「挑战奖励：」', t3._find_challenge(CHALLENGE_REWARD) is None)
check('3c 不误命中「继续挑战」',
      t3._find_challenge([it(900, 640, '继续挑战', 0.96)]) is None)

# ═══════════════════════════════════════════════════════════
#  4. 端到端：目录 → 天穹试炼 → 出发 → 挑战 → 备战 → F
#     → 塔内无挑战 → 返回 → 塔列表无出发 → done
# ═══════════════════════════════════════════════════════════
print('\n── 4. 端到端 ──')
frames = (
    [PLAYBOOK]        # startup_probe（不在执行域）
    + [PLAYBOOK]      # 滚动找卡片 → 命中
    + [TOWER_LIST]    # 等塔列表
    + [TOWER_LIST]    # 主循环 → 点「出发！」
    + [FLOOR]         # v2.3.22 _wait_enter_tower：确认进入塔内
    + [FLOOR]         # 主循环 → 点「挑战！」
    + [PREP]          # 主循环 → 备战
    + [PREP]          # _setup_lineup：点通关阵容
    + [PREP_ADOPT]    # _setup_lineup：点一键采用
    + [FLOOR_DONE]    # _wait_result：已回塔内（已通关）
    # v2.3.22：无「挑战」需连续 3 帧确认（防转场误判）→ 给足 3 帧
    + [FLOOR_DONE]    # 无挑战确认 1/3
    + [FLOOR_DONE]    # 无挑战确认 2/3
    + [FLOOR_DONE]    # 无挑战确认 3/3 → _back
    + [TOWER_LIST_CLOSED]   # 主循环：无出发 → done
)
t4, bot4 = _make_task(frames)
t4._goto_playbook = lambda: True      # 导航由 GlobalNavigator 负责，干跑替换
r4 = t4.run()
_s4 = [c[2] for c in bot4.clicks]
check('4a 结果 done', r4 == 'done', f'r={r4!r}')
check('4b 点了「天穹试炼」卡片', '玩法目录_天穹试炼' in _s4, str(_s4))
check('4c 点了「出发！」', '天穹试炼_出发' in _s4, str(_s4))
check('4d 点了「挑战！」', '天穹试炼_挑战' in _s4, str(_s4))
check('4e 走了 通关阵容 → 一键采用',
      '备战_通关阵容' in _s4 and '备战_一键采用' in _s4, str(_s4))
check('4f 按了 1 次 F', bot4.keys.count('F') == 1, str(bot4.keys))
check('4g 局数与统计一致（1 局）',
      t4.battles == 1 and t4.stats.get('total_battles') == 1,
      f"battles={t4.battles} stats={t4.stats}")
check('4h 塔内无挑战 → 走了返回', bot4.backs >= 1, f'backs={bot4.backs}')

# ═══════════════════════════════════════════════════════════
#  5. 手动停止：点「出发！」后置位 → 立即 stopped（≤2 轮）
# ═══════════════════════════════════════════════════════════
print('\n── 5. 手动停止 ──')
t5, _ = _make_task([TOWER_LIST], stop_on_click='天穹试炼_出发')
t5.startup_probe = lambda: 'ready'
r5 = t5.run()
check('5a 停止即刻返回 stopped', r5 == 'stopped', f'r={r5!r}')
check('5b 主循环迭代 ≤2 轮', getattr(t5, '_loop_iters', 0) <= 2,
      f"iters={getattr(t5, '_loop_iters', 0)}")

# ═══════════════════════════════════════════════════════════
#  6. 全部「不在开放时间内」→ done 且日志说明「今日无开放」
# ═══════════════════════════════════════════════════════════
print('\n── 6. 今日无开放塔 ──')
t6, _ = _make_task([TOWER_LIST_CLOSED])
t6.startup_probe = lambda: 'ready'
r6 = t6.run()
check('6a 结果 done', r6 == 'done', f'r={r6!r}')
check('6b 日志含「今日无开放」',
      any('今日无开放' in str(m) for m in t6._logs),
      str(t6._logs[-1:]))

# ═══════════════════════════════════════════════════════════
#  7. max_battles 上限 → done
# ═══════════════════════════════════════════════════════════
print('\n── 7. 最多次数上限 ──')
t7, _ = _make_task([TOWER_LIST], options={'max_battles': 0})
check('7a max_battles=0 → summary 正常', t7.summary() == '自动打当天开放的天穹塔',
      t7.summary())
check('7b summary ≤14 字', len(t7.summary()) <= 14, t7.summary())

# ═══════════════════════════════════════════════════════════
#  8. 点击无效（画面永不变化）→ 不得无限重试
#     v2.3.21：QA 实测「挑战/出发/F 失效时会连点 120 次永不结束」。
#     这里用同一帧循环模拟"点了没反应"，验证硬上限/无进展护栏生效。
# ═══════════════════════════════════════════════════════════
print('\n── 8. 点击无效不死循环 ──')
t8, b8 = _make_task([FLOOR])          # 单帧：每轮返回同一画面 = 点击无效
r8 = t8.run()
check('8a 画面永不变化 → failed（不死循环）', r8 == 'failed', f'r={r8}')
check('8b 触发过返回重试', b8.backs >= 1, f'backs={b8.backs}')
_nch = sum(1 for c in b8.clicks if c[2] == '天穹试炼_挑战')
check('8c 挑战点击次数被限制（≤8）', _nch <= 8, f'n={_nch}')
check('8d 日志含「无变化」诊断',
      any('无变化' in m for m in t8._logs),
      str([m for m in t8._logs if '无变化' in m][:1]))

# ═══════════════════════════════════════════════════════════
#  9. 进塔转场不误判（真机 2026-11-05 19:35 复现）
#     点「出发！」后塔内页有转场动画，首帧只读到「已通关」读不到「挑战！」
#     → 旧逻辑当场判「本塔今日已无可挑战层」直接返回，实际有可挑战层。
# ═══════════════════════════════════════════════════════════
print('\n── 9. 进塔转场不误判 ──')
TRANSIT_FLOOR = [              # 转场帧：塔名 + 已通关，但「挑战！」尚未渲染
    it(50, 347, '赛季不朽之塔', 0.97),
    it(864, 472, '已通关', 0.96),
]
frames9 = (
    [PLAYBOOK, PLAYBOOK, TOWER_LIST, TOWER_LIST]
    + [TRANSIT_FLOOR]          # _wait_enter_tower：读到「已通关」→ 确认已进入
    + [TRANSIT_FLOOR]          # 主循环：楼层页无挑战 → 确认 1/3（等待）
    + [TRANSIT_FLOOR]          # 确认 2/3（等待）
    + [FLOOR]                  # 确认 3 帧之前读到「挑战！」→ 应当点击
    + [PREP, PREP, PREP_ADOPT]
    + [FLOOR_DONE] * 4
    + [TOWER_LIST_CLOSED]
)
t9, bot9 = _make_task(frames9)
t9._goto_playbook = lambda: True
r9 = t9.run()
_s9 = [c[2] for c in bot9.clicks]
check('9a 转场后仍点到了「挑战！」（未被误判结束）',
      '天穹试炼_挑战' in _s9, str(_s9))
check('9b 结果 done（打完后正常结束）', r9 == 'done', f'r={r9!r}')
check('9c 日志有「等待转场结束」的确认过程',
      any('等待转场结束' in m for m in t9._logs),
      str([m for m in t9._logs if '等待转场' in m][:1]))

# ═══════════════════════════════════════════════════════════
#  10. 塔列表首帧漏读「出发！」→ 不得立即结束（真机 19:38 复现）
#      实际当天有开放塔，但首帧 OCR 没读到「出发」就 2 秒误退。
# ═══════════════════════════════════════════════════════════
print('\n── 10. 塔列表漏读「出发」不误判 ──')
NO_GO_LIST = [
    it(261, 639, '选择目的地', 0.99),
    it(324, 490, '赛季光辉之塔', 0.97),
    it(419, 517, '不在开放时间内', 0.99),
]                                   # 塔列表，但没有「出发！」（模拟漏读）
frames10 = (
    [PLAYBOOK, PLAYBOOK]
    + [NO_GO_LIST, NO_GO_LIST]       # 首两帧漏读 → 应等待确认，不结束
    + [TOWER_LIST]                   # 第三帧读到「出发！」→ 点击
    + [FLOOR] + [FLOOR]              # 进塔 + 点挑战
    + [PREP, PREP, PREP_ADOPT]
    + [FLOOR_DONE] * 4
    + [TOWER_LIST_CLOSED] * 3        # 无出发（3 帧确认）→ done
)
t10, bot10 = _make_task(frames10)
t10._goto_playbook = lambda: True
r10 = t10.run()
_s10 = [c[2] for c in bot10.clicks]
check('10a 首帧漏读「出发」后仍继续并点到它',
      '天穹试炼_出发' in _s10, str(_s10))
check('10b 最终 done', r10 == 'done', f'r={r10!r}')
check('10c 日志有「暂未找到「出发！」」的确认过程',
      any('暂未找到「出发' in m for m in t10._logs),
      str([m for m in t10._logs if '暂未找到' in m][:1]))

# ═══════════════════════════════════════════════════════════
#  11. 胜利结算点「下一关」（真机 19:44 复现）
#      天穹胜利结算页主按钮是「下一关」，旧逻辑只找「继续挑战/再次挑战」
#      → 找不到就按返回，直接退出塔内。数据来自用户截图 OCR。
# ═══════════════════════════════════════════════════════════
print('\n── 11. 胜利结算点「下一关」 ──')
RESULT_WIN = [
    it(190, 432, '战斗胜利', 0.86),      # 艺术字，conf 偏低
    it(636, 267, '第94层', 0.95),
    it(636, 349, '第95层', 0.95),
    it(636, 431, '第96层', 0.95),
    it(636, 513, '当前关卡', 0.93),
    it(654, 513, '97', 0.96),
    it(636, 595, '第98层', 0.95),
    it(812, 690, '下一关', 0.97),        # 绿色主按钮
]
t11, b11 = _make_task([RESULT_WIN])
check('11a 结算页判据命中（含「下一关」）', t11._is_result_page(RESULT_WIN))
t11._close_result(RESULT_WIN)
_l11 = [c[2] for c in b11.clicks]
check('11b 点了「下一关」', '天穹试炼_下一关' in _l11, str(_l11))
check('11c 没有走返回（旧逻辑会退出塔内）', b11.backs == 0, f'backs={b11.backs}')

print('\n' + '─' * 54)
if _FAIL:
    print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
    print(f'  ❌ 失败: {_FAIL}')
    sys.exit(1)
print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
print('  ✅ 天穹试炼干跑测试全部通过')
sys.exit(0)
