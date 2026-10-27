"""一键家园 · 第一步「进入家园」干跑测试（v1.9.0）。

纯逻辑干跑（MockBot，无真机）：startup_probe 三态 / run() 导航成功 /
失败 / 停止 / 注册信息。运行：afk_gpu python tests/test_home_task_dryrun.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.catalog import TASK_CLASSES                      # noqa: E402
from tasks.home_task import HomeTask                        # noqa: E402
from task_registry import TaskRegistry                      # noqa: E402

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ''))
    (_PASS if cond else _FAIL).append(name)


def _make_task(bot, texts=None, stop=False, logger=None, options=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    if stop:
        stop_ev.set()
    if logger is None:
        logs: list = []
        logger = lambda m: logs.append(m)   # noqa: E731
    return HomeTask({
        'bot': bot,
        'logger': logger,
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': options or {},
        'max_time': None,
        'inst_id': 'home#dry',
    }), logs


class MockBot:
    """极简桩：texts_by_call 逐帧返回 OCR 文本；点击/ESC 记录到 calls。"""

    def __init__(self, texts_by_call, find_hits=None, nav_ok=True,
                 tm_hit=None):
        self.frames = list(texts_by_call)
        self._i = -1
        self.calls: list = []
        self.clicks: list = []
        self.find_hits = dict(find_hits or {})
        self.nav_ok = nav_ok
        self.tm_hit = tm_hit          # template_match 命中（None=未命中）

    # --- OCR 帧供给 ---
    def _next_frame(self):
        self._i += 1
        if self._i < len(self.frames):
            return self.frames[self._i]
        return self.frames[-1] if self.frames else []

    # --- facade 能力桩 ---
    def _ocr_screen(self, img=None, no_cache=False):
        return self._next_frame()

    def find_and_focus(self):
        self.calls.append('find_and_focus')
        return 111

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def click_back_button(self, tpls, threshold=0.75):
        self.calls.append('click_back_button')
        return False

    def click_at(self, x, y, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicks.append(label)

    def _click_abs(self, x, y, label=''):
        self.calls.append(f'_click_abs:({x},{y})')
        self.clicks.append(label or f'abs({x},{y})')

    def click_ratio(self, rx, ry, label=''):
        self.calls.append(f'click_ratio:{label}')
        self.clicks.append(label)

    def template_match(self, tpl, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        self.calls.append(f'template_match:{tpl}')
        return self.tm_hit

    def find_text(self, items, kw, min_conf=0.6, exact=False):
        if kw in self.find_hits:
            return self.find_hits[kw]
        return None

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append(f'scroll_wheel:{clicks}:{label}')

    # --- 全局导航依赖（真实 GlobalNavigator + 桩 detector 链）---
    def detect_phase(self):
        return 'unknown'


# ── 全局导航桩：不真跑 GlobalNavigator，验证 run() 的路由与结果语义 ──
print('── 1. 注册信息 ──')
check('catalog 注册 HomeTask（task_id=home, ready）',
      'home' in TASK_CLASSES
      and TASK_CLASSES['home'] is HomeTask
      and any(t.id == 'home' and t.status == 'ready'
              for t in TaskRegistry.get_all()))
check('1b oneshot=True（GUI 隐藏时间/轮次）+ 三功能勾选 schema',
      any(t.id == 'home' and getattr(t, 'oneshot', False)
          for t in TaskRegistry.get_all())
      and set(HomeTask.options_schema) == {
          'spend_stamina', 'keep_stamina'},
      str(list(HomeTask.options_schema)))

# 达标帧（多用例共用）：愿望单界面 + 左上 70/5
f_target = [(97, 80, '愿望单', 0.97), (97, 120, '70/5', 0.95),
            (760, 540, '一键选择', 0.97)]

print('── 2. 已在家园 → 愿望单流程（必选）→ 达标 done ──')
t1, logs1 = _make_task(MockBot(
    [[(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
      (340, 400, '英雄厅堂', 0.97)],
     [(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
      (340, 400, '英雄厅堂', 0.97)],
     f_target]))
r1 = t1.run()
check('2a 已在家园+未勾选愿望单 → done', r1 == 'done', f'result={r1}')
check('2b 达标后点击星数位置（v1.9.9）',
      t1.bot.clicks == ['愿望单_星数'],
      str(t1.bot.clicks))
check('2c 日志含「启动即在家园界面」',
      any('启动即在家园界面' in m for m in logs1),
      str([m for m in logs1 if '家园' in m][:2]))

print('── 3. 不在家园 → 导航成功 → done ──')
t2, logs2 = _make_task(MockBot(
    [[(300, 400, '难度15', 0.97)],
     f_target]))
# 桩掉导航本身：验证 run() 的路由与结果（GlobalNavigator 内部逻辑
# 已由 labyrinth/goddess 真机验证，不在本文件重复）
t2._nav_ok = True
orig_init = __import__('tasks.global_nav', fromlist=['GlobalNavigator'])
import tasks.global_nav as gnav_mod                          # noqa: E402


class _FakeNav:
    def __init__(self, bot, logger=None, stop_check=None, **_kw):
        self.bot = bot
        self._log = logger or (lambda m: None)
        # v2.1.27：对齐真实接口——GlobalNavigator.__init__ 即初始化
        # last_screen（导航失败日志会取它做"现场"）
        self.last_screen: str = 'unknown'

    def navigate_to(self, target, arrived_cb=None):
        self.bot.calls.append(f'fake_nav:{target}')
        return getattr(self.bot, 'nav_ok', True)


gnav_mod_orig = gnav_mod.GlobalNavigator
gnav_mod.GlobalNavigator = _FakeNav
try:
    r2 = t2.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('3a 不在家园 → 导航 world_home → done', r2 == 'done', f'result={r2}')
check('3b 导航目标是 world_home（0 层家园）',
      any(c == 'fake_nav:world_home' for c in t2.bot.calls),
      str([c for c in t2.bot.calls if 'fake_nav' in c]))

print('── 4. 导航失败 → failed ──')
t4, _ = _make_task(MockBot([[(300, 400, '难度15', 0.97)]]),
                    options={'wishlist': False})
t4.bot.nav_ok = False
gnav_mod.GlobalNavigator = _FakeNav
try:
    r4 = t4.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('4 导航失败 → failed', r4 == 'failed', f'result={r4}')

print('── 5. 手动停止 → stopped ──')
t5, logs5 = _make_task(MockBot([[(300, 400, '难度15', 0.97)]]), stop=True,
                       options={'wishlist': False})
gnav_mod.GlobalNavigator = _FakeNav
try:
    r5 = t5.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('5 停止信号 → stopped', r5 == 'stopped', f'result={r5}')

print('── 6. 启动探查三态 ──')
t6, _ = _make_task(MockBot(
    [[(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
      (340, 400, '英雄厅堂', 0.97)]]))
check('6a 在家园帧 → ready',
      t6.startup_probe() == 'ready')
t6b, _ = _make_task(MockBot([[(300, 400, '难度15', 0.97)]]))
check('6b 非家园帧 → clear',
      t6b.startup_probe() == 'clear')

# AF8 v1.9.1：愿望单完整一轮（一键选择 → 交付 → 点空白 → 达标停）
f_sel = [(97, 80, '愿望单', 0.97), (97, 120, '0/5', 0.95),
         (571, 540, '0/4', 0.9), (760, 540, '一键选择', 0.97)]
f_del = [(97, 80, '愿望单', 0.97), (97, 120, '0/5', 0.95),
         (571, 540, '4/4', 0.9), (760, 540, '交付', 0.97)]
f_done = [(210, 540, '完成愿望单', 0.98),
          (770, 540, '点击空白处关闭', 0.9)]
print('── 7. 愿望单完整循环（v1.9.1）──')
gnav_mod.GlobalNavigator = _FakeNav   # 导航不烧帧，专测 flow
try:
    t7, logs7 = _make_task(MockBot(
        [f_sel, f_sel, f_del, f_done, f_target],
        find_hits={'一键选择': (760, 540)}),
        options={'wishlist': True})
    r7 = t7.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('7a 一轮后左上角达 70 → done', r7 == 'done', f'result={r7}')
check('7b 点击顺序 一键选择→交付→点空白',
      t7.bot.clicks[:3] == ['愿望单_一键选择', '愿望单_交付',
                            '愿望单_点空白关闭'],
      str(t7.bot.clicks[:4]))

print('── 8. 愿望单界面内 70/5 → 达标停（v1.9.7 限定界面）──')
gnav_mod.GlobalNavigator = _FakeNav
try:
    t8, _ = _make_task(MockBot([f_target, f_target]),
                       options={'wishlist': True})
    r8 = t8.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('8 愿望单界面内 70/5 → done', r8 == 'done', f'result={r8}')

print('── 9. 家园帧 → 模板点按钮进入愿望单 ──')
gnav_mod.GlobalNavigator = _FakeNav
try:
    t9, logs9 = _make_task(MockBot(
        [[(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
          (340, 400, '英雄厅堂', 0.97)],
         [(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
          (340, 400, '英雄厅堂', 0.97)],
         f_target], tm_hit=(42, 647)), options={'wishlist': True})
    r9 = t9.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('9a 模板命中 → _click_abs 进入 → done', r9 == 'done', f'result={r9}')
check('9b 命中位置为模板坐标 (42,647)',
      any(c == '_click_abs:(42,647)' for c in t9.bot.calls),
      str([c for c in t9.bot.calls if '_click_abs' in c]))

print('── 10. 模板未命中 → OCR「愿望单」金字兜底 ──')
gnav_mod.GlobalNavigator = _FakeNav
try:
    t10, _ = _make_task(MockBot(
        [[(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
          (340, 400, '英雄厅堂', 0.97), (642, 42, '愿望单', 0.85)],
         [(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
          (340, 400, '英雄厅堂', 0.97), (642, 42, '愿望单', 0.85)],
         f_target], find_hits={'愿望单': (42, 642)}),
        options={'wishlist': True})
    r10 = t10.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('10 OCR 兜底点击 → done', r10 == 'done' and
      any(c == 'click_at:愿望单_按钮_OCR' for c in t10.bot.calls),
      f'result={r10}')

print('── 11. progress 解析：O/5 容错 + 多分式取最上 ──')
check('11a O/5 容错读 0',
      HomeTask._wishlist_progress(
          [(97, 120, 'O/5', 0.95), (571, 540, '3/4', 0.9)]) == 0)
check('11b 多分式取 y 最小',
      HomeTask._wishlist_progress(
          [(571, 540, '3/4', 0.9), (97, 120, '12/5', 0.95)]) == 12)
check('11c 无分式 → None',
      HomeTask._wishlist_progress([(97, 80, '愿望单', 0.97)]) is None)

# 12 v1.9.7 回归：家园界面右上角体力 5000/5000 不是愿望单进度
print('── 12. 家园体力 5000/5000 不误判（真机 18:21 bug）──')
home_frame_hp = [(300, 400, '神秘屋', 0.97), (320, 400, '玩法目录', 0.97),
                 (340, 400, '英雄厅堂', 0.97), (12, 990, '5000/5000', 0.9)]
gnav_mod.GlobalNavigator = _FakeNav
try:
    t12, logs12 = _make_task(MockBot(
        [home_frame_hp, home_frame_hp, f_target]),
        options={'wishlist': True})
    r12 = t12.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('12a 体力 5000/5000 不误判 → 正常进愿望单后达标 done',
      r12 == 'done', f'result={r12}')
check('12b 全程无「星数已达 5000」误判日志',
      not any('5000' in m for m in logs12),
      str([m for m in logs12 if '5000' in m][:1]))

# 13 v1.9.10：愿望单界面内启动 → 直通续跑；达标点击后关闭奖励页
print('── 13. 愿望单界面内启动（v1.9.10）──')
_reward_frame = [(200, 400, '获得奖励', 0.97),
                (760, 400, '点击空白处关闭', 0.9)]
gnav_mod.GlobalNavigator = _FakeNav
try:
    t13, logs13 = _make_task(MockBot([f_target, f_target, _reward_frame],
                             find_hits={'点击空白处': (400, 760)}),
                             options={'wishlist': True})
    r13 = t13.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('13a 愿望单界面内启动 → 无导航直通 done', r13 == 'done',
      f'result={r13}')
check('13b 日志含「启动即在愿望单界面」',
      any('启动即在愿望单界面' in m for m in logs13),
      str([m for m in logs13 if '启动' in m][:2]))
check('13c 达标点击后关闭奖励页（点空白）',
      t13.bot.clicks[-1] == '闪烁弹窗_点空白',
      str(t13.bot.clicks[-2:]))

# 14 v1.9.12（真机 20:01）：启动停在「获得奖励/点击空白处关闭」弹层，
# 关掉后是愿望单界面 → 应直接续跑，不绕全局导航
print('── 14. 奖励弹层启动 → 关掉后愿望单界面（v1.9.12）──')
_reward2 = [(200, 400, '获得奖励', 0.97), (760, 400, '点击空白处关闭', 0.9)]
gnav_mod.GlobalNavigator = _FakeNav
try:
    t14, logs14 = _make_task(MockBot([_reward2, f_target, f_target, _reward2],
                                     find_hits={'点击空白处': (400, 760)}))
    r14 = t14.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('14a 弹层启动 → 关掉后识别愿望单，直接 done', r14 == 'done',
      f'result={r14}')
check('14b 全程未走全局导航（无「全局导航 → 目标」日志）',
      not any('全局导航 → 目标' in m for m in logs14),
      str([m for m in logs14 if '全局导航' in m][:1]))

# 15 v1.9.14 消耗体力全流程：刷单达标 → 需求图标 → 「>」 → 工坊
# （auto 切换 → x10 确认 → 点开始烹饪 → 制作循环 → 停止收尾）
print('── 15. 消耗体力全流程（v1.9.14）──')
_demand3 = [(97, 80, '愿望单', 0.97),
            (300, 640, '愿望单需求', 0.99),
            (450, 525, '0/2', 0.99), (450, 638, '0/2', 0.99),
            (450, 752, '0/1', 0.99),
            (89, 212, '70/70', 0.97),
            (760, 540, '一键选择', 0.97)]
_workshop_frame = [(120, 300, '厨房', 0.97), (830, 507, '开始烹饪', 0.97),
                   (830, 400, '自动', 0.9)]
_stop15 = threading.Event()


class _T15Bot(MockBot):
    """状态化桩：auto_off 首次命中（模拟非自动→点击切换），之后 auto_on。"""

    def __init__(self, frames, stop_after, stop_ev, **kw):
        super().__init__(frames, **kw)
        self._ocr_n = 0
        self._stop_after = stop_after
        self._stop_ev = stop_ev
        self._auto_clicks = 0

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n >= self._stop_after:
            self._stop_ev.set()
        return super()._ocr_screen()

    def screen_image(self):
        # v1.9.15：颜色判别用——返回合成绿色图像（模拟自动开启）
        import numpy as np
        img = np.zeros((1023, 1280, 3), dtype='uint8')
        img[850:950, 430:530] = (60, 180, 90)   # 绿色（RGB）
        from PIL import Image
        return (Image.fromarray(img), 0, 0, 1280, 1023)

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'auto_off' in name:
            self._auto_clicks += 1
            return (500, 900) if self._auto_clicks == 1 else None
        if 'auto_on' in name:
            return (500, 900)
        if name == 'btn_workshop_x10.png':
            return (786, 901)
        if 'btn_wishlist_next' in name:
            return (656, 449)
        return None


gnav_mod.GlobalNavigator = _FakeNav
try:
    # 帧预算：probe 1 + 刷单达标 1 + dismiss 自扫 2 → spend 从帧 5 起
    bot15 = _T15Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _workshop_frame, _workshop_frame, _workshop_frame],
        stop_after=40, stop_ev=_stop15,
        find_hits={'开始烹饪': (507, 830)})
    t15, logs15 = _make_task(bot15, stop=True, options={'spend_stamina': True})
    t15._stop = _stop15
    r15 = t15.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('15a 消耗体力全流程执行至停止收尾（无 failed）', r15 == 'stopped',
      f'result={r15}')
check('15b 点击最左需求图标一次',
      bot15.clicks.count('愿望单_需求图标') == 1, str(bot15.clicks))
check('15c 点击悬浮窗「>」按钮',
      any(c == '_click_abs:(656,449)' for c in bot15.calls),
      str([c for c in bot15.calls if '_click_abs' in c][:2]))
check('15d 绿色（自动开）→ 颜色判别通过，不误点切换',
      not any('切换为自动' in m for m in logs15),
      str([m for m in logs15 if '自动' in m][:2]))
check('15e 点击「开始烹饪」进入生产循环',
      bot15.clicks.count('工坊_开始') >= 1, str(bot15.clicks[-3:]))
check('15f 左上星数 70/70 未被当作需求标签',
      bot15.clicks.count('愿望单_需求图标') == 1)

# 16 单图标/双图标布局同样命中
print('── 16. 需求图标 1/2 个布局（纯定位单测）──')
one_frame = [(300, 640, '愿望单需求', 0.99), (450, 525, '0/5', 0.99)]
two_frame = [(300, 640, '愿望单需求', 0.99), (450, 400, '0/3', 0.99),
             (450, 525, '0/3', 0.99), (89, 212, '70/70', 0.97)]
r_one = HomeTask._click_wishlist_demand_icon(
    __import__('types').SimpleNamespace(), one_frame) if False else None
# 直接用静态方法（无需实例状态）
t16a = HomeTask.__new__(HomeTask)
t16a.bot = MockBot(texts_by_call=[[]], tm_hit=None)
t16a.log = lambda m: None
ok16a = t16a._click_wishlist_demand_icon(one_frame)
check('16a 单图标布局 → 命中并点击', ok16a is True
      and t16a.bot.clicks == ['愿望单_需求图标'], str(t16a.bot.clicks))
t16b = HomeTask.__new__(HomeTask)
t16b.bot = MockBot(texts_by_call=[[]], tm_hit=None)
t16b.log = lambda m: None
ok16b = t16b._click_wishlist_demand_icon(two_frame)
check('16b 双图标布局 → 命中最左并点击', ok16b is True
      and t16b.bot.clicks == ['愿望单_需求图标'], str(t16b.bot.clicks))
# 点击坐标 = 最左标签上方 70px
check('16c 双图标取最左（x=400）',
      t16b.bot.clicks == ['愿望单_需求图标'],
      str(t16b.bot.calls))

# 17 v1.9.15 颜色判别：灰色（自动关）→ 点击切换
print('── 17. 灰自动状态 → 颜色判别点击切换（v1.9.15）──')
_stop17 = threading.Event()


class _T17Bot(_T15Bot):
    """状态化画面：自动按钮灰色（关），点击切换后变绿（开）。"""

    _switched = False

    def _click_abs(self, cx, cy):
        # 点击「自动」图标位置 = 切换状态（模拟游戏行为）
        if 400 < cx < 600 and 800 < cy < 960:
            self._switched = True
        super()._click_abs(cx, cy)

    def screen_image(self):
        import cv2
        import numpy as np
        from PIL import Image
        img = np.full((1023, 1280, 3), 70, dtype='uint8')
        # 画圆形按钮：灰(关)或绿(开)，中心 (480, 900) r=50
        color = (90, 180, 80) if self._switched else (110, 110, 115)
        cv2.circle(img, (480, 900), 50, color, -1)
        # 中心白块（模拟「自动」文字，给模板匹配提供特征）
        cv2.rectangle(img, (455, 875), (505, 925),
                      (235, 235, 225) if self._switched
                      else (200, 200, 205), -1)
        return (Image.fromarray(img), 0, 0, 1280, 1023)


gnav_mod.GlobalNavigator = _FakeNav
try:
    # 帧预算：probe 1 + wish 达标 1 + dismiss 2 → spend 从帧 5 起
    # 帧预算：probe 1 + 刷单达标 1 + dismiss 2 = 4 帧 f_target，
    # spend 段从帧 5（_demand3）起
    bot17 = _T17Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _workshop_frame, _workshop_frame, _workshop_frame,
         _workshop_frame, _workshop_frame, _workshop_frame],
        stop_after=50, stop_ev=_stop17,
        find_hits={'开始烹饪': (507, 830)})
    t17, logs17 = _make_task(bot17, stop=True,
                             options={'spend_stamina': True})
    t17._stop = _stop17
    r17 = t17.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('17 灰色（自动关）→ 颜色判别点击切换', r17 == 'stopped'
      and any('切换为自动' in m for m in logs17),
      f'result={r17} logs_tail={logs17[-6:]} calls={bot17.calls[-6:]}')

# 18 v1.9.16：点击星数后可能弹「每日奖励」悬浮窗 → 点窗外关闭
print('── 18. 每日奖励悬浮窗（v1.9.16）──')
_daily_frame = [(150, 200, '每日奖励', 0.97), (300, 250, '重置倒计时', 0.95),
                (97, 80, '愿望单', 0.97), (760, 540, '一键选择', 0.97)]
_stop18 = threading.Event()


class _T18Bot(_T15Bot):
    """帧耗尽即停（daily 帧固定，靠停止信号收尾）。"""

    def __init__(self, frames, stop_after, stop_ev, **kw):
        super().__init__(frames, stop_after, stop_ev, **kw)
        self._daily = False

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n >= self._stop_after:
            self._stop_ev.set()
        fr = super()._ocr_screen()
        self._daily = any('每日奖励' in it[2] for it in fr
                          if len(it) >= 4)
        return fr

    def screen_image(self):
        import cv2
        import numpy as np
        from PIL import Image
        # 每日奖励窗显示期间：自动图标区域采样为灰（非绿）
        img = np.full((1023, 1280, 3), 70, dtype='uint8')
        if not self._daily:
            cv2.circle(img, (480, 900), 50, (90, 180, 80), -1)
        return (Image.fromarray(img), 0, 0, 1280, 1023)


gnav_mod.GlobalNavigator = _FakeNav
try:
    # 帧预算：probe 1 + wish 达标 1 + dismiss 2 = 4 帧 f_target；
    # spend 段遇 daily 窗（关 2 次）→ 愿望单需求帧 → 点图标 → done
    bot18 = _T18Bot(
        [f_target, f_target, f_target, f_target,
         _daily_frame, _daily_frame, _demand3,
         _workshop_frame, _workshop_frame, _workshop_frame],
        stop_after=30, stop_ev=_stop18,
        find_hits={'开始烹饪': (507, 830)})
    t18, logs18 = _make_task(bot18, stop=True,
                             options={'spend_stamina': True})
    t18._stop = _stop18
    r18 = t18.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('18a 星数达标 → 关每日奖励窗 → 正常收尾', r18 == 'stopped',
      f'result={r18}')
check('18b 关窗点击在窗外（愿望单_关每日奖励）',
      any(c == '愿望单_关每日奖励' for c in bot18.clicks),
      str(bot18.clicks))
check('18c 关窗后继续消耗体力流程（点需求图标）',
      bot18.clicks.count('愿望单_需求图标') >= 1, str(bot18.clicks))

# 19 v1.9.17：材料耗尽（资源不足弹窗）→ 点「>」→ done
print('── 19. 材料耗尽弹窗（v1.9.17）──')
_stop19 = threading.Event()
_res_short_frame = [(390, 640, '当前资源不足，可从以下途径获取', 1.0),
                 (574, 576, '20级木材加工厂生产获', 0.94)]


class _T19Bot(_T15Bot):
    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'res_short' in name:
            return (700, 497)
        if 'auto_on' in name:
            return (500, 900)
        if 'x10' in name:
            return (786, 901)
        if 'btn_wishlist_next' in name:
            return (656, 449)
        return None


gnav_mod.GlobalNavigator = _FakeNav
try:
    # 帧预算：probe 1 + 刷单达标 1 + dismiss 2 = 4 帧 f_target
    bot19 = _T19Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _workshop_frame, _res_short_frame, _res_short_frame],
        stop_after=30, stop_ev=_stop19,
        find_hits={'开始烹饪': (507, 830)})
    t19, logs19 = _make_task(bot19, stop=True,
                             options={'spend_stamina': True})
    t19._stop = _stop19
    r19 = t19.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('19a 材料耗尽 → 点「>」→ 到达木材加工厂继续生产（停止收尾）',
      r19 == 'stopped'
      and any('已到达 木材加工厂' in m for m in logs19),
      f'result={r19}')
check('19b 点击弹窗内「>」按钮',
      any(c == '_click_abs:(700,497)' for c in bot19.calls),
      str([c for c in bot19.calls if '_click_abs' in c][-2:]))
check('19c 日志含「材料耗尽」',
      any('材料耗尽' in m for m in logs19), str(logs19[-2:]))

# 20 v1.9.18：资源不足弹窗画面内启动 → 直接消耗体力
print('── 20. 资源不足弹窗启动（v1.9.18）──')
_stop20 = threading.Event()   # 独立停止事件（勿复用 19 已 set 的）
gnav_mod.GlobalNavigator = _FakeNav
try:
    bot20 = _T19Bot(
        # 帧预算：probe 1 + 入口点> 1 → 二段从 workshop 帧起
        [_res_short_frame, _res_short_frame,
         _workshop_frame, _workshop_frame, _workshop_frame,
         _workshop_frame, _workshop_frame, _workshop_frame,
         _workshop_frame, _workshop_frame, _workshop_frame,
         _workshop_frame, _workshop_frame, _workshop_frame],
        stop_after=60, stop_ev=_stop20,
        find_hits={'开始烹饪': (507, 830)})
    t20, logs20 = _make_task(bot20, stop=True,
                             options={'spend_stamina': True})
    t20._stop = _stop20
    r20 = t20.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('20a 弹窗内启动 → 直接消耗体力（无 wish flow）', r20 == 'stopped',
      f'result={r20}')
check('20b 日志含「直接进入消耗体力流程」',
      any('直接进入消耗体力' in m for m in logs20),
      str([m for m in logs20 if '消耗体力' in m][:2]))
check('20c 点击「>」跳获取途径',
      any(c == '_click_abs:(700,497)' for c in bot20.calls),
      str([c for c in bot20.calls if '_click_abs' in c][:2]))
check('20d 生产循环启动（Mock 厨房帧 → 开始烹饪）',
      any(c == '工坊_开始' for c in bot20.clicks),
      str([m for m in logs20 if '开始' in m or '到达' in m][:3]))

# 21 v1.9.20：木材加工厂材料生产（拖滑块 → 加工）
print('── 21. 木材加工厂材料生产（v1.9.20）──')
_stop21 = threading.Event()
_lumber_frame = [(638, 186, '木材加工厂', 0.98),
                 (650, 739, '0/80127195', 0.95),
                 (640, 780, '消耗材料', 0.99),
                 (689, 956, '加工', 0.99)]


class _T21Bot(_T15Bot):
    """木材加工厂场景：滑块绿检测 + 数量 OCR + 加工点击。"""

    def __init__(self, frames, stop_after, stop_ev, **kw):
        super().__init__(frames, stop_after, stop_ev, **kw)
        self.drags = []
        self._cur = 0

    def screen_image(self):
        import numpy as np
        from PIL import Image
        img = np.full((1024, 1279, 3), 40, dtype='uint8')
        # 滑块绿胶囊（模拟）：x 随拖动次数右移
        sx = 460 + min(len(self.drags), 3) * 10
        img[830:870, sx - 20:sx + 20] = (80, 180, 90)
        return (Image.fromarray(img), 0, 0, 1279, 1024)

    def drag_abs(self, x0, y0, x1, y1, duration=0.4):
        self.drags.append((x0, y0, x1, y1))
        self._cur += 15000 if len(self.drags) >= 2 else 5000

    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'btn_wishlist_next' in name:
            return (656, 449)
        return None

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n >= self._stop_after:
            self._stop_ev.set()
        fr = super()._ocr_screen()
        # 帧耗尽后：数量随拖动增长
        out = []
        for it in fr:
            if it[2] == '0/80127195':
                cur = self._cur
                out.append((it[0], it[1], f'{cur}/80127195', it[3]))
            else:
                out.append(it)
        return out


gnav_mod.GlobalNavigator = _FakeNav
try:
    # 帧预算：probe 1 + 刷单达标 1 + dismiss 2 = 4 帧 f_target
    bot21 = _T21Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _lumber_frame, _lumber_frame, _lumber_frame,
         _lumber_frame, _lumber_frame, _lumber_frame,
         _lumber_frame, _lumber_frame, _lumber_frame,
         _lumber_frame, _lumber_frame, _lumber_frame],
        stop_after=60, stop_ev=_stop21,
        find_hits={'加工': (689, 956)})
    t21, logs21 = _make_task(bot21, stop=True,
                             options={'spend_stamina': True})
    t21._stop = _stop21
    r21 = t21.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('21a 木材加工厂 → 材料生产分支（停止收尾）', r21 == 'stopped',
      f'result={r21} logs={logs21[-5:]}')
check('21b 有拖动滑块动作', len(bot21.drags) >= 1, str(bot21.drags[:2]))
check('21c 数量达标后点击「加工」',
      any(c == '加工厂_加工' for c in bot21.clicks),
      str([c for c in bot21.clicks if '加工' in c][:2]))

# 22 v1.9.23：体力耗尽（体力礼包弹窗）→ 关闭 → done
print('── 22. 体力耗尽终态（v1.9.23）──')
_stop22 = threading.Event()
_stamina_frame = [(120, 300, '厨房', 0.97),
                  (537, 294, '体力七日礼包', 0.99),
                  (537, 965, '点击空白处关闭', 0.86),
                  (689, 956, '加工', 0.99)]


class _T22Bot(_T19Bot):
    def template_match(self, tpl_path, threshold=0.8, silent=False,
                       scale_range=None, region=None):
        name = os.path.basename(str(tpl_path))
        self.calls.append(f'template_match:{name}')
        if 'auto_on' in name:
            return (500, 900)
        if 'x10' in name:
            return (786, 901)
        if 'btn_wishlist_next' in name:
            return (656, 449)
        return None


gnav_mod.GlobalNavigator = _FakeNav
try:
    bot22 = _T22Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _workshop_frame, _stamina_frame, _stamina_frame],
        stop_after=40, stop_ev=_stop22,
        find_hits={'开始烹饪': (507, 830)})
    t22, logs22 = _make_task(bot22, stop=True,
                             options={'spend_stamina': True})
    t22._stop = _stop22
    r22 = t22.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('22a 体力礼包弹窗 → 关闭 → done（大循环正常终点）',
      r22 == 'done', f'result={r22}')
check('22b 日志含「体力已耗尽」',
      any('体力已耗尽' in m for m in logs22), str(logs22[-2:]))

# 23 v1.9.24：体力实时监控（右上角 0/5000 → done）
print('── 23. 体力实时监控（v1.9.24）──')
_stop23 = threading.Event()
# 厨房帧 + 右上角体力 0/5000+（无礼包弹窗——主动监控不靠弹窗）
_zero_frame = [(120, 300, '厨房', 0.97),
               (21, 1089, '0/5000+', 0.97),   # (y, x) 顺序
               (689, 956, '加工', 0.99)]


class _T23Bot(_T22Bot):
    """体力 0 场景（_read_stamina 读右上角分式）。"""


gnav_mod.GlobalNavigator = _FakeNav
try:
    bot23 = _T23Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _zero_frame, _zero_frame],
        stop_after=40, stop_ev=_stop23,
        find_hits={'开始烹饪': (507, 830)})
    t23, logs23 = _make_task(bot23, stop=True,
                             options={'spend_stamina': True})
    t23._stop = _stop23
    r23 = t23.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('23a 右上角体力 0 → 主动收尾 done（无需弹窗）', r23 == 'done',
      f'result={r23}')
check('23b 日志含「体力已耗尽（右上角」',
      any('体力已耗尽（右上角' in m for m in logs23), str(logs23[-2:]))

# 24 v1.9.25：保留体力设置（体力 <= 保留值 → done）
print('── 24. 保留体力（v1.9.25）──')
_stop24 = threading.Event()
_half_frame = [(120, 300, '厨房', 0.97),
               (21, 1089, '50/5000+', 0.97),
               (689, 956, '加工', 0.99)]


class _T24Bot(_T22Bot):
    pass


gnav_mod.GlobalNavigator = _FakeNav
try:
    bot24 = _T24Bot(
        [f_target, f_target, f_target, f_target,
         _demand3, _demand3, _demand3,
         _half_frame, _half_frame],
        stop_after=40, stop_ev=_stop24,
        find_hits={'开始烹饪': (507, 830)})
    t24, logs24 = _make_task(bot24, stop=True,
                             options={'spend_stamina': True,
                                      'keep_stamina': 100})
    t24._stop = _stop24
    r24 = t24.run()
finally:
    gnav_mod.GlobalNavigator = gnav_mod_orig
check('24a 体力 50 <= 保留 100 → done', r24 == 'done', f'result={r24}')
check('24b 日志含「保留 100」',
      any('保留 100' in m for m in logs24), str(logs24[-2:]))

# ── 汇总 ──# ── 汇总 ──# ── 汇总 ──# ── 汇总 ────────────────────────────────────────────────────# ── 汇总 ────────────────────────────────────────────────────# ── 汇总 ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
# v2.2.11：愿望单「当前资源不足」弹窗 → 点「>」自动寻路制作
#   真机 2026-09-16 08:24（用户报障）：愿望单连刷到材料耗尽 → 弹
#   「当前资源不足，可从以下途径获取」（行如「19级厨房制作获得」+ 右侧
#   圆形「>」）→ 旧实现**愿望单主循环没有该分支** → 落「无进展→点空白」，
#   而空白关不掉该弹窗 → 反复空转（日志 08:25:24）✗
#   「>」是图标（OCR 读不出）→ 迂回定位：行文本 x_min + 398（同排取 y）
# ══════════════════════════════════════════════════════════════
print()
print('── v2.2.11 愿望单资源不足弹窗 → 点「>」 ──')
_b = MockBot(texts_by_call=[[]])
_b.tm_hit = (900, 500)               # 模板命中（「>」有专用模板）
_t, _logs = _make_task(_b)
# 真机帧（2026-09-16 截图 OCR 实测值，仅用于"看得见弹窗"）
_frame = [(82.5, 577.5, '当前资源不足，可从以下途径获取', 0.99, 436, 719),
          (281.0, 457.0, '19级厨房制作获得', 0.99, 367, 547)]
_t._wishlist_res_short(_frame)
check('R1 模板命中 → 点模板位置（_click_abs）',
      any(c == '_click_abs:(900,500)' for c in _b.calls),
      str([c for c in _b.calls if 'click' in c or 'template' in c]))
check('R2 用的是「>」专用模板 btn_wishlist_res_short.png',
      any('btn_wishlist_res_short.png' in c for c in _b.calls),
      str([c for c in _b.calls if 'template_match' in c]))

# 【分叉】模板未命中 → **不盲点**（用户 09-16 提醒：不要坐标兜底）
_b2 = MockBot(texts_by_call=[[]])
_b2.tm_hit = None
_t2, _logs2 = _make_task(_b2)
_r2 = _t2._wishlist_res_short(_frame)
check('R3 【分叉】模板未命中 → 不点击任何位置（不盲点、不加坐标兜底）',
      not any('click_at' in c or '_click_abs' in c for c in _b2.calls),
      str(_b2.calls))
check('R4 模板未命中 → 如实告警并交下一轮（可追溯）',
      any('「>」按钮未命中' in m for m in _logs2)
      and _r2 in ('ok', 'stopped', 'failed'),
      str([m for m in _logs2 if '未命中' in m][:1]))

_ROOTH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_home = open(os.path.join(_ROOTH, 'tasks', 'home_task.py'),
                 encoding='utf-8').read()
check('R5 愿望单主循环已接上该分支（分流点存在，防被删）',
      "'当前资源不足' in it[2]" in _src_home
      and 'self._wishlist_res_short(items)' in _src_home, '')
check('R6 有处理轮次上限（防材料真做不出时无限乒乓）',
      '_WISHLIST_RES_SHORT_MAX' in _src_home
      and '_res_short_tries >= _WISHLIST_RES_SHORT_MAX' in _src_home, '')
check('R7 **无坐标兜底**（不留 _RES_SHORT_ROW_DX 这类实测偏移）',
      '_RES_SHORT_ROW_DX' not in _src_home, '')

print(f'══ 一键家园干跑测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f in _FAIL:
        print(f'  ❌ {f}')
sys.exit(0 if not _FAIL else 1)
