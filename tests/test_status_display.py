# -*- coding: utf-8 -*-
"""v2.3.15：GUI 实时状态显示（基线差值法）纯逻辑单测。

不构造 Tk 窗口：直接把 afk_gui.App._refresh_ui 作为未绑定函数，
绑定到 SimpleNamespace 伪 self 上调用；FakeLabel 记录 configure(text=...)
的最终值。

覆盖：
  1. 启动置零（基线=累计 → 0）
  2. 执行中刷新（增量）
  3. 结束保留（running=False 数值不变）
  4. 跨运行不串味（重新设基线后只显示本轮增量）
  5. 边界输入（None / 空 / 脏值 / 负值）不抛异常且显示 0
  6. 不破坏原流程（返回 None / 不改 data / 不调 after/_post_ui）
  7. 源码一致性（_on_start 记录基线并置零、_on_stop/_reset_buttons 不碰
     状态 Label、_refresh_ui 用基线且无裸 return 后的死代码）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_status_display.py
"""

import copy
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 与 test_gui_drawer_smoke.py 相同的隔离套路，避免 import 副作用污染真实配置
from tests import fast_time
fast_time.install()

_TMP = tempfile.mkdtemp(prefix='afkj_status_display_')
import task_registry
task_registry._CONFIG = os.path.join(_TMP, 'tasks_enabled.json')

sys.excepthook = sys.__excepthook__
import afk_gui
sys.excepthook = sys.__excepthook__   # v1.8.64：afk_gui 安装了全局 excepthook
afk_gui.GUI_CONFIG = os.path.join(_TMP, 'gui_config.json')

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


class FakeLabel:
    """只实现 _refresh_ui 用到的 configure(text=...)。"""

    def __init__(self, initial='0'):
        self.text = initial
        self.calls = []

    def configure(self, **kwargs):
        self.calls.append(kwargs)
        if 'text' in kwargs:
            self.text = kwargs['text']


class _Spy:
    """若 _refresh_ui 误调 after()/_post_ui 会被记录（纯逻辑测试禁止跨线程）。"""

    def __init__(self):
        self.n = 0

    def __call__(self, *a, **k):
        self.n += 1


def make_fake(base_b=0, base_s=0):
    """构造只暴露 _refresh_ui 所需属性的伪 self。"""
    return SimpleNamespace(
        _run_base_battles=base_b,
        _run_base_stages=base_s,
        _lbl_stat_battles=FakeLabel(),
        _lbl_stat_stages=FakeLabel(),
        after=_Spy(),
        _post_ui=_Spy(),
    )


_refresh = afk_gui.App._refresh_ui   # 未绑定方法：显式传伪 self


def values(app):
    return app._lbl_stat_battles.text, app._lbl_stat_stages.text


# ── 1. 启动置零（基线=当前累计 → 差值为 0）──
try:
    _a = make_fake(10, 4)
    _refresh(_a, {'stats': {'total_battles': 10, 'stage_progress': 4}})
    check('v2.3.15 启动置零：基线=累计 → 0/0',
          values(_a) == ('0', '0'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 启动置零', False, str(_e))

# ── 2. 执行中刷新（累计 13/7 - 基线 10/4 = 3/3）──
try:
    _a = make_fake(10, 4)
    _refresh(_a, {'stats': {'total_battles': 13, 'stage_progress': 7}})
    check('v2.3.15 执行中刷新 → 3/3',
          values(_a) == ('3', '3'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 执行中刷新', False, str(_e))

# ── 3. 结束保留（running=False 不改变数值）──
try:
    _a = make_fake(10, 4)
    _refresh(_a, {'stats': {'total_battles': 13, 'stage_progress': 7}})
    _refresh(_a, {'stats': {'total_battles': 13, 'stage_progress': 7},
                  'running': False})
    check('v2.3.15 结束保留：running=False 值不变 3/3',
          values(_a) == ('3', '3'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 结束保留', False, str(_e))

# ── 4. 跨运行不串味（第二次运行重设基线 13/7，累计 14/7 → 1/0）──
try:
    _a = make_fake(13, 7)
    _refresh(_a, {'stats': {'total_battles': 14, 'stage_progress': 7}})
    check('v2.3.15 跨运行不串味 → 1/0',
          values(_a) == ('1', '0'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 跨运行不串味', False, str(_e))

# ── 5. 边界：不抛异常且显示 0 ──
_edge_cases = [
    ('data=None', None),
    ('data={}', {}),
    ('stats=None', {'stats': None}),
    ("total_battles='abc'", {'stats': {'total_battles': 'abc',
                                       'stage_progress': 0}}),
    ('stage_progress=None', {'stats': {'total_battles': 0,
                                       'stage_progress': None}}),
    ('负值', {'stats': {'total_battles': -5, 'stage_progress': -3}}),
]
for _name, _data in _edge_cases:
    try:
        _a = make_fake(0, 0)
        _r = _refresh(_a, _data)
        _b, _s = values(_a)
        check(f'v2.3.15 边界[{_name}]：不抛异常且显示 0/0',
              _r is None and _b == '0' and _s == '0', f'{_b}/{_s}')
    except Exception as _e:
        check(f'v2.3.15 边界[{_name}]', False, str(_e))

# 额外强断言：负值确实经 max(0,...) 兜底 configure 成 '0'（不是没刷新）
try:
    _a = make_fake(0, 0)
    _a._lbl_stat_battles.text = '99'
    _a._lbl_stat_stages.text = '99'
    _refresh(_a, {'stats': {'total_battles': -5, 'stage_progress': -3}})
    check('v2.3.15 负值经 max(0,...) 兜底被 clamp 到 0',
          values(_a) == ('0', '0'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 负值兜底', False, str(_e))

# 5b. 不含 stats 的状态快照（phase/round/running）不得清零已显示数值
try:
    _a = make_fake(2, 1)
    _refresh(_a, {'stats': {'total_battles': 8, 'stage_progress': 5}})
    check('v2.3.15 先刷新到 6/4', values(_a) == ('6', '4'), f'{values(_a)}')
    _refresh(_a, {'running': False})
    check('v2.3.15 running=False 无 stats 时保持 6/4（不清零）',
          values(_a) == ('6', '4'), f'{values(_a)}')
    _refresh(_a, {'phase': 'battle', 'round': 6})
    check('v2.3.15 phase/round 无 stats 时保持 6/4（不清零）',
          values(_a) == ('6', '4'), f'{values(_a)}')
    _refresh(_a, {'stats': None, 'running': False})
    check('v2.3.15 stats=None 时保持 6/4（不清零）',
          values(_a) == ('6', '4'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 无 stats 快照保持当前值', False, str(_e))

# ── 6. 不破坏原流程 ──
try:
    _a = make_fake(0, 0)
    data = {'stats': {'total_battles': 5, 'stage_progress': 2},
            'running': True}
    snapshot = copy.deepcopy(data)
    _r = _refresh(_a, data)
    check('v2.3.15 _refresh_ui 返回 None', _r is None)
    check('v2.3.15 _refresh_ui 不修改传入的 data', data == snapshot)
    check('v2.3.15 _refresh_ui 不调用 after()/_post_ui',
          _a.after.n == 0 and _a._post_ui.n == 0,
          f'after={_a.after.n} post={_a._post_ui.n}')
    check('v2.3.15 _refresh_ui 正常刷新数值 5/2',
          values(_a) == ('5', '2'), f'{values(_a)}')
except Exception as _e:
    check('v2.3.15 不破坏原流程', False, str(_e))

# ── 7. 源码一致性断言 ──
try:
    with open(os.path.join(ROOT, 'afk_gui.py'), encoding='utf-8') as _f:
        _SRC = _f.read()

    def _method_body(src, name):
        i = src.find('def ' + name + '(')
        if i < 0:
            return ''
        j = src.find('\n    def ', i + 1)
        return src[i:j if j != -1 else len(src)]

    _start = _method_body(_SRC, '_on_start')
    check('v2.3.15 _on_start 同时记录基线并启动置零',
          'self._run_base_battles' in _start
          and "self._lbl_stat_battles.configure(text='0')" in _start)

    _stop = _method_body(_SRC, '_on_stop')
    _reset = _method_body(_SRC, '_reset_buttons')
    check('v2.3.15 _on_stop/_reset_buttons 内不出现 _lbl_stat_',
          '_lbl_stat_' not in _stop and '_lbl_stat_' not in _reset)

    _ru = _method_body(_SRC, '_refresh_ui')
    # 「裸 return」= 函数体顶层（8 空格）的 return；其后若还有同级语句即死代码。
    _naked = [ln for ln in _ru.splitlines()
              if ln.startswith('        return')]
    check('v2.3.15 _refresh_ui 使用基线差值且无裸 return 死代码',
          'self._run_base_battles' in _ru and not _naked,
          f'naked_returns={_naked}')
except Exception as _e:
    check('v2.3.15 源码一致性断言', False, str(_e))

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)
