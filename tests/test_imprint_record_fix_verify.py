# -*- coding: utf-8 -*-
"""独立验证：_record_imprint_gain 惰性建列表修复（v2.1.10）。

验证目标（不复述修复说明，而是自建可复现链路 + 设法证伪）：
  1. 未进过烙印晶球门时，_record_imprint_gain 现在会真实入库
     （修复前该守卫 `if not isinstance(_orb_inventory, list): return` 会静默丢弃）。
  2. 端到端：门外获得的烙印能被晶球替换流程读到，流程真的执行替换
     （出现「替换目标「x」」+ click_at:替换_x + 已替换为），且不再「替换目标耗尽」。
  3. 反向：进入晶球门不会重置已有库存；显式空库存仍按「无目标即终止」收敛。
  4. 证伪项：替换成功新增的烙印是否会回写库存镜像（_core_requirement_met 读取源）。

全部断言通过 → exit 0；任一失败 → exit 1。
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time  # noqa: E402
fast_time.install()

from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ''))


def _it(y, x, t, c=0.9):
    """OCR item：(cy, cx, text, conf, x0, x1)。"""
    return (y, x, t, c, x - 40, x + 40)


def orb_frame(pairs, cy0=240):
    """构造烙印条目帧：品质标签在名称下方 60px（同 v1820 的 _orb_frame）。"""
    items = []
    for i, (n, q) in enumerate(pairs):
        ly = cy0 + i * 200
        items.append(_it(ly, 178, q))        # 品质标签（conf 0.9 ≥ 0.7）
        items.append(_it(ly - 60, 366, n))   # 名称在标签上方
    return items


class MockBot:
    """按脚本返回 OCR 帧，记录 click_at / scroll 调用。"""

    def __init__(self, frames=None):
        self._frames = list(frames or [])
        self.calls = []
        self.clicks = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._frames:
            return self._frames.pop(0)
        return [_it(100, 300, '玩法目录')]

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and kw in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, x, y, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicks.append(label)
        return True

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append(f'scroll_wheel:{label}')

    def template_match(self, *a, **kw):
        return None

    def find_and_focus(self):
        return 12345


def make_task(bot, logger=None, options=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    ctx = {
        'bot': bot, 'logger': logger or (lambda m: None), 'stats': {},
        'stop_event': stop_ev, 'pause_event': pause_ev,
        'options': options or {}, 'max_time': None, 'max_turns': None,
        'start_turns': 0, 'inst_id': 'labyrinth#verify',
    }
    return LabyrinthTask(ctx)


# 帧：第一步（继续前进 + 确认替换 + 三条烙印）与第二步（保留烙印/确认选择 + 候选）
F_TOP = ([_it(955, 234, '继续前进'), _it(955, 732, '确认替换')]
         + orb_frame([('暖流之力', '史诗'), ('暴怒速攻', '精英'),
                      ('流沙风暴', '稀有')]))
F_S2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
        _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),   # 首行 → y<0.38 被滤
        _it(523, 365, '执裁之盾'), _it(605, 177, '史诗')]    # 候选（史诗，可换）
F_END = [_it(100, 300, '晶粹不足'), _it(955, 234, '继续前进')]

print('══ 独立验证：烙印记录修复 + 晶球替换流程（v2.1.10）══')

# ═══════════════════════════════════════════════════════════
# 1. 未进晶球门时获得烙印 → 真实入库
# ═══════════════════════════════════════════════════════════
print('── 1. 门外（未进晶球门）获得烙印 → 入库 ──')
logs1 = []
t1 = make_task(MockBot(), logger=logs1.append)
check('1a 初始 _orb_inventory 不存在（未进晶球门）',
      getattr(t1, '_orb_inventory', None) is None,
      repr(getattr(t1, '_orb_inventory', None)))

t1._record_imprint_gain('暖流之力', '精英')
t1._record_imprint_gain('流沙风暴', '稀有')
t1._record_imprint_gain('聚能风刃', '史诗')

check('1b 三次获得 → 库存按调用顺序累积（修复前恒为 None）',
      t1._orb_inventory == [('暖流之力', '精英'), ('流沙风暴', '稀有'),
                            ('聚能风刃', '史诗')],
      str(getattr(t1, '_orb_inventory', None)))
check('1c 史诗计入 _orb_epic_count（=1）',
      getattr(t1, '_orb_epic_count', 0) == 1,
      f'epic_count={getattr(t1, "_orb_epic_count", None)}')
_rec_logs = [m for m in logs1 if '烙印记录 +' in m]
check('1d 每次获得均打印「烙印记录 +」行（3 条）',
      len(_rec_logs) == 3, f'n={len(_rec_logs)}')
check('1e 日志含名称/品质 + 累计数递增',
      any('烙印记录 +「暖流之力」（精英）' in m for m in logs1)
      and any('本局累计 3 枚' in m for m in logs1),
      str([m for m in logs1 if '烙印记录' in m][:1]) + ' ...')

# ═══════════════════════════════════════════════════════════
# 2. 端到端：门外收获 → 进晶球门真的执行替换（最关键）
# ═══════════════════════════════════════════════════════════
print('── 2. 端到端：门外收获 → 晶球门真的替换（不耗尽）──')
logs2 = []
b2 = MockBot(frames=[F_TOP, F_TOP, F_TOP,   # 第一步：校准 + 取帧选中 + 确认替换
                     F_S2, F_S2, F_S2,      # 等第二步 + 第二步选候选 + 确认选择
                     F_TOP,                 # 替换成功弹窗循环 → 回第一步
                     F_END, F_END, F_END, F_END])  # 收尾（晶粹不足终止）
t2 = make_task(b2, logger=logs2.append)
# 关键：全程未进过晶球门，先获得烙印（复现真机 15 关前）
t2._record_imprint_gain('暖流之力', '史诗')
t2._record_imprint_gain('暴怒速攻', '精英')
t2._record_imprint_gain('流沙风暴', '稀有')
check('2a 进门前库存已由门外记录建立（非空）',
      t2._orb_inventory == [('暖流之力', '史诗'), ('暴怒速攻', '精英'),
                            ('流沙风暴', '稀有')],
      str(t2._orb_inventory))

t2._imprint_orb_flow()

check('2b 门内首步读到的本局库存 = 门外记录（3 枚，非 0）',
      any('本局已获烙印(3)' in m for m in logs2),
      str([m for m in logs2 if '本局已获烙印' in m][:1]))
check('2c 出现「替换目标「暖流之力」（史诗）」选中日志',
      any('替换目标「暖流之力」（史诗）' in m for m in logs2),
      str([m for m in logs2 if '替换目标' in m][:2]))
check('2d 出现实际替换点击 click_at:替换_暖流之力',
      any(c == 'click_at:替换_暖流之力' for c in b2.calls),
      str([c for c in b2.calls if c.startswith('click_at:替换_')]))
check('2e 替换流程完整走通（确认选择已点 + 已替换为日志）',
      any(c == 'click_at:烙印_确认选择' for c in b2.calls)
      and any('已替换为「执裁之盾」' in m for m in logs2),
      str([m for m in logs2 if '已替换为' in m][:1]))
check('2f 全程不再出现「替换目标耗尽」（修复前必然出现）',
      not any('替换目标耗尽' in m for m in logs2),
      str([m for m in logs2 if '耗尽' in m][:2]))
check('2g 【回写】替换把「执裁之盾」换到手后，库存镜像同步回写',
      any('已替换为「执裁之盾」' in m for m in logs2)
      and '执裁之盾' in {n for n, _q in t2._orb_inventory},
      str(t2._orb_inventory))

# ═══════════════════════════════════════════════════════════
# 3. 反向：入库不被重置 + 显式空库存仍收敛
# ═══════════════════════════════════════════════════════════
print('── 3. 反向验证（防退化）──')
logs3 = []
# 只给 2 帧（选中目标后「确认替换」找不到 → 走两次点击未激活兜底退出）
b3 = MockBot(frames=[F_TOP, F_TOP])
t3 = make_task(b3, logger=logs3.append)
t3._orb_inventory = [('暖流之力', '史诗'), ('暴怒速攻', '精英')]
_before3 = list(t3._orb_inventory)
t3._imprint_orb_flow()
check('3a-1 门内首步不重置已有库存（读到 2 而非 0）',
      any('本局已获烙印(2)' in m for m in logs3),
      str([m for m in logs3 if '本局已获烙印' in m][:1]))
check('3a-2 未发生替换时库存原样保留、未被清空',
      t3._orb_inventory == _before3,
      f'before={_before3} after={t3._orb_inventory}')

logs4 = []
b4 = MockBot(frames=[F_TOP] * 12)
t4 = make_task(b4, logger=logs4.append)
t4._orb_inventory = []      # 本局确实 0 收获（显式空列表）
t4._imprint_orb_flow()
check('3b 显式空库存 → 保持「替换目标耗尽」收敛（不死循环）',
      any('本局已获烙印(0)' in m for m in logs4)
      and any('替换目标耗尽' in m for m in logs4),
      str([m for m in logs4 if '已获烙印' in m or '耗尽' in m][:2]))
check('3c 空库存流程不滚动、不点击替换（无副作用）',
      not any(c.startswith('click_at:替换_') for c in b4.calls)
      and not any('scroll_wheel' in c for c in b4.calls),
      str([c for c in b4.calls if c.startswith('click_at')][:2]))

# ═══════════════════════════════════════════════════════════
# 4. 证伪项：替换“新获得”的烙印是否回写库存镜像？
#     （_core_requirement_met 的读取源就是 _orb_inventory）
# ═══════════════════════════════════════════════════════════
print('── 4. 证伪项：替换新增烙印是否写入库存镜像 ──')
logs5 = []
b5 = MockBot(frames=[F_TOP, F_TOP, F_TOP, F_S2, F_S2, F_S2, F_TOP,
                     F_END, F_END, F_END, F_END])
t5 = make_task(b5, logger=logs5.append, options={'run_mode': 'boss20_dual'})
t5._record_imprint_gain('暖流之力', '史诗')   # 门外只有这 1 枚
t5._imprint_orb_flow()
_swapped = any('已替换为「执裁之盾」' in m for m in logs5)
_inv5 = getattr(t5, '_orb_inventory', None)
_met5, _desc5 = t5._core_requirement_met()
_inv5_names = {n for n, _q in _inv5} if isinstance(_inv5, list) else set()
print(f"    替换发生={_swapped} 替换后库存={_inv5} "
      f"核数达标={_met5}（需 {_desc5}）")
if _swapped and '执裁之盾' in _inv5_names and not _met5:
    print('    → 结论：替换已回写库存（含「执裁之盾」）；双核未达标仅因'
          '尚未获得「溢出防御」（单次替换不足，端到端见第 5 节）')
elif _swapped and _met5:
    print('    → 结论：替换已回写且已达双核')
else:
    print('    → 未复现该边界（替换新增烙印未反映到库存）')

# ═══════════════════════════════════════════════════════════
# 5. 端到端：门外收获 → 门内连续两次替换 → 双核达标（本轮验收核心）
# ═══════════════════════════════════════════════════════════
print('── 5. 端到端：连续替换凑齐双核 → 门前核数达标 ──')
# 第 2 轮候选帧（结构同 F_S2，候选换成「溢出防御」）
F_S2B = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),   # 首行 → y<0.38 被滤
         _it(523, 365, '溢出防御'), _it(605, 177, '史诗')]    # 候选（史诗）
logs6 = []
b6 = MockBot(frames=[F_TOP, F_TOP, F_TOP, F_S2, F_S2, F_S2, F_TOP,       # 第1轮→执裁之盾
                     F_TOP, F_TOP, F_TOP, F_S2B, F_S2B, F_S2B, F_TOP,   # 第2轮→溢出防御
                     F_END, F_END, F_END, F_END])
t6 = make_task(b6, logger=logs6.append, options={'run_mode': 'boss20_dual'})
# 门外获得（未进过晶球门）：两枚，第 2 枚用作第 2 轮的被换项
t6._record_imprint_gain('暖流之力', '史诗')
t6._record_imprint_gain('暴怒速攻', '精英')
check('5a 进门时库存 = 门外获得的 2 枚',
      t6._orb_inventory == [('暖流之力', '史诗'), ('暴怒速攻', '精英')],
      str(t6._orb_inventory))

t6._imprint_orb_flow()
_inv6_names = {n for n, _q in t6._orb_inventory}
check('5b 第1轮替换（暖流之力→执裁之盾）已回写库存',
      any('已替换为「执裁之盾」' in m for m in logs6)
      and '执裁之盾' in _inv6_names,
      str(t6._orb_inventory))
check('5c 第2轮替换（暴怒速攻→溢出防御）已回写库存',
      any('已替换为「溢出防御」' in m for m in logs6)
      and '溢出防御' in _inv6_names,
      str(t6._orb_inventory))
_met6, _desc6 = t6._core_requirement_met()
check('5d 【核心】连续替换后 _core_requirement_met()[0] is True',
      _met6 is True, f'met={_met6} inv={t6._orb_inventory}')
check('5e 【核心】达标条件描述含「双核」',
      '双核' in _desc6, _desc6)
check('5f 回写日志采用「本局已获烙印(N)」措辞',
      any('本局已获烙印(' in m and ('执裁之盾' in m or '溢出防御' in m)
          for m in logs6),
      str([m for m in logs6 if '本局已获烙印' in m][:3]))
check('5g 全程无「替换目标耗尽」',
      not any('替换目标耗尽' in m for m in logs6),
      str([m for m in logs6 if '耗尽' in m][:2]))

print()
print('═' * 56)
print(f'独立验证断言：{len(_PASS)}/{len(_PASS) + len(_FAIL)} 通过')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print('  -', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)
