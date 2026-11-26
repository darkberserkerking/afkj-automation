# -*- coding: utf-8 -*-
"""v2.4.7：OCR 阈值一致性回归（全库审计 + 防回退）。

背景（用户 2026-11-14 要求）：排查「同一目标在判画面 / 点按钮两处用了
不同阈值或不同匹配标准」。v2.4.6 天穹「挑战！」是判 0.7 / 点 0.85，
真机实时帧 conf 0.78 卡在中间 → 认得出页面却点不到按钮 → 误判通关。

治理口径：
  可点目标（按钮/卡片/页签）→ 判定与定位共用 OCR_TARGET_CONF=0.6
                                且**共用同一个匹配函数**
  画面级多词 AND 判据   → OCR_SCREEN_CONF=0.5（语义不同，刻意保留）
  安全/坐标/图像模板判据 → 各自独立常量（误点代价高 / 量纲不同）

覆盖：
  A. 单一真相源常量的取值与分层
  B. 各模块常量是否指向统一值（防漂移）+ 死常量清理 + 字面量清零
  C. 友情点「一键赠礼」：0.65 帧「判得出 ⇒ 点得到」行为分叉
  D. 天穹：判定与定位同标准（判子串 / 点全等的分叉已消除）
  E. 保留项未被误改（画面级 0.5 / 退出弹窗 0.8 / 模板阈值）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_threshold_consistency.py
"""
import glob
import inspect
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time  # noqa: E402
fast_time.install()

import tasks.text_conf as tc            # noqa: E402
import tasks.screen_registry as sr      # noqa: E402
import tasks.base as base               # noqa: E402
import tasks.tianqiong_trial as tq      # noqa: E402
import tasks.goddess_trial as gd        # noqa: E402
import tasks.friend_gift as fg          # noqa: E402
import tasks.season_equipment as se     # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def it(cy, cx, text, conf):
    return (cy, cx, text, conf, int(cx) - 40, int(cx) + 40)


def src_of(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def task_cls(mod):
    return next(v for k, v in vars(mod).items()
                if k.endswith('Task') and isinstance(v, type)
                and v.__module__ == mod.__name__)


def mk(mod):
    """最小 ctx：判据类方法只用 items，不需要真 bot。"""
    pe = threading.Event()
    pe.set()
    return task_cls(mod)({
        'bot': None, 'logger': lambda m: None, 'stats': {},
        'stop_event': threading.Event(), 'pause_event': pe,
        'options': {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'conf#1',
    })


print('══ v2.4.7 OCR 阈值一致性回归 ══')
print()

# ── A. 单一真相源 ───────────────────────────────────────────
print('── A. 单一真相源取值与分层 ──')
check('A1 可点目标统一值 = 0.6', tc.OCR_TARGET_CONF == 0.6,
      str(tc.OCR_TARGET_CONF))
check('A2 画面级判据 = 0.5 且严格低于统一值',
      tc.OCR_SCREEN_CONF == 0.5
      and tc.OCR_SCREEN_CONF < tc.OCR_TARGET_CONF,
      f'{tc.OCR_SCREEN_CONF} < {tc.OCR_TARGET_CONF}')
check('A3 退出确认/家园按钮保留 0.8（安全与坐标约束）',
      tc.OCR_QUIT_CONF == 0.8 and tc.OCR_HOME_BTN_CONF == 0.8)
check('A4 常量模块零外部依赖（不会引发循环导入）',
      'import ' not in src_of('tasks/text_conf.py').replace('# import ', ''))
print()

# ── B. 各模块是否真的指向统一值 ──────────────────────────────
print('── B. 常量接线 / 死常量 / 字面量清零 ──')
check('B1 天穹 4 个文本常量全部 = 统一值',
      tq._TEXT_MIN_CONF == tq._CHALLENGE_MIN_CONF
      == tq._GO_MIN_CONF == tq._CARD_MIN_CONF
      == tc.OCR_TARGET_CONF,
      f'{tq._TEXT_MIN_CONF}/{tq._CHALLENGE_MIN_CONF}/'
      f'{tq._GO_MIN_CONF}/{tq._CARD_MIN_CONF}')
check('B2 女神 _MIN_CONF / _CARD_MIN_CONF = 统一值',
      gd._MIN_CONF == gd._CARD_MIN_CONF == tc.OCR_TARGET_CONF)
check('B3 screen_registry / BaseTask / 女神._texts 默认都是画面级常量',
      inspect.signature(sr._texts).parameters['conf'].default
      == tc.OCR_SCREEN_CONF
      and inspect.signature(base.BaseTask._texts
                            ).parameters['min_conf'].default
      == tc.OCR_SCREEN_CONF
      and inspect.signature(task_cls(gd)._texts
                            ).parameters['min_conf'].default
      == tc.OCR_SCREEN_CONF)
check('B4 _BTN_MIN_CONF 死常量已移除（0.85 无残留引用）',
      not hasattr(tq, '_BTN_MIN_CONF')
      and 'tq._BTN_MIN_CONF' not in src_of(
          'tests/test_tianqiong_challenge_conf.py'))

_bad = []
for _p in glob.glob(os.path.join(ROOT, 'tasks', '*.py')):
    _s = src_of(os.path.relpath(_p, ROOT).replace('\\', '/'))
    for _m in re.finditer(r'min_conf\s*=\s*0\.(?:7|75|8|85)\b', _s):
        _bad.append((os.path.basename(_p), _m.group(0)))
check('B5 全库不再有 0.7/0.75/0.8/0.85 的 OCR 按钮阈值字面量',
      not _bad, str(_bad[:4]))
_modules = ['tasks/base.py', 'tasks/screen_registry.py',
            'tasks/global_nav.py', 'tasks/tianqiong_trial.py',
            'tasks/goddess_trial.py', 'tasks/guild_battle.py',
            'tasks/arena.py', 'tasks/daily_quest.py',
            'tasks/friend_gift.py', 'tasks/auto_farm.py',
            'tasks/labyrinth.py', 'tasks/home_task.py']
check('B6 全部 12 个改动模块都 import 了共享常量',
      all('tasks.text_conf import' in src_of(m) for m in _modules),
      str([m for m in _modules
           if 'tasks.text_conf import' not in src_of(m)]))
print()

# ── C. 友情点：判得出 ⇒ 点得到（旧 0.5/0.8 分叉）────────────
print('── C. 友情点「一键赠礼」行为分叉 ──')
_g = mk(fg)
_frame = [it(700, 640, '一键赠礼并收取', 0.65)]
check('C1 0.65 帧仍判为好友界面', _g._on_friend_page(_frame) is True)
check('C2 同一帧能定位到按钮（旧 0.8 必然漏检）',
      _g._find_gift_btn(_frame) == (640, 700),
      str(_g._find_gift_btn(_frame)))
check('C3 判定与定位共用同一函数（源码级证据）',
      src_of('tasks/friend_gift.py').count('_find_gift_btn(') >= 3
      and "find_text(items, '一键赠礼'" not in src_of('tasks/friend_gift.py'))
_low = [it(700, 640, '一键赠礼并收取', 0.45)]
check('C4 0.45 噪声帧不判为好友界面（不误点）',
      _g._on_friend_page(_low) is False
      and _g._find_gift_btn(_low) is None)
_old_miss = not any(
    len(x) >= 4 and x[3] >= 0.8 and '一键赠礼' in str(x[2])
    for x in _frame)
check('C5 旧阈值 0.8 在 0.65 帧确实漏检（锁死改版理由）', _old_miss)
check('C6 旧判定 0.5 会把它当好友页（复现分叉）',
      any(len(x) >= 4 and x[3] >= tc.OCR_SCREEN_CONF
          and '一键赠礼' in str(x[2]) for x in _frame))
print()

# ── D. 天穹：判定与定位同标准 ────────────────────────────────
print('── D. 天穹判定/定位同标准（判子串 / 点全等已消除）──')
_t = mk(tq)
_prep = [it(900, 300, '通关阵容', 0.65), it(901, 940, '战斗', 0.99)]
check('D1 备战页 0.65 帧判定 True', _t._is_battle_prep(_prep) is True)
check('D2 同帧定位「通关阵容」非空',
      _t._find_exact(_prep, '通关阵容', tc.OCR_TARGET_CONF) == (300, 900))
_res = [it(950, 800, '下一关', 0.65)]
check('D3 结算页 0.65 帧判定 True', _t._is_result_page(_res) is True)
check('D4 同帧定位「下一关」非空',
      _t._find_exact(_res, '下一关', tc.OCR_TARGET_CONF) == (800, 950))
_trap = [it(950, 800, '点击继续挑战按钮', 0.99)]
check('D5 说明文字不再误判为结算页（修复前子串会命中、按钮却点不到）',
      _t._is_result_page(_trap) is False
      and _t._find_exact(_trap, '继续挑战', tc.OCR_TARGET_CONF) is None)
_art = [it(300, 640, '战斗胜利', 0.99)]
check('D6 艺术字标题仍作子串兜底（横竖屏差异 v2.3.42）',
      _t._is_result_page(_art) is True)
print()

# ── E. 保留项未被误改 ────────────────────────────────────────
print('── E. 合理差异保留 ──')
check('E1 画面级判据仍 0.5（0.55 结构词依然可读）',
      sr._texts([it(1, 1, '神秘屋', 0.55)]) == ['神秘屋'])
check('E2 退出确认弹窗仍 0.8（低分帧不误点「确定」）',
      base.OCR_QUIT_CONF == tc.OCR_QUIT_CONF == 0.8)
check('E3 模板阈值保持独立（图像相似度，量纲不同）',
      se._TPL_THRESHOLD == 0.82 and se._MAIN_ICON_THRESHOLD == 0.70)
print()

_total = PASSED[0] + len(FAILED)
print(f'通过 {PASSED[0]}/{_total}')
if FAILED:
    print('失败用例：')
    for _n in FAILED:
        print(f'❌ {_n}')
    sys.exit(1)
