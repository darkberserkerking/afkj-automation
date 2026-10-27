# -*- coding: utf-8 -*-
"""v1.9.48：全局导航区分「家园场景/世界场景」测试。

世界帧/家园帧取自用户 13:00 双截图 OCR 实测数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fast_time  # noqa: F401  虚拟时钟
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY
from tasks.global_nav import GlobalNavigator

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'通过' if cond else '不通过'}] {name}  {detail}".rstrip())


def it(y, x, text, conf=0.95):
    return (y, x, text, conf)


# 真机 OCR 实测帧（13:00 截图）
WORLD_FRAME = [
    it(1008, 519, '神秘屋', 0.98), it(1008, 600, '玩法目录', 0.98),
    it(1008, 680, '英雄厅堂', 0.99), it(1008, 762, '公会', 0.98),
    it(889, 1243, '家园', 0.99),          # 右下角切换按钮（世界独有）
    it(991, 66, '先锋关卡', 0.97),
]
HOME_FRAME = [
    it(1008, 521, '神秘屋', 0.98), it(1008, 607, '玩法目录', 0.97),
    it(1008, 680, '英雄厅堂', 0.99), it(1008, 764, '公会', 0.98),
    it(746, 44, '建设', 0.98),             # 家园独有
    it(671, 43, '愿望单', 0.98),           # 左侧按钮
]

print('── 1. 注册表区分 ──')
check('1a 世界帧 → nav.world_scene',
      GLOBAL_SCREEN_REGISTRY.detect(WORLD_FRAME) == 'nav.world_scene',
      GLOBAL_SCREEN_REGISTRY.detect(WORLD_FRAME))
check('1b 家园帧 → nav.home_scene',
      GLOBAL_SCREEN_REGISTRY.detect(HOME_FRAME) == 'nav.home_scene',
      GLOBAL_SCREEN_REGISTRY.detect(HOME_FRAME))


class _SceneBot:
    """帧序列桩：每次 _ocr_screen 弹一帧。"""

    def __init__(self, frames, btn_hits):
        self._frames = list(frames)
        self._i = -1
        self._btn_hits = btn_hits   # {'家园': (x, y), ...}
        self.clicks = []

    def _ocr_screen(self, img=None, no_cache=False):
        self._i = min(self._i + 1, len(self._frames) - 1)
        return self._frames[self._i]

    def find_text(self, items, kw, min_conf=0.5, exact=False):
        for it in items:
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]:
                return (it[1], it[0])
        return self._btn_hits.get(kw)

    def click_at(self, x, y, label=''):
        self.clicks.append(label)
        return True

    def press_esc(self, label=''):
        self.clicks.append('ESC')
        return True

    def click_back_button(self, tpls, threshold=0.7):
        return False


print('── 2. 全局导航互切 ──')
# 2a 世界 → 家园场景：点「家园」按钮 → 到达
bot = _SceneBot([WORLD_FRAME, WORLD_FRAME, HOME_FRAME],
                {'家园': (1243, 889), '世界': (1215, 858)})
nav = GlobalNavigator(bot, logger=lambda m: print('   ', m[:70]),
                      stop_check=lambda: False)
r = nav.navigate_to('home_scene')
check('2a navigate_to(home_scene) 起于世界 → 点家园按钮到达',
      r is True and any('家园按钮' in c for c in bot.clicks),
      f'r={r} clicks={bot.clicks}')

# 2b 家园 → 世界场景：点「世界」按钮 → 到达
bot2 = _SceneBot([HOME_FRAME, HOME_FRAME, WORLD_FRAME],
                 {'家园': (1243, 889), '世界': (1215, 858)})
nav2 = GlobalNavigator(bot2, logger=lambda m: print('   ', m[:70]),
                       stop_check=lambda: False)
r2 = nav2.navigate_to('world_scene')
check('2b navigate_to(world_scene) 起于家园 → 点世界按钮到达',
      r2 is True and any('世界按钮' in c for c in bot2.clicks),
      f'r={r2} clicks={bot2.clicks}')

# 2c 已在目标场景 → 直达 True（零动作）
bot3 = _SceneBot([HOME_FRAME], {})
nav3 = GlobalNavigator(bot3, logger=lambda m: None,
                       stop_check=lambda: False)
r3 = nav3.navigate_to('home_scene')
check('2c 已在家园场景 → 直达 True（无点击）',
      r3 is True and not bot3.clicks, f'r={r3} clicks={bot3.clicks}')

# 2d 兼容：navigate_to('world_home') 起于任一场景 → 直达 True（0 层即达）
bot4 = _SceneBot([WORLD_FRAME], {})
nav4 = GlobalNavigator(bot4, logger=lambda m: None,
                       stop_check=lambda: False)
r4 = nav4.navigate_to('world_home')
check('2d 兼容 world_home 目标：世界场景帧 → 直达 True',
      r4 is True and not bot4.clicks, f'r={r4}')
bot5 = _SceneBot([HOME_FRAME], {})
nav5 = GlobalNavigator(bot5, logger=lambda m: None,
                       stop_check=lambda: False)
r5 = nav5.navigate_to('world_home')
check('2e 兼容 world_home 目标：家园场景帧 → 直达 True',
      r5 is True and not bot5.clicks, f'r={r5}')

print(f'══ 场景区分测试：通过 {len(PASS)}/{len(PASS) + len(FAIL)} ══')
if FAIL:
    print('失败项：')
    for f_ in FAIL:
        print(' ', f_)
sys.exit(0 if not FAIL else 1)
