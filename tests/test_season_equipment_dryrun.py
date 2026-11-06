# -*- coding: utf-8 -*-
"""更新装备（赛季职业装备锻造）干跑测试（v2.3.37）。

覆盖：
  1. 入口导航 / 主界面进职业 / 职业页找锻造 / 弹窗二次锻造 / 穿戴
  2. **弹窗态不得误点背景 ry≈0.87 的 6 个「锻造」**（真机截图陷阱）
  3. 「无锻造按钮」→ 切下一职业；6 职业全完成 → done（自然完成不中止队列）
  4. 已达 700 级装备无按钮（法师/战士/坦克页按钮数不足 6）不被强点
  5. 无进展护栏 / 全局点击硬上限 / 模板未命中判失败 / 即时停止
  6. 6 个 RGBA 模板存在、圆外透明、颜色一致、生产同款 maskedNCC 命中

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_season_equipment_dryrun.py
"""
import os
import sys
import threading

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time  # noqa: E402
fast_time.install()

import tasks.catalog  # noqa: F401,E402  import 即注册
from task_registry import TaskRegistry  # noqa: E402
from tasks.season_equipment import (  # noqa: E402
    CLASS_ORDER, CLASS_TPL, SeasonEquipmentTask,
    _DIALOG_FORGE_RY_MAX, _IMG_H, _W_CLASS, _W_CLASS_DONE, _W_DIALOG,
    _W_MAIN,
)

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def it(y, x, text, conf=0.99):
    """OCR item：(cy, cx, text, conf, x0, x1) —— y 在前。"""
    return (y, x, text, conf, x - 20, x + 20)


# ── 帧构造（坐标来自真机 MaaOCR，1283x1026 截图）──────────────
CARD_X = (434, 518, 603, 688, 772, 856)
CARD_Y = 798          # ry≈0.776
DIALOG_Y = 687        # ry≈0.671
BG_FORGE_Y = 888      # 弹窗态背景卡片按钮 ry≈0.867


def f_entry():
    return [it(32, 210, '英雄厅堂'), it(340, 636, '赛季共鸣等级：705'),
            it(890, 770, '赛季装备'), it(1006, 684, '英雄厅堂')]


def f_main():
    return [it(58, 292, '季职业装备'), it(900, 641, '待领宝箱列表'),
            it(983, 859, '当前赛季'), it(524, 407, '辅助'),
            it(634, 419, '法师'), it(752, 433, '战士')]


def f_class(zh='辅助', forge_cols=(0, 1, 2, 3, 4, 5)):
    """职业装备页；forge_cols 指定哪些件还有「锻造」按钮。"""
    rows = [it(644, 212, f'{zh}装备'),
            it(644, 1004, '锻造等级上限：700'),
            it(856, 638, '当前无可替换装备')]
    for c in forge_cols:
        rows.append(it(CARD_Y, CARD_X[c], '锻造'))
    return rows


def f_class_no_forge(zh='辅助'):
    return f_class(zh, ())


def f_dialog():
    """锻造弹窗：弹窗「锻造」在 ry 0.671，背景还有 6 个 ry 0.867。"""
    rows = [it(386, 508, '当前装备'), it(386, 752, '可锻造装备'),
            it(534, 510, '冰蓝秘典（620级）'),
            it(534, 744, '冰蓝秘典（700级）'),
            it(734, 1012, '锻造等级上限：700'),
            it(DIALOG_Y, 745, '锻造')]
    for c in CARD_X:
        rows.append(it(BG_FORGE_Y, c, '锻造'))
    return rows


def f_success():
    return [it(72, 639, '锻造成功'), it(492, 642, '700级'),
            it(950, 642, '穿戴新装备')]


class _SeqBot:
    """帧序列桩：耗尽后保持最后一帧（每用例必须新建，防假绿）。"""

    def __init__(self, frames, tpl_hits=None, nav_ok=True):
        self.frames = list(frames) or [[]]
        self._i = -1
        self.clicks = []
        self.tpl_calls = []
        self.tpl_hits = tpl_hits or {}
        self.nav_ok = nav_ok
        self.stop_calls = 0

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

    def _click_abs(self, x, y):
        self.clicks.append(('_click_abs', x, y))
        return True

    def click_ratio(self, rx, ry, label=''):
        self.clicks.append((label, rx, ry))
        return True

    def drag_abs(self, *a, **kw):
        return True

    def dismiss_blink_popup(self, items=None):
        return False


def make_task(bot, stop=False):
    logs = []
    paus = threading.Event()
    paus.set()
    stop_ev = threading.Event()
    if stop:
        stop_ev.set()
    t = SeasonEquipmentTask({
        'bot': bot, 'logger': lambda m: logs.append(m),
        'stats': {}, 'stop_event': stop_ev, 'pause_event': paus,
        'options': {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'season_equipment#1',
    })
    return t, logs


def run_case(bot, stop=False):
    t, logs = make_task(bot, stop=stop)
    return t.run(), logs, bot


# ══ 1. 元数据与模板 ═══════════════════════════════════════
print('── 1. 注册与模板 ──')
m = TaskRegistry.get_by_id('season_equipment')
check('1a 任务已注册', m is not None, str(m.label if m else None))
check('1b 名称=更新装备 / 默认不勾选',
      m.label == '更新装备' and not m.default_enabled,
      f'{m.label} enabled={m.default_enabled}')
check('1c description ≤20 字', len(m.description) <= 20,
      f'{len(m.description)}字: {m.description}')
check('1d 六职业顺序正确',
      CLASS_ORDER == ('辅助', '法师', '战士', '游侠', '射手', '坦克'),
      str(CLASS_ORDER))

for zh, path in CLASS_TPL.items():
    a = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                     cv2.IMREAD_UNCHANGED)
    ok = (a is not None and a.ndim == 3 and a.shape[2] == 4
          and bool((a[:, :, 3] < 128).any())
          and int(np.sum(a[:, :, 3] == 255)) > 0)
    check(f'1e [{zh}] RGBA 模板圆外透明', ok,
          f'{a.shape if a is not None else None}')

# ══ 2. 状态识别与弹窗陷阱 ═════════════════════════════════
print('\n── 2. 状态识别 / 弹窗不得误点背景「锻造」 ──')
bot = _SeqBot([f_dialog()])
t, _ = make_task(bot)
items = f_dialog()
pos = t._dialog_forge_pos(items)
check('2a 弹窗态取 ry<0.72 的锻造按钮', pos == (745, DIALOG_Y), str(pos))
check('2b 弹窗按钮 y 确实小于背景按钮', DIALOG_Y / _IMG_H < 0.72
      and BG_FORGE_Y / _IMG_H > 0.72,
      f'{DIALOG_Y/_IMG_H:.3f} vs {BG_FORGE_Y/_IMG_H:.3f}')
check('2c 背景 6 个锻造按钮全在卡片行之外',
      all(x / _IMG_H > _DIALOG_FORGE_RY_MAX
          for x in [BG_FORGE_Y]), '')
card = t._card_forge_pos(f_class('辅助'))
check('2d 职业页取最左卡片锻造按钮', card == (CARD_X[0], CARD_Y), str(card))
check('2e 无可锻造按钮时返回 None',
      t._card_forge_pos(f_class_no_forge()) is None)
check('2f 已达 700 级（4 个按钮）不被强点成 6 个',
      t._card_forge_pos(f_class('法师', (0, 1, 3, 5))) == (CARD_X[0], CARD_Y))
blob_main = ' '.join(x[2] for x in f_main())
blob_cls = ' '.join(x[2] for x in f_class())
check('2g 主界面双词判据成立', all(k in blob_main for k in _W_MAIN))
check('2h 职业页双词判据成立', all(k in blob_cls for k in _W_CLASS))
check('2i 弹窗双词判据成立',
      all(k in ' '.join(x[2] for x in f_dialog()) for k in _W_DIALOG))

# ══ 3. 完整一轮：锻造 → 穿戴 → 完成 → 切职业 → 全 done ════
print('\n── 3. 完整状态机（六职业轮换）──')
all_tpl = {os.path.basename(p): (500, 986) for p in CLASS_TPL.values()}
# 构造：每职业 1 件可锻造 → 弹窗 → 成功；再无按钮 → 切下一个
frames = [f_class('辅助', (0,))]
for _ in range(6):
    frames += [f_dialog(), f_success(), f_class_no_forge()]
bot = _SeqBot(frames, tpl_hits=all_tpl)
res, logs, bot = run_case(bot)
check('3a 自然完成返回 done（不中止队列）', res == 'done', res)
n_forge = sum(1 for c in bot.clicks if c[0] == '弹窗_锻造')
n_wear = sum(1 for c in bot.clicks if c[0] == '穿戴新装备')
check('3b 弹窗二次锻造被点击', n_forge >= 1, f'{n_forge} 次')
check('3c 穿戴新装备被点击', n_wear >= 1, f'{n_wear} 次')
check('3d 无一次误点背景按钮（弹窗点击均取 y=687）',
      all(c[2] == DIALOG_Y for c in bot.clicks if c[0] == '弹窗_锻造'),
      str([c for c in bot.clicks if c[0] == '弹窗_锻造'][:3]))
check('3e 职业 tab 模板被调用（纯图标走模板）',
      any(n.startswith('season_class_') for n in bot.tpl_calls),
      str(bot.tpl_calls[:4]))
check('3f 完成日志含「六职业全部锻造完成」',
      any('六职业全部锻造完成' in l for l in logs),
      str([l for l in logs if '完成' in l][-2:]))

# ══ 4. 护栏 ═══════════════════════════════════════════════
print('\n── 4. 护栏 ──')
# 4a 一直停在职业页点锻造但永不出弹窗 → 无进展护栏
bot = _SeqBot([f_class('辅助')] * 30, tpl_hits=all_tpl)
res, logs, _ = run_case(bot)
check('4a 点锻造无进展 → failed（不死循环）', res == 'failed', res)
check('4b 失败原因含「未见锻造成功页」',
      any('未见锻造成功页' in l for l in logs),
      str([l for l in logs if '卡住' in l][-1:]))
# 4c 模板未命中 → failed（符号类不得改 OCR 猜）
bot = _SeqBot([f_main()] * 5, tpl_hits={})
res, logs, _ = run_case(bot)
check('4c 主界面圆标未命中 → failed 而非 OCR 兜底', res == 'failed', res)
check('4d 失败日志说明检索范围与未匹配原因',
      any('符号类不改用 OCR' in l for l in logs),
      str([l for l in logs if '模板未命中' in l][-1:]))
# 4e 即时停止
bot = _SeqBot([f_class('辅助')], tpl_hits=all_tpl)
res, logs, bot = run_case(bot, stop=True)
check('4e 停止即时响应 → stopped', res == 'stopped', res)
check('4f 停止后未产生任何点击', len(bot.clicks) == 0, str(bot.clicks[:3]))

# ══ 5. 模板在真实截图上交叉验证（生产同款 maskedNCC）═══════
print('\n── 5. 模板交叉验证（真实截图）──')
from afk_bot.infra.input import _masked_template_scan  # noqa: E402

CLIP = r'C:/Users/x9502/.workbuddy/clipboard-images'
shot = os.path.join(CLIP, 'clipboard-2026-11-06T06-35-35-887Z-f64ab957.jpg')
TAB_X = (392, 520, 656, 788, 920, 1056)
if os.path.exists(shot):
    screen = cv2.imdecode(np.fromfile(shot, dtype=np.uint8),
                          cv2.IMREAD_COLOR)
    roi = screen[930:1024, 330:1150]
    for zh, path in CLASS_TPL.items():
        tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                           cv2.IMREAD_UNCHANGED)
        r = _masked_template_scan(roi, tpl[:, :, :3],
                                  (tpl[:, :, 3] > 0).astype(np.float32),
                                  (0.8, 1.4, 0.1))
        ok = False
        cx = -1
        if r is not None:
            score, loc, scale, (th, tw) = r
            cx = 330 + loc[0] + tw / 2
            ok = score >= 0.90 and min(abs(cx - x) for x in TAB_X) <= 12
        check(f'5 [{zh}] 真机 tab 命中且位置正确', ok,
              f'score={r[0]:.3f} cx={cx:.0f}' if r else '未命中')
else:
    print('  （截图不在本机，跳过交叉验证）')


# ══ 6. v2.3.38 真机回归：OCR 缓存导致反复点已消失的按钮 ══════
#   现场 logs/bug_reports/20261106_165948/：
#     · 日志连点 (770,797) 8 次后 failed
#     · shot.png 新鲜 OCR 显示唯一「锻造」在 (854,797)，(770,797) 已是
#       700级空白面板（实测 conf 0.98 的「700级」）
#     · screen.py:523-528 已知坑：稀疏指纹采不到单卡变化 → 60s TTL 返回旧帧
#   分叉标准：修复前（无 no_cache）→ failed；修复后 → done。
#   坐标全部来自真机 shot.png 的 MaaOCR 实测，不凑数。
print('\n── 6. OCR 缓存行为分叉（真机 16:59 回归）──')

TASK_SRC = os.path.join(ROOT, 'tasks', 'season_equipment.py')
_src = open(TASK_SRC, encoding='utf-8').read()
check('6a 源码全部 _ocr_screen 调用都带 no_cache=True（防新增调用漏改）',
      _src.count('self.bot._ocr_screen(no_cache=True)') == 4
      and 'self.bot._ocr_screen()' not in _src,
      f"带参数 {_src.count('self.bot._ocr_screen(no_cache=True)')} / "
      f"裸调用 {_src.count('self.bot._ocr_screen()')}")

# 真机坐标：锻造前 2 件可锻造 (770,797)/(854,797)；锻造后仅剩 (854,797)
Y_FORGE = 797
X_STALE, X_FRESH = 770, 854


def _class_frame(forges, zh='辅助'):
    rows = [it(643, 211, f'{zh}装备'),
            it(643, 1000, '锻造等级上限：700'),
            it(855, 636, '当前无可替换装备'),
            it(773, 770, '700级')]
    for x in forges:
        rows.append(it(Y_FORGE, x, '锻造'))
    return rows


def _dialog_frame():
    return [it(386, 508, '当前装备'), it(386, 752, '可锻造装备'),
            it(688, 744, '锻造'), it(888, 854, '锻造')]   # 背景按钮 ry0.87


def _success_frame():
    return [it(72, 639, '锻造成功'), it(950, 642, '穿戴新装备')]


# 修复前源码：把 no_cache=True 全部回退，并截掉注册块避免重复注册
_legacy_src = _src.replace('no_cache=True', 'no_cache=False')
_legacy_src = _legacy_src.split('TaskRegistry.register(')[0]
_ns = {'__name__': '_season_equipment_legacy', '__file__': TASK_SRC}
exec(compile(_legacy_src, TASK_SRC, 'exec'), _ns)   # noqa: S102
LegacyTask = _ns['SeasonEquipmentTask']


class _CacheAwareBot(_SeqBot):
    """模拟 screen.py 缓存：no_cache=False 恒返回陈旧帧，True 才推进。"""

    def __init__(self, fresh_frames, stale, tpl_hits=None):
        super().__init__(fresh_frames, tpl_hits=tpl_hits)
        self.stale = list(stale)
        self.fresh_calls = 0
        self.stale_calls = 0

    def _ocr_screen(self, img=None, no_cache=False):
        if not no_cache:
            self.stale_calls += 1
            return list(self.stale)     # 60s TTL 内命中旧缓存
        self.fresh_calls += 1
        return super()._ocr_screen(img=img, no_cache=True)


def _cache_case(task_cls):
    # 新鲜帧序列：2 件可锻造 → 弹窗 → 成功 → 只剩 1 件 → 弹窗 → 成功
    # → 0 件（职业完成）→ 之后保持 0 件，逐个切完 6 职业
    stale = _class_frame((X_STALE, X_FRESH))
    fresh = [
        stale,                                   # startup_probe
        _class_frame((X_STALE, X_FRESH)),        # loop: 点 770
        _dialog_frame(), _success_frame(),
        _class_frame((X_FRESH,)),                # 点 854（关键分叉帧）
        _dialog_frame(), _success_frame(),
        _class_frame(()),                        # 无可锻造 → 切职业
    ]
    bot = _CacheAwareBot(fresh, stale,
                         tpl_hits={os.path.basename(v): (500, 986)
                                   for v in CLASS_TPL.values()})
    t, logs = make_task(bot)
    if task_cls is not SeasonEquipmentTask:
        t = task_cls({
            'bot': bot, 'logger': lambda m: logs.append(m),
            'stats': {}, 'stop_event': t._stop,
            'pause_event': t._pause, 'options': {},
            'max_time': None, 'max_turns': None,
            'start_turns': 0, 'inst_id': 'season_equipment#1',
        })
    return t.run(), bot, logs


# 6b 修复前：源码回退版 → 必须 failed（证明用例对 bug 敏感）
res_old, bot_old, logs_old = _cache_case(LegacyTask)
check('6b 修复前源码 → failed（连点已消失的 770 触发护栏）',
      res_old == 'failed', res_old)
check('6c 修复前确实反复点 (770,797)',
      sum(1 for c in bot_old.clicks
          if c[0] == '卡片_锻造' and c[1] == X_STALE) >= 8,
      f'{sum(1 for c in bot_old.clicks if c[0]=="卡片_锻造")} 次点击')
check('6d 修复前从未点到真机真实按钮 (854,797)',
      not any(c[0] == '卡片_锻造' and c[1] == X_FRESH
              for c in bot_old.clicks))
check('6e 修复前全部走陈旧缓存（stale_calls>0, fresh_calls=0）',
      bot_old.stale_calls > 0 and bot_old.fresh_calls == 0,
      f'stale={bot_old.stale_calls} fresh={bot_old.fresh_calls}')

# 6f 修复后：当前源码 → 必须 done（同一场景不同结果 = 有效分叉）
res_new, bot_new, logs_new = _cache_case(SeasonEquipmentTask)
check('6f 修复后源码 → done（同一场景分叉成功）', res_new == 'done', res_new)
check('6g 修复后点到真机真实按钮 (854,797)',
      any(c[0] == '卡片_锻造' and c[1] == X_FRESH
          for c in bot_new.clicks),
      str([c for c in bot_new.clicks if c[0] == '卡片_锻造'][:4]))
check('6h 修复后全部强制重识别（fresh_calls>0, stale_calls=0）',
      bot_new.fresh_calls > 0 and bot_new.stale_calls == 0,
      f'stale={bot_new.stale_calls} fresh={bot_new.fresh_calls}')
check('6i 修复后累计穿戴 2 件（两件各一次，不重复）',
      sum(1 for c in bot_new.clicks if c[0] == '穿戴新装备') == 2,
      f'{sum(1 for c in bot_new.clicks if c[0]=="穿戴新装备")} 件')


# ══ 7. 护栏补强：弹窗不关闭（资源不足）不得空转至硬上限 ══════
print('\n── 7. 弹窗路径护栏 ──')
# 场景：点卡片 → 弹窗出现 → 点弹窗锻造但资源不足、弹窗不关闭；
# 修复前 no_progress 只统计卡片点击，会一直点到 90 次硬上限。
bot_res = _SeqBot([_class_frame((X_STALE,))] + [_dialog_frame()] * 40)
res_res, logs_res, bot_res = run_case(bot_res)
dlg_clicks = sum(1 for c in bot_res.clicks if c[0] == '弹窗_锻造')
check('7a 资源不足/弹窗不关闭 → failed', res_res == 'failed', res_res)
check('7b 弹窗点击计入无进展（≤8 次即止损，不空转 90 次）',
      0 < dlg_clicks <= 8, f'弹窗点击 {dlg_clicks} 次')
check('7c 失败日志带末次坐标（便于真机定位）',
      any('末次坐标=' in l for l in logs_res),
      str([l for l in logs_res if '末次坐标' in l][-1:]))


# ══ 8. v2.3.39 真机回归：六件全 700 后横幅文案变化 ══════════
#   现场 logs/bug_reports/20261106_173303/shot.png（1280x1024）MaaOCR：
#     「锻造等级上限：700」(1000,642) conf .98、「辅助装备」(211,644) conf 1.00
#     六张卡全 700级、无「锻造」按钮
#     横幅「当前暂无可锻造的装备」(636,856) conf .98
#   旧判据 _W_CLASS=('锻造等级上限','当前无可替换装备') 双词 AND →
#   完成态判不出 → 20 帧 unknown → failed（真机 17:33）。
#   分叉标准：修复前 failed；修复后识别完成态 → 切下一职业 → 六职业 done。
print('\n── 8. 完成态横幅行为分叉（真机 17:33 回归）──')

# 真机实测坐标/文案，不凑数
def _done_frame(zh='辅助'):
    return [it(53, 188, '辅'), it(61, 292, '助装备'),
            it(440, 1038, '装备回收'),
            it(514, 640, zh),
            it(642, 1000, '锻造等级上限：700'),
            it(644, 211, f'{zh}装备'),
            *[it(773, x, '700级') for x in (434, 518, 598, 684, 770, 854)],
            it(856, 636, '当前暂无可锻造的装备')]


def _legacy_done_class():
    """把源码判据回退成 v2.3.38 的死守横幅版，exec 出等价旧类。"""
    src = open(TASK_SRC, encoding='utf-8').read()
    old = "_W_CLASS_DONE = ('当前无可替换装备', '当前暂无可锻造的装备')"
    assert src.count(old) == 1
    # 旧版：_W_CLASS 双词含横幅，且无完成态兜底分支
    back = src.replace(
        "_W_CLASS = ('锻造等级上限',)      # 8/8 截图全中；弹窗亦含此词，靠优先级排除",
        "_W_CLASS = ('锻造等级上限', '当前无可替换装备')")
    back = back.replace(
        """            if (all(k in blob for k in _W_CLASS)
                    and not all(k in blob for k in _W_DIALOG)
                    and not all(k in blob for k in _W_SUCCESS)):""",
        "            if all(k in blob for k in _W_CLASS):")
    # 去掉完成态兜底分支，模拟修复前
    marker = "            # ⑥ 完成态兜底"
    if marker in back:
        head, tail = back.split(marker, 1)
        _, tail = tail.split("            # ⑦ 未知 / 动画过渡", 1)
        back = head + "            # ⑥ 未知 / 动画过渡" + tail
    back = back.split('TaskRegistry.register(')[0]
    ns = {'__name__': '_season_done_legacy', '__file__': TASK_SRC}
    exec(compile(back, TASK_SRC, 'exec'), ns)   # noqa: S102
    return ns['SeasonEquipmentTask']


LegacyDoneTask = _legacy_done_class()


def _done_case(task_cls):
    # 启动即在完成态（辅助已锻完）→ 应识别并依次切完 5 个职业 → done
    bot = _SeqBot([_done_frame()] * 12,
                  tpl_hits={os.path.basename(v): (500, 986)
                            for v in CLASS_TPL.values()})
    logs = []
    paus = threading.Event(); paus.set()
    t = task_cls({
        'bot': bot, 'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'options': {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'season_equipment#1',
    })
    return t.run(), bot, logs


res_old8, bot_old8, logs_old8 = _done_case(LegacyDoneTask)
check('8a 修复前完成态 → failed（20 帧 unknown）', res_old8 == 'failed', res_old8)
check('8b 修复前日志确为「未识别到可操作界面」',
      any('未识别到可操作界面' in l for l in logs_old8),
      str([l for l in logs_old8 if '错误' in l][-1:]))
check('8c 修复前未切换任何职业',
      not any(n.startswith('season_class_') for n in bot_old8.tpl_calls))

res_new8, bot_new8, logs_new8 = _done_case(SeasonEquipmentTask)
check('8d 修复后完成态 → done（分叉成功）', res_new8 == 'done', res_new8)
check('8e 修复后识别到「当前暂无可锻造的装备」完成信号',
      any('当前暂无可锻造的装备' in l for l in logs_new8),
      str([l for l in logs_new8 if '已无可锻造' in l][:2]))
check('8f 修复后切完 5 个剩余职业',
      sum(1 for c in bot_new8.clicks if c[0] == '_click_abs') == 5,
      f'{sum(1 for c in bot_new8.clicks if c[0]=="_click_abs")} 次切换')
check('8g 修复后无 unknown 报错',
      not any('未识别到可操作界面' in l for l in logs_new8))

# registry 五态不抢判（稳定词 + 显式排除）
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY as _REG  # noqa: E402


def _detect(texts):
    return _REG.detect([(0, 0, t, 0.99, 0, 0) for t in texts])


check('8h registry 完成态 → season.equip_class',
      _detect(['锻造等级上限：700', '当前暂无可锻造的装备', '辅助装备'])
      == 'season.equip_class')
check('8i registry 弹窗（也含锻造等级上限）→ 不被抢判',
      _detect(['锻造等级上限：700', '当前装备', '可锻造装备'])
      == 'season.forge_dialog')
check('8j registry 成功页 → 不被抢判',
      _detect(['锻造成功', '穿戴新装备']) == 'season.forge_success')


# 9. 混合场景：A 职业已完成、B 职业仍可锻造（真机最常见的跨职业状态）
print('\n── 9. 完成态 + 可锻造态混合 ──')


def _class_with_forge(zh='法师'):
    """职业页：稳定词 + 「当前无可替换装备」横幅 + 1 个锻造按钮。"""
    return [it(642, 1000, '锻造等级上限：700'),
            it(644, 211, f'{zh}装备'),
            it(514, 640, zh),
            it(773, 770, '620级'),
            it(797, 854, '锻造'),
            it(856, 636, '当前无可替换装备')]


# 帧序：辅助完成态 → 切法师(模板) → 法师有按钮 → 弹窗 → 成功
#       → 法师完成态 → 切战士/游侠/射手/坦克（均完成态）→ done
mixed = [
    _done_frame('辅助'),                    # startup_probe
    _done_frame('辅助'),                    # loop: 识别完成 → 切法师
    _class_with_forge('法师'),              # 法师有按钮 → 点锻造
    _dialog_frame(), _success_frame(),
    _done_frame('法师'),                    # 法师完成 → 切战士
    _done_frame('战士'),
    _done_frame('游侠'),
    _done_frame('射手'),
    _done_frame('坦克'),
]
bot_mix = _SeqBot(mixed,
                  tpl_hits={os.path.basename(v): (500, 986)
                            for v in CLASS_TPL.values()})
res_mix, logs_mix, bot_mix = run_case(bot_mix)
check('9a 混合场景 → done', res_mix == 'done', res_mix)
check('9b 完成态职业没有漏掉后续可锻造职业（法师被锻造）',
      any(c[0] == '卡片_锻造' and c[1] == 854 for c in bot_mix.clicks),
      str([c for c in bot_mix.clicks if c[0] == '卡片_锻造'][:3]))
check('9c 穿戴 1 件（只锻法师那件，不重复锻已完成的）',
      sum(1 for c in bot_mix.clicks if c[0] == '穿戴新装备') == 1,
      f'{sum(1 for c in bot_mix.clicks if c[0]=="穿戴新装备")} 件')
check('9d 完成信号被记录，且两种横幅都已登记为完成词',
      any('当前暂无可锻造的装备' in l for l in logs_mix)
      and '当前无可替换装备' in _W_CLASS_DONE
      and '当前暂无可锻造的装备' in _W_CLASS_DONE,
      str([l for l in logs_mix if '已无可锻造' in l][:2]))
# 「当前无可替换装备」+ 有锻造按钮同帧时，必须点锻造而非误判完成
bot_banner = _SeqBot([_class_with_forge('法师'), _class_with_forge('法师')],
                     tpl_hits={})
res_b, logs_b, bot_b = run_case(bot_banner)
check('9e 「当前无可替换装备」不触发完成（有按钮照样锻）',
      any(c[0] == '卡片_锻造' for c in bot_b.clicks)
      and not any('已无可锻造' in l for l in logs_b),
      str([c[0] for c in bot_b.clicks][:3]))


# ══ 10. v2.3.40 一键装备：其他途径获得更高级装备 ══════════
#   真机 clipboard-2026-11-06T12-08-03-535Z-e7229000.jpg (1281x1026)
#   MaaOCR：「一键装备」(647,856) conf .98 / rx.505 ry.834（橙大按钮）
#          「锻造等级上限：700」(1002,644) conf .99、六件全 700级
#          两种完成横幅都没有、无「锻造」按钮。
#   旧状态机：命中 _W_CLASS → 无按钮 → 误判「本职业完成」直接切走，
#             漏掉换装。
#   分叉：修复前从不点一键装备；修复后先点，再继续判后续界面。
print('\n── 10. 一键装备行为分叉（真机 v2.3.40）──')

X_ONEKEY, Y_ONEKEY = 647, 856       # 真机 MaaOCR 实测


def _onekey_frame(zh='法师'):
    """一键装备态：稳定词 + 橙色大按钮，两种完成横幅均无。"""
    return [it(62, 293, '师装备'),
            it(643, 212, f'{zh}装备'),
            it(644, 1002, '锻造等级上限：700'),
            it(514, 640, zh),
            *[it(772, x, '700级') for x in (434, 518, 602, 686, 772, 856)],
            it(Y_ONEKEY, X_ONEKEY, '一键装备')]


def _legacy_onekey_class():
    """把源码回退成 v2.3.39（无一键装备分支），exec 出等价旧类。"""
    src = open(TASK_SRC, encoding='utf-8').read()
    marker = "            # ②.5 一键装备"
    assert src.count(marker) == 1
    head, tail = src.split(marker, 1)
    _, tail = tail.split("            # ③ 职业装备页", 1)
    back = head + "            # ③ 职业装备页" + tail
    back = back.split('TaskRegistry.register(')[0]
    ns = {'__name__': '_season_onekey_legacy', '__file__': TASK_SRC}
    exec(compile(back, TASK_SRC, 'exec'), ns)   # noqa: S102
    return ns['SeasonEquipmentTask']


LegacyOneKeyTask = _legacy_onekey_class()


def _onekey_case(task_cls):
    # 启动即一键装备态 → 点击 → 之后变完成态 → 切完剩余职业 → done
    frames = [
        _onekey_frame('法师'),       # startup_probe
        _onekey_frame('法师'),       # loop: 应点一键装备
        _done_frame('法师'),         # 换装后完成态
        *[_done_frame(z) for z in ('战士', '游侠', '射手', '坦克', '辅助')],
    ]
    bot = _SeqBot(frames,
                  tpl_hits={os.path.basename(v): (500, 986)
                            for v in CLASS_TPL.values()})
    logs = []
    paus = threading.Event(); paus.set()
    t = task_cls({
        'bot': bot, 'logger': lambda m: logs.append(m), 'stats': {},
        'stop_event': threading.Event(), 'pause_event': paus,
        'options': {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'season_equipment#1',
    })
    return t.run(), bot, logs


res_o, bot_o, logs_o = _onekey_case(LegacyOneKeyTask)
check('10a 修复前从不点「一键装备」（漏换装）',
      not any(c[0] == '一键装备' for c in bot_o.clicks),
      str([c[0] for c in bot_o.clicks][:5]))
check('10b 修复前把该界面误判为本职业完成',
      any('已无可锻造' in l for l in logs_o),
      str([l for l in logs_o if '已无可锻造' in l][:1]))

res_n, bot_n, logs_n = _onekey_case(SeasonEquipmentTask)
check('10c 修复后先点「一键装备」且坐标=真机实测 (647,856)',
      any(c[0] == '一键装备' and c[1] == X_ONEKEY and c[2] == Y_ONEKEY
          for c in bot_n.clicks),
      str([c for c in bot_n.clicks if c[0] == '一键装备'][:2]))
check('10d 一键装备只点一次（不重复换装）',
      sum(1 for c in bot_n.clicks if c[0] == '一键装备') == 1,
      f'{sum(1 for c in bot_n.clicks if c[0]=="一键装备")} 次')
check('10e 换装后继续走完成态 → done', res_n == 'done', res_n)
check('10f 换装日志带职业名与坐标',
      any('一键装备' in l and '法师' in l for l in logs_n),
      str([l for l in logs_n if '一键装备' in l][:1]))

# 同一职业连点一键装备无变化 → 护栏止损，不空转
bot_stuck = _SeqBot([_onekey_frame('法师')] * 12,
                    tpl_hits={os.path.basename(v): (500, 986)
                              for v in CLASS_TPL.values()})
res_s, logs_s, bot_s = run_case(bot_stuck)
# v2.3.40 边界决策：按钮可能常驻，不误杀 → 告警后按完成态逐个切职业
check('10g 按钮常驻不误杀 → 收敛到 done', res_s == 'done', res_s)
check('10h 有明确告警且带末次坐标',
      any('已执行' in l and '按钮仍显示' in l and '末次坐标' in l
          for l in logs_s),
      str([l for l in logs_s if '按钮仍显示' in l][-1:]))
check('10i-2 点击有界（首次职业重试 5 帧，其余职业各 1 次）',
      sum(1 for c in bot_s.clicks if c[0] == '一键装备') <= 10,
      f'一键装备点击 {sum(1 for c in bot_s.clicks if c[0]=="一键装备")} 次')

# 混合：一键装备换装后，界面出现可锻造按钮 → 必须继续锻造
frames_mix = [
    _onekey_frame('法师'),
    _onekey_frame('法师'),
    _class_with_forge('法师'),       # 换装后出现锻造按钮
    _dialog_frame(), _success_frame(),
    _done_frame('法师'),
    *[_done_frame(z) for z in ('战士', '游侠', '射手', '坦克', '辅助')],
]
bot_mix10 = _SeqBot(frames_mix,
                    tpl_hits={os.path.basename(v): (500, 986)
                              for v in CLASS_TPL.values()})
res_m10, logs_m10, bot_m10 = run_case(bot_mix10)
check('10i 换装后出现可锻造按钮 → 正常继续锻造',
      res_m10 == 'done'
      and any(c[0] == '卡片_锻造' for c in bot_m10.clicks)
      and any(c[0] == '穿戴新装备' for c in bot_m10.clicks),
      str([c[0] for c in bot_m10.clicks][:6]))

print('\n' + '=' * 60)
if _FAIL:
    print(f'❌ 失败 {len(_FAIL)} 项：')
    for n in _FAIL:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{len(_PASS)}/{len(_PASS)}）')
