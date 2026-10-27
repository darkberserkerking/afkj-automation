"""结社战线任务干跑验证（v2.2.25，**判据来自真机截图**）。

截图来源：C:\\Users\\x9502\\Pictures\\Screenshots\\结社战线\\（2026-10-27 用户提供）
  · 主界面   ：结社战线(464,36) / 扫荡(464,951) / 今日剩余：N(463,982) / 挑战(917,961)
              另有「关卡扫荡奖励（…」含"扫荡" → 故扫荡按钮必须**全等**匹配
  · 备战     ：通关阵容(285,972) / 战斗(942,945) / F(940,971)
  · 阵容弹窗  ：一键采用(694,852) / 点击空白处关闭(639,965)
  · 战斗     ：跳过(886,832)（**文本**按钮，不用模板）
  · 结算     ：战斗失败/胜利 + **再次挑战**(863,967)，**没有**点空白

覆盖口径（用户 2026-10-27）：
  扫荡一次（今日剩余=0 则跳过）/ 每局通关阵容→一键采用→F /
  失败原阵容重试一次（连败两次收手）/ max_battles 上限
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


def F(*texts):
    """一帧：按顺序给不同 y 的文本（模拟 OCR 结果）。"""
    return [_it(200 + i * 90, 640, t) for i, t in enumerate(texts)]


# ── 真机页面（文本取自截图 OCR）──
MAIN = F('结社战线', '扫荡', '今日剩余：1', '挑战', '关卡扫荡奖励（')   # 有次数
MAIN0 = F('结社战线', '扫荡', '今日剩余：0', '挑战')                  # 扫荡无次数
MAIN_NOBTN = F('结社战线', '扫荡', '今日剩余：0')                    # 挑战也没了
READY = F('结社战线-第23层', '通关阵容', '战斗', 'F')
POPUP = F('匹配阵容', '一键采用', '点击空白处关闭')
BATTLE = F('结社战线-第23层', '85秒', '跳过')
WIN_RETRY = F('战斗胜利', '再次挑战')
LOSE_RETRY = F('战斗失败', '再次挑战')
WIN_LAST = F('战斗胜利')          # 次数用尽 → 无「再次挑战」


class MockBot:
    def __init__(self, frames):
        self.frames = list(frames)
        self.i = 0
        self.clicks = []
        self.keys = []
        self.reads = []

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
        return None

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in str(it[2]):
                return (it[1], it[0])
        return None

    def send_key_f(self):
        self.keys.append('F')
        return True

    def scroll_wheel(self, amount, rx=0.5, ry=0.5, label=''):
        pass

    def find_and_focus(self):
        return 12345


def _mk(frames, options=None):
    ev = threading.Event()
    pause = threading.Event()
    pause.set()
    logs = []
    ctx = {
        'bot': MockBot(frames), 'logger': logs.append, 'stats': {},
        'stop_event': ev, 'pause_event': pause, 'options': options or {},
        'max_time': None, 'max_turns': None, 'inst_id': 'gb#dry',
    }
    t = GuildBattleTask(ctx)
    t._logs = logs
    t.startup_probe = lambda: 'ready'
    return t, ctx['bot']


print('=' * 64)
print('结社战线 干跑验证（v2.2.25 · 判据来自真机截图）')
print('=' * 64)

# ── 1. 扫荡(有次数) + 两胜(第二次次数用尽) → done ──
frames = (
    [MAIN]                     # _sweep：查剩余=1 → 点扫荡
    + [MAIN]                   # _sweep：点后复读
    + [MAIN]                   # 循环 → 点挑战
    + [READY, READY, POPUP, BATTLE, WIN_RETRY]   # 第1局(胜) → 再次挑战
    + [READY, READY, POPUP, BATTLE, WIN_LAST]    # 第2局(胜，次数尽) → 点空白
    + [MAIN_NOBTN]             # 主界面无挑战 → done
)
t, bot = _mk(frames)
r = t.run()
check('1a 正常结束 done', r == 'done', f'r={r!r}')
check('1b 点过「扫荡」', any(c[2] == '结社战线_扫荡' for c in bot.clicks),
      str([c[2] for c in bot.clicks][:3]))
check('1c 「挑战」点了 1 次（第2局由「再次挑战」进入）',
      sum(1 for c in bot.clicks if c[2] == '结社战线_挑战') == 1,
      str([c[2] for c in bot.clicks]))
check('1d 「再次挑战」点了 1 次',
      sum(1 for c in bot.clicks if c[2] == '结社战线_再次挑战') == 1,
      str([c[2] for c in bot.clicks]))
check('1e 「跳过」点了 2 次（文本判据）',
      sum(1 for c in bot.clicks if c[2] == '结社战线_跳过') == 2,
      str([c[2] for c in bot.clicks]))
check('1f 每局都点通关阵容/一键采用、各按 2 次 F',
      sum(1 for c in bot.clicks if c[2] == '备战_通关阵容') == 2
      and sum(1 for c in bot.clicks if c[2] == '备战_一键采用') == 2
      and bot.keys.count('F') == 2,
      f"通关={sum(1 for c in bot.clicks if c[2]=='备战_通关阵容')} "
      f"采用={sum(1 for c in bot.clicks if c[2]=='备战_一键采用')} "
      f"F={bot.keys}")
check('1g 战绩 2 胜 0 负', (t.wins, t.losses, t.battles) == (2, 0, 2),
      f'{t.wins}胜/{t.losses}负/{t.battles}局')

# ── 2. 扫荡「今日剩余：0」→ 不点扫荡 ──
frames2 = (
    [MAIN0]                    # 剩余 0 → 跳过扫荡（不点）
    + [MAIN0]                  # 循环 → 挑战
    + [READY, READY, POPUP, BATTLE, WIN_LAST]
    + [MAIN_NOBTN]
)
t2, bot2 = _mk(frames2)
r2 = t2.run()
check('2a 剩余 0 → 不点扫荡，直接进挑战', r2 == 'done'
      and not any(c[2] == '结社战线_扫荡' for c in bot2.clicks),
      f'r={r2!r} clicks={[c[2] for c in bot2.clicks][:4]}')
check('2b 仍然打了一局', t2.battles == 1, f'battles={t2.battles}')

# ── 3. 失败 → 原阵容重试一次 → 再失败 → failed ──
frames3 = (
    [MAIN0]
    + [MAIN0]
    + [READY, READY, POPUP, BATTLE, LOSE_RETRY]   # 第1局 失败 → 再次挑战
    + [READY, READY, POPUP, BATTLE, LOSE_RETRY]   # 重试 又失败 → 收手
    + [MAIN_NOBTN]
)
t3, bot3 = _mk(frames3)
r3 = t3.run()
check('3a 连败两次 → failed（原阵容重试一次后收手）', r3 == 'failed', f'r={r3!r}')
check('3b 战绩 0 胜 2 负', (t3.wins, t3.losses) == (0, 2),
      f'{t3.wins}胜/{t3.losses}负')

# ── 4. max_battles=1 → 打完一局就停 ──
frames4 = (
    [MAIN0] + [MAIN0] + [READY, READY, POPUP, BATTLE, WIN_LAST]
)
t4, bot4 = _mk(frames4, options={'max_battles': 1})
r4 = t4.run()
check('4a max_battles=1 → 只打 1 局就 done',
      r4 == 'done' and t4.battles == 1, f'r={r4!r} battles={t4.battles}')

# ── 5. 判据健壮性（真机文本）──
_g = GuildBattleTask.__new__(GuildBattleTask)
_main = F('结社战线', '扫荡', '今日剩余：1', '挑战', '关卡扫荡奖励（')
_pos = _g._find_exact(_main, '扫荡', 0.7)
check('5a 「扫荡」全等匹配不命中「关卡扫荡奖励（」',
      _pos is not None and _pos[1] == 290,   # 第二行（y=200+90）才是按钮
      f'pos={_pos}')
_ready = F('结社战线-第23层', '通关阵容', '战斗', 'F')
check('5b 备战页能认出「通关阵容」',
      _g._find_exact(_ready, '通关阵容', 0.7) is not None)
_settle = F('战斗失败', '再次挑战')
check('5c 结算页能认出「再次挑战」（而不是"挑战"）',
      _g._find_exact(_settle, '再次挑战', 0.7) is not None
      and _g._find_exact(_settle, '挑战', 0.7) is None)
_battle = F('结社战线-第23层', '85秒', '跳过')
check('5d 战斗页能认出文本「跳过」',
      _g._find_exact(_battle, '跳过', 0.7) is not None)

print()
print(f'══ 结社战线干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
    print('  --- 帧读取序列 ---')
    for idx, texts in bot.reads[:20]:
        print(f'    F{idx}: {texts}')
sys.exit(0 if not _FAIL else 1)
