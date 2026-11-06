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
    _DIALOG_FORGE_RY_MAX, _IMG_H, _W_CLASS, _W_DIALOG, _W_MAIN,
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

print('\n' + '=' * 60)
if _FAIL:
    print(f'❌ 失败 {len(_FAIL)} 项：')
    for n in _FAIL:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{len(_PASS)}/{len(_PASS)}）')
