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

from tasks.tianqiong_trial import (  # noqa: E402
    TianqiongTrialTask, description as TQ_DESC,
)

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
        self.tpl_regions: list = []      # v2.3.32：记录 template_match 的 region
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
        self.tpl_regions.append(region)
        return None

    def window_rect(self):
        """窗口矩形 (l, t, w, h)，供模板 region 限界用。"""
        return (0, 0, 1280, 1024)

    def _click_abs(self, cx, cy, label=''):
        """屏幕绝对坐标点击（模板匹配返回值用这个，不能用 click_at）。

        用一个固定 label，让测试能区分「走了屏幕坐标分支」。
        """
        self.clicks.append((cx, cy, label or 'ABS'))


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

# ── 7c~7e：任务栏文案（GUI `_instance_summary` 直接取 meta.description）──
# 用户 2026-11-06 要求：只说核心功能、保留必要信息、不写实现/运行逻辑、
# 长度控制在任务栏内完整显示（对照「竞技场」24 字可完整显示）。
check('7c description ≤24 字（任务栏内可完整显示）',
      len(TQ_DESC) <= 24, f'{len(TQ_DESC)} 字: {TQ_DESC}')
check('7d 必要信息完整（当日开放 / 失败换阵 / 全部完成）',
      all(k in TQ_DESC for k in ('当日开放', '失败', '全部完成')), TQ_DESC)
_impl_kws = ('出发！', '挑战！', '通关阵容', '一键采用', '→', '模板', 'OCR', '/F')
check('7e 不含实现细节与操作步骤',
      not any(k in TQ_DESC for k in _impl_kws), TQ_DESC)

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

# ═══════════════════════════════════════════════════════════
#  12. 失败结算 → 再次挑战 + 换阵容 + 换塔（v2.3.25 用户截图）
#      失败结算主按钮是「再次挑战」；按失败次数点 `>` 换下一套推荐阵容；
#      `>` 消失或达上限 → 换另一座塔；所有塔都打完 → done。
# ═══════════════════════════════════════════════════════════
print('\n── 12. 失败结算与换阵容 ──')
RESULT_LOSE = [
    it(190, 432, '战斗失败', 0.86),      # 艺术字
    it(490, 500, '推荐提升', 0.95),
    it(812, 690, '再次挑战', 0.97),      # 绿色主按钮
]
t12, b12 = _make_task([RESULT_LOSE])
check('12a 失败结算判据命中', t12._is_result_page(RESULT_LOSE))
_won12 = t12._close_result(RESULT_LOSE)
check('12b _close_result 返回 False（失败）', _won12 is False, f'{_won12}')
check('12c 点了「再次挑战」',
      '天穹试炼_再次挑战' in [c[2] for c in b12.clicks],
      str([c[2] for c in b12.clicks]))

# 12d：通关阵容面板里有点「>」→ 按次数切换
PREP_ARROW = [
    it(285, 973, '通关阵容', 0.95),
    it(500, 900, '>', 0.90),
    it(694, 852, '一键采用', 0.95),
]
t12b, b12b = _make_task([PREP_ARROW])
# v2.3.30 用户规则：`>` 只走模板库，禁止 OCR 兜底 → 让模板命中
t12b.bot.template_match = lambda *a, **k: (1000, 600)
t12b._setup_lineup(2)
_n_sw = sum(1 for c in b12b.clicks if c[2] == 'ABS')
check('12d 失败 2 次 → 模板命中后点 2 次「>」换 2 套阵容',
      _n_sw == 2, f'n={_n_sw}')

# 12e：没有「>」→ 判定推荐阵容已用完
NO_ARROW = [it(285, 973, '通关阵容', 0.95),
            it(694, 852, '一键采用', 0.95)]
t12c, _ = _make_task([NO_ARROW])
_r12c = t12c._switch_formation(1)
check('12e 首次无「>」→ 返回 False 但**不**判用完（防 OCR 抖动）',
      _r12c is False and t12c._formation_exhausted is False,
      f'r={_r12c} exhausted={t12c._formation_exhausted}')
_r12c2 = t12c._switch_formation(1)
check('12e2 连续 2 次无「>」→ 才判定阵容用完',
      _r12c2 is False and t12c._formation_exhausted is True,
      f'r={_r12c2} exhausted={t12c._formation_exhausted}')

# 12f：失败次数达阵容上限 → 换塔（返回塔列表）并结束
t12d, b12d = _make_task([RESULT_LOSE] + [TOWER_LIST_CLOSED] * 3)
t12d._cur_go = (850, 700)
t12d._fail_idx = t12d._max_formations - 1
r12d = t12d._play_loop()
check('12f 阵容试完仍失败 → 换塔（走了返回）', b12d.backs >= 1,
      f'backs={b12d.backs}')
check('12g 当前塔被标记为已打完（不再重复进）',
      (850, 700) in t12d._exhausted_go, str(t12d._exhausted_go))
check('12h 所有塔都试过 → done', r12d == 'done', f'r={r12d!r}')

# ═══════════════════════════════════════════════════════════
#  13. 今日开放塔快照 + 去重（v2.3.26）
#      进塔列表先记录"今天开了哪几座塔"，每座只进一次；
#      塔内无「挑战！」= 该塔已通关 → 标记完成，返回后跳过。
# ═══════════════════════════════════════════════════════════
print('\n── 13. 今日开放塔快照与去重 ──')

# 真实四塔帧，只有「不朽」开放（数据来自用户截图 OCR）
TOWER_ONE_OPEN = [
    it(324, 490, '赛季光辉之塔', 0.97), it(352, 449, '当前层数：91', 0.96),
    it(402, 833, '周一/周五/周日', 0.97), it(419, 517, '不在开放时间内', 0.99),
    it(472, 482, '赛季自然之塔', 0.97), it(502, 442, '当前层数：88', 0.98),
    it(551, 824, '周三/周六/周日', 0.97), it(572, 508, '不在开放时间内', 0.98),
    it(618, 510, '赛季不朽之塔', 0.93), it(646, 469, '当前层数：93', 0.97),
    it(701, 853, '出发！', 0.96),
    it(774, 496, '赛季意志之场', 0.89), it(803, 449, '当前层数：91', 0.95),
    it(842, 834, '周二/周五/周日', 0.97), it(867, 518, '不在开放时间内', 0.99),
]
t13, _ = _make_task([TOWER_ONE_OPEN])
_s13 = t13._scan_open_towers(TOWER_ONE_OPEN)
check('13a 单塔开放 → 只记录 1 座', len(_s13) == 1, str(_s13))
check('13b 「出发」归属正确的塔（不朽，非自然/意志）',
      _s13 and '不朽' in _s13[0]['name'], str(_s13[0]['name'] if _s13 else ''))

# 周六周日四塔全开（坐标按卡片布局推算：塔名 cy + 83）
TOWER_ALL_OPEN = [
    it(324, 490, '赛季光辉之塔', 0.97), it(407, 853, '出发！', 0.96),
    it(472, 482, '赛季自然之塔', 0.97), it(555, 853, '出发！', 0.96),
    it(618, 510, '赛季不朽之塔', 0.93), it(701, 853, '出发！', 0.96),
    it(774, 496, '赛季意志之场', 0.89), it(857, 853, '出发！', 0.96),
]
_s13b = t13._scan_open_towers(TOWER_ALL_OPEN)
check('13c 四塔全开 → 记录 4 座', len(_s13b) == 4, str(len(_s13b)))
_names13 = [t['name'] for t in _s13b]
check('13d 四塔名各归其位（含「意志之场」误读）',
      all(k in n for k, n in zip(('光辉', '自然', '不朽', '意志'), _names13)),
      str(_names13))

# 去重逻辑：两座塔都开放 → done 的塔被跳过，全部 done 则结束
TOWER_TWO_OPEN = [
    it(618, 510, '赛季不朽之塔', 0.93), it(701, 853, '出发！', 0.96),
    it(774, 496, '赛季意志之场', 0.89), it(857, 853, '出发！', 0.96),
]
t13c, _ = _make_task([TOWER_TWO_OPEN])
t13c._today_towers = t13c._scan_open_towers(TOWER_TWO_OPEN)
check('13e 快照含 2 座塔', len(t13c._today_towers) == 2,
      str([x['name'] for x in t13c._today_towers]))


def _next_pending(task):
    return next((x for x in (task._today_towers or []) if not x['done']), None)


_n1 = _next_pending(t13c)
check('13f 初始选第 1 座（不朽）', _n1 is not None and '不朽' in _n1['name'],
      str(_n1['name'] if _n1 else ''))
t13c._today_towers[0]['done'] = True            # 第 1 座打完
_n2 = _next_pending(t13c)
check('13g 第 1 座 done 后选第 2 座（意志）',
      _n2 is not None and '意志' in _n2['name'],
      str(_n2['name'] if _n2 else ''))
check('13h 不会重复返回第 1 座', _n2 is not _n1)
for _x in t13c._today_towers:
    _x['done'] = True
check('13i 全部 done → 无候选（任务结束）', _next_pending(t13c) is None)

# 返回确认：`_back_to_tower_list` 必须等到塔列表出现才返回 True
t13d, b13d = _make_task([TOWER_TWO_OPEN])
check('13j 返回后确认在塔列表', t13d._back_to_tower_list() is True)
check('13k 确实点了返回按钮', b13d.backs >= 1, f'backs={b13d.backs}')

# 返回确认：一直是塔内页 → 多次尝试后返回 False（不会误判成功）
t13e, b13e = _make_task([TRANSIT_FLOOR])
check('13l 始终未回塔列表 → 返回 False（不误判）',
      t13e._back_to_tower_list(attempts=2) is False)
check('13m 失败时也重试了多次返回', b13e.backs >= 2, f'backs={b13e.backs}')

# ═══════════════════════════════════════════════════════════
#  14. 坐标语义：模板兜底必须走 _click_abs（真机 20:39 踩坑）
#      `template_match` 返回**屏幕绝对坐标**，`click_at` 期望**图像坐标**。
#      混用会双重偏移（实测 2040,719 落到工具自己的「定时」按钮上）。
# ═══════════════════════════════════════════════════════════
print('\n── 14. 切换阵容坐标语义 ──')
PREP_NO_ARROW = [it(285, 973, '通关阵容', 0.95),
                 it(694, 852, '一键采用', 0.95)]
t14, b14 = _make_task([PREP_NO_ARROW])
t14.bot.template_match = lambda *a, **k: (2040, 719)   # 屏幕坐标
t14._switch_formation(1)
_labels14 = [c[2] for c in b14.clicks]
check('14a 模板兜底用 _click_abs（屏幕坐标）', 'ABS' in _labels14, str(_labels14))
check('14b 不把模板坐标当图像坐标传给 click_at',
      '天穹试炼_切换阵容' not in _labels14, str(_labels14))
_abs14 = [c for c in b14.clicks if c[2] == 'ABS']
check('14c 点击坐标未被二次偏移（仍是 2040,719）',
      bool(_abs14) and _abs14[0][0] == 2040 and _abs14[0][1] == 719,
      str(_abs14[:1]))

# v2.3.30：模板未命中时**禁止 OCR 兜底** → 只降级、不点击，并说明原因
t14b, b14b = _make_task([PREP_ARROW])        # 帧里故意放 OCR 能读到的「>」
t14b.bot.template_match = lambda *a, **k: None
_r14b = t14b._switch_formation(1)
check('14d 模板未命中 → 不点击（禁止 OCR 兜底）',
      _r14b is False
      and not any(c[2] in ('ABS', '天穹试炼_切换阵容')
                  for c in b14b.clicks),
      str([c[2] for c in b14b.clicks]))
check('14d2 降级日志说明检索范围与未匹配原因',
      any('已检索 templates/' in m and '可能原因' in m
          for m in t14b._logs),
      str([m for m in t14b._logs if '已检索' in m][:1]))

# 找不到「>」→ 单次不判"用完"（避免 OCR 抖动导致过早换塔）
t14c, _ = _make_task([PREP_NO_ARROW])
t14c.bot.template_match = lambda *a, **k: None
t14c._switch_formation(1)
# v2.3.29：模板优先 —— 模板命中时不消耗 OCR（单字符 OCR 不稳，别让它挡道）
t14d, _ = _make_task([PREP_ARROW])          # 帧里有 OCR 的「>」
t14d.bot.template_match = lambda *a, **k: (1000, 600)
t14d.bot.i = 0                              # 重置帧游标，便于判断是否读了 OCR
_pos14 = t14d._find_arrow()
check('14g 模板命中 → 直接返回屏幕坐标（无 OCR 坐标对）',
      _pos14 == (1000, 600), str(_pos14))
check('14h 模板命中时不读 OCR 帧', t14d.bot.i == 0, f'ocr_calls={t14d.bot.i}')

check('14e 首次找不到「>」不判用完（防抖）',
      t14c._formation_exhausted is False,
      f'exhausted={t14c._formation_exhausted}')
t14c._switch_formation(1)          # 第 2 次仍找不到
check('14f 连续 2 次找不到 → 才判用完',
      t14c._formation_exhausted is True,
      f'exhausted={t14c._formation_exhausted}')

# ═══════════════════════════════════════════════════════════
#  15. 模板 region 限界 + 计时埋点（v2.3.32）
#      此前全屏 16 尺度匹配 ~8s/次；改为窗口比例圈 14% 面积，
#      未命中再全屏兜底（不漏检），每次匹配打印耗时。
# ═══════════════════════════════════════════════════════════
print('\n── 15. 模板 region 与计时 ──')
_EXPECT_REGION = (768, 256, 358, 512)   # 1280×1024 × (0.60,0.25,0.28,0.50)
t15, _ = _make_task([PREP_ARROW])
check('15a region 按窗口比例计算（约 14% 面积）',
      t15._arrow_region() == _EXPECT_REGION, str(t15._arrow_region()))

t15b, b15b = _make_task([PREP_ARROW])


def _hit_rec(*a, **k):
    b15b.tpl_regions.append(k.get('region'))
    return (1915, 756)


t15b.bot.template_match = _hit_rec
_pos15b = t15b._find_arrow()
check('15b 命中时 region 非 None（走局部搜索）',
      _pos15b == (1915, 756)
      and len(b15b.tpl_regions) == 1
      and b15b.tpl_regions[0] == _EXPECT_REGION,
      str(b15b.tpl_regions))
check('15c 每次匹配都有 [耗时] 日志',
      any('[耗时] 模板匹配' in m for m in t15b._logs),
      str([m for m in t15b._logs if '[耗时]' in m][:1]))

t15c, b15c = _make_task([PREP_ARROW])


def _miss_rec(*a, **k):
    b15c.tpl_regions.append(k.get('region'))
    return None


t15c.bot.template_match = _miss_rec
check('15d 局部未命中 → 全屏兜底重试（不漏检）',
      t15c._find_arrow() is None
      and b15c.tpl_regions == [_EXPECT_REGION, None],
      str(b15c.tpl_regions))

t15d, b15d = _make_task([PREP_ARROW])
t15d.bot.window_rect = lambda: (0, 0, 0, 0)
def _miss_rec2(*a, **k):
    b15d.tpl_regions.append(k.get('region'))
    return None


t15d.bot.template_match = _miss_rec2
t15d._find_arrow()
check('15e 窗口尺寸不可用 → 只全屏一次（不重复匹配）',
      b15d.tpl_regions == [None], str(b15d.tpl_regions))

print('\n' + '─' * 54)
if _FAIL:
    print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
    print(f'  ❌ 失败: {_FAIL}')
    sys.exit(1)
print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
print('  ✅ 天穹试炼干跑测试全部通过')
sys.exit(0)
