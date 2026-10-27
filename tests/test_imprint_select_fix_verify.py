# -*- coding: utf-8 -*-
"""烙印「选中态校验 + 重试」修复（v2.1.13）**独立验证 + 证伪**。

与 `tests/test_imprint_select_verify.py` 的定位不同：本文件不复述修复说明，
而是用**自建 MockBot / 自建帧**驱动被测代码，断言**当前代码的真实行为**，
并主动构造「重试反而更糟」的场景（toggle 语义 + 渲染延迟、备用坐标取错卡、
OCR 空帧假成功）。风险点以 `⚠ RISK` 打印，不美化。

被测对象：`tasks/labyrinth.py`
  · `_imprint_select_and_confirm`（选中态校验 + 备用坐标重试一次）
  · `_imprint_card_click_pos`（备用坐标：同卡名位置 / 0.45W 退化）
  · `_handle_imprint`（就绪等待 8s 窗口 + 记录链路）

坐标语义（核对自源码，勿凭记忆）：
  · items 元素 = (cy, cx, text, conf, x1, x2)；find_text 返回 (cx, cy)
  · pick 形态   = (cy, cx, ...)（_imprint_pick 返回）
  · pick[0]=cy, pick[1]=cx
  · _imprint_select_and_confirm 内 _pos=(pick[1], pick[0]) → (x, y)
  · _read_card_name(items, tag_pos) 内 dy = tag_pos[1] - it[0] → **仅用 tag_pos 的 y**

运行：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_imprint_select_fix_verify.py
    追加 --no-regression 可跳过末尾三套回归（调试用）
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
except Exception:
    pass

from tests import fast_time  # noqa: E402
fast_time.install()          # v1.9.2 虚拟时钟：sleep 只推进偏移，语义不变

import tasks.catalog  # noqa: F401,E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []
_RISKS = []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def risk(name, detail=""):
    _RISKS.append(name)
    print(f"[RISK] {name}  {detail}".rstrip())


def _it(cy, cx, text, conf=0.9):
    """items 元素 = (cy, cx, text, conf, x1, x2)。"""
    return (cy, cx, text, conf, cx - 50, cx + 50)


class MockBot:
    """按调用顺序吐帧的 OCR mock；记录每次点击的 (x, y, label)。"""

    def __init__(self, frames=None):
        self._frames = list(frames or [])
        self.clicked = []            # (x, y, label)
        self.ocr_calls = 0

    def _ocr_screen(self, img=None, no_cache=False):
        self.ocr_calls += 1
        if self._frames:
            return self._frames.pop(0)
        return []                    # 帧耗尽 → 空帧（模拟转场/遮挡）

    def click_at(self, x, y, label=''):
        self.clicked.append((x, y, label))
        return True

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        # 与 infra/ocr_engine.find_text 语义一致：子串匹配，返回 (cx, cy)
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def window_rect(self):
        return (0, 0, 1280, 1024)

    def template_match(self, *a, **kw):
        return None

    def press_esc(self, label=''):
        self.clicked.append((-1, -1, 'ESC'))
        return True

    def _click_abs(self, x, y):
        return True

    def scroll_wheel(self, *a, **kw):
        return True

    def click_ratio(self, *a, **kw):
        return True

    def long_press_at(self, *a, **kw):
        return True


def _mk(bot, logs=None):
    import threading
    se, pe = threading.Event(), threading.Event()
    pe.set()
    t = LabyrinthTask({
        'bot': bot, 'logger': (lambda m: logs.append(m)) if logs is not None
        else (lambda m: None),
        'stats': {}, 'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#fixverify',
    })
    return t


def _spy_record(t, sink):
    orig = t._record_imprint_gain

    def _spy(name, qual):
        sink.append((name, qual))
        return orig(name, qual)
    t._record_imprint_gain = _spy


# ── 真实布局帧 ──────────────────────────────────────────────
# 品质标签在**名称下方 ~82px**（_collect_imprint_entries / _card_quality 注释核实），
# 名称 x 比标签 x 更大（标签在卡面左侧）。
TAG_Y, TAG_X = 300, 176
NAME_Y, NAME_X = 218, 365
NAME_TXT = '弱点洞悉'
# pick 形态 = (cy, cx, text, ...)（与 _imprint_pick 返回一致）
PICK = (TAG_Y, TAG_X, '史诗')


def frame(button, name=(NAME_TXT, NAME_Y, NAME_X), extra=()):
    """构造一帧：品质标签 + 卡名(可选) + 底部按钮 + 附加项。

    button: '选择烙印'（未选中态）| '确认'（已选中态）
    """
    items = [_it(TAG_Y, TAG_X, '史诗')]
    if name is not None:
        items.append(_it(name[1], name[2], name[0]))
    items.append(_it(950, 640, button))
    items += list(extra)
    return items


def labels(bot):
    return [c[2] for c in bot.clicked]


print('=' * 62)
print('烙印选中态校验 + 重试（v2.1.13）· 独立验证 / 证伪')
print('=' * 62)

# ══════════════════════════════════════════════════════════════
# 1. 核心行为复现（自选帧）
# ══════════════════════════════════════════════════════════════
print()
print('── 1. 核心行为复现 ──')

# 1A. 首点被吞 → 重试 → 最终确认成功 → 只记录一次（走完整 _handle_imprint 链路）
logs_a, rec_a = [], []
ba = MockBot(frames=[frame('选择烙印'), frame('确认')])   # 点1后未选中；重试后选中
ta = _mk(ba, logs_a)
_spy_record(ta, rec_a)
ta._last_imprint_at = 0.0
ta._handle_imprint(frame('选择烙印'))
check('1A1 首点被吞→发生一次重试',
      labels(ba).count('烙印_选卡_重试') == 1, str(labels(ba)))
check('1A2 【v2.1.13 加固】重试用**同坐标**（真机 03:55:40 同坐标再点即生效）',
      ba.clicked[1][:2] == (TAG_X, TAG_Y), str(ba.clicked[1][:2]))
check('1A3 最终点了一次「确认」',
      labels(ba).count('烙印_确认') == 1, str(labels(ba)))
check('1A4 只记录一次烙印（无重复计数）',
      rec_a == [(NAME_TXT, '史诗')], str(rec_a))
check('1A5 日志仅一条「烙印记录 +」',
      sum(1 for m in logs_a if '烙印记录 +' in m) == 1,
      str([m for m in logs_a if '烙印记录 +' in m]))

# 1B. 两次都被吞 → 返回 False、不点确认、不记录
logs_b, rec_b = [], []
bb = MockBot(frames=[frame('选择烙印'), frame('选择烙印')])
tb = _mk(bb, logs_b)
_spy_record(tb, rec_b)
ok_b, _ = tb._imprint_select_and_confirm(frame('选择烙印'), PICK)
check('1B1 两次未选中→返回 False', ok_b is False, str(ok_b))
check('1B2 未点「确认」', '烙印_确认' not in labels(bb), str(labels(bb)))
check('1B3 调用方未记录烙印', rec_b == [], str(rec_b))
check('1B4 日志提示「不记录」',
      any('不记录' in m for m in logs_b),
      str([m for m in logs_b if '不记录' in m][:1]))

# 1C. 首次即成功 → 无重试（回归）
logs_c, rec_c = [], []
bc = MockBot(frames=[frame('确认')])
tc = _mk(bc, logs_c)
_spy_record(tc, rec_c)
tc._last_imprint_at = 0.0
tc._handle_imprint(frame('选择烙印'))
check('1C1 首点即选中→无重试',
      '烙印_选卡_重试' not in labels(bc), str(labels(bc)))
check('1C2 点一次「确认」并记录一次',
      labels(bc).count('烙印_确认') == 1 and rec_c == [(NAME_TXT, '史诗')],
      f'{labels(bc)} {rec_c}')

# ══════════════════════════════════════════════════════════════
# 2. 【重点证伪】重试会不会反而更糟？
# ══════════════════════════════════════════════════════════════
print()
print('── 2. 证伪：toggle(切换) 语义 + 渲染延迟 > 1s ──')
print('   场景 X：第1次点击**其实已选中**，但界面渲染慢，OCR 读到旧帧「选择烙印」')
print('   → 代码重试再点同一张卡 → 若点击是 toggle，则**取消选中** → 由成功变失败')

# 2X. toggle + 延迟：真实状态 click1=ON（读到旧帧 UNSEL），retry 点同一张卡 → OFF
#     帧序列：点1后 OCR=UNSEL(旧帧) → 重试点同卡 → 点2后 OCR=UNSEL(被取消)
logs_x, rec_x = [], []
bx = MockBot(frames=[frame('选择烙印'), frame('选择烙印')])
tx = _mk(bx, logs_x)
_spy_record(tx, rec_x)
ok_x, _ = tx._imprint_select_and_confirm(frame('选择烙印'), PICK)
check('2X1 toggle+延迟下，代码返回 False（丢失本可成功的一次）',
      ok_x is False, str(ok_x))
check('2X2 【v2.1.13 加固】重试用同坐标；toggle 风险为条件化且非致命（下帧自会重试）',
      bx.clicked[0][:2] == (TAG_X, TAG_Y) and bx.clicked[1][:2] == (TAG_X, TAG_Y),
      f'点1={bx.clicked[0][:2]} 点2={bx.clicked[1][:2]}')
risk('重试存在「取消选中」风险',
     '仅当游戏点击为 toggle 语义 且 渲染延迟>1.0s 时触发；此时由"能成功"变"失败"（下一帧会重试，非永久卡死）')

# 对照：非 toggle（点击=幂等"设为选中"）→ 重试无害
bx2 = MockBot(frames=[frame('选择烙印'), frame('确认')])
tx2 = _mk(bx2)
ok_idem, _ = tx2._imprint_select_and_confirm(frame('选择烙印'), (TAG_X, TAG_Y, '史诗'))
check('2X3 对照：非 toggle（幂等选中）下重试无害，返回 True',
      ok_idem is True, str(ok_idem))

# 论证：日志证据是否支持"第1次真没生效"而非"延迟生效"？
#   真机 03:55:34 点卡(176,655) → 03:55:37 按钮仍「选择烙印」→ 03:55:40 点**同一坐标**(176,655)
#   → 03:55:43 按钮变「确认」。
#   推理：若第1次已选中只是延迟渲染，则第2次**同一坐标**的点击在 toggle 语义下必然取消选中，
#   03:55:43 不应出现「确认」。既然出现了「确认」，说明第1次点击当时确实未生效（被吞）。
#   → 日志证据支持"真被吞"，间接**降低**（但不消除）toggle+延迟的组合概率。
print('   [推理] 真机 03:55:34 点卡 → 03:55:40 点**同一坐标**(176,655) → 03:55:43 才变「确认」')
print('          若第1次已选中仅延迟渲染，则同坐标第2击在 toggle 下应取消 → 不应出现「确认」')
print('          ⇒ 证据支持"第1次真被吞"，而非"延迟生效"（但无法排除非 toggle 情形）')

# 2Y. 残留假成功：点后 OCR 空帧（转场/遮挡） → 代码按"已确认"处理并记录
print()
print('── 2. 证伪：点后 OCR 空帧 → 是否仍会假成功记录？──')
logs_y, rec_y = [], []
by = MockBot(frames=[[]])                    # 点1后 OCR 返回空帧
ty = _mk(by, logs_y)
_spy_record(ty, rec_y)
ty._last_imprint_at = 0.0
ty._handle_imprint(frame('选择烙印'))
check('2Y1 【v2.1.13 已修】空帧不再按「已确认」处理（改打「按未确认处理」）',
      any('均未读到烙印按钮' in m for m in logs_y), str(logs_y[:3]))
check('2Y2 【v2.1.13 已修】空帧 → 不写入烙印记录（假成功已消除）',
      '烙印_确认' not in labels(by) and len(rec_y) == 0,
      f'{labels(by)} rec={rec_y}')
if '烙印_确认' not in labels(by) and len(rec_y) == 1:  # 旧行为已消除，此分支恒不触发
    risk('OCR 空帧/漏检时仍会"假成功"入库',
         '点击被吞后若下一帧 OCR 全空（转场/遮挡），代码把"找不到按钮"当作已确认 → 记录一条未确认的烙印')

# ══════════════════════════════════════════════════════════════
# 3. 备用坐标准确性（_imprint_card_click_pos）
# ══════════════════════════════════════════════════════════════
print()
print('── 3. 备用坐标准确性 ──')
t3 = _mk(MockBot())

p_real = t3._imprint_card_click_pos(frame('选择烙印'), (TAG_Y, TAG_X, '史诗'))
check('3A 真实布局(名在标签上方82px) → 取到同卡名位置',
      p_real == (NAME_X, NAME_Y), str(p_real))

p_deg = t3._imprint_card_click_pos(frame('选择烙印', name=None), (TAG_Y, TAG_X, '史诗'))
check('3B 名称缺失 → 退化 0.45W 且与标签同 y',
      p_deg == (int(0.45 * 1280), TAG_Y), str(p_deg))

# 3C. 取错卡：本卡名 OCR 漏检，邻卡名恰好落在窗口 → 点到邻卡
adj = [_it(TAG_Y, TAG_X, '史诗'),
       _it(NAME_Y, 520, '沸腾怒火'),          # 邻卡名：上方82px、x更大
       _it(950, 640, '选择烙印')]
p_adj = t3._imprint_card_click_pos(adj, (TAG_Y, TAG_X, '史诗'))
check('3C 同 y、x 更大的其它文本会被当作"同卡名"选中',
      p_adj == (520, NAME_Y), str(p_adj))
risk('备用坐标筛选过宽（无卡宽/形状约束）',
     '仅按"y 在上方 40~130px 且 x 更大"取最近，无卡宽约束、无名称形状过滤。'
     '按文档的纵向行距(~180px)，上下相邻行会被 40~130px 窗口排除，'
     '故"邻卡"命中需要存在**与标签同 y、x 更大**的文本（如右侧详情/描述面板）；'
     '此时重试可能点到非卡区域或另一张卡')

# 3D. 宽松度对比：9 字描述文本会被备用坐标接受，但被 _read_card_name 拒绝
loose = [_it(TAG_Y, TAG_X, '史诗'),
         _it(NAME_Y, 365, '战斗开始时敌人受到'),  # 9 字、无标点（描述片段）
         _it(950, 640, '选择烙印')]
p_loose = t3._imprint_card_click_pos(loose, (TAG_Y, TAG_X, '史诗'))
name_loose = t3._read_card_name(loose, (TAG_X, TAG_Y))
check('3D 备用坐标接受 9 字描述文本（_read_card_name 拒绝）',
      p_loose == (365, NAME_Y) and name_loose is None,
      f'pos={p_loose} name={name_loose}')

# ══════════════════════════════════════════════════════════════
# 4. 就绪等待（8s 窗口）副作用
# ══════════════════════════════════════════════════════════════
print()
print('── 4. 就绪等待（_last_imprint_at 8s 窗口）──')


def wait_count(logs):
    return sum(1 for m in logs if '就绪等待' in m)


# 4A 首次进入 → 等待一次
logs_d = []
bd = MockBot(frames=[frame('确认'), frame('确认'), frame('确认'), frame('确认')])
td = _mk(bd, logs_d)
td._last_imprint_at = 0.0
td._handle_imprint(frame('选择烙印'))
check('4A 首次进入 → 等待一次（日志出现1次）', wait_count(logs_d) == 1, f'n={wait_count(logs_d)}')

# 4B 紧接着再次处理（<8s）→ 不再等待
td._handle_imprint(frame('选择烙印'))
check('4B 间隔 <8s 连续处理 → 不再等待（预期）',
      wait_count(logs_d) == 1, f'n={wait_count(logs_d)}')

# 4C 长停留后重回（>8s）→ 会再次等待（预期）
td._sleep(9.0)                       # 虚拟时钟推进 9s（等价于"离上次处理已过 9s"）
td._handle_imprint(frame('选择烙印'))
check('4C 间隔 >8s → 再次等待（预期）', wait_count(logs_d) == 2, f'n={wait_count(logs_d)}')

# 4D 条件化风险：若"两次处理之间"真实耗时 >8s（长时间停留同一界面 / OCR 很慢），
#    则**每一帧**都会再等 0.9s（累计额外延迟）
logs_e2 = []
be2 = MockBot(frames=[frame('确认')] * 4)
te2 = _mk(be2, logs_e2)
te2._last_imprint_at = 0.0
for _ in range(3):
    te2._sleep(9.0)                  # 模拟帧间真实耗时 >8s
    te2._handle_imprint(frame('选择烙印'))
check('4D 帧间 >8s 时每帧都等待（条件化副作用）',
      wait_count(logs_e2) == 3, f'n={wait_count(logs_e2)}')
if wait_count(logs_e2) == 3:
    risk('长停留/慢帧时每帧额外 0.9s',
         '等待窗口基于"距上次 _handle_imprint"而非"进入界面时刻"；只要帧间耗时>8s，每帧都会再等 0.9s')

# ══════════════════════════════════════════════════════════════
# 5. _read_card_name 坐标语义（重试后用原 pick 读名是否正确）
# ══════════════════════════════════════════════════════════════
print()
print('── 5. 重试后用原 pick 坐标读卡名是否正确 ──')
t5 = _mk(MockBot())
f5 = frame('选择烙印')
n_orig = t5._read_card_name(f5, (TAG_X, TAG_Y))     # 调用方实际传入：(pick[1], pick[0])
n_other_x = t5._read_card_name(f5, (9999, TAG_Y))   # x 换掉
check('5A 读名只用 tag 的 y（x 变化不影响结果）→ 重试后仍按原坐标读名正确',
      n_orig == NAME_TXT and n_other_x == NAME_TXT,
      f'orig={n_orig} other_x={n_other_x}')
check('5B _read_card_name 源码仅用 tag_pos[1]（无 tag_pos[0] 参与）',
      'dy = tag_pos[1] - it[0]' in open(
          os.path.join(ROOT, 'tasks', 'labyrinth.py'), encoding='utf-8').read())
print('   [结论] 重试点击改的是**点击坐标**，而读名用的是**原 pick 的 tag y**，二者解耦 →')
print('          用原 pick 读卡名**正确**，不会因点击落到备用坐标而读错（x 不参与读名）')

# ══════════════════════════════════════════════════════════════
# 6. 代码事实（两条路径是否都走了公共方法）
# ══════════════════════════════════════════════════════════════
print()
print('── 6. 代码事实 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'), encoding='utf-8').read()
check('6A 新增 _imprint_select_and_confirm', 'def _imprint_select_and_confirm' in _src)
check('6B 新增 _imprint_card_click_pos', 'def _imprint_card_click_pos' in _src)
check('6C 就绪等待常量存在', '_IMPRINT_READY_WAIT' in _src)
check('6D 具名优先级路径 + 普通路径均调用公共方法（≥2 处）',
      _src.count('self._imprint_select_and_confirm(') >= 2,
      f"count={_src.count('self._imprint_select_and_confirm(')}")
# 旧的无校验点击（直接点选卡后不校验）应已消失
check('6E 旧「点选卡后无条件点按钮」模式已移除',
      "self.bot.click_at(int(pick[1]), int(pick[0]), '烙印_选卡')" not in _src)

# ══════════════════════════════════════════════════════════════
# 6. v2.2.9：卡名必须是**烙印名**
#    真机 22:44 日志实证：「卡名识别「遗物之门」（精英）」——「遗物之门」是
#    `_DOOR_PRIORITY` 里的**门卡标题**，不是烙印 ✗。根因：取名只按"标签上方
#    0~120px 内最靠近的非保留文本"，**不校验是不是烙印名** → 烙印三选一弹层
#    上方/背后残留的门卡标题被当成卡名入库（本局累计虚增）。
# ══════════════════════════════════════════════════════════════
print()
print('── 6. v2.2.9 卡名排除表（门卡标题不得当烙印名）──')
t6 = _mk(MockBot())
t6.bot._ocr_screen = lambda: []      # 禁掉"再读一遍"，确保只考首帧判定
_c6 = lambda txt: [_it(TAG_Y - 100, TAG_X, txt), _it(TAG_Y, TAG_X, '精英')]
check('6A 【分叉】标签上方是门卡标题「遗物之门」→ 判**读不出**（None）',
      t6._read_card_name(_c6('遗物之门'), (TAG_X, TAG_Y)) is None,
      f"got={t6._read_card_name(_c6('遗物之门'), (TAG_X, TAG_Y))!r}"
      '（旧实现返回「遗物之门」→ 当烙印名入库、本局累计虚增）')
check('6B 含「之门」一律不当卡名（如「馈赠之门」）',
      t6._read_card_name(_c6('馈赠之门'), (TAG_X, TAG_Y)) is None, '')
check('6C 界面词（结束探索/点击空白处/强力敌人）也不当卡名',
      t6._read_card_name(_c6('结束探索'), (TAG_X, TAG_Y)) is None
      and t6._read_card_name(_c6('强力敌人'), (TAG_X, TAG_Y)) is None, '')
check('6D 不误伤：普通烙印名「藏品柜」仍读出',
      t6._read_card_name(_c6('藏品柜'), (TAG_X, TAG_Y)) == '藏品柜',
      f"got={t6._read_card_name(_c6('藏品柜'), (TAG_X, TAG_Y))!r}")
check('6E 不误伤：具名核「执裁之盾」仍读出',
      t6._read_card_name(_c6('执裁之盾'), (TAG_X, TAG_Y)) == '执裁之盾',
      f"got={t6._read_card_name(_c6('执裁之盾'), (TAG_X, TAG_Y))!r}")

# ══════════════════════════════════════════════════════════════
# 7. v2.2.10：卡名必须在**点击前**读（用户 22:45 时序报障）
#    机制：战斗后获得遗物、某类遗物累计到 2/4/6/8 → 弹出烙印选择；
#    旧实现「先点选卡+确认 → 弹层关闭回到选门界面 → 才重读卡名」✗ →
#    只能抓到背后选门界面残留的门卡标题（真机 22:44「卡名识别「遗物之门」」）。
#    分叉构造：真名**只在弹层帧可读**；点击后的帧只剩门卡标题 →
#    只有"先读名、再选择"才可能记到真名。
# ══════════════════════════════════════════════════════════════
print()
print('── 7. v2.2.10 卡名读取时序（先读名、再选择）──')
rec7 = []
_popup7 = frame('选择烙印', name=('藏品柜', NAME_Y, NAME_X))      # 弹层帧：真名可读
_after7 = [_it(240, 220, '遗物之门'), _it(950, 640, '确认')]      # 点后：残留门卡标题 + 确认钮
b7 = MockBot(frames=[_popup7] + [_after7] * 10)
t7 = _mk(b7)
t7._last_imprint_at = 9e18        # 跳过"距上次 >8s 就绪等待"，避免真等
_spy_record(t7, rec7)
t7._handle_imprint(_popup7)
check('7A 【分叉】弹层帧可读的真名「藏品柜」被记录（旧实现点完才读 → 记不到）',
      any(n == '藏品柜' for n, _q in rec7), f'recorded={rec7}')
check('7B 点后残留的门卡标题「遗物之门」绝不被记成烙印名',
      all(n != '遗物之门' for n, _q in rec7), f'recorded={rec7}')

# ══════════════════════════════════════════════════════════════
# 8. 回归套件
# ══════════════════════════════════════════════════════════════
RUN_REGRESSION = '--no-regression' not in sys.argv
_SUITES = [
    ('tests/test_imprint_select_verify.py', '16/16'),
    ('tests/test_labyrinth_v1820.py', '175/175'),
    ('tests/test_labyrinth_nav_dryrun.py', '133/133'),
]
if RUN_REGRESSION:
    print()
    print('── 7. 回归套件 ──')
    for rel, _exp in _SUITES:
        try:
            pr = subprocess.run(
                [sys.executable, os.path.join(ROOT, rel)],
                cwd=ROOT, capture_output=True, text=True, timeout=900,
            )
            tail = (pr.stdout or '').strip().splitlines()[-1:]
            ratio = ''
            for line in reversed((pr.stdout or '').splitlines()):
                import re
                m = re.search(r'(\d+)\s*/\s*(\d+)', line)
                if m:
                    ratio = f'{m.group(1)}/{m.group(2)}'
                    break
            check(f'7 {rel} exit=0', pr.returncode == 0,
                  f'rc={pr.returncode} ratio={ratio} tail={tail}')
            if ratio:
                a, b = ratio.split('/')
                check(f'7 {rel} 全部通过({ratio})', a == b and int(b) > 0, ratio)
        except Exception as exc:  # noqa: BLE001
            check(f'7 {rel} exit=0', False, f'异常 {exc!r}')

# ══════════════════════════════════════════════════════════════
print()
print('=' * 62)
print(f'独立验证：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _RISKS:
    print(f'证伪/风险点：{len(_RISKS)}')
    for r in _RISKS:
        print(f'  ⚠ {r}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print('  ', f_)
print('=' * 62)
sys.exit(0 if not _FAIL else 1)
