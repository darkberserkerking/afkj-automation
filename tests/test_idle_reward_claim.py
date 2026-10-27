# -*- coding: utf-8 -*-
"""v2.1.18 定向验证：挂机沙漏收益「假成功」修复 + 停止即时生效修复。

被测代码：
  Fix 1  tasks/auto_farm.py::AutoFarmTask._claim_idle_rewards / _hourglass_region
  Fix 2  tasks/base.py::BaseTask._sleep

本文件是**独立验证脚本**（Mock bot），不依赖真实游戏窗口 / OCR 引擎 / 模板文件。
真机实测基准帧（1277x1019，图像坐标）：
  - 倒计时 00:00:37  → 图像 (640.5, 797.5)  conf 0.92
  - 沙漏次数 x3      → 图像 (1087, 786)     conf 0.78~0.82（恰跨旧 0.80 门槛）
  - 模板 farm_hourglass.png 命中 → 图像 (1087, 756)  score 0.975

注意：本文件**不安装 fast_time 虚拟时钟**——Fix 2 必须用真实时钟测量耗时。

运行： python tests/test_idle_reward_claim.py
"""
import os
import sys
import time
import textwrap
import inspect
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tasks.auto_farm as AF  # noqa: E402
from tasks.auto_farm import (AutoFarmTask, _HG_MAX_CLAIMS,
                             _HG_MAX_RETRY, _HG_NO_PROGRESS_MAX,
                             _HG_SAFETY_CLICKS)  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


# ══════════════════════════════════════════════════════════════
#  Mock Bot：只暴露 _claim_idle_rewards 实际使用的方法
# ══════════════════════════════════════════════════════════════
class MockBot:
    def __init__(self, window=(0, 0, 1280, 1024), hg_screen=(1087, 756),
                 start_remaining=3, decrement_on_click=True,
                 hg_present=True, hg_none_when_regioned=False,
                 has_reward=True, chest=(640.5, 797.5), hg_conf=0.78,
                 events=None, swallow_first=0, decrement_step=1,
                 paid_cost=None):
        self.window = window
        self.hg_screen = hg_screen
        self.remaining = start_remaining
        self.decrement_on_click = decrement_on_click
        self.hg_present = hg_present
        self.hg_none_when_regioned = hg_none_when_regioned
        self.has_reward = has_reward
        self.chest = chest
        self.hg_conf = hg_conf
        self.events = events if events is not None else []
        self.clicks_at = []      # click_at 调用（图像坐标）
        self.clicks_abs = []     # _click_abs 调用（屏幕坐标）
        self.tm_calls = []       # template_match 调用（留证 region/threshold）
        self.swallow_first = swallow_first   # 前 N 次点击被结算动画吞掉（不递减）
        self.decrement_step = decrement_step  # 单次点击的递减量
        self._swallowed = 0      # 已被吞掉的点击数
        self.decrements = 0      # 游戏端实际生效的递减次数
        # v2.2.2（用户 09-13 10:02 判据）：免费次数用尽后按钮显示**钻石定价**
        # （无 x 前缀的纯数字，如 50/100/200）→ 不应再点
        self.paid_cost = paid_cost

    # ── 截图 / OCR ──
    def _ocr_screen(self, img=None, no_cache=False):
        items = []
        if self.chest is not None:
            cx, cy = self.chest
            items.append((cy, cx, "00:00:37", 0.92))        # (y, x, text, conf)
        if self.has_reward:
            items.append((500.0, 640.0, "收获奖励", 0.99))
        if self.remaining > 0:
            items.append((786.0, 1087.0, f"x{self.remaining}", self.hg_conf))
        else:
            # v2.2.22：免费次数归零后，按钮**必然**变成钻石定价（真机如此，
            # 用户 10-27 规则）—— 旧桩在 x0 时什么都不显示 ✗，导致"最后一领"
            # 永远读不到递减（不写实）。paid_cost 显式给 None 时默认 50。
            items.append((786.0, 1087.0,
                          str(self.paid_cost if self.paid_cost is not None
                              else 50), 0.97))
        return items

    def find_text(self, items, kw, min_conf=0.5):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    # ── 模板匹配：region 入参 = 屏幕绝对坐标；返回 = 屏幕绝对坐标 ──
    def template_match(self, tpl_path, threshold=0.8,
                       scale_range=(0.5, 2.0, 0.1), silent=False,
                       region=None):
        self.tm_calls.append({'tpl': tpl_path, 'threshold': threshold,
                              'scale_range': scale_range, 'region': region})
        if not self.hg_present:
            return None
        if self.hg_none_when_regioned and region is not None:
            return None
        return self.hg_screen

    # ── 坐标 ──
    def screen_to_image(self, sx, sy):
        l, t, w, h = self.window
        return (sx - l, sy - t)

    def window_rect(self):
        return self.window

    # ── 点击 ──
    def click_at(self, x, y, label=''):
        self.clicks_at.append((x, y, label))
        self.events.append(('click_at', (x, y, label)))

    def _click_abs(self, x, y):
        self.clicks_abs.append((x, y))
        self.events.append(('click_abs', (x, y)))
        if self.decrement_on_click and self.remaining > 0:
            if self._swallowed < self.swallow_first:
                self._swallowed += 1            # 该次点击被吞（次数不递减）
            else:
                self.remaining = max(0, self.remaining - self.decrement_step)
                self.decrements += 1


def _make_task(bot, stop=None, pause=None, patch_sleep=True):
    logs = []

    def logger(m):
        logs.append(m)
        if bot is not None:
            bot.events.append(('log', m))

    if stop is None:
        stop = threading.Event()
    if pause is None:
        pause = threading.Event()
        pause.set()                       # 默认非暂停
    ctx = {'bot': bot, 'logger': logger, 'stats': {},
           'stop_event': stop, 'pause_event': pause}
    t = AutoFarmTask(ctx)
    if patch_sleep:
        t._sleep = lambda *a, **k: None   # Fix1 测试不真等
    t._logs = logs
    return t


def _run_claim(bot):
    t = _make_task(bot)
    t._claim_idle_rewards()
    return t, bot


# ══════════════════════════════════════════════════════════════
#  Fix 1：沙漏收益领取
# ══════════════════════════════════════════════════════════════
print("── Fix1-A1 真实帧 x3(conf0.78)：必须点 3 次且仅在消失后报成功 ──")
bot = MockBot(start_remaining=3, decrement_on_click=True)
t, bot = _run_claim(bot)
succ = [m for m in t._logs if "[成功]" in m]
check("1a 真实帧点击沙漏 3 次（x3）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)} {bot.clicks_abs}")
check("1b 每次 _click_abs 用模板屏幕坐标 (1087,756)",
      bot.clicks_abs == [(1087, 756)] * 3, str(bot.clicks_abs))
check("1c 点击过 → 确实报成功", bool(succ), str(succ[-1] if succ else None))
check("1d 成功文案含实际次数（共 3 次）",
      any("共 3 次" in m for m in succ), str(succ))
_last_click = max([i for i, e in enumerate(bot.events)
                   if e[0] == 'click_abs'], default=-1)
_first_succ = next((i for i, e in enumerate(bot.events)
                    if e[0] == 'log' and '[成功]' in e[1]), -1)
check("1e 成功日志出现在最后一次点击之后（非零点击假成功）",
      _first_succ > _last_click, f"first_succ={_first_succ} last_click={_last_click}")

print("── Fix1-A2 无 xN：绝不假成功 ──")
bot = MockBot(start_remaining=0)
t, bot = _run_claim(bot)
check("2a 无 xN → 零点击", len(bot.clicks_abs) == 0, f"clicks={len(bot.clicks_abs)}")
check("2b 无 xN → 绝不出现 [成功]",
      not any("[成功]" in m for m in t._logs), str(t._logs))
check("2c 无 xN → 如实报「未检测到沙漏剩余次数」",
      any("未检测到沙漏剩余次数" in m for m in t._logs), str(t._logs))

print("── Fix1-A3 region 单位：屏幕绝对坐标 ──")
bot = MockBot(window=(100, 50, 1280, 1024), start_remaining=3)
t, bot = _run_claim(bot)
reg0 = bot.tm_calls[0]['region'] if bot.tm_calls else None
check("3a region == (1060,664,320,307)（屏幕坐标）",
      reg0 == (1060, 664, 320, 307), f"region[0]={reg0}")
check("3b region 不是图像坐标 (960,614,320,307)",
      reg0 != (960, 614, 320, 307), f"region[0]={reg0}")

print("── Fix1-A4 跨坐标系：模板屏幕坐标 → 图像坐标后再比较 ──")
bot = MockBot(window=(100, 50, 1280, 1024), hg_screen=(1187, 806),
              start_remaining=3)
t, bot = _run_claim(bot)
chest_clicks = [c for c in bot.clicks_at if c[2] == '挂机_宝箱']
cx, cy = (chest_clicks[0][0], chest_clicks[0][1]) if chest_clicks else (None, None)
check("4a 宝箱点击 y 已换算回图像坐标 756", cy == 756, f"click_at={chest_clicks[:1]}")
check("4b 不是屏幕 y=806（旧坐标系混用）", cy != 806, f"click_at={chest_clicks[:1]}")
check("4c 宝箱点击 x≈640（倒计时中心 640.5）",
      cx is not None and abs(cx - 640.5) <= 0.5, f"x={cx}")
check("4d 沙漏点击仍用屏幕坐标 (1187,806)",
      bot.clicks_abs == [(1187, 806)] * 3, str(bot.clicks_abs))

print("── Fix1-A5 证伪：把代码变异回旧行为，本测试必须能抓到 ──")
# 说明：tasks/ 在 git 中未被跟踪、backup/ 里也没有含 _claim_idle_rewards 的旧版，
#       因此无法直接 checkout 修复前版本。改用**源码等价变异**复原旧缺陷：
#         A) 仅把 xN 门槛 0.60 → 0.80（旧门槛，真实帧 0.78 被滤掉）
#         B) 门槛 0.80 + 「读不到即报成功」（旧实现的纯负向判成功）
def _mutant(replacements):
    src = textwrap.dedent(inspect.getsource(AutoFarmTask._claim_idle_rewards))
    for a, b in replacements:
        if a not in src:
            raise RuntimeError(f"变异锚点缺失: {a[:40]!r}")
        src = src.replace(a, b)
    ns = dict(vars(AF))
    exec(src, ns)             # noqa: S102 —— 测试内构造变异体
    return ns['_claim_idle_rewards']


m_gate = _mutant([("it[3] < _HG_CONF_MIN", "it[3] < 0.80")])
b1 = MockBot(start_remaining=3)
t1 = _make_task(b1)
m_gate(t1)
check("5a 变异(门槛0.80)：零点击 → 断言1(期望3次)必失败，测试非空洞",
      len(b1.clicks_abs) == 0, f"clicks={len(b1.clicks_abs)}")

m_old = _mutant([("it[3] < _HG_CONF_MIN", "it[3] < 0.80"),
                 ("[信息] 未检测到沙漏剩余次数（xN），本轮不做沙漏领取",
                  "[成功] 沙漏收益领取完毕")])
b2 = MockBot(start_remaining=3)
t2 = _make_task(b2)
m_old(t2)
old_succ = [m for m in t2._logs if "[成功]" in m]
check("5b 变异(旧假成功)：零点击 + 假 [成功]（复原原始缺陷）",
      len(b2.clicks_abs) == 0 and bool(old_succ),
      f"clicks={len(b2.clicks_abs)} success={old_succ}")
check("5c 该变异同时违反断言1的两个期望(3次点击/点击后才成功) → 测试可检测",
      len(b2.clicks_abs) != 3 and len(old_succ) > 0, "mutation detected")

print("── Fix1-A6 循环安全：xN 永不消失 → 有界停止，绝不多领 ──")
# v2.1.19 契约：生效次数上限 = 首次读到的游戏额度(5)；
# 点击预算 = 额度 + _HG_MAX_RETRY，且「一次都没成功」时连续被吞
# _HG_NO_PROGRESS_MAX 次即停手 → 不死点。
bot = MockBot(start_remaining=5, decrement_on_click=False)
t, bot = _run_claim(bot)
cheap = min(5 + _HG_MAX_RETRY, _HG_NO_PROGRESS_MAX)
check(f"6a 全程无进展 → 停在 {cheap} 次点击（远小于 {_HG_MAX_CLAIMS}）",
      len(bot.clicks_abs) == cheap and cheap < _HG_MAX_CLAIMS,
      f"clicks={len(bot.clicks_abs)} 期望={cheap} MAX={_HG_MAX_CLAIMS}")
check("6b 如实报「未实际领取」，且不生造成功",
      any("未实际领取" in m for m in t._logs)
      and not any("[成功]" in m for m in t._logs), str(t._logs[-2:]))
check("6c 每次被吞点击都有「未被结算」警告（可解释多余点击）",
      sum(1 for m in t._logs if "未被结算" in m) == cheap,
      f"warn={sum(1 for m in t._logs if '未被结算' in m)}")

print("── Fix1-附加：兜底与边界 ──")
bot = MockBot(hg_none_when_regioned=True, start_remaining=3)
t, bot = _run_claim(bot)
regs = [c['region'] for c in bot.tm_calls]
check("E1 区域失配 → 全屏兜底（第 2 次调用 region=None）",
      len(regs) >= 2 and regs[0] == (960, 614, 320, 307) and regs[1] is None,
      str(regs[:2]))
check("E2 兜底命中后仍点 3 次", len(bot.clicks_abs) == 3, str(bot.clicks_abs))

bot = MockBot(hg_present=False, start_remaining=5)
t, bot = _run_claim(bot)
check("E3 xN 在但图标未命中 → 零点击且不报成功",
      len(bot.clicks_abs) == 0 and not any("[成功]" in m for m in t._logs),
      f"clicks={len(bot.clicks_abs)}")

# ══════════════════════════════════════════════════════════════
#  Fix1-v2.1.19 增量验证：逐次结算验证 / 生效次数 ≤ 游戏额度
#  真机 14:31 场景：屏上 x3，前 K 次点击被结算动画吞掉
# ══════════════════════════════════════════════════════════════
import re as _re


def _reported_claims(logs):
    """解析 bot 自报的生效领取次数：'共 N 次'=成功 / '已生效 N 次'=部分。"""
    for m in reversed(logs):
        mm = _re.search(r"共 (\d+) 次", m)
        if mm:
            return int(mm.group(1)), "success"
        mm = _re.search(r"已生效 (\d+) 次", m)
        if mm:
            return int(mm.group(1)), "partial"
    return 0, "none"


print("── V1 复现 14:31：x3 且前 3 次点击被吞 ──")
bot = MockBot(start_remaining=3, swallow_first=3)
t, bot = _run_claim(bot)
_eff, _kind = _reported_claims(t._logs)
_warn = sum(1 for m in t._logs if "未被结算" in m)
_succ = [m for m in t._logs if "[成功]" in m]
check("V1a 生效领取次数 == 3（x3 就应恰领 3 次；用户硬规则）",
      _eff == 3,
      f"observed eff={_eff} kind={_kind} clicks={len(bot.clicks_abs)} remaining={bot.remaining}")
check("V1b 物理点击 ≤ allowance+_HG_MAX_RETRY",
      len(bot.clicks_abs) <= 3 + _HG_MAX_RETRY,
      f"clicks={len(bot.clicks_abs)} cap={3 + _HG_MAX_RETRY}")
# v2.1.19（用户 14:45 定案）：点击被吞时**宁可重试也要领满**，
# 所以「物理点击 = 被吞数 + 生效数」是预期行为，不是 bug。
# 真正的硬约束是「生效数 ≤ 游戏额度」（见 V3 不变式）。
check("V1c 每次点击都有去向（点击 = 被吞 + 生效，无整轮空转）",
      len(bot.clicks_abs) == _warn + _eff and _kind == "success",
      f"clicks={len(bot.clicks_abs)} warn={_warn} eff={_eff} kind={_kind}")
check("V1d 每次被吞点击各有一条「未被结算」警告",
      _warn == 3, f"warn={_warn}")
check("V1e [成功] 仅在次数真正归零时出现",
      (not _succ) or bot.remaining == 0,
      f"success={_succ} remaining={bot.remaining}")
check("V1f 未归零则如实报部分/未领取（不假成功）",
      (bot.remaining == 0) == bool(_succ),
      f"remaining={bot.remaining} success={bool(_succ)} tail={t._logs[-2:]}")

print("── V2 值单调：x3 正常递减 → 恰 3 次、共 3 次 ──")
bot = MockBot(start_remaining=3, decrement_on_click=True)
t, bot = _run_claim(bot)
check("V2a 恰好 3 次物理点击（无重试）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)}")
check("V2b [成功] 且文案含「共 3 次」",
      any("[成功]" in m for m in t._logs) and any("共 3 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m]))
check("V2c 无「未被结算」告警",
      not any("未被结算" in m for m in t._logs))

# v2.1.20（用户 15:12「最高可能不止 3」）：初始次数可以 >3，
# 不得被 12 之类的人为上限截断 —— 以屏幕读到的 N 为准。
for _n in (5, 7, 15):
    bot = MockBot(start_remaining=_n, decrement_on_click=True)
    t, bot = _run_claim(bot)
    check(f"V2d x{_n} 正常递减 → 恰领 {_n} 次（不被人为上限截断）",
          len(bot.clicks_abs) == _n
          and any(f"共 {_n} 次" in m for m in t._logs),
          f"clicks={len(bot.clicks_abs)} "
          f"succ={[m for m in t._logs if '[成功]' in m]}")

print("── V3 不变式：生效领取次数 ≤ 首次读到的 n_val ──")
_inv = [
    ("正常递减 x3", dict(start_remaining=3)),
    ("前 1 次被吞 x3", dict(start_remaining=3, swallow_first=1)),
    ("前 3 次被吞 x3", dict(start_remaining=3, swallow_first=3)),
    ("次数永不递减 x5", dict(start_remaining=5, decrement_on_click=False)),
    ("单次掉 2 x3", dict(start_remaining=3, decrement_step=2)),
]
for _name, _kw in _inv:
    _b = MockBot(**_kw)
    _t, _b = _run_claim(_b)
    _n = _kw["start_remaining"]
    _e, _k = _reported_claims(_t._logs)
    check(f"V3 不变式[{_name}] 生效 {_e} ≤ n_val {_n}",
          _e <= _n,
          f"eff={_e} n_val={_n} clicks={len(_b.clicks_abs)} "
          f"decrements={_b.decrements} kind={_k}")

print("── V4 证伪：本套断言确实能抓到超领 / 两整轮 ──")
# A) 变异：循环不受生效上限约束 + 每次点击都计一次（旧式不计验证）→ 应超领
_m_over = _mutant([
    ("while remaining > 0 and clicks < _HG_SAFETY_CLICKS:",
     "while clicks < _HG_SAFETY_CLICKS:"),
    ("if ok:", "if True:"),
])
_b = MockBot(start_remaining=3, swallow_first=3)
_t = _make_task(_b)
_m_over(_t)
_e, _k = _reported_claims(_t._logs)
check("V4a 变异后出现超领（生效 > n_val）→ V3 断言非空洞",
      _e > 3, f"observed eff={_e} n_val=3 clicks={len(_b.clicks_abs)} kind={_k}")

# B) 变异：去掉"连续未确认即停"的安全阀 → 读数失灵时会一路点到硬上限
#    （v2.2.22：这条守卫是"绝不盲点花钻"的关键防线，必须证明它有用）
_m_noguard = _mutant([
    ("if misses >= _HG_NO_PROGRESS_MAX:", "if False:"),
])
_b = MockBot(start_remaining=5, decrement_on_click=False)
_t = _make_task(_b)
_m_noguard(_t)
check("V4b 变异(去掉安全阀)：无进展时会点到硬上限 → 6a/6b 断言非空洞",
      len(_b.clicks_abs) == _HG_SAFETY_CLICKS,
      f"clicks={len(_b.clicks_abs)} 硬上限={_HG_SAFETY_CLICKS}")

# ══════════════════════════════════════════════════════════════
#  Fix 2：_sleep 停止即时生效（真实时钟）
# ══════════════════════════════════════════════════════════════
print("── Fix2：_sleep 停止即时生效（真实时钟，不装 fast_time）──")


def _sleep_task(stop=None, pause=None):
    return _make_task(MockBot(start_remaining=0), stop=stop, pause=pause,
                      patch_sleep=False)


# 7. 预先置停止 → 立即返回
stop = threading.Event()
stop.set()
t = _sleep_task(stop=stop)
t0 = time.perf_counter()
t._sleep(5.0)
dt = time.perf_counter() - t0
check("7 已停止 → _sleep(5.0) 立即返回 (<0.5s)", dt < 0.5, f"dt={dt:.3f}s")

# 8. 运行中 ~0.2s 后置停止 → 及时中断
stop = threading.Event()
t = _sleep_task(stop=stop)


def _set_later():
    time.sleep(0.2)
    stop.set()


th = threading.Thread(target=_set_later, daemon=True)
th.start()
t0 = time.perf_counter()
t._sleep(5.0)
dt = time.perf_counter() - t0
th.join()
check("8 运行中置停止 → 及时中断 (<0.5s，而非睡满 5s)", dt < 0.5, f"dt={dt:.3f}s")

# 9. 未停止 → 正常计时（未被静默跳过）
stop = threading.Event()
t = _sleep_task(stop=stop)
t0 = time.perf_counter()
t._sleep(0.2)
dt = time.perf_counter() - t0
check("9 未停止 → _sleep(0.2) 仍实际等约 0.2s", 0.15 <= dt < 0.45, f"dt={dt:.3f}s")

# 10. None / 0 → 立即返回且不抛错
t = _sleep_task(stop=threading.Event())
for val in (None, 0):
    t0 = time.perf_counter()
    r = t._sleep(val)
    dt = time.perf_counter() - t0
    check(f"10 _sleep({val!r}) 立即返回且不抛错",
          dt < 0.05 and r is None, f"dt={dt:.4f}s r={r!r}")

# 11. 暂停态 check_stop 不无限递归（thread + 超时保险）
stop = threading.Event()          # 未停止
pause = threading.Event()         # 已清空 = 暂停态
t = _sleep_task(stop=stop, pause=pause)
_res = {}


def _cs():
    try:
        _res['r'] = t.check_stop()
    except BaseException as e:    # noqa: BLE001 —— 递归会抛 RecursionError
        _res['err'] = repr(e)


th = threading.Thread(target=_cs, daemon=True)
th.start()
time.sleep(0.3)
pause.set()                       # 恢复暂停
th.join(timeout=5.0)
check("11 暂停态 check_stop 不无限递归（可正常终止）",
      (not th.is_alive()) and 'err' not in _res,
      f"alive={th.is_alive()} res={_res}")

# 11b. 结构证明：_sleep 直接查 self._stop，不回调 check_stop
t = _sleep_task(stop=threading.Event())
_cnt = {'n': 0}


def _boom():
    _cnt['n'] += 1
    raise AssertionError('_sleep 递归调用了 check_stop')


t.check_stop = _boom
_raised = None
t0 = time.perf_counter()
try:
    t._sleep(0.3)
    dt = time.perf_counter() - t0
except BaseException as e:        # noqa: BLE001
    _raised = repr(e)
    dt = -1.0
check("11b _sleep 不回调 check_stop（无递归风险）",
      _raised is None and _cnt['n'] == 0,
      f"raised={_raised} check_stop_calls={_cnt['n']} dt={dt:.3f}s")

# 11c. 暂停期间收到停止 → check_stop 不得挂满 30s（_sleep 立即返回 → 快速终止）
stop = threading.Event()          # 未停止
pause = threading.Event()         # 已清空 = 暂停态
t = _sleep_task(stop=stop, pause=pause)
_res2 = {}


def _cs2():
    try:
        _res2['r'] = t.check_stop()
    except BaseException as e:    # noqa: BLE001
        _res2['err'] = repr(e)


th = threading.Thread(target=_cs2, daemon=True)
th.start()
time.sleep(0.2)
stop.set()                        # 暂停中置停止
th.join(timeout=3.0)
check("11c 暂停中置停止 → check_stop 立即返回 True（不挂 30s）",
      (not th.is_alive()) and _res2.get('r') is True,
      f"alive={th.is_alive()} res={_res2}")

# ══════════════════════════════════════════════════════════════
#  v2.2.2：次数读数失效防护（真机 09-13 09:04 报障）
#    现象：点击**生效了**（x3→x2→x1→50 钻石），工具却一直判「未结算」，
#          连点 4 次 —— 第 4 次落在**钻石付费档**上。
#    根因：读取器取**宽区域内的第一个 xN**（区占屏 25%×25%），区内若存在
#          另一个恒定 xN，读数就永远停在它上面。
#    修：① 以沙漏图标为锚取最近的 xN ② 超额度点击预算 4→0
#        ③ 未递减时打印区内全部候选（复发自带证据）
# ══════════════════════════════════════════════════════════════
print()
print("── v2.2.2 读数失效防护 ──")


class _DistractorBot(MockBot):
    """读取区内多一个**恒定 xN**干扰项（真机症状还原）。

    干扰项故意**排在 OCR 列表最前**（旧实现取"第一个"就会永远读到它），
    默认放在**远离**沙漏图标 (1087,756) 的位置 —— 新实现在锚点邻域读，
    超出 _HG_ANCHOR_MAX_DIST 的候选会被拒绝。
    """

    def __init__(self, *a, distractor=3, distractor_pos=(1120.0, 400.0),
                 **kw):
        super().__init__(*a, **kw)
        self.distractor = distractor
        self.distractor_pos = distractor_pos      # (cx, cy) 图像坐标

    def _ocr_screen(self, img=None, no_cache=False):
        items = super()._ocr_screen()
        if self.distractor is not None:
            cx, cy = self.distractor_pos
            items.insert(0, (cy, cx, f"x{self.distractor}", 0.97))
        return items


print("── 12. 区内有远离图标的恒定干扰 xN，真实次数可用 ──")
bot = _DistractorBot(start_remaining=3, decrement_on_click=True, distractor=3)
t, bot = _run_claim(bot)
check("12a 【分叉】只点 3 次（= 初始额度），不再多点第 4 次",
      len(bot.clicks_abs) == 3,
      f"clicks={len(bot.clicks_abs)}（旧实现取'第一个'= 干扰项 → 每轮都 miss，"
      f"会点到 {3 + _HG_MAX_RETRY} 次）")
check("12b 游戏端实际被消耗的次数不超过免费额度 3",
      bot.decrements <= 3, f"decrements={bot.decrements}")
check("12c 锚定读取生效：至少识别到 1 次递减（旧实现恒为 0）",
      any("已生效 1/" in m or "已生效 2/" in m for m in t._logs),
      str([m for m in t._logs if "已生效" in m][:3]))

print("── 13. （用例已并入 12 / 既有 A6）──")
# 注：曾另设「读数恒定但计数有效 → 停在额度」的用例，但它与用户硬规则冲突：
# 若紧邻图标的 xN 一直存在且不变，按契约（V1：前 3 次点击被吞仍须补点领满）
# **本就应当继续补点**，直到 _HG_NO_PROGRESS_MAX 次无进展才收手
# —— 该行为已由既有 A6 用例覆盖。钻石付费档的防护由「免费次数用尽后
# 图标旁已无计数 → _HG_ANCHOR_MAX_DIST 拒绝远处 xN → 读数 None → 收手」
# 承担，已由用例 12 覆盖。

print("── 14. 诊断与措辞 ──")
bot = MockBot(start_remaining=3, swallow_first=1)     # 第 1 次点击真被吞
t, bot = _run_claim(bot)
_diag = [m for m in t._logs if "读数区候选" in m]
check("14a 未结算的警告里带「读数区候选」（复发自带证据）",
      bool(_diag), str(_diag[:1]))
check("14b 候选项带坐标与置信度（可直接定位是哪个 xN）",
      bool(_diag) and "rx=" in _diag[0] and "conf=" in _diag[0],
      _diag[0][-70:] if _diag else "")
check("14c 措辞不再写死「仍显示 x3」这种误导表述",
      all("仍显示" not in m for m in t._logs),
      str([m for m in t._logs if "仍显示" in m][:1]))
check("14d 「未被结算」措辞保留（既有契约依赖该子串）",
      any("未被结算" in m for m in t._logs),
      str([m for m in t._logs if "未被结算" in m][:1]))

print("── 15. 静态检查 ──")
_src_af = open(os.path.join(ROOT, "tasks", "auto_farm.py"),
               encoding="utf-8").read()
import re as _re
_m = _re.search(r"^_HG_MAX_RETRY\s*=\s*(\d+)", _src_af, _re.M)
check("15a _HG_MAX_RETRY == 4（用户硬规则要求保留补点预算）",
      _m and _m.group(1) == "4", _m.group(0) if _m else "未找到")
check("15b _read_hg_count 支持锚点参数",
      "def _read_hg_count(anchor: Optional[Tuple[int, int]] = None)" in _src_af)
check("15c 存在候选抽取函数 _hg_candidates",
      "def _hg_candidates()" in _src_af)
check("15d 存在图标邻域距离上限（超距 xN 视为无关文本）",
      "_HG_ANCHOR_MAX_DIST" in _src_af
      and "<= _HG_ANCHOR_MAX_DIST ** 2" in _src_af)

print("── 16. 免费用尽 → 按钮转钻石定价（用户 09-13 10:02 判据）──")
bot = MockBot(start_remaining=3, decrement_on_click=True, paid_cost=50)
t, bot = _run_claim(bot)
check("16a 只点 3 次（免费额度用尽即收手），**绝不点第 4 次**（那是 50 钻石）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)}")
check("16b 识别钻石定价并如实报告「未消耗钻石」",
      any("钻石定价 50" in m and "未消耗钻石" in m for m in t._logs),
      str([m for m in t._logs if "钻石定价" in m][:1]))
check("16c 定价出现 = 前一次点击已是最后一次免费领取（不漏记，仍报 3 次）",
      any("共 3 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m][:1]))

print("── 17. 【真机复现】干扰 xN 仍在 + 免费用尽后出现钻石定价 ──")
# 这是 09:04 那次的最完整还原：干扰项让旧实现一直读到 xN，
# 而真实按钮已转入钻石付费档 → 旧实现会一路点到第 7 次。
bot = _DistractorBot(start_remaining=3, decrement_on_click=True,
                    distractor=3, paid_cost=50)
t, bot = _run_claim(bot)
check("17a 只点 3 次（旧实现读到干扰 xN 会点到 7 次）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)}")
check("17b 明确识别钻石定价 50 并按「已用尽」收手（不是靠读不到次数）",
      any("钻石定价 50" in m for m in t._logs),
      str([m for m in t._logs if "钻石定价" in m][:1]))
check("17c 游戏端消耗 ≤ 免费额度（未点到付费档）",
      bot.decrements <= 3, f"decrements={bot.decrements}")

print("── 18. v2.2.13 读取窗口放宽（xN 位置随状态浮动）──")
# 真机 2026-09-17 11:37：屏幕上明明有 x3 却报「未检测到」。实测证据表明
# xN 的 y 随界面状态浮动：主界面 ry≈0.77~0.80 ✓、领取弹层内 ry≈0.93 ✗、
# 「点击收获奖励.png」素材 ry≈0.625 ✗ —— 旧窗格 0.65<ry<0.90 把后两者
# 全排除。本用例即"低位置"分叉：旧窗格 → 0 点击；新窗口 → 正常领 3 次。


class _LowHourglassBot(MockBot):
    """xN 位于更低处（领取弹层内，真机 ry≈0.93）。"""

    def _ocr_screen(self, img=None, no_cache=False):
        return [(956.0 if t.startswith('x') else y, x, t, c)
                for (y, x, t, c) in super()._ocr_screen()]


bot = _LowHourglassBot(start_remaining=3, decrement_on_click=True)
t, bot = _run_claim(bot)
check("18a 【分叉】xN 在 ry≈0.93 → 新窗口仍读到并领满 3 次（旧窗格 0 次）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)} {bot.clicks_abs}")
check("18b 如实报「共 3 次」",
      any("共 3 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m][:1]))

# 低位置 + 高置信度之外：失败时**必须打印候选**（此前无痕）
bot2 = MockBot(start_remaining=0, has_reward=True)   # 无 xN（remaining=0）
t2, _ = _run_claim(bot2)
_diag2 = [m for m in t2._logs if "右下区候选" in m]
check("18c 读不到时打印右下区候选（失败自带证据）",
      bool(_diag2), str(_diag2[:1])[:120])

print("── 19. v2.2.13 置信度门槛下调（真机弹层里 OCR 只给 0.66）──")
# 用户 2026-09-17 两张现场截图实测：同一枚徽标 ——「收获奖励」弹层里读作
# `X3` **conf 0.66**（紧贴旧门槛 0.60 ✗）、点完之后读作 `x3` conf 0.84。
# 动画/光晕帧再差一点就掉到 0.5x → 被门槛滤掉 → 「未检测到 xN」✗
bot = MockBot(start_remaining=3, decrement_on_click=True, hg_conf=0.55)
t, bot = _run_claim(bot)
check("19a 【分叉】conf=0.55 → 新门槛(0.45)读到并领满 3 次（旧门槛 0.60 → 0 次）",
      len(bot.clicks_abs) == 3, f"clicks={len(bot.clicks_abs)}")
check("19b 如实报「共 3 次」",
      any("共 3 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m][:1]))

bot2 = MockBot(start_remaining=3, decrement_on_click=True, hg_conf=0.30)
t2, bot2 = _run_claim(bot2)
check("19c 门槛仍有效：conf=0.30 → 零点击（不放行低质读数）",
      len(bot2.clicks_abs) == 0, f"clicks={len(bot2.clicks_abs)}")

print("── 20. v2.2.15 沙漏读数强制 no_cache（缓存指纹漏小面积变化）──")
# 真机 2026-09-17：点击生效、数字确实递减（用户截图 x3→…→x1），但轮询读到的
# 永远是 x3 ✗。根因：OCR 结果缓存的键是**稀疏采样指纹**（10 点），徽标位置
# 离最近采样点约 100px → 指纹不变 → 60s 内一直命中缓存返回旧结果 ✗
_calls20: list = []


class _NoCacheSpyBot(MockBot):
    def _ocr_screen(self, img=None, no_cache=False):
        _calls20.append(no_cache)
        return super()._ocr_screen(img=img)


bot = _NoCacheSpyBot(start_remaining=3, decrement_on_click=True)
t, bot = _run_claim(bot)
check("20a 【关键分叉】沙漏次数读数**全部** no_cache=True（旧代码一次都不带 ✗）",
      _calls20.count(True) >= 4 and _calls20.count(False) >= 1,
      f"no_cache=True 共 {_calls20.count(True)} 次 / False {_calls20.count(False)} 次"
      "（True≥4=首次+轮询；False=宝箱倒计时等非变化检测读数，本就该走缓存）")
_src20 = open(os.path.join(ROOT, 'tasks', 'auto_farm.py'),
              encoding='utf-8').read()
check("20b 沙漏读数与失败诊断在源码里都显式 no_cache=True",
      "for it in self.bot._ocr_screen(no_cache=True):" in _src20
      and "for it in self.bot._ocr_screen(no_cache=True)" in _src20, '')
check("20c 仍能正常领满 3 次",
      any("共 3 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m][:1]))

print("── 21. v2.2.16 OCR 数字/字母混淆纠正（真机把 x1 读成 xl）──")
# 真机 2026-09-17 12:30 诊断日志实证：
#   右下区候选=[('xl', 1086, 789, 0.85)]  ← 徽标是 x1，OCR 给了 'xl'（小写L）✗
# 旧正则 [xX×](\d{1,2}) 不匹配 → 误报「未检测到沙漏剩余次数」✗


class _ConfusedBot(MockBot):
    """模拟 OCR 把 x1 读成 'xl'。"""

    def _ocr_screen(self, img=None, no_cache=False):
        return [(y, x, ('xl' if t == 'x1' else t), c)
                for (y, x, t, c) in super()._ocr_screen(img=img, no_cache=no_cache)]


bot = _ConfusedBot(start_remaining=1, decrement_on_click=True)
t, bot = _run_claim(bot)
check("21a 【分叉】'xl' 被纠正为 x1 → 正常领取（旧正则 → 「未检测到」零点击 ✗）",
      len(bot.clicks_abs) == 1, f"clicks={len(bot.clicks_abs)}")
check("21b 日志显示读到 x1（而非报未检测到）",
      any("沙漏剩余领取次数 x1" in m for m in t._logs),
      str([m for m in t._logs if "剩余领取次数" in m][:1]))
check("21c 领完如实报「共 1 次」",
      any("共 1 次" in m for m in t._logs),
      str([m for m in t._logs if "[成功]" in m][:1]))

print("── 23. v2.2.22 读数抖动不得提前收手（用户规则：显示 xN 就是免费）──")
# 真机 11:59：x3 只领 2 次就收手 —— 旧实现把"某次轮询没读到 xN"当成
# "剩 0 次"✗。本用例模拟 OCR 每隔一次漏检徽标，要求仍领满 3 次。


class _FlakyReadBot(MockBot):
    """xN 间歇性读不到（模拟 OCR 漏检波动）。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._n = 0

    def _ocr_screen(self, img=None, no_cache=False):
        items = super()._ocr_screen(img=img)
        self._n += 1
        if self._n % 2 == 0:                 # 偶数次：徽标"看不见"
            return [it for it in items if not str(it[2]).startswith('x')]
        return items


_b23 = _FlakyReadBot(start_remaining=3, decrement_on_click=True)
_t23, _b23 = _run_claim(_b23)
check("23a 【关键分叉】xN 间歇读不到 → 仍领满 3 次（旧实现遇 None 当领完 ✗）",
      _b23.decrements == 3,
      f"实际递减={_b23.decrements} 点击={len(_b23.clicks_abs)}")
check("23b 如实报「共 3 次」",
      any("共 3 次" in m for m in _t23._logs),
      str([m for m in _t23._logs if "[成功]" in m][:1])[:80])

# ══════════════════════════════════════════════════════════════
#  汇总
# ══════════════════════════════════════════════════════════════
print()
print(f"══ 沙漏领取 / 停止即时生效 定向验证：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══")
if _FAIL:
    print("失败项：")
    for f_ in _FAIL:
        print(" ", f_)
sys.exit(0 if not _FAIL else 1)
