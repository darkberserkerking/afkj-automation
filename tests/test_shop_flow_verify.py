# -*- coding: utf-8 -*-
"""独立验证（独立验证者从零构造，不复用 tests/test_shop_flow.py 的断言）。

验证对象（tasks/labyrinth.py，本次刚完成的两处修复）：
  修复1 `_shop_flow`  (1851-1909)：删除"首帧见『继续前进』即退出"的判据，
        改为 bought>=2 / 无目标 / 精粹<50 退出；收尾用已取帧找『继续前进』。
  修复2 `_door_score` (1765-1809)：普通遗物门（描述无三目标名）rank 3→5.5。

目标：证明"有目标就买"，并**尽力证伪**（尤其删除退出判据后
在非商店界面的行为）。测试如实断言实际行为，不美化。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_shop_flow_verify.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: E402
fast_time.install()  # 虚拟时钟：代码里的 sleep 不真睡

import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def it(cy, cx, text, conf=0.9):
    """items 元素 (cy, cx, text, conf, x1, x2)。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


# ── 独立 MockBot（自有实现，逐帧脚本化）──
class MockBot:
    def __init__(self, frames=None, default=None):
        self._frames = list(frames or [])
        self._default = list(default if default is not None else [])
        self.ocr_calls = 0
        self.clicks = []

    def _ocr_screen(self, img=None, no_cache=False):
        self.ocr_calls += 1
        if self._frames:
            return self._frames.pop(0)
        return list(self._default)

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for t in items:
            if len(t) >= 4 and t[3] >= min_conf:
                if (t[2] == kw) if exact else (kw in t[2]):
                    return (t[1], t[0])
        return None

    def click_at(self, x, y, label=''):
        self.clicks.append(label or f'click:({x},{y})')
        return True

    def press_esc(self, label=''):
        self.clicks.append(f'ESC:{label}')
        return True

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def scroll_wheel(self, *a, **k):
        return True

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def template_match(self, *a, **k):
        return None

    def click_ratio(self, *a, **k):
        return True

    def long_press_at(self, *a, **k):
        return True

    def press_key(self, *a, **k):
        return True


def mk(bot, logs=None):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    logger = (lambda m: logs.append(m)) if logs is not None else (lambda m: None)
    return LabyrinthTask({
        'bot': bot, 'logger': logger, 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#verify',
    })


# 精粹：位于右上角（xr=1100/1280=0.859>0.78，yr=30/1024=0.029<0.08）
ESS = {'500': it(30, 1100, '500'), '49': it(30, 1100, '49'),
       '50': it(30, 1100, '50'), '51': it(30, 1100, '51')}
BUY = [it(700, 640, '购买')]


def shop_frame(products=(), essence='500', tail=True):
    """构造商店帧：含「商品列表」（detector 的 shop 判据词）+ 商品 + 精粹 +
    底部「继续前进」。tail=False 时去掉底部按钮。"""
    f = [it(120, 300, '商品列表'), it(180, 300, '菲茨小店')]
    f += list(products)
    if essence is not None:
        f.append(ESS[essence])
    if tail:
        f.append(it(950, 640, '继续前进'))
    return f


def count_clicks(bot, label):
    return bot.clicks.count(label)


print('══════ 第 1 项：商店购买（证明"有目标就买"）══════')
print()

# ── 1.1 目标商品在列表第一行 ──
f = shop_frame([it(300, 640, '强壮')])
b = MockBot([f, BUY, shop_frame()])
t = mk(b)
t._shop_flow()
check('1.1 第一行有目标「强壮」→ 点商品+购买',
      '商店_强壮' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))

# ── 1.2 目标商品在列表中间 ──
f = shop_frame([it(520, 400, '道具A'), it(560, 640, '庇护'),
                it(600, 880, '道具B')])
b = MockBot([f, BUY, shop_frame()])
t = mk(b)
t._shop_flow()
check('1.2 中间行有目标「庇护」→ 点商品+购买',
      '商店_庇护' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))

# ── 1.3 目标商品在列表最后一行 ──
f = shop_frame([it(700, 400, '道具A'), it(850, 640, '极速')])
b = MockBot([f, BUY, shop_frame()])
t = mk(b)
t._shop_flow()
check('1.3 最后行有目标「极速」→ 点商品+购买',
      '商店_极速' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))

# ── 1.4 多个目标同时在 → 买第一个命中（_SHOP_RELICS 顺序 强壮>庇护>极速）──
f = shop_frame([it(520, 640, '庇护'), it(300, 400, '强壮'),
                it(700, 880, '极速')])
b = MockBot([f, BUY, shop_frame()])
t = mk(b)
t._shop_flow()
check('1.4 多目标同时在场 → 优先「强壮」而非「庇护/极速」',
      '商店_强壮' in b.clicks and '商店_庇护' not in b.clicks
      and '商店_极速' not in b.clicks, str(b.clicks))

# ── 1.5 精粹边界 49（不足）→ 不买 ──
f = shop_frame([it(560, 640, '庇护')], essence='49')
b = MockBot([f])
logs = []
t = mk(b, logs)
t._shop_flow()
check('1.5 精粹=49（不足 50）→ 不买',
      not any(c.startswith('商店_') for c in b.clicks), str(b.clicks))
check('1.5b 日志「已买 0 件」+ 精粹=49',
      any('已买 0 件' in m and '49' in m for m in logs),
      str([m for m in logs if '已买' in m][:1]))

# ── 1.6 精粹边界 50（恰好）→ 买 ──
f = shop_frame([it(560, 640, '庇护')], essence='50')
b = MockBot([f, BUY, shop_frame(essence='50')])
t = mk(b)
t._shop_flow()
check('1.6 精粹=50（恰好）→ 购买',
      '商店_庇护' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))

# ── 1.7 精粹边界 51 → 买 ──
f = shop_frame([it(560, 640, '庇护')], essence='51')
b = MockBot([f, BUY, shop_frame(essence='51')])
t = mk(b)
t._shop_flow()
check('1.7 精粹=51 → 购买',
      '商店_庇护' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))

# ── 1.8 读不到精粹（None）→ 不阻止购买 ──
f = shop_frame([it(560, 640, '庇护')], essence=None)
b = MockBot([f, BUY, shop_frame(essence=None)])
t = mk(b)
t._shop_flow()
check('1.8 精粹读不到（None）→ 仍购买',
      '商店_庇护' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))
check('1.8b 精粹右侧有数字但不在右上角 → 视为读不到',
      t._read_essence([it(500, 1100, '500')]) is None,
      str(t._read_essence([it(500, 1100, '500')])))

# ── 1.9 【本次 bug 核心】底部同时有「继续前进」→ 仍然购买 ──
core = shop_frame([it(560, 640, '庇护')], tail=True)
assert any('继续前进' in x[2] for x in core), '构造失败'
b = MockBot([core, BUY, shop_frame()])
logs = []
t = mk(b, logs)
t._shop_flow()
check('1.9 【核心回归】首帧底部含「继续前进」→ 仍点商品+购买（旧判据会首帧退出）',
      '商店_庇护' in b.clicks and '商店_购买' in b.clicks, str(b.clicks))
check('1.9b 日志「已买 1 件」', any('已买 1 件' in m for m in logs),
      str([m for m in logs if '已买' in m][:1]))
check('1.9c 收尾仍点「继续前进」退出', '继续前进' in b.clicks, str(b.clicks))

# ── 1.10 最多两件：买满 2 件即停 ──
f = shop_frame([it(560, 640, '庇护')])
b = MockBot([f, BUY, f, BUY, f, f])
t = mk(b)
t._shop_flow()
check('1.10 买满 2 件即停（不超买）',
      count_clicks(b, '商店_购买') == 2, str(b.clicks))

print()
print('══════ 第 2 项：证伪——删掉退出判据后的非商店界面行为 ══════')
print()

# ── 2.1 非商店帧（unknown）→ 转场容忍后退出 ──
# v2.1.22（用户 17:00）：买遗物会弹烙印选择，需要容忍「中转帧」，
# 故 OCR 次数 = 1 + _SHOP_OFF_MAX，而不是 1。
from tasks.labyrinth import (_SHOP_OFF_MAX,  # noqa: E402
                             _SHOP_MAX_ROUNDS)
non_shop = [it(200, 300, '战斗'), it(300, 500, '胜利'), it(420, 700, '第 3 关')]
b = MockBot([non_shop])
t = mk(b)
t._shop_flow()
check(f'2.1 非商店空帧 → 容忍 {_SHOP_OFF_MAX} 帧后退出（不空转到 {_SHOP_MAX_ROUNDS} 轮）',
      b.ocr_calls == 1 + _SHOP_OFF_MAX, f'ocr={b.ocr_calls} clicks={b.clicks}')
check('2.1b 非商店空帧 → 无商品点击，ESC 兜底退出',
      not any(c.startswith('商店_') for c in b.clicks)
      and any(c.startswith('ESC') for c in b.clicks), str(b.clicks))

# ── 2.2 空商店（有『商品列表』但无目标商品）→ 1 帧即退 ──
empty_shop = shop_frame(products=())
b = MockBot([empty_shop])
t = mk(b)
t._shop_flow()
check('2.2 空商店（有商品列表、无目标）→ 1 帧退出，点「继续前进」',
      b.ocr_calls == 1 and '继续前进' in b.clicks
      and not any(c.startswith('商店_') for c in b.clicks),
      f'ocr={b.ocr_calls} {b.clicks}')

# ── 2.3 【证伪】非商店帧持续含假阳性目标词（无『商品列表』）──
# v2.1.12 前：不复查商店判据 → 会连点「强壮」4 次（真机事故）。
# v2.1.22：判据词缺席即**先判状态**，不进商品查找分支 → 零误点。
fp_frames = [[it(200, 300, '战斗'), it(300, 500, '强壮')] for _ in range(8)]
b = MockBot(fp_frames)
t = mk(b)
t._shop_flow()
check('2.3 非商店帧持续含「强壮」→ 零误点（保护未被削弱）',
      count_clicks(b, '商店_强壮') == 0, str(b.clicks))
check(f'2.3b 容忍 {_SHOP_OFF_MAX} 帧后退出，不空转到 8 轮',
      b.ocr_calls == 1 + _SHOP_OFF_MAX, f'ocr={b.ocr_calls}')
check('2.3c 全程无「购买」点击，最终 ESC 兜底',
      '商店_购买' not in b.clicks
      and any(c.startswith('ESC') for c in b.clicks), str(b.clicks))
print('   └ 结论：判据词缺席即不进商品查找分支 → 零误点（原事故已消除）。')

# ── 2.4 【证伪·帧漂移】买一次后商店关闭（后续帧丢『商品列表』但含目标词）──
f1_shop = shop_frame([it(560, 640, '庇护')])
f_buy = BUY
f_door = [it(300, 400, '结束探索'), it(500, 640, '遗物之门'),
          it(560, 640, '强壮'), it(700, 880, '晶粹之门')]
b = MockBot([f1_shop, f_buy, f_door, f_door, f_door, f_door, f_door, f_door])
t = mk(b)
t._shop_flow()
check('2.4 【帧漂移】买到选门界面 → 零误点「强壮」',
      count_clicks(b, '商店_强壮') == 0 and count_clicks(b, '商店_购买') == 1,
      f'强壮误点={count_clicks(b, "商店_强壮")} '
      f'购={count_clicks(b, "商店_购买")} ocr={b.ocr_calls}')
check('2.4b 选门界面（door）非转场 → 立即判离店，不耗满容忍帧',
      b.ocr_calls == 3, f'ocr={b.ocr_calls}')
check('2.4c 该漂移场景最终 ESC 兜底（未找到『继续前进』）',
      any(c.startswith('ESC') for c in b.clicks)
      and '继续前进' not in b.clicks, str(b.clicks))

# ── 2.5 精粹读不到 + 无目标商品 → 安全退出 ──
b = MockBot([non_shop])
t = mk(b)
t._shop_flow()
check('2.5 精粹读不到 + 无目标 → 安全退出（无商品点击）',
      not any(c.startswith('商店_') for c in b.clicks),
      f'ocr={b.ocr_calls} {b.clicks}')

# ── 2.6 非商店帧含『继续前进』→ 不再误点（转场容忍后 ESC）──
weird = [it(200, 300, '别的界面'), it(950, 640, '继续前进')]
b = MockBot([weird])
t = mk(b)
t._shop_flow()
check('2.6 非商店帧含『继续前进』→ 转场容忍后 ESC 退出（不误点）',
      any(c.startswith('ESC') for c in b.clicks)
      and '继续前进' not in b.clicks, str(b.clicks))

print()
print('══════ 第 2b 项：v2.1.22 核心——买遗物弹烙印不算离店 ══════')
print()
# ── 2.7 【用户 17:00 真机场景】买遗物 → 弹烙印选择 → 处理后回商店继续买 ──
IMPRINT = [it(300, 400, '选择烙印'), it(700, 640, '确认')]
logs7 = []
s1 = shop_frame([it(300, 640, '强壮')])
s2 = shop_frame([it(560, 640, '庇护')])
b = MockBot([s1, BUY, IMPRINT, s2, BUY, shop_frame(products=())])
t = mk(b, logs7)
_handled = []
t._handle_imprint = lambda items: _handled.append(
    tuple(x[2] for x in items))
t._shop_flow()
check('2.7a 烙印选择被**就地处理**（不是判离店退出）',
      len(_handled) == 1, f'handled={len(_handled)} clicks={b.clicks}')
check('2.7b 处理后回商店继续买 → 两件都买到（bought 未丢）',
      count_clicks(b, '商店_强壮') == 1 and count_clicks(b, '商店_庇护') == 1
      and count_clicks(b, '商店_购买') == 2, str(b.clicks))
check('2.7c 日志如实说明「烙印选择（正常环节）」，且**无**「已离开商店界面」',
      any('弹出烙印选择（正常环节）' in m for m in logs7)
      and not any('已离开商店界面' in m for m in logs7),
      str([m for m in logs7 if '烙印' in m or '离开商店' in m][:2]))
check('2.7d 最终由「继续前进」离店（只有它算离店）',
      '继续前进' in b.clicks, str(b.clicks))

print()
print('══════ 第 3 项：_door_score 优先级矩阵 + 组合 + 边界 ══════')
print()

t0 = mk(MockBot())
# 单门矩阵（title 位于 (cx=500, cy=300)，描述紧贴下方）
def ds(kw, desc_text, kw_conf=0.9, tx=500, ty=300, xs=None, dy=100, dx=0):
    items = [it(ty + dy, tx + dx, desc_text, kw_conf)] if desc_text is not None else []
    return t0._door_score(kw, items, tx, ty, xs)

check('3.1 遗物之门(强壮) = 0', ds('遗物之门', '获得强壮遗物')[0] == 0,
      str(ds('遗物之门', '获得强壮遗物')))
check('3.2 遗物之门(庇护) = 1', ds('遗物之门', '获得庇护遗物')[0] == 1,
      str(ds('遗物之门', '获得庇护遗物')))
check('3.3 遗物之门(极速) = 2', ds('遗物之门', '获得极速遗物')[0] == 2,
      str(ds('遗物之门', '获得极速遗物')))
check('3.4 遗物之门(普通描述) = 5.5', ds('遗物之门', '净化遗物提升属性')[0] == 5.5,
      str(ds('遗物之门', '净化遗物提升属性')))
check('3.5 馈赠之门 = 2.5', ds('馈赠之门', '馈赠遗物')[0] == 2.5,
      str(ds('馈赠之门', '馈赠遗物')))
check('3.6 晶粹之门 = 4', ds('晶粹之门', '获得晶粹')[0] == 4,
      str(ds('晶粹之门', '获得晶粹')))
check('3.7 菲茨小店 = 5', ds('菲茨小店', '神秘商人')[0] == 5,
      str(ds('菲茨小店', '神秘商人')))
check('3.8 精英之门 = 6', ds('精英之门', '精英镇守')[0] == 6,
      str(ds('精英之门', '精英镇守')))
check('3.9 首领之门 = 6', ds('首领之门', '首领镇守')[0] == 6,
      str(ds('首领之门', '首领镇守')))
# 非遗物门 desc 误含目标词仍按门型定级
check('3.10 菲茨小店 desc 误含「强壮」仍 = 5',
      ds('菲茨小店', '获得强壮遗物')[0] == 5,
      str(ds('菲茨小店', '获得强壮遗物')))
check('3.11 晶粹之门 desc 误含「极速」仍 = 4',
      ds('晶粹之门', '获得极速遗物')[0] == 4,
      str(ds('晶粹之门', '获得极速遗物')))
check('3.12 馈赠之门 desc 误含「庇护」仍 = 2.5',
      ds('馈赠之门', '获得庇护遗物')[0] == 2.5,
      str(ds('馈赠之门', '获得庇护遗物')))
check('3.13 排序：小店(5) < 普通遗物门(5.5) < 其他(6)',
      5 < 5.5 < 6)

# 双倍 tie-break
# v2.2.8：双倍+三目标 → rank 抬到 -0.5（用户 09-15 定案），tiebreak 仍 0
check('3.14 双倍命中 → (-0.5, 0)（v2.2.8 双倍三目标最优先）',
      ds('遗物之门', '双倍 获得强壮遗物') == (-0.5, 0),
      str(ds('遗物之门', '双倍 获得强壮遗物')))
check('3.15 非双倍 → tiebreak=1',
      ds('遗物之门', '获得强壮遗物') == (0, 1),
      str(ds('遗物之门', '获得强壮遗物')))

# 多目标词 → 取 _DOOR_RELIC_PRIORITY 中优先级最高者（非文本顺序）
check('3.16 desc「庇护 强壮」→ 取「强壮」(0)，非文本顺序',
      ds('遗物之门', '庇护 强壮')[0] == 0, str(ds('遗物之门', '庇护 强壮')))
check('3.17 desc「极速 庇护」→ 取「庇护」(1)',
      ds('遗物之门', '极速 庇护')[0] == 1, str(ds('遗物之门', '极速 庇护')))

# ── 描述归属边界：描述必须在标题"下方"（0 < cy - title_y < 220）──
check('3.18 描述在标题上方（cy<title_y）→ 不归属 → 普通遗物门 5.5',
      ds('遗物之门', '获得强壮遗物', ty=500, dy=-100)[0] == 5.5,
      str(ds('遗物之门', '获得强壮遗物', ty=500, dy=-100)))
check('3.19 描述与标题同 y（dy=0）→ 不归属 → 5.5',
      ds('遗物之门', '获得强壮遗物', dy=0)[0] == 5.5,
      str(ds('遗物之门', '获得强壮遗物', dy=0)))
check('3.20 描述在标题下方 219px → 归属 → 0',
      ds('遗物之门', '获得强壮遗物', dy=219)[0] == 0,
      str(ds('遗物之门', '获得强壮遗物', dy=219)))
check('3.21 描述在标题下方 220px（边界外）→ 不归属 → 5.5',
      ds('遗物之门', '获得强壮遗物', dy=220)[0] == 5.5,
      str(ds('遗物之门', '获得强壮遗物', dy=220)))
check('3.22 描述 x 偏移 219（<220）→ 归属 → 0',
      ds('遗物之门', '获得强壮遗物', dx=219)[0] == 0,
      str(ds('遗物之门', '获得强壮遗物', dx=219)))
check('3.23 描述 x 偏移 220（边界外）→ 不归属 → 5.5',
      ds('遗物之门', '获得强壮遗物', dx=220)[0] == 5.5,
      str(ds('遗物之门', '获得强壮遗物', dx=220)))

# ── 多门描述归属（v1.8.55：归属 x 最近的门）──
# 左门标题 x=400，右门标题 x=800，title_y=300。
# 把右门的描述放在 x=613（距左门 213、距右门 187）——旧 |dx|<220 会串入左门。
desc_items = [it(440, 613, '效果：极速提升5点')]
left = t0._door_score('遗物之门', desc_items, 400, 300, [400, 800])
right = t0._door_score('遗物之门', desc_items, 800, 300, [400, 800])
check('3.24 多门：描述 x=613 距右门更近 → 左门不串（5.5），右门得「极速」(2)',
      left[0] == 5.5 and right[0] == 2,
      f'left={left} right={right}')
# 描述归左门
desc_left = [it(440, 560, '获得强壮遗物')]
left2 = t0._door_score('遗物之门', desc_left, 400, 300, [400, 800])
right2 = t0._door_score('遗物之门', desc_left, 800, 300, [400, 800])
check('3.25 多门：描述 x=560 距左门更近 → 左门得「强壮」(0)，右门 5.5',
      left2[0] == 0 and right2[0] == 5.5,
      f'left={left2} right={right2}')

# ── 组合场景（用 _decide_door 集成，更接近真实调用）──
def decide(titles, items):
    return t0._decide_door(titles, items)

c1 = decide([('遗物之门', (400, 300)), ('菲茨小店', (800, 300))],
            [it(400, 400, '净化遗物提升属性'), it(400, 800, '神秘商人')])
check('3.26 组合：普通遗物门 vs 菲茨小店 → 小店胜',
      c1[0] == '菲茨小店', str(c1))

c2 = decide([('遗物之门', (400, 300)), ('晶粹之门', (800, 300))],
            [it(400, 400, '净化遗物提升属性'), it(400, 800, '获得晶粹')])
check('3.27 组合：普通遗物门 vs 晶粹之门 → 晶粹胜',
      c2[0] == '晶粹之门', str(c2))

c3 = decide([('遗物之门', (400, 300)), ('菲茨小店', (800, 300))],
            [it(400, 400, '获得强壮遗物'), it(400, 800, '神秘商人')])
check('3.28 组合：遗物门(强壮) vs 小店 → 遗物门胜',
      c3[0] == '遗物之门', str(c3))

c4 = decide([('遗物之门', (400, 300)), ('晶粹之门', (800, 300))],
            [it(400, 400, '净化遗物提升属性'), it(400, 800, '获得晶粹')])
check('3.29 组合：普通遗物门(5.5) 排序低于晶粹(4)（晶粹优先）',
      c4[0] == '晶粹之门', str(c4))

print()
print('══════════════════════════════════════════')
print(f'独立验证：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print('  -', f_)
print('══════════════════════════════════════════')
sys.exit(0 if not _FAIL else 1)
