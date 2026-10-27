# -*- coding: utf-8 -*-
"""独立验证：烙印品质读取修复（v2.1.11）—— 自建断言，并**尽力证伪**。

被验证对象（tasks/labyrinth.py，本脚本不修改任何 tasks/ 文件）：
  ① `_card_quality(items, pos)` 三级取值：
       下方 30~130px 最近标签 → 任意方向 ≤120px 最近 → '未知'
  ② `_handle_imprint` 具名分支改记 `_actual`（实际 OCR 文本）并打
     「疑似字形误认」警告。

本脚本独立于 tests/test_card_quality.py（不照抄其坐标），并额外做一条
【证伪】：既然 `_card_quality` 的唯一调用点是具名分支（labyrinth.py:1620），
而第二步「品质保护」走的是 `_collect_imprint_entries`（非 `_card_quality`），
那么把 `_card_quality` 换回旧实现后，第二步替换结果应**不受影响**——若
成立，则「修 `_card_quality` 解决品质保护空转」的因果链需重新审视。

运行：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_card_quality_verify.py
"""
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
from fast_time import install as _fast_install  # noqa: E402

_fast_install()

import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import (  # noqa: E402
    LabyrinthTask, _IMPRINT_QUAL_TAGS,
)

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.9):
    """OCR 项 (y, x, 文本, conf, x1, x2)。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


def old_card_quality(items, pos):
    """**旧实现**（修复前）：返回第一个 |Δy|≤120 命中的标签。"""
    _px, py = pos
    for it in items:
        if (len(it) >= 4 and it[3] >= 0.6
                and it[2] in _IMPRINT_QUAL_TAGS
                and abs(it[0] - py) <= 120):
            return it[2]
    return '未知'


q = LabyrinthTask._card_quality

print('════════════════════════════════════════════════════════')
print(' 独立验证：烙印品质读取修复（v2.1.11）')
print('════════════════════════════════════════════════════════')

# ═══════════════════════════════════════════════════════════
#  A. `_card_quality` 单元（自选帧，覆盖多种邻卡布局）
# ═══════════════════════════════════════════════════════════
print()
print('── A. `_card_quality` 布局单元 ──')

# A5 核心语义：邻卡标签**比本卡更近**但在上方 → 必须仍取本卡（下方）
#   本卡卡名 y=558；本卡标签 y=670（Δ=+112）；邻卡标签 y=520（Δ=-38，更近）
_frame_core = [
    _it(520, 176, '精英'),      # 邻卡标签（上方，|Δ|=38 更近）
    _it(670, 176, '史诗'),      # 本卡标签（下方，Δ=+112）
    _it(558, 365, '某本卡名'),  # 本卡卡名（pos 基准）
]
_new_core = q(_frame_core, (365, 558))
_old_core = old_card_quality(_frame_core, (365, 558))
check('A5 核心：邻卡更近但在上方 → 新实现仍取本卡「史诗」',
      _new_core == '史诗', f'new={_new_core!r}')
check('A5b 旧实现在同帧会误取邻卡「精英」（证明修复真实改变行为）',
      _old_core == '精英', f'old={_old_core!r}')

# A1 邻卡在上方（Δ=-100，旧实现会命中）
_f1 = [
    _it(462, 177, '精英'),      # 邻卡标签（上方 -100）
    _it(562, 366, '某个卡名'),  # 本卡卡名
    _it(646, 176, '史诗'),      # 本卡标签（+84）
]
check('A1 邻卡在上方 → 取本卡「史诗」', q(_f1, (366, 562)) == '史诗',
      repr(q(_f1, (366, 562))))

# A2 邻卡在下方（Δ>130，落窗口外）
_f2 = [
    _it(562, 366, '某个卡名'),
    _it(646, 176, '精英'),      # 本卡标签 +84
    _it(770, 176, '稀有'),      # 下方邻卡标签 +208（窗口外）
]
check('A2 邻卡在下方（+208 窗口外）→ 仍取本卡「精英」',
      q(_f2, (366, 562)) == '精英', repr(q(_f2, (366, 562))))

# A3 邻卡标签与本卡卡名**同一 y**（左右不同列）
_f3 = [
    _it(562, 900, '稀有'),      # 同 y 的另一列标签（Δ=0；函数忽略了 x）
    _it(562, 366, '某个卡名'),
    _it(646, 176, '精英'),      # 本卡标签 +84
]
check('A3 同 y 异列标签存在 → 仍取本卡「精英」',
      q(_f3, (366, 562)) == '精英', repr(q(_f3, (366, 562))))

# A4 多张邻卡（上方/下方/同 y）+ 本卡标签同时存在
_f4 = [
    _it(462, 176, '稀有'),      # 上邻卡 -100
    _it(562, 900, '精英'),      # 同 y 异列 0
    _it(646, 200, '史诗'),      # 本卡 +84
    _it(770, 176, '精英'),      # 下邻卡 +208（窗口外）
    _it(562, 366, '某个卡名'),
]
check('A4 三邻卡共存 → 取本卡「史诗」', q(_f4, (366, 562)) == '史诗',
      repr(q(_f4, (366, 562))))

# A6 无标签 → '未知'
check('A6 完全无标签 → 未知',
      q([_it(562, 366, '某个卡名')], (366, 562)) == '未知',
      repr(q([_it(562, 366, '某个卡名')], (366, 562))))

# A7 边界：下方 +131（窗口外）但上方 -100 有标签 → 按设计取上方
_f7 = [
    _it(458, 176, '精英'),      # 上方 -100
    _it(558, 365, '某卡名'),
    _it(689, 176, '史诗'),      # 下方 +131（超 130 窗口）
]
_r7 = q(_f7, (365, 558))
check('A7 下方 +131 超窗口、上方 -100 内 → 取上方「精英」（=near 退化）',
      _r7 == '精英', f'actual={_r7!r}（设计如此：below 排除 131，near≤120 命中 -100）')

# A8 端点：+30 / +130 应取到；+20 由 near 兜底取到
for _d, _tag in ((30, '史诗'), (130, '精英')):
    _fd = [_it(558, 365, '某卡名'), _it(558 + _d, 176, _tag)]
    check(f'A8 下方 Δ=+{_d}（含端点）→ 取「{_tag}」',
          q(_fd, (365, 558)) == _tag, repr(q(_fd, (365, 558))))
_f8d = [_it(558, 365, '某卡名'), _it(578, 176, '稀有')]   # +20（<30）
check('A8b 下方 Δ=+20（不足 30）→ 由 near 兜底取「稀有」',
      q(_f8d, (365, 558)) == '稀有', repr(q(_f8d, (365, 558))))

# ═══════════════════════════════════════════════════════════
#  B. 端到端 `_imprint_orb_flow`（最关键）
# ═══════════════════════════════════════════════════════════
print()
print('── B. 端到端：第二步品质保护 ──')


class MockBot:
    """照 tests/test_labyrinth_v1820.py 的 MockBot 精简复刻（不 import 其模块）。"""

    def __init__(self, texts_by_call=None):
        self._texts_by_call = list(texts_by_call or [])
        self.calls = []
        self.clicked = []
        self.click_coords = []
        self.tpl_paths = []
        self.skip_hit = None
        self.skip_hit_delay = 0

    def detect_phase(self):
        self.calls.append('detect_phase')
        return 'labyrinth'

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            texts = self._texts_by_call.pop(0)
        else:
            texts = ['玩法目录', '常规玩法', '活动']
        if texts and isinstance(texts[0], tuple):
            return list(texts)
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, keyword, min_conf=0.5):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and keyword in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicked.append(label)
        self.click_coords.append((label, cx, cy))

    def click_ratio(self, rx, ry, label=''):
        self.calls.append(f'click_ratio:{label}@{rx},{ry}')
        self.clicked.append(label)

    def press_f(self, label=''):
        self.calls.append(f'press_f:{label}')

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def long_press_at(self, cx, cy, duration=2.5, label=''):
        self.calls.append(f'long_press_at:{label}@({cx},{cy})')
        self.clicked.append(label)

    def long_press_ratio(self, rx, ry, duration=2.5, label=''):
        self.calls.append(f'long_press_ratio:{label}@{rx},{ry}')
        self.clicked.append(label)

    def template_match(self, tpl_path, threshold=0.8, scale_range=(0.5, 2.0, 0.1),
                       silent=False, region=None):
        self.calls.append(f'template_match:{os.path.basename(tpl_path)}')
        self.tpl_paths.append(tpl_path)
        return None

    def _click_abs(self, cx, cy):
        self.calls.append(f'_click_abs:({cx},{cy})')

    def screen_to_image(self, screen_x, screen_y):
        return (screen_x, screen_y)

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append(f'scroll_wheel:{clicks}:{label}')

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append('click_back_button')
        return (500, 500)

    def find_and_focus(self):
        return 12345

    def _handle_common_phase(self, phase):
        self.calls.append(f'common:{phase}')
        return phase


class _StopBot(MockBot):
    """第 N 次 OCR 后 set 停止事件，保证 `_imprint_orb_flow` 能退出。"""

    def __init__(self, stop_after, stop_ev, **kw):
        super().__init__(**kw)
        self._stop_after = stop_after
        self._stop_ev = stop_ev
        self._ocr_n = 0

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n > self._stop_after:
            self._stop_ev.set()
        return super()._ocr_screen(img=img)


def _make_task(mock_bot, logger=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    ctx = {
        'bot': mock_bot,
        'logger': logger or (lambda m: None),
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': {},
        'max_time': None,
        'inst_id': 'labyrinth#verify',
    }
    return LabyrinthTask(ctx)


def _orb_frame(entries_quals, cy0=240):
    """[(名称, 品质), ...] → 标签在名称下方 60px（名称 y=标签 y-60）。"""
    items = []
    for i, (n, qq) in enumerate(entries_quals):
        ly = cy0 + i * 200
        items.append(_it(ly, 178, qq))
        items.append(_it(ly - 60, 366, n))
    return items


STEP1 = [_it(955, 234, '继续前进'), _it(955, 732, '确认替换')]
S1_ELITE_TARGET = STEP1 + _orb_frame([('暖流之力', '精英')])
S1_EPIC_TARGET = STEP1 + _orb_frame([('暖流之力', '史诗')])

# 第二步帧：首行 y=303<0.38*1024 被滤；候选 坚固壁垒 / 进发浪潮
S2_POS = [
    _it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
    _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),    # 首行（被滤）
    _it(523, 365, '坚固壁垒'), _it(605, 177, '史诗'),    # 候选1 = 史诗
    _it(640, 365, '进发浪潮'), _it(722, 176, '稀有'),    # 候选2 = 稀有
]
S2_NEG = [
    _it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
    _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),
    _it(523, 365, '坚固壁垒'), _it(605, 177, '精英'),    # 候选1 = 精英（低于旧史诗）
    _it(640, 365, '进发浪潮'), _it(722, 176, '稀有'),
]


def run_orb_flow(step1_frame, step2_frame, inventory, stop_after=12):
    logs = []
    ev = threading.Event()
    seq = [step1_frame] * 3 + [step2_frame] * 3 + [step1_frame] * 8
    bot = _StopBot(stop_after, ev, texts_by_call=seq)
    t = _make_task(bot, logger=logs.append)
    t._orb_inventory = list(inventory)
    t._stop = ev
    t._imprint_orb_flow()
    return bot, logs


# B1 正例：旧=精英，候选=史诗（下方标签布局）→ 应真的替换
bot_p, logs_p = run_orb_flow(S1_ELITE_TARGET, S2_POS, [('暖流之力', '精英')])
_clk_p = set(bot_p.clicked)
check('B1 正例：选中旧烙印「暖流之力」并点确认替换',
      '替换_暖流之力' in _clk_p, str(sorted(_clk_p)))
check('B1b 正例：候选史诗被选中替换（烙印_坚固壁垒）',
      '烙印_坚固壁垒' in bot_p.clicked, str(sorted(_clk_p)))
check('B1c 正例：日志出现「已替换为「坚固壁垒」（史诗）」',
      any('已替换为「坚固壁垒」' in m for m in logs_p),
      str([m for m in logs_p if '已替换' in m][:2]))
check('B1d 正例：**未**触发品质保护（无「候选不如旧烙印」、无「保留烙印」）',
      not any('候选不如旧烙印' in m for m in logs_p)
      and '保留烙印' not in _clk_p,
      f'prot={"候选不如旧烙印" in "".join(logs_p)} keep={"保留烙印" in _clk_p}')

# B2 反例：旧=史诗，候选=精英 → 应正确保留（品质保护）
bot_n, logs_n = run_orb_flow(S1_EPIC_TARGET, S2_NEG, [('暖流之力', '史诗')])
_clk_n = set(bot_n.clicked)
check('B2 反例：触发「候选不如旧烙印（品质保护）」',
      any('候选不如旧烙印' in m for m in logs_n),
      str([m for m in logs_n if '品质保护' in m][:2]))
check('B2b 反例：点「保留烙印」且**未**替换',
      '保留烙印' in _clk_n and '烙印_坚固壁垒' not in bot_n.clicked
      and not any('已替换为' in m for m in logs_n),
      str(sorted(_clk_n)))

# B3 证伪：`_card_quality` 换回旧实现 → 第二步品质保护结果应**不变**
_orig_cq = LabyrinthTask.__dict__['_card_quality']
LabyrinthTask._card_quality = staticmethod(old_card_quality)
try:
    bot_o, logs_o = run_orb_flow(
        S1_ELITE_TARGET, S2_POS, [('暖流之力', '精英')])
finally:
    LabyrinthTask._card_quality = _orig_cq
check('B3 证伪：`_card_quality` 换回旧实现后，正例仍「已替换为」（第二步不经过它）',
      any('已替换为「坚固壁垒」' in m for m in logs_o)
      and not any('候选不如旧烙印' in m for m in logs_o),
      str([m for m in logs_o if '已替换' in m or '品质保护' in m][:2]))

# B4 代码事实：`_card_quality` 全文件只在 1 处被调用（具名分支）
# v2.1.23：改为数**调用点/定义点**，而非「出现该字符串的行」——
# 旧写法会被注释/docstring 里提到 `_card_quality` 误伤（本次即如此）。
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
_cq_calls = _src.count('self._card_quality(')
_cq_defs = _src.count('def _card_quality(')
check('B4 代码事实：`_card_quality` 仅 1 处调用（=具名分支）',
      _cq_calls == 1 and _cq_defs == 1,
      f'调用={_cq_calls} 定义={_cq_defs}')
check('B4b 第二步品质保护取自 `_collect_imprint_entries`（非 `_card_quality`）',
      'self._collect_imprint_entries(items)' in _src
      and 'min_qual=old_qual' in _src, '')

# ═══════════════════════════════════════════════════════════
#  C. 具名路径与形近字机制
# ═══════════════════════════════════════════════════════════
print()
print('── C. 具名路径 / 形近字匹配机制 ──')

check('C1 `find_text` 为子串匹配：「执裁之盾」**不**是「制裁之盾」的子串',
      '执裁之盾' not in '制裁之盾', "('执裁之盾' in '制裁之盾') = False")

_bot_c1 = MockBot()
_t1 = _make_task(_bot_c1)
_frame_zhixing = [_it(738, 351, '制裁之盾'), _it(820, 176, '史诗')]
check('C1b 帧里是「制裁之盾」→ find_text("执裁之盾") 返回 None（具名分支不触发）',
      _bot_c1.find_text(_frame_zhixing, '执裁之盾',
                        min_conf=0.6) is None,
      str(_bot_c1.find_text(_frame_zhixing, '执裁之盾', min_conf=0.6)))

# C2 帧文本「包含」关键词（子串命中）→ 应打「疑似字形误认」并记实读名
_frame_c2 = [_it(738, 351, '执裁之盾的守护'), _it(820, 176, '史诗'),
             _it(958, 500, '确认')]
_logs_c2 = []
_bot_c2 = MockBot(texts_by_call=[_frame_c2, _frame_c2])
_t2 = _make_task(_bot_c2, logger=_logs_c2.append)
_t2._handle_imprint(_frame_c2)
check('C2 子串命中但实读≠关键词 → 打「疑似字形误认」',
      any('疑似字形误认' in m for m in _logs_c2),
      str([m for m in _logs_c2 if '警告' in m][:2]))
check('C2b 按实读名记录（具名优先级烙印「执裁之盾的守护」）',
      any('具名优先级烙印「执裁之盾的守护」' in m for m in _logs_c2),
      str([m for m in _logs_c2 if '具名' in m][:2]))

# C3 OCR 已输出关键词本身 → _actual==kw → 不打警告（名错代码不可纠）
_frame_c3 = [_it(738, 351, '执裁之盾'), _it(820, 176, '史诗'),
             _it(958, 500, '确认')]
_logs_c3 = []
_bot_c3 = MockBot(texts_by_call=[_frame_c3, _frame_c3])
_t3 = _make_task(_bot_c3, logger=_logs_c3.append)
_t3._handle_imprint(_frame_c3)
check('C3 OCR 已输出关键词（执裁之盾）→ 不打印警告（名错在 OCR 层，代码不纠正）',
      not any('疑似字形误认' in m for m in _logs_c3),
      str([m for m in _logs_c3 if '警告' in m][:2]))

# ═══════════════════════════════════════════════════════════
#  D. 残余风险：尽力证伪（找新实现仍可能读错邻卡的布局）
# ═══════════════════════════════════════════════════════════
print()
print('── D. 残余风险（尽力证伪）──')

# D1 真实行距（≈180px）下，邻卡标签不可能落进本卡下方 30~130 窗口
#    本卡标签偏移 L=82（实测）；行距 R 时：
#      下邻卡标签 Δ = R+82 → 落入 [30,130] 需 R ≤ 48
#      上邻卡标签 Δ = 82-R → 落入 [30,130] 需 R ≤ 52
#    R≈180 → 上邻卡 Δ=-98、下邻卡 Δ=+262，均在窗口外：
_f_d1 = [
    _it(558 - 180, 176, '稀有'),   # 上邻卡标签 Δ=-98（窗口外）
    _it(558, 365, '本卡名'),
    _it(558 + 82, 176, '史诗'),    # 本卡标签 Δ=+82
    _it(558 + 180, 176, '精英'),   # 下邻卡标签 Δ=+262（窗口外）
]
check('D1 真实行距(~180px) → 本卡「史诗」唯一落入下方窗口',
      q(_f_d1, (365, 558)) == '史诗', repr(q(_f_d1, (365, 558))))

# D2 x 盲区（真正的残余风险）：函数**只比 y、不比 x**。
#    若本卡标签被 OCR 漏检，而**邻列/别处**的标签恰好落在本卡下方
#    30~130px，则会被当作本卡品质 → 仍会读错。
_f_d2 = [
    _it(558, 365, '本卡名'),        # 本卡标签（下方）OCR 漏检
    _it(650, 900, '精英'),          # 另一列某标签 Δ=+92（落窗口内）
]
_r_d2 = q(_f_d2, (365, 558))
check('D2 残余风险：本卡标签漏检 + 他列标签落窗内 → 被误当本卡品质',
      _r_d2 == '精英',
      f'actual={_r_d2!r}（x 未参与判据；仅在本卡标签 OCR 漏检时触发）')

# D3 极端行距：R=40 时**上邻卡**的标签落到本卡名下方 +42（<本卡 82）→ 会胜出
#    上邻卡名 y=558-40=518，其标签=518+82=600 → Δ=+42
_f_d3 = [
    _it(600, 900, '稀有'),          # 上邻卡的标签（Δ=+42，比本卡 +82 更近）
    _it(558, 365, '本卡名'),
    _it(558 + 82, 176, '史诗'),     # 本卡标签 Δ=+82
]
_r_d3 = q(_f_d3, (365, 558))
check('D3 残余风险：行距≤52px 的极端布局下，上邻卡标签(Δ+42)会压过本卡(+82)',
      _r_d3 == '稀有',
      f'actual={_r_d3!r}（需行距≤~52px；实测行距≈180px，余量≈3.5x）')

# ═══════════════════════════════════════════════════════════
print()
print('═' * 56)
print(f'独立验证：{len(_PASS)}/{len(_PASS) + len(_FAIL)} 通过')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print('  -', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)
