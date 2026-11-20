# -*- coding: utf-8 -*-
"""v2.4.8 真机回归：取消「更新装备」全局点击硬上限 90 次（误杀正常任务）。

现场（用户日志 2026-11-15 17:42~17:43）：
    连续成功穿戴 24→28 件，随后
    [错误] 点击硬上限 90 次，判卡住（可能资源不足或动画异常）
    → season_equipment#1 失败、调度中止。

根因：_CLICK_HARD_MAX=90 按「6 职业 × 6 件 × 2 次」估算，但每件成功
锻造实际要 3 次点击（卡片锻造 → 弹窗锻造 → 穿戴新装备），加上切职业
远超 90；累计点击数根本不是「卡住」的证据。

分叉标准（真实构造 + 真实调用 _forge_loop，非 mock 断言）：
  · 修复前（等价旧类注入 90 次上限）→ failed，日志含「点击硬上限」
  · 修复后（当前源码）→ done，穿戴 30 件、累计点击 > 90 次
  · 真卡死场景仍须 failed：弹窗不关闭 / 成功页常驻 / 未知帧

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_season_click_cap.py
"""
import io
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time  # noqa: E402
fast_time.install()

import tasks.catalog  # noqa: F401,E402  import 即注册
from tasks.season_equipment import (  # noqa: E402
    CLASS_TPL, SeasonEquipmentTask,
)

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def it(y, x, text, conf=0.99):
    """OCR item：(cy, cx, text, conf, x0, x1) —— y 在前。"""
    return (y, x, text, conf, x - 20, x + 20)


# 坐标取自真机 MaaOCR（1280x1024 / 1283x1026 截图），不凑数
CARD_Y, CARD_X = 797, 516        # 卡片「锻造」 ry≈0.778
DIALOG_Y, DIALOG_X = 688, 744    # 弹窗「锻造」 ry≈0.672
WEAR_Y, WEAR_X = 950, 642        # 「穿戴新装备」 ry≈0.927


def f_class(forge=True, zh='坦克'):
    rows = [it(644, 212, f'{zh}装备'),
            it(644, 1004, '锻造等级上限：700')]
    if forge:
        rows.append(it(CARD_Y, CARD_X, '锻造'))
    else:
        rows.append(it(856, 636, '当前暂无可锻造的装备'))
    return rows


def f_dialog():
    return [it(386, 508, '当前装备'), it(386, 752, '可锻造装备'),
            it(DIALOG_Y, DIALOG_X, '锻造')]


def f_success():
    return [it(72, 639, '锻造成功'),
            it(WEAR_Y, WEAR_X, '穿戴新装备')]


class _SeqBot:
    """帧序列桩：耗尽后保持最后一帧（每用例必须新建，防假绿）。"""

    def __init__(self, frames, tpl_hits=None):
        self.frames = list(frames) or [[]]
        self._i = -1
        self.clicks = []
        self.tpl_calls = []
        self.tpl_hits = tpl_hits if tpl_hits is not None else ALL_TPL

    def _ocr_screen(self, img=None, no_cache=False):
        self._i = min(self._i + 1, len(self.frames) - 1)
        return list(self.frames[self._i])

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.tpl_calls.append(name)
        return self.tpl_hits.get(name)

    def find_and_focus(self):
        return 12345

    def window_rect(self):
        return (0, 0, 1283, 1026)

    def click_at(self, x, y, label=''):
        self.clicks.append((label, x, y))
        return True

    def find_text(self, items, kw, min_conf=0.6, exact=False):
        """v2.4.10：BaseTask.dismiss_blink_popup 会调此方法，桩返回未命中。"""
        return None

    def _click_abs(self, x, y):
        self.clicks.append(('_click_abs', x, y))
        return True

    def dismiss_blink_popup(self, items=None):
        return False


ALL_TPL = {os.path.basename(p): (500, 986) for p in CLASS_TPL.values()}


def run_case(task_cls, frames):
    bot = _SeqBot(frames, tpl_hits=ALL_TPL)
    logs = []
    paus = threading.Event()
    paus.set()
    t = task_cls({
        'bot': bot, 'logger': lambda m: logs.append(m),
        'stats': {}, 'stop_event': threading.Event(), 'pause_event': paus,
        'options': {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'season_equipment#1',
    })
    return t.run(), bot, logs


# ══ A. 源码静态断言（防硬上限回潮）══════════════════════════
print('── A. 源码静态断言 ──')
TASK_SRC = os.path.join(ROOT, 'tasks', 'season_equipment.py')
_src = io.open(TASK_SRC, encoding='utf-8').read()
check('A1 源码不再定义 _CLICK_HARD_MAX', '_CLICK_HARD_MAX' not in _src)
check('A2 源码不再有「累计点击数」失败判据',
      'if clicks >=' not in _src and '点击硬上限 {_CLICK_HARD_MAX}' not in _src)
check('A3 clicks 仍保留作日志诊断（未误删计数）',
      'clicks += 1' in _src and '累计点击 {clicks} 次' in _src)
check('A4 失败判定唯一收口在循环顶部（底部无重复分支）',
      _src.count('未识别到可操作界面') == 1
      and 'if stall >= _STALL_MAX:' in _src,
      f"日志出现 {_src.count('未识别到可操作界面')} 次")
check('A5 成功页按「未推进」累加 stall（防点击无效死循环）',
      'stall += 1\n                    no_progress = 0' in _src)
check('A6 所有 _ocr_screen 仍强制 no_cache=True',
      _src.count('self.bot._ocr_screen(no_cache=True)') == 4
      and 'self.bot._ocr_screen()' not in _src)
check('A7 v2.4.10 主界面判据已并回全局注册表（任务内不再自建 _W_MAIN）',
      '_W_MAIN =' not in _src and 'GLOBAL_SCREEN_REGISTRY' in _src)


# ══ B. 修复前等价旧类：把删除的 90 次硬上限注回去 ══════════
print('\n── B. 修复前等价旧类 ──')
_ANCHOR = """            if self.check_stop():
                return self._stop_result()
            # v2.4.8：删除「累计点击 ≥90 判卡住」（误杀正常推进）。
"""
assert _src.count(_ANCHOR) == 1, '锚点必须唯一，否则旧类构造不可信'
_back = _src.replace(
    _ANCHOR,
    """            if self.check_stop():
                return self._stop_result()
            if clicks >= 90:
                self.log("[错误] 点击硬上限 90 次，判卡住")
                return 'failed'
            # v2.4.8：删除「累计点击 ≥90 判卡住」（误杀正常推进）。
""", 1)
_back = _back.split('TaskRegistry.register(')[0]
_ns = {'__name__': '_season_click_cap_legacy', '__file__': TASK_SRC}
exec(compile(_back, TASK_SRC, 'exec'), _ns)   # noqa: S102
LegacyTask = _ns['SeasonEquipmentTask']
check('B1 等价旧类构造成功（注入了 90 次累计点击上限）',
      LegacyTask is not SeasonEquipmentTask)

# 31 件连续成功锻造：每件 3 次点击 = 93 次，再加 5 次切职业 = 98 次。
# 旧上限在第 90 次点击后下一轮触发 → 恰好卡在第 30 件穿戴完、第 31 件之前。
N_FORGE = 31
LONG_FRAMES = (
    [f_class()]                                             # startup_probe
    + [f_class(), f_dialog(), f_success()] * N_FORGE        # 循环
    + [f_class(False)] * 8                                  # 收尾：逐职业完成
)

res_old, bot_old, logs_old = run_case(LegacyTask, LONG_FRAMES)
check('B2 修复前 → failed（真机同款误杀）', res_old == 'failed', res_old)
check('B3 修复前日志明确是「点击硬上限 90 次」',
      any('点击硬上限 90 次' in l for l in logs_old),
      str([l for l in logs_old if '错误' in l][-1:]))
wear_old = sum(1 for c in bot_old.clicks if c[0] == '穿戴新装备')
check('B4 修复前在正常推进中途被打断（穿戴未跑完、非真卡死）',
      0 < wear_old < N_FORGE and len(bot_old.clicks) >= 90,
      f'穿戴 {wear_old}/{N_FORGE} 件 / 点击 {len(bot_old.clicks)} 次')


# ══ C. 修复后：同一场景必须正常跑到 done ══════════════════
print('\n── C. 修复后行为分叉 ──')
res_new, bot_new, logs_new = run_case(SeasonEquipmentTask, LONG_FRAMES)
check('C1 修复后 → done（同一场景分叉成功）', res_new == 'done', res_new)
wear_new = sum(1 for c in bot_new.clicks if c[0] == '穿戴新装备')
check('C2 31 件全部穿戴完成，未中途被打断',
      wear_new == N_FORGE, f'{wear_new}/{N_FORGE} 件')
check('C3 累计点击确实超过旧上限 90（证明不是靠少点击绕开）',
      len(bot_new.clicks) > 90, f'{len(bot_new.clicks)} 次')
check('C4 日志全程无「点击硬上限」',
      not any('点击硬上限' in l for l in logs_new))
check('C5 done 日志带累计点击诊断（clicks 仅诊断）',
      any('累计点击' in l and '六职业' in l for l in logs_new),
      str([l for l in logs_new if '累计点击' in l][-1:]))
check('C6 穿戴后正常切完剩余 5 个职业',
      sum(1 for c in bot_new.clicks if c[0] == '_click_abs') == 5,
      f'{sum(1 for c in bot_new.clicks if c[0] == "_click_abs")} 次切换')


# ══ D. 护栏未被削弱：真卡死仍须 failed ════════════════════
print('\n── D. 真卡死护栏 ──')
# D1 弹窗不关闭（资源不足）：连续点锻造无成功页 → 8 次止损
res_d1, bot_d1, logs_d1 = run_case(
    SeasonEquipmentTask, [f_class()] + [f_dialog()] * 40)
check('D1 弹窗不关闭 → failed（不空转）', res_d1 == 'failed', res_d1)
check('D2 弹窗点击 ≤8 次即止损',
      0 < sum(1 for c in bot_d1.clicks if c[0] == '弹窗_锻造') <= 8,
      f'{sum(1 for c in bot_d1.clicks if c[0] == "弹窗_锻造")} 次')

# D3 成功页常驻（穿戴点击无效）：旧代码此处是死循环（continue 绕过
#   底部 stall 检查，且 clicks 不增长 → 90 次上限也兜不住）。
#   新护栏必须在 20 帧内退出。
res_d3, bot_d3, logs_d3 = run_case(
    SeasonEquipmentTask, [f_success()] * 40)
check('D3 成功页常驻 → failed（新护栏覆盖被 continue 绕过的分支）',
      res_d3 == 'failed', res_d3)
check('D4 成功页常驻在 20 帧内止损（点击数有界）',
      len(bot_d3.clicks) <= 21, f'{len(bot_d3.clicks)} 次点击')
check('D5 失败日志含「未识别到可操作界面」+ 成功页常驻说明',
      any('未识别到可操作界面' in l and '成功页常驻' in l
          for l in logs_d3),
      str([l for l in logs_d3 if '错误' in l][-1:]))

# D6 循环内全未知帧（动画卡住）→ stalled failed。
#   首帧给职业页使 startup_probe 返回 ready，避免在桩里补导航接口。
res_d6, bot_d6, logs_d6 = run_case(
    SeasonEquipmentTask,
    [f_class()] + [[it(300, 300, '无法识别的画面')]] * 30)
check('D6 连续未知帧 → failed', res_d6 == 'failed', res_d6)

# D7 手动停止仍即时响应
bot_stop = _SeqBot([f_class()], tpl_hits=ALL_TPL)
logs_stop = []
paus = threading.Event(); paus.set()
stop_ev = threading.Event(); stop_ev.set()
t_stop = SeasonEquipmentTask({
    'bot': bot_stop, 'logger': lambda m: logs_stop.append(m),
    'stats': {}, 'stop_event': stop_ev, 'pause_event': paus,
    'options': {}, 'max_time': None, 'max_turns': None,
    'start_turns': 0, 'inst_id': 'season_equipment#1',
})
check('D7 手动停止 → stopped 且零点击',
      t_stop.run() == 'stopped' and len(bot_stop.clicks) == 0,
      str(bot_stop.clicks[:2]))

print('\n' + '=' * 60)
if _FAIL:
    print(f'失败 {len(_FAIL)} 项：')
    for n in _FAIL:
        print('   -', n)
    sys.exit(1)
print(f'全部通过（{len(_PASS)}/{len(_PASS)}）')
