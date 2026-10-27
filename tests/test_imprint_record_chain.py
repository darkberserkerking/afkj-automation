# -*- coding: utf-8 -*-
"""烙印记录链路**回归守卫**（v2.1.10 起，用户 23:21 排查的固化）

背景（真机日志暴露的两个缺陷，均已在 v2.1.10 修复）：
  ① `_record_imprint_gain` 保留了方案 2 时代的前置守卫 → 未进过晶球门时
     「15 关前获得的烙印」被静默丢弃 → 进晶球门时「本局已获烙印(0)」
     →「替换目标耗尽」→ 双核永远凑不齐；
  ② 替换成功后的库存回写被 `if getattr(self, '_orb_scanned', False)` 门控，
     而该标记在生产路径恒为 False（`_scan_orb_inventory` 自 v2.1.8 起无调用点）
     → 换到手的新烙印不回写 → `_core_requirement_met()` 永远看不到新核。

本脚本把这两条固化为断言，防止再退化。
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def _item(cy, cx, text, conf=0.9):
    return (cy, cx, text, conf, cx - 50, cx + 50)


class MockBot:
    def __init__(self, texts_by_call=None):
        self._texts_by_call = list(texts_by_call or [])
        self.calls = []
        self.clicks = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            return self._texts_by_call.pop(0)
        return [_item(100, 300, '继续前进')]

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def template_match(self, *a, **kw):
        return None

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def click_ratio(self, *a, **kw):
        self.clicks.append('click_ratio')
        return True

    def long_press_at(self, *a, **kw):
        return True


def _make_task(bot, logger=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    ctx = {
        'bot': bot, 'logger': logger or (lambda m: None), 'stats': {},
        'stop_event': stop_ev, 'pause_event': pause_ev, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#chain',
    }
    return LabyrinthTask(ctx)


print('══ 烙印记录链路回归守卫（v2.1.10）══')
print()
print('── A. 录入侧：15 关前（未进晶球门）获得烙印 → 必须入库 ──')
logs_a = []
t_a = _make_task(MockBot(), logger=lambda m: logs_a.append(m))
check('A0 初始未建立库存', getattr(t_a, '_orb_inventory', None) is None,
      repr(getattr(t_a, '_orb_inventory', None)))
t_a._record_imprint_gain('暖流之力', '精英')
t_a._record_imprint_gain('流沙风暴', '稀有')
t_a._record_imprint_gain('聚能风刃', '史诗')
check('A1 【守卫】未进晶球门时获得烙印 → 已入库',
      getattr(t_a, '_orb_inventory', None) == [
          ('暖流之力', '精英'), ('流沙风暴', '稀有'), ('聚能风刃', '史诗')],
      str(getattr(t_a, '_orb_inventory', None)))
check('A2 【守卫】有「烙印记录 +」日志（真机 15 关前缺失的那条）',
      sum(1 for m in logs_a if '烙印记录' in m) == 3,
      str([m for m in logs_a if '烙印记录' in m][:1]))
check('A3 史诗计入 _orb_epic_count',
      getattr(t_a, '_orb_epic_count', 0) == 1,
      str(getattr(t_a, '_orb_epic_count', None)))
print()

print('── B. 对照：库存显式为空（本局确实 0 收获）→ 保持收敛 ──')
logs_b = []
t_b = _make_task(MockBot(), logger=lambda m: logs_b.append(m))
t_b._orb_inventory = []
t_b._record_imprint_gain('庇佑之力', '精英')
check('B1 空列表存在时 → 正常追加（不再丢弃）',
      getattr(t_b, '_orb_inventory', []) == [('庇佑之力', '精英')],
      str(getattr(t_b, '_orb_inventory', [])))
print()

print('── C. 晶球门侧：门内读到的数量 = 门外实际获得数 ──')
_repl = [
    _item(950, 234, '保留烙印'), _item(950, 732, '确认选择'),
    _item(303, 366, '缓慢凋零'), _item(385, 178, '稀有'),
    _item(523, 365, '暴怒速攻'), _item(605, 177, '精英'),
    _item(100, 640, '继续前进'),
]
logs_c = []
t_c = _make_task(MockBot(texts_by_call=[_repl] * 12),
                 logger=lambda m: logs_c.append(m))
t_c._record_imprint_gain('暖流之力', '精英')
t_c._record_imprint_gain('聚能风刃', '史诗')
t_c._imprint_orb_flow()
_inv_logs = [m for m in logs_c if '本局已获烙印' in m]
check('C1 【守卫】门内读到 ≥ 门外获得数 2 枚（此前恒为 0）',
      bool(_inv_logs) and not any('已获烙印(0)' in m for m in _inv_logs)
      and any('已获烙印(2)' in m or '已获烙印(3)' in m
              or '已获烙印(4)' in m for m in _inv_logs),
      str(_inv_logs[:1]))
check('C2 【守卫】不再立即「替换目标耗尽」',
      not any('替换目标耗尽' in m for m in logs_c),
      str([m for m in logs_c if '耗尽' in m][:1]))
print()

print('── D. 代码事实：两处缺陷的守卫已落在源码里 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
check('D1 录入侧：守卫改为惰性建立列表（不再是 return 丢弃）',
      'self._orb_inventory = []     # v2.1.10' in _src)
check('D2 回写侧：门控改为「库存已建立」（不再是 _orb_scanned）',
      "if isinstance(getattr(self, '_orb_inventory', None), list):"
      in _src)
check('D3 回写日志措辞统一为「本局已获烙印」',
      '[信息] 本局已获烙印(" + str(len(_inv))' in _src)
print()

# ══════════════════════════════════════════════════════════════
#  v2.2.6：执裁之盾 / 制裁之盾 是**两个不同烙印**（用户 2026-09-15 澄清）
#    名字仅首字不同（执／制）→ OCR 易混读；**稀有度不同**（精英／史诗）
#    → 用稀有度交叉校验。不纠正的后果：屏幕上实际是「制裁之盾」，
#    却被计入收集核「执裁之盾」→ `_core_requirement_met()` 误判达标。
# ══════════════════════════════════════════════════════════════
print()
print('── 5. 执裁之盾 vs 制裁之盾：按稀有度交叉校验 ──')

logs5 = []
t5 = _make_task(MockBot(), logger=lambda m: logs5.append(m))
t5._record_imprint_gain('执裁之盾', '史诗')       # 混读帧
_n5 = [n for n, _q in getattr(t5, '_orb_inventory', [])]
check('5a 【分叉】名读「执裁之盾」但稀有度=史诗 → 库存记「制裁之盾」',
      _n5 == ['制裁之盾'], str(_n5))
check('5b 告警说明纠正依据（形近字混读）',
      any('形近字混读' in m for m in logs5),
      str([m for m in logs5 if '混读' in m][:1])[:90])
check('5c 【核心影响·分叉】集齐判定**不得**因混读而达标',
      t5._core_requirement_met()[0] is False,
      f'旧实现记为执裁之盾 → 误判达标 ok={t5._core_requirement_met()[0]}')

t6 = _make_task(MockBot(), logger=lambda m: None)
t6._record_imprint_gain('制裁之盾', '精英')       # 反向混读
check('5d 反向混读（制裁之盾+精英）→ 纠正为执裁之盾并可判达标',
      [n for n, _q in t6._orb_inventory] == ['执裁之盾']
      and t6._core_requirement_met()[0] is True, str(t6._orb_inventory))

logs7 = []
t7 = _make_task(MockBot(), logger=lambda m: logs7.append(m))
t7._record_imprint_gain('执裁之盾', '精英')
check('5e 正确配对（执裁之盾+精英）原样入库、不告警',
      [n for n, _q in t7._orb_inventory] == ['执裁之盾']
      and not any('混读' in m for m in logs7), str(t7._orb_inventory))

t8 = _make_task(MockBot(), logger=lambda m: None)
t8._record_imprint_gain('溢出防御', '史诗')       # 表外烙印
t8._record_imprint_gain('执裁之盾', '未知')       # 品质未知 → 不校验
_n8 = [n for n, _q in t8._orb_inventory]
check('5f 表外烙印与品质未知均原样入库（不误伤）',
      _n8 == ['溢出防御', '执裁之盾'], str(_n8))

print('═' * 56)
print(f'烙印记录链路守卫：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)
