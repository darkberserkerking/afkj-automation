"""结社战线任务干跑验证（v2.2.24）。

覆盖用户 2026-10-27 指定的流程与口径：
  · 扫荡：点一次；无次数（提示"次数不足"）→ 跳过
  · 循环：挑战 → 备战（通关阵容 → 一键采用 → F）→ 结算 → 再挑战
  · 失败：**原阵容重试一次**；连续两次失败 → 结束（failed）
  · 停止：没有「挑战」按钮（次数用尽）/ max_battles 上限
  · 「扫荡」必须**全等**命中（不能被"扫荡掉落"这类标签抢答）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_guild_battle_dryrun.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

import fast_time                      # noqa: E402  虚拟时钟
fast_time.CLOCK.install()

from tasks.guild_battle import GuildBattleTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f'[{"PASS" if cond else "FAIL"}] {name}'
          + (f'  {detail}' if detail else ''))


def _it(cy, cx, text, conf=0.98):
    return (cy, cx, text, conf)


def F(*texts, cy=300):
    """一帧：按顺序给不同 y 的文本（模拟 OCR 结果）。"""
    return [_it(cy + i * 60, 640, t) for i, t in enumerate(texts)]


def ROUND(main=('结社战线', '挑战'), result=None):
    """一局的帧序列（实测 5 次读取）：
       主界面(点挑战) → 备战(循环判定) → 备战(①通关阵容) → 备战(②一键采用) → 结算
    """
    return [F(*main), F(*READY), F(*READY), F(*READY), F(*(result or WIN))]


MAIN = ('结社战线', '挑战', '扫荡')
MAIN_SWEPT = ('结社战线', '挑战')          # 扫荡后
MAIN_NOBTN = ('结社战线',)                 # 次数用尽（无挑战）
READY = ('通关阵容', '一键采用', '战斗')    # 备战（两种状态合并，读取顺序无关）
WIN = ('战斗胜利', '点击空白处关闭')
LOSE = ('战斗失败', '点击空白处关闭')


class MockBot:
    def __init__(self, frames):
        self.frames = list(frames)
        self.i = 0
        self.clicks = []       # (x, y, label)
        self.keys = []         # 'F'
        self.scrolls = 0
        self.reads = []        # (帧序号, 该帧文本) —— 诊断用

    def _ocr_screen(self, img=None, no_cache=False):
        f = self.frames[min(self.i, len(self.frames) - 1)]
        self.reads.append((self.i, tuple(str(x[2]) for x in f)))
        self.i += 1
        return f

    def click_at(self, x, y, label=''):
        self.clicks.append((x, y, label))
        return True

    def _click_abs(self, x, y, label=''):
        self.clicks.append((x, y, label))
        return True

    def template_match(self, tpl, threshold=0.8, silent=True,
                       scale_range=None, region=None):
        return None            # 无「跳过」按钮

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in str(it[2]):
                return (it[1], it[0])
        return None

    def send_key_f(self):
        self.keys.append('F')
        return True

    def scroll_wheel(self, amount, rx=0.5, ry=0.5, label=''):
        self.scrolls += 1

    def find_and_focus(self):
        return 12345


def _mk(frames, options=None):
    ev = threading.Event()
    pause = threading.Event()
    pause.set()
    logs = []
    ctx = {
        'bot': MockBot(frames),
        'logger': logs.append,
        'stats': {},
        'stop_event': ev,
        'pause_event': pause,
        'options': options or {},
        'max_time': None,
        'max_turns': None,
        'inst_id': 'guild_battle#dry',
    }
    t = GuildBattleTask(ctx)
    t._logs = logs
    # 跳过导航：让 startup_probe 直接 ready（本测试只验证循环）
    t.startup_probe = lambda: 'ready'
    return t, ctx['bot']


print('=' * 62)
print('结社战线 干跑验证（v2.2.24）')
print('=' * 62)

# ── 1. 扫荡 + 两连胜 + 次数用尽 → done ──
frames = (
    [F(*MAIN)]                       # _sweep 读 → 点扫荡
    + [F(*MAIN_SWEPT)]               # _sweep 点后复读
    + ROUND() + ROUND()              # 第1、2局（各胜）
    + [F(*MAIN_NOBTN)]               # 无挑战 → done
)
t, bot = _mk(frames)
r = t.run()
check('1a 任务正常结束（done）', r == 'done', f'r={r!r}')
check('1b 点过「扫荡」（全等命中）',
      any(c[2] == '结社战线_扫荡' for c in bot.clicks),
      str([c[2] for c in bot.clicks][:3]))
check('1c 「挑战」点了 2 次', 
      sum(1 for c in bot.clicks if c[2] == '结社战线_挑战') == 2,
      str([c[2] for c in bot.clicks]))
check('1d 每局都点了「通关阵容」和「一键采用」',
      sum(1 for c in bot.clicks if c[2] == '备战_通关阵容') == 2
      and sum(1 for c in bot.clicks if c[2] == '备战_一键采用') == 2,
      f"通关阵容={sum(1 for c in bot.clicks if c[2]=='备战_通关阵容')} "
      f"一键采用={sum(1 for c in bot.clicks if c[2]=='备战_一键采用')}")
check('1e 按了 2 次 F', bot.keys.count('F') == 2, str(bot.keys))
check('1f 战绩 2 胜 0 负', (t.wins, t.losses, t.battles) == (2, 0, 2),
      f'{t.wins}胜/{t.losses}负/{t.battles}局')
if t.wins != 2:
    print('  --- 帧读取序列（诊断）---')
    for idx, texts in bot.reads:
        print(f'    F{idx}: {texts}')
    print('  --- 任务日志（诊断）---')
    for m in t._logs[-25:]:
        print('   ', m)

# ── 2. 扫荡无次数 → 跳过（不点第二次、不影响挑战）──
frames2 = (
    [F(*MAIN), F('次数不足', '确定')]        # 点扫荡 → 提示次数不足
    + ROUND()
    + [F(*MAIN_NOBTN)]
)
t2, bot2 = _mk(frames2)
r2 = t2.run()
check('2a 扫荡无次数 → 仍能继续挑战并 done', r2 == 'done', f'r={r2!r}')
check('2b 只点了一次扫荡', 
      sum(1 for c in bot2.clicks if c[2] == '结社战线_扫荡') == 1)

# ── 3. 失败 → 原阵容重试一次 → 再失败 → failed ──
frames3 = (
    [F(*MAIN), F(*MAIN_SWEPT)]
    + ROUND(result=LOSE) + ROUND(result=LOSE)   # 失败 → 重试 → 又失败
    + [F(*MAIN_NOBTN)]
)
t3, bot3 = _mk(frames3)
r3 = t3.run()
check('3a 连续两次失败 → failed（原阵容重试一次后收手）', r3 == 'failed', f'r={r3!r}')
check('3b 重试用的是原阵容（未做换阵容动作）',
      not any('换' in c[2] or '>' in c[2] for c in bot3.clicks),
      str([c[2] for c in bot3.clicks]))
check('3c 战绩 0 胜 2 负', (t3.wins, t3.losses) == (0, 2),
      f'{t3.wins}胜/{t3.losses}负')

# ── 4. 挑战次数上限（max_battles=1）→ 打完一局就停 ──
frames4 = (
    [F(*MAIN), F(*MAIN_SWEPT)]
    + ROUND() + ROUND()
)
t4, bot4 = _mk(frames4, options={'max_battles': 1})
r4 = t4.run()
check('4a max_battles=1 → 只打 1 局就 done', r4 == 'done' and t4.battles == 1,
      f'r={r4!r} battles={t4.battles}')
check('4b F 只按了 1 次', bot4.keys.count('F') == 1, str(bot4.keys))

# ── 5. 「扫荡」全等判据：含"扫荡掉落"标签时不得误点 ──
from tasks.guild_battle import GuildBattleTask as _G
_f = _G.__new__(_G)
_items = [F('扫荡掉落', '扫荡次数', '扫荡')][0]
_exact = _f._find_exact(_items, '扫荡', 0.7)
check('5a 全等匹配只命中「扫荡」按钮（跳过"扫荡掉落/扫荡次数"）',
      _exact is not None and _exact[1] == 420,
      f'pos={_exact}（应为第三行 y=420）')
_only_tags = [F('扫荡掉落', '扫荡次数')][0]
check('5b 只有标签时判为无按钮 → 不误点',
      _f._find_exact(_only_tags, '扫荡', 0.7) is None)

print()
print(f'══ 结社战线干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)
