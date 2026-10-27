# -*- coding: utf-8 -*-
"""每日任务任务干跑测试（v1.9.49）：导航+主菜单+一键领取全链路 Mock。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
import tasks.global_nav as gnav_mod
from tasks.catalog import DailyQuestTask  # noqa: F401  触发注册

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


class _FakeNav:
    def __init__(self, bot, logger=None, stop_check=None):
        self._log = logger or (lambda m: None)

    def navigate_to(self, target, arrived_cb=None):
        self._log(f"[信息] 全局导航 → 目标「{target}」（0 层）")
        return True


def _it(y, x, text, conf=0.95):
    return (y, x, text, conf)


f_world = [_it(1008, 519, '神秘屋'), _it(1008, 600, '玩法目录'),
           _it(1008, 680, '英雄厅堂')]
# v2.1.26：帧里必须带**底部一级标签行**（5 个全给）——页面判据已从
# 「标题=每日任务」放宽为「任务界面」（判据=一级标签行 ≥3）。真机实测
# 8 张标签截图该行**全部**常驻，故这样更贴近真机画面。
_TABROW = [_it(1006, 500, '日常任务'), _it(1006, 593, '翻翻乐'),
           _it(1006, 686, '成长挑战'), _it(1006, 778, '赛季任务'),
           _it(1006, 872, '家园目标')]
f_daily = [_it(30, 208, '每日任务'), _it(882, 414, '每日任务'),
           _it(810, 1036, '一键领取')] + _TABROW
f_daily_no_btn = [_it(30, 208, '每日任务'), _it(882, 414, '每日任务')] + _TABROW


class _DailyBot:
    def __init__(self, frames, stop_after=1000):
        self._frames = list(frames)
        self._i = -1
        self._n = 0
        self._stop_after = stop_after
        self.clicks = []
        self.calls = []

    def _ocr_screen(self, img=None, no_cache=False):
        self._n += 1
        self._i = min(self._i + 1, len(self._frames) - 1)
        return self._frames[self._i]

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'btn_menu_l0' in name:
            return (60, 1170)
        if 'btn_quest' in name:
            return (300, 1100)
        return None

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def _click_abs(self, x, y):
        self.clicks.append(f'_click_abs:({x},{y})')
        return True

    def click_ratio(self, rx, ry, label=''):
        # v2.1.26：两级标签切换走 click_ratio（真 bot 的公开接口）
        self.clicks.append(label)
        return True

    def dismiss_blink_popup(self, items=None):
        for it in (items or []):
            if len(it) >= 4 and '点击空白处' in it[2]:
                self.clicks.append('闪烁弹窗_点空白')
                return True
        return False


# v2.1.26（用户要求）：**公会任务页** —— 底部同样有「每日任务」「公会任务」
# 两行文字，旧判据必误判成每日任务页。标题为「公会任务」。
f_guild = [_it(30, 208, '公会任务'), _it(882, 414, '每日任务'),
           _it(886, 924, '公会任务')] + _TABROW

def _paused():
    ev = threading.Event()
    ev.set()
    return ev

def make_task(bot):
    logs = []
    t = DailyQuestTask({
        'bot': bot,
        'logger': lambda m: logs.append(m),
        'stats': {},
        'stop_event': threading.Event(),
        'pause_event': _paused(),
        'max_time': None,
    })
    t._stop = threading.Event()
    return t, logs


def run_case(name, frames, checks):
    print(f'── {name} ──')
    gnav_mod.GlobalNavigator = _FakeNav
    bot = _DailyBot(frames)
    t, logs = make_task(bot)
    r = t.run()
    for cname, cond, detail in checks:
        check(cname, cond(r, bot, logs),
              detail(r, bot, logs) if callable(detail) else detail)
    return bot, logs


run_case('1. 导航+主菜单+一键领取 全链路',
         [f_world, f_world, f_world,
              f_daily, f_daily, f_daily, f_daily_no_btn],
         [
             ('1a 全链路 done',
              lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('1b 点了主菜单按钮',
              lambda r, b, l: '_click_abs:(60,1170)' in b.clicks,
              lambda r, b, l: str(b.clicks[:4])),
             ('1c 点了任务按钮',
              lambda r, b, l: '_click_abs:(300,1100)' in b.clicks,
              lambda r, b, l: str(b.clicks[:6])),
             ('1d 点了一键领取',
              lambda r, b, l: '每日任务_一键领取' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '一键领取' in c][:1])),
             ('1e 领取完成日志',
              lambda r, b, l: any('领取完毕' in m for m in l),
              lambda r, b, l: str([m for m in l if '完毕' in m][:1])),
             ('1f 0 层启动：直接点主菜单，无导航绕路（v1.9.59）',
              lambda r, b, l: not any('全局导航' in m for m in l),
              lambda r, b, l: str([m for m in l if '导航' in m][:1])),
         ])

print('── 1bis. 2 层启动 → 先试主菜单按钮（v2.1.15：未登记画面不再直接导航）──')
f_idle = [_it(862, 639, '已完成全部挑战'), _it(536, 287, '先锋关卡'),
          _it(494, 268, '赛季挂机关卡'), _it(980, 638, '英雄厅堂')]
run_case('1bis. 挂机关卡页(2层)启动 → 主菜单 → 菜单项',
         [f_idle, f_idle, f_world, f_world,
              f_daily, f_daily, f_daily, f_daily_no_btn],
         [
             ('1g 【v2.1.15】2 层启动：先试点主菜单按钮，不再直接导航绕路',
              lambda r, b, l: (not any('全局导航' in m for m in l)
                               and any('主菜单按钮' in m for m in l)),
              lambda r, b, l: str([m for m in l if '主菜单' in m][:1])),
             ('1h 最终 done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
         ])

print('── 1ter. 启动即在奖励弹窗（250 钻石）→ 关弹窗后领取（v1.9.60）──')
f_popup = [_it(30, 208, '每日任务'), _it(882, 414, '每日任务'),
           _it(1006, 500, '日常任务'), _it(430, 640, '250')]
run_case('1ter. 奖励弹窗启动 → 点 250 关闭 → 领取',
         [f_popup, f_popup, f_popup,
              f_daily, f_daily, f_daily, f_daily_no_btn],
         [
             ('1i 点了奖励弹窗数字', 
              lambda r, b, l: '每日任务_奖励弹窗' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '奖励' in c][:1])),
             ('1j 后续领取 1 次并 done',
              lambda r, b, l: r == 'done' and '每日任务_一键领取' in b.clicks,
              lambda r, b, l: f'result={r}'),
         ])

run_case('2. 已在每日界面启动 → 直通',
         [f_daily, f_daily, f_daily, f_daily_no_btn],
         [
             ('2a 直通领取 done',
              lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('2b 无导航（无 fake_nav 日志）',
              lambda r, b, l: not any('全局导航' in m for m in l),
              lambda r, b, l: str([m for m in l if '导航' in m][:1])),
         ])

print('── 1quater. 领取后活跃度 250（100 档）→ 点击领活跃度奖励（v1.9.61）──')
# v2.1.25：`_ensure_daily_tab` 会先读一次**左上角标题**（多 1 帧 OCR），
# 故各用例的帧脚本相应多留一帧 f_daily/f_act（下方均已按此调整）。
f_act = f_daily + [_it(153, 1080, '250')]           # 顶部进度条 100 档
f_act_no_btn = f_daily_no_btn + [_it(153, 1080, '250')]
run_case('1quater. 顶部 250 活跃度档 → 点击',
         [f_act, f_act, f_act, f_act_no_btn, f_act_no_btn, f_act_no_btn],
         [
             ('1k 点了活跃度 250 档（100 档）',
              lambda r, b, l: '每日任务_活跃度250' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '活跃度' in c][:1])),
             ('1l done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
         ])

print('── v2.2.4 活跃度奖励加固（真机 11:54：一键领取后没领到活跃度奖励，'
      '旧实现静默无痕）──')
# 真机 OCR 实测：「250」@ 图像 (1082,185.5) conf 0.99 / ry 0.181；
# 活跃度数值「160」@ (169,136.5) conf 0.99。

# A. 250 存在但**置信度偏低**（真机 0.78~0.99 波动）→ 阈值 0.8 会漏、0.6 能收
f_act_low = f_daily + [_it(136, 169, '160'), _it(186, 1082, '250', 0.70)]
f_act_low_no = f_daily_no_btn + [_it(136, 169, '160'),
                                 _it(186, 1082, '250', 0.70)]
run_case('5. 「250」置信度 0.70 → 仍须领到（阈值 0.8→0.6）',
         [f_act_low] * 3 + [f_act_low_no] * 4,
         [
             ('5a 【分叉】低置信度 250 也被点到（旧阈值 0.8 会漏）',
              lambda r, b, l: '每日任务_活跃度250' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '活跃度' in c][:1])),
             ('5b done', lambda r, b, l: r == 'done',
              lambda r, b, l: f'result={r}'),
         ])

# B. 顶部读不到「250」但活跃度已 ≥100 → 按**真机实测比例**兜底补点
# v2.2.18：兜底改为**以可读的「100」标签为锚**（真机实测该标签在遮挡态
# 仍可读 conf 0.98；宝箱本体读不到）→ 帧里给「100」@(1055,189.5)
f_act_hi = f_daily + [_it(136, 169, '160'), _it(189, 1055, '100')]
f_act_hi_no = f_daily_no_btn + [_it(136, 169, '160'), _it(189, 1055, '100')]
run_case('6. 读不到「250」（动画遮挡）→ 以「100」标签为锚推算宝箱并点击',
         [f_act_hi] * 3 + [f_act_hi_no] * 6,
         [
             ('6a 兜底仍点了活跃度档（旧实现直接静默 return）',
              lambda r, b, l: '每日任务_活跃度250' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '活跃度' in c][:1])),
             ('6b 日志点明「可能被动画遮挡」+ 以「100」标签为锚（可追溯）',
              lambda r, b, l: any('动画遮挡' in m and '为锚推算' in m
                                  for m in l),
              lambda r, b, l: str([m for m in l if '为锚' in m][:1])),
             ('6c 点击坐标 = 标签(1055,189.5) + (23,-35) = (1078,154)',
              lambda r, b, l: any('(1078, 154)' in m or '(1078,154)' in m
                                  for m in l),
              lambda r, b, l: str([m for m in l if '点击' in m and '活跃度' in m][:1])),
         ])

# C. 活跃度未满 100 → 不点，但**必须留痕**（这是本次修的核心可观测性）
f_act_lo = f_daily + [_it(136, 169, '80'), _it(189, 1055, '100')]
f_act_lo_no = f_daily_no_btn + [_it(136, 169, '80'), _it(189, 1055, '100')]
# v2.2.17/18（用户 09-17）：**不用活跃度当门槛** —— 活跃度读数同样可能被
# 动画遮住，用它当门槛会「彻底漏领」✗；改为只要有可读的「100」标签就补点
run_case('7. 活跃度读数不足（80）→ **仍按「100」标签补点**（旧实现：不点 ✗）',
         [f_act_lo] * 3 + [f_act_lo_no] * 6,
         [
             ('7a 【分叉】活跃度 80 也照样点活跃度档（旧实现跳过 ✗）',
              lambda r, b, l: '每日任务_活跃度250' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '活跃度' in c][:1])),
             ('7b 日志点明「可能被动画遮挡」',
              lambda r, b, l: any('动画遮挡' in m for m in l),
              lambda r, b, l: str([m for m in l if '遮挡' in m][:1])),
         ])

run_case('4. 【用户要求】停在「公会任务」标签 → 不得误判成每日任务',
         [f_guild, f_guild, f_daily, f_daily, f_daily, f_daily_no_btn],
         [
             ('4a 识别出不在每日任务 → 点了二级「每日任务」标签',
              lambda r, b, l: '每日任务_二级_每日任务' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '二级' in c][:1])),
             ('4b 也点了一级「日常任务」',
              lambda r, b, l: '每日任务_一级_日常任务' in b.clicks,
              lambda r, b, l: str([c for c in b.clicks if '一级' in c][:1])),
             ('4c 最终 done（切标签后正常领取）',
              lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('4d 日志明示当前标题是「公会任务」',
              lambda r, b, l: any('公会任务' in m and '不在「每日任务」标签' in m
                                  for m in l),
              lambda r, b, l: str([m for m in l if '不在「每日任务」' in m][:1])),
         ])

run_case('3. 一键领取 ×3 上限',
         [f_daily] * 60,
         [
             ('3a 3 次上限后 done',
              lambda r, b, l: r == 'done', lambda r, b, l: f'result={r}'),
             ('3b 恰好 3 次点击',
              lambda r, b, l: sum(1 for c in b.clicks if '一键领取' in c) == 3,
              lambda r, b, l: str(sum(1 for c in b.clicks
                                      if '一键领取' in c))),
         ])

print(f'══ 每日任务干跑：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)
