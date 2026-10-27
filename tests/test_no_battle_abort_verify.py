# -*- coding: utf-8 -*-
"""独立验证 + 证伪：未开战收尾修复（v2.1.14 三项修复）。

本文件是**独立第三方验证**（自建 mock，不复用被测作者的任何断言），目标：

  A. 用 mock 驱动真实 `_play_loop`，验证跨轮计数 `_no_battle_rounds`
     的边界语义（连续失败停止 / 正常开战清零 / 收尾成功清零 / 未初始化）。
  B. **主动证伪**——构造边界帧，暴露该修复可能引入或残留的风险：
       B1 转场(unknown)帧被 `_leave_battle_prep` 提前判为"已离开"；
       B2 "陈旧帧"下 ESC 实际已生效却仍点左下角返回按钮 (0.139,0.825)；
       B3 door 帧的防误判守卫（正/负控）；
       B4 `_end_exploration` 仅凭"点中按钮"就报 done 的残留缺口。
  C. 机器可核查地确认：`press_esc` 已委托 `send_esc`（抢焦点）、
     `press_esc_background` 无生产调用点、并枚举全部生产调用点。

运行：python tests/test_no_battle_abort_verify.py
通过 exit 0 / 失败 exit 1（与 tests/ 既有脚本约定一致）。
"""
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

import fast_time  # noqa: F401,E402
try:                                  # 本测试大量 _sleep，加速（语义不变）
    fast_time.install()
except Exception:
    pass

import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.9):
    """OCR 六元组 (cy, cx, text, conf, x0, x1)，与生产解析一致。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


# ── 帧素材：全部喂给 tasks/screen_registry.py 的**真实**判据 ──────────
PREP = [_it(31, 641, '难度15'), _it(760, 490, '战斗'),
        _it(31, 700, '17/20'), _it(200, 300, '精兵来袭')]           # → battle_prep
DOOR = [_it(700, 640, '结束探索'), _it(374, 517, '遗物之门')]        # → door
FINAL = [_it(200, 640, '探索完成')]                                  # → final_settle
VICTORY = [_it(300, 640, '战斗胜利')]                                # → victory
UNKNOWN = [_it(500, 500, 'xf9k 噪声')]                               # → unknown
# 门卡详情漏读 door 词帧（保留 难度+战斗 → 会撞 battle_prep）——证伪 B3c/B2 前提
DOOR_DETAIL_LEAK = [_it(31, 641, '难度15'), _it(760, 490, '战斗'),
                    _it(300, 400, '精英 一点麻烦')]


class MockBot:
    """独立最小 mock，只实现被测路径真正触达的接口。"""

    def __init__(self, seq=None, default=None):
        self._seq = list(seq) if seq is not None else None
        self._default = list(default) if default is not None else list(PREP)
        self.calls = []
        self.clicked = []
        self.exited = []          # 记录"退出备战_返回按钮"类比例点击
        self.last_items = None

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('ocr')
        items = list(self._seq.pop(0)) if self._seq else list(self._default)
        self.last_items = items
        return items

    def find_hwnd(self):
        return 12345

    def press_esc(self, label=''):
        self.clicked.append(f'ESC:{label}')

    def press_f(self, label=''):
        self.clicked.append(f'F:{label}')

    def click_ratio(self, rx, ry, label=''):
        self.clicked.append(f'click_ratio:{label}@{rx},{ry}')
        if '退出备战' in label and '返回按钮' in label:
            self.exited.append((rx, ry))
        return True

    def click_at(self, x, y, label=''):
        self.clicked.append(f'click_at:{label}')
        return True

    def _click_abs(self, x, y):
        return True

    def click_back_button(self, *a, **kw):
        return False

    def template_match(self, *a, **kw):
        return None

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def scroll_wheel(self, *a, **kw):
        return True

    def long_press_at(self, *a, **kw):
        return True

    def long_press_ratio(self, *a, **kw):
        return True


def _mk(bot, logger=None):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    t = LabyrinthTask({
        'bot': bot, 'logger': logger or (lambda m: None), 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#verify',
    })
    # 生产侧所有等待统一走 _sleep 钩子；此处 no-op 以零真实耗时
    t._sleep = lambda sec=None: None
    return t


def _scan(pattern, roots):
    """在给定根下扫描 .py（跳过 backup 目录、注释行），返回命中列表。"""
    pat = re.compile(pattern)
    hits = []
    for root in roots:
        files = [root] if os.path.isfile(root) else []
        if not files:
            for dp, dn, fns in os.walk(root):
                if (os.sep + 'backup') in dp or os.path.basename(dp) == 'backup':
                    continue
                files += [os.path.join(dp, f) for f in fns if f.endswith('.py')]
        for p in files:
            try:
                lines = open(p, encoding='utf-8').read().splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, 1):
                if ln.strip().startswith('#'):
                    continue
                if pat.search(ln):
                    hits.append((os.path.relpath(p, ROOT), i, ln.strip()))
    return hits


print('═' * 62)
print('独立验证 + 证伪：未开战收尾修复（v2.1.14）')
print('═' * 62)

# ═══ 0. 帧素材判据自检（正控/负控 —— 保证后续结论建立在真实判据上）═══
print()
print('── 0. 帧素材判据自检（真实 screen_registry）──')
_t0 = _mk(MockBot())
check('0a PREP 帧 → battle_prep', _t0._detect_play_state(PREP) == 'battle_prep',
      _t0._detect_play_state(PREP))
check('0b DOOR 帧 → door', _t0._detect_play_state(DOOR) == 'door',
      _t0._detect_play_state(DOOR))
check('0c FINAL 帧 → final_settle', _t0._detect_play_state(FINAL) == 'final_settle',
      _t0._detect_play_state(FINAL))
check('0d VICTORY 帧 → victory', _t0._detect_play_state(VICTORY) == 'victory',
      _t0._detect_play_state(VICTORY))
check('0e UNKNOWN 帧 → unknown', _t0._detect_play_state(UNKNOWN) == 'unknown',
      _t0._detect_play_state(UNKNOWN))

# ═══ A. 跨轮计数 _no_battle_rounds 边界（mock 驱动真实 _play_loop）═══
print()
print('── A. 跨轮计数 _no_battle_rounds 边界 ──')

# (a) 连续两轮收尾失败 → 第 2 次 stopped
_ba = MockBot()                      # 永远返回 PREP
_ta = _mk(_ba)
_ta._battle_timeout_streak = 0
_ta._no_battle_rounds = 0
_ta._start_and_wait_battle = lambda: 'no_battle'
_ta._abort_round = lambda: ''
_ra1 = _ta._play_loop()
check('A1 第1次收尾失败 → done 且 _no_battle_rounds==1',
      _ra1 == 'done' and _ta._no_battle_rounds == 1,
      f'ret={_ra1!r} n={_ta._no_battle_rounds}')
_ta._battle_timeout_streak = 0       # 忠实模拟 _run_once 每轮重置该字段
_ra2 = _ta._play_loop()
check('A2 第2次连续失败 → stopped 且 _no_battle_rounds==2',
      _ra2 == 'stopped' and _ta._no_battle_rounds == 2,
      f'ret={_ra2!r} n={_ta._no_battle_rounds}')

# (b) 收尾失败后，下一轮正常开战 → 清零；再失败一次不应立即 stopped
_bb = MockBot(seq=[PREP, FINAL])     # 先 battle_prep（正常开战）→ 再结算页收尾
_tb = _mk(_bb)
_tb._battle_timeout_streak = 0
_tb._no_battle_rounds = 1            # 模拟上一轮收尾失败残留
_tb._start_and_wait_battle = lambda: ''   # '' = 正常开战
_rb1 = _tb._play_loop()
check('A3 正常开战 → _no_battle_rounds 清零',
      _tb._no_battle_rounds == 0, f'n={_tb._no_battle_rounds}')
check('A4 该轮正常结束', _rb1 == 'done', f'ret={_rb1!r}')

_bc = MockBot()
_tc = _mk(_bc)
_tc._battle_timeout_streak = 0
_tc._no_battle_rounds = 0
_tc._start_and_wait_battle = lambda: 'no_battle'
_tc._abort_round = lambda: ''
_rc1 = _tc._play_loop()
check('A5 清零后再失败一次 → 仍 done（计数=1，不得立即 stopped）',
      _rc1 == 'done' and _tc._no_battle_rounds == 1,
      f'ret={_rc1!r} n={_tc._no_battle_rounds}')

# (c) 收尾成功 → 清零
_bd = MockBot()
_td = _mk(_bd)
_td._no_battle_rounds = 1            # 已有 1 次失败
_td._battle_timeout_streak = 1       # 该轮已积 1 次未开战
_td._start_and_wait_battle = lambda: 'no_battle'
_td._abort_round = lambda: 'done'    # 收尾成功
_rd = _td._play_loop()
check('A6 收尾成功 → 计数清零且 done',
      _td._no_battle_rounds == 0 and _rd == 'done',
      f'ret={_rd!r} n={_td._no_battle_rounds}')

# (d) 未初始化访问：不先经 run() 直接进 _play_loop
_be = MockBot()
_te = _mk(_be)
# 故意不设置 _no_battle_rounds（模拟"从前未走过 run()"的调用路径）
_te._battle_timeout_streak = 1
_te._start_and_wait_battle = lambda: 'no_battle'
_te._abort_round = lambda: ''
_raised = None
try:
    _te._play_loop()
except AttributeError as exc:
    _raised = exc
check('A7 【v2.1.14 已加固】类级默认 `_no_battle_rounds` → 直调 _play_loop 不再抛 AttributeError',
      _raised is None, repr(_raised))

# ═══ B. 证伪探针 ═══
print()
print('── B. 证伪探针（重点：是否引入/残留新风险）──')

# B1 转场 unknown 帧 → 提前判"已离开备战"
_b1 = MockBot(seq=[UNKNOWN])
_t1 = _mk(_b1)
_r1 = _t1._leave_battle_prep()
check('B1 【v2.1.14 已修】unknown 帧不再直接判"已离开"（会再确认一帧）',
      _r1 is False, f'ret={_r1!r}  (unknown → 二次确认)')

# B2 陈旧帧：ESC 实际已生效(真帧 DOOR)，但首次 OCR 返回旧 PREP 帧 → 仍点左下角
_b2 = MockBot(seq=[PREP, DOOR])
_t2 = _mk(_b2)
_r2 = _t2._leave_battle_prep()
_back_clicks = [c for c in _b2.clicked if '返回按钮' in c]
check('B2 【v2.1.14 已修】陈旧帧被二次 OCR 过滤 → 不再白点左下角返回按钮',
      _r2 is True and not _back_clicks, str(_b2.clicked))

# B3 守卫：door 帧绝不能被判 battle_prep
_t3 = _mk(MockBot())
_st_door = _t3._detect_play_state(DOOR)
check('B3a door 帧 → 非 battle_prep（DOOR_WORDS 排除生效）',
      _st_door != 'battle_prep', f'state={_st_door}')
_mixed = [_it(31, 641, '难度15'), _it(760, 490, '战斗'), _it(700, 640, '结束探索')]
_st_mix = _t3._detect_play_state(_mixed)
check('B3b 含「结束探索」+难度+战斗 → 非 battle_prep',
      _st_mix != 'battle_prep', f'state={_st_mix}')
_st_leak = _t3._detect_play_state(DOOR_DETAIL_LEAK)
check('B3c 漏读 door 词的「难度+战斗」帧 → 确被判 battle_prep（误判前提成立）',
      _st_leak == 'battle_prep', f'state={_st_leak}')

# B4 残留缺口：_end_exploration 仅凭"点中按钮"就报 done
_b4 = MockBot(seq=[DOOR, DOOR, DOOR, PREP])
_t4 = _mk(_b4)
_r4 = _t4._abort_round()
_last_state = _t4._detect_play_state(_b4.last_items)
check('B4 【v2.1.14 已修】结束探索后末帧仍为 door/备战 → 不再误报 done（返回空串）',
      _r4 == '' and _last_state in ('battle_prep', 'door'),
      f'ret={_r4!r} last_state={_last_state}')

# ═══ C. press_esc 抢焦点 / press_esc_background 调用点核查 ═══
print()
print('── C. press_esc 抢焦点 与 press_esc_background 调用点核查 ──')
_roots = [os.path.join(ROOT, 'tasks'), os.path.join(ROOT, 'afk_bot'),
          os.path.join(ROOT, 'afk_gui.py')]

# C1: press_esc_background —— 应只有定义、无调用
_bg = _scan(r'press_esc_background', _roots)
_bg_calls = [h for h in _bg if 'def press_esc_background' not in h[2]]
check('C1 press_esc_background 无任何生产调用点（仅定义）',
      len(_bg_calls) == 0, f'hits={_bg_calls}')

# C2/C3: press_esc 委托 send_esc；send_esc 抢焦点 + 真实按键
_input_src = open(os.path.join(ROOT, 'afk_bot', 'infra', 'input.py'),
                  encoding='utf-8').read()
check('C2 press_esc 已委托 send_esc（不再是 SendMessage）',
      re.search(r'def press_esc\(.*?\n(?:.*\n)*?\s*self\.send_esc\(\)', _input_src)
      is not None, '')
check('C3 send_esc = activate_window(抢焦点) + keybd_event(0x1B)',
      'activate_window' in _input_src and 'keybd_event(0x1B' in _input_src, '')
check('C4 press_esc_background 降级实现仍用 SendMessage（后台无焦点）',
      re.search(r'def press_esc_background\(.*?SendMessage', _input_src,
                re.S) is not None, '')

# C5: 枚举全部生产 press_esc 调用点
_calls = _scan(r'\.press_esc\(', _roots)
print(f'   生产 press_esc 调用点共 {len(_calls)} 处：')
for rel, ln, txt in _calls:
    print(f'     - {rel}:{ln}  {txt}')
_by_file = {}
for rel, ln, txt in _calls:
    _by_file[rel] = _by_file.get(rel, 0) + 1
check('C5 生产调用点集中在 tasks/（facade 透传 1 处）且均走 send_esc(抢焦点)',
      len(_calls) >= 9 and any('facade' in r for r in _by_file),
      f'files={_by_file}')

# ═══ 汇总 ═══
print()
print('═' * 62)
print(f'独立验证结果：{len(_PASS)}/{len(_PASS) + len(_FAIL)} 通过')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print('  ', f_)
print('═' * 62)
sys.exit(0 if not _FAIL else 1)
