# -*- coding: utf-8 -*-
"""Navigator 构造与关键方法冒烟（v1.8.51）。

背景：v1.8.49/50 给 Navigator 增加 `_navlog`（状态日志去重 + 心跳）时，
实例属性 `_last_navlog` 的初始化**没有真正落地**（锚点不存在 + replace
未加断言），而既有测试全是干跑/Mock，不真实构造 Navigator，导致测试全绿
但真机一启动就 AttributeError → 自动推图直接 failed。

本测试用最小 stub **真实构造 Navigator** 并调用关键方法，专门防守这类
"属性/方法没落地"的缺陷。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from afk_bot.services.navigator import Navigator  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(('✅' if cond else '❌'), name, ('— ' + detail) if detail else '')


# ── 最小 stub ────────────────────────────────────────────────
class StubScreen:
    def snap(self):
        return None, 0, 0, 1280, 1024

    def ocr_screen(self):
        return [(100, 200, '赛季挂机关卡', 0.9, 150, 250)]


class StubRecognizer:
    def find_text(self, items, kw, min_conf=0.5):
        return None


class StubInput:
    def send_esc(self, label=''):
        pass

    def click_back_button(self, tpls=None, threshold=0.75):
        return None


class StubDetector:
    def detect_phase(self):
        return 'idle_stage'

    def is_on_level0(self):
        return False


def make_nav(logs=None):
    logs = logs if logs is not None else []
    return Navigator(
        screen_capture=StubScreen(),
        recognizer=StubRecognizer(),
        input_controller=StubInput(),
        detector=StubDetector(),
        log_callback=logs.append,
        stop_check=lambda: False,
    ), logs


# ── 1. 构造后属性必须存在 ──
nav, logs = make_nav()
check('构造后 _last_navlog 存在', hasattr(nav, '_last_navlog'))
check('构造后 _navlog_repeat 存在', hasattr(nav, '_navlog_repeat'))

# ── 2. _navlog 可调用（去重 + 心跳）──
nav2, logs2 = make_nav()
try:
    nav2._navlog('[信息] 导航中 | 挂机关卡页')
    nav2._navlog('[信息] 导航中 | 挂机关卡页')   # 重复 → 静默
    nav2._navlog('[信息] 导航中 | 挂机关卡页')   # 第 3 次 → 心跳
    nav2._navlog('[信息] 导航中 | 挂机关卡页')
    ok = True
    detail = str(logs2)
except Exception as e:
    ok = False
    detail = f'{type(e).__name__}: {e}'
check('_navlog 调用不抛异常', ok, detail)
check('_navlog 去重生效（首条之外不重复刷屏）',
      logs2.count('[信息] 导航中 | 挂机关卡页') == 1, str(logs2))
check('_navlog 心跳生效（每 3 轮一条）',
      any('已等待' in m for m in logs2), str(logs2))

# ── 3. _should_stop 可调用且未停止时返回 False ──
nav3, logs3 = make_nav()
try:
    r = nav3._should_stop()
    check('_should_stop 未停止时返回 False', r is False)
except Exception as e:
    check('_should_stop 未停止时返回 False', False, f'{type(e).__name__}: {e}')

# ── 4. 停止时留痕并返回 True ──
nav4, logs4 = make_nav()
nav4._stop_check = lambda: True
try:
    r4 = nav4._should_stop()
    check('_should_stop 停止时返回 True', r4 is True)
    check('_should_stop 停止时留痕', any('停止信号' in m for m in logs4), str(logs4))
except Exception as e:
    check('_should_stop 停止时返回 True', False, f'{type(e).__name__}: {e}')

# ── 5. navigate_to_idle_stage 真实跑一轮（已在挂机页 → 立即完成）──
nav5, logs5 = make_nav()
try:
    nav5.is_target = lambda: True
    r5 = nav5.navigate_to_idle_stage()
    check('navigate_to_idle_stage 可达（stub 已在挂机页）', r5 is True, str(logs5[:3]))
except Exception as e:
    check('navigate_to_idle_stage 可达（stub 已在挂机页）', False,
          f'{type(e).__name__}: {e}')

print(f'══ Navigator 冒烟：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：', _FAIL)
sys.exit(0 if not _FAIL else 1)
