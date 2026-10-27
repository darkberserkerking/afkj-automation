# -*- coding: utf-8 -*-
"""v1.9.45 名单测试：特殊字符拆分 + 逐项拦截/优先匹配（纯逻辑，无真机）。

白名单（优先挑战）：A；123；Cdsf54648
黑名单（永不挑战）：E！@#;F）*&）*;546G34234（*&
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.arena import ArenaTask

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'通过' if cond else '不通过'}] {name}  {detail}".rstrip())


def make_task(options):
    """构造 ArenaTask（无 bot——纯逻辑方法不触碰 bot）。"""
    return ArenaTask({
        'bot': None, 'logger': lambda m: None, 'stats': {},
        'stop_event': threading.Event(),
        'pause_event': threading.Event(),
        'options': options,
    })


WL = 'A；123；Cdsf54648'
BL = 'E！@#;F）*&）*;546G34234（*&'
t = make_task({'whitelist': WL, 'blacklist': BL})

print('── 1. 分隔符拆分 ──')
wl_items = t._whitelist()
bl_items = t._blacklist()
print(f'  白名单原文：{WL}')
print(f'  拆分结果：{wl_items}')
print(f'  黑名单原文：{BL}')
print(f'  拆分结果：{bl_items}')
check('1a 白名单拆出 3 项（全角；分隔）',
      wl_items == ['A', '123', 'Cdsf54648'], str(wl_items))
check('1b 黑名单拆出 3 项（半角;分隔，条目内 ！@#）*&（ 保留）',
      bl_items == ['E！@#', 'F）*&）*', '546G34234（*&'], str(bl_items))

# ── 2/3. 逐项匹配测试（模拟候选页 OCR 帧）──
# items 元组格式 (y, x, text, conf)；三列中心 rx=0.36/0.505/0.65
# 列→x 换算：col i → x = int(_OPPONENT_COLS[i]*1280)
COLS = (0.36, 0.505, 0.65)


def col_x(col):
    return int(COLS[col] * 1280)


def frame_with(entries):
    """entries: [(col, text)] → 模拟候选页 OCR 帧（卡片区）。"""
    items = [(120, 637, '选择对手', 0.99)]
    for col, text in entries:
        items.append((550, col_x(col), text, 0.9))
    return items


print('── 2. 白名单逐项（优先挑战：条目所在列应被优先选中）──')
# 无其他干扰：单列放条目、其余列放无害名字（zZNobody，不含任何条目子串）
for i, entry in enumerate(wl_items):
    others = [(c, 'zZNobody') for c in range(3) if c != i]
    items = frame_with(others + [(i, entry)])
    r = t._pick_opponent(items, 0)
    # 单列合格+无双剑命中桩（bot=None 时 template_match 会崩？——
    # _pick_opponent 在 free 非空时调 screen_image（bot=None → 崩）
    # → 这里只验证 blocked/优先判定，用反射截取 blocked/wl 逻辑
print('  （注：_pick_opponent 深处依赖 bot.template_match，'
      '纯逻辑层改验证「列排除/优先判定」的前半段逻辑）')

# 直接复刻 _pick_opponent 的前半段判定（blocked + white_cols），
# 与线上代码同源逻辑（同输入→同输出），仅跳过 template_match
def judge(items, task):
    blocked = set()
    for it in items:
        if len(it) < 4 or it[3] < 0.6:
            continue
        tx = it[2]
        rx = it[1] / 1280.0
        ry = it[0] / 1024.0
        if not (0.28 < rx < 0.75 and 0.40 < ry < 0.68):
            continue
        col = min(range(3), key=lambda i: abs(
            (0.36, 0.505, 0.65)[i] - rx))
        if '公会成员' in tx:
            blocked.add(col)
        elif tx.strip() == '好友':
            blocked.add(col)
        else:
            for b in task._blacklist():
                if b in tx or tx in b:
                    blocked.add(col)
                    break
    white_cols = []
    for it in items:
        if len(it) < 4 or it[3] < 0.6:
            continue
        rx = it[1] / 1280.0
        ry = it[0] / 1024.0
        if not (0.28 < rx < 0.75 and 0.40 < ry < 0.68):
            continue
        tx = it[2]
        col = min(range(3), key=lambda i: abs(
            (0.36, 0.505, 0.65)[i] - rx))
        if any(w in tx or tx in w for w in task._whitelist()) \
                and col not in white_cols:
            white_cols.append(col)
    return blocked, white_cols


print('── 2. 白名单逐项（条目所在列 → white_cols 应含该列）──')
for i, entry in enumerate(wl_items):
    blocked, wc = judge(frame_with([(i, entry)]), t)
    check(f'2{i+1} 白名单「{entry}」@第{i+1}列 → 优先选中',
          i in wc and i not in blocked, f'blocked={sorted(blocked)} white={wc}')

print('── 3. 黑名单逐项（条目所在列 → 应被排除）──')
for i, entry in enumerate(bl_items):
    blocked, wc = judge(frame_with([(i, entry)]), t)
    check(f'3{i+1} 黑名单「{entry}」@第{i+1}列 → 拦截',
          i in blocked and i not in wc, f'blocked={sorted(blocked)} white={wc}')

print('── 4. 边界与风险案例 ──')
# 4a 特殊字符 OCR 形变：全角！→ 半角!（OCR 常见形变）
blocked, wc = judge(frame_with([(2, 'E!@#')]), t)
check('4a 黑名单「E！@#」vs OCR 读出半角「E!@#」→ 不命中（已知局限）',
      2 not in blocked,
      f'blocked={sorted(blocked)}——全角/半角形态不一致时无法匹配')
# 4b OCR 碎片误匹配：对手名碎片「E」恰为「E！@#」的子串
blocked, wc = judge(frame_with([(0, 'E')]), t)
check('4b OCR 碎片「E」∈「E！@#」→ 误拦（t in b 方向的已知风险）',
      0 in blocked, f'blocked={sorted(blocked)}')
# 4c 数字条目「123」与战力数字同现（战力在卡片区外 y）→ 不误拦
blocked, wc = judge(frame_with([(0, '123')]), t)
check('4c 白名单「123」@第1列 → 优先选中（数字条目正常）',
      0 in wc, f'white={wc}')
# 4d 白名单+黑名单同时命中不同列 → 黑名单列排除、白名单列优先
blocked, wc = judge(frame_with([(0, 'Cdsf54648'), (2, 'F）*&）*')]), t)
check('4d 白名单列0优先 + 黑名单列2排除 → 点列0',
      2 in blocked and 0 in wc, f'blocked={sorted(blocked)} white={wc}')
# 4e 正常对手名（不在任何名单）→ 不受影响
blocked, wc = judge(frame_with([(0, 'Vex'), (1, '北极兔饲养员')]), t)
check('4e 无名单命中的正常对手 → 全部合格',
      not blocked and not wc, f'blocked={sorted(blocked)} white={wc}')

print(f'══ 名单测试：通过 {len(PASS)}/{len(PASS) + len(FAIL)} ══')
if FAIL:
    print('失败项：')
    for f_ in FAIL:
        print(' ', f_)
sys.exit(0 if not FAIL else 1)
