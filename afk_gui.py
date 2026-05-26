VERSION = 'v0.8'

f"""
AFK Journey 自动化工具 {VERSION} — 状态机导航架构
======================================================
v0.6（2026-04-29）：
  ✅ 弃用所有模板匹配，改用比例坐标（_BTN_POS）
  ✅ 所有图标按钮使用比例坐标：challenge/huanling/normal/activity/options/gear/auto
  ✅ 移除 _load_templates / find_template 死代码
  ✅ 移除 click_ratio_btn，click_template 直接使用 _BTN_POS
  ✅ 死代码清理：DEBUG print、死return、重复import

v0.1.5.6-beta（2026-04-16）：
  ⚠️ 模板匹配兜底系统（已被弃用）
  - 新增模板匹配系统（_TMPL_BUTTONS + find_template / click_template）
  - 新增 ocr_then_template 统一点击方法：OCR优先，失败后模板兜底

v0.1.5.4-beta（2026-04-14）：
  ✅ OCR 记忆系统（OCRMemory）：记住成功的识别策略
  ✅ 持久化到 ocr_memory.json，避免重复试错
  ✅ navigate 场景的 ocr_and_click 已集成记忆系统

v0.1.5.5-beta（2026-04-13）：
  ✅ 引入 Task 模块系统（参考 MaaFramework preset）
  ✅ 自动推图迁移为 tasks/auto_farm.py
  ✅ GUI 支持任务勾选启/停，配置持久化

v0.1.5.2-beta（2026-04-08）：
  ✅ _activate_window 用 AttachThreadInput 解决焦点抢占问题
  ✅ click_ratio 去掉重复 _win_dims 调用
  ✅ step_click_auto_challenge 比例坐标失败后重试一次 OCR
  ✅ _click_next_formation 增强日志和阵容面板状态验证

v0.1.5.1-beta（2026-04-03）：
  ✅ OCR 引擎：RapidOCR(v4) → MaaOCR(v5) 自研引擎
  ✅ PP-OCRv5 mobile det+rec，识别精度更高
  ✅ 利用 ROI 区域截图优化，加速 OCR
  ✅ 移除 rapidocr 依赖，打包体积更小
  ✅ 利用 MaaOCR expected 正则 + color_filter 特性

v0.1.5.0-beta（2026-04-01）：
  ✅ MaaFramework 启发：Pipeline 声明式步骤系统
  ✅ ROI 区域截图优化：减少无效区域，加速 OCR
  ✅ 战斗检测：颜色匹配（ColorMatch）备用 + OCR
  ✅ 截图优化：去除标题栏/装饰区域，只截游戏内容

需要管理员权限运行
"""

import os
import sys
import time
import threading
import traceback
import queue
import io
import re
import unicodedata
import json
import hashlib
import math
import random
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List, Dict, Any

import pyautogui
import win32api
import win32gui
import win32con
import win32process
import win32ui
import keyboard
from PIL import ImageGrab, Image
from maa_ocr import MaaOCR
import customtkinter as ctk
import tkinter as tk
import ctypes

# ── 任务模块系统 ─────────────────────────────────────────
import task_registry
from task_registry import TaskRegistry, TaskMeta
import tasks
import tasks.auto_farm  # 触发注册
import tasks.goddess_challenge  # 触发注册
# tasks.daily_quest 已禁用


# ═══════════════════════════════════════════════════════════════════
#  OCR 记忆系统 — 记住成功的识别策略，避免重复试错
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OCRStrategy:
    """OCR 策略参数"""
    roi: Optional[Tuple[float, float, float, float]] = None  # (rx, ry, rw, rh) 比例坐标
    color_filter: Optional[Dict[str, Any]] = None            # MaaOCR color_filter
    min_conf: float = 0.5                                    # 置信度阈值
    retries: int = 1                                         # 重试次数
    note: str = ""                                           # 备注


class OCRMemory:
    """
    OCR 记忆器 — 记录每个关键词/场景对应的最佳识别策略。

    使用方式：
        memory = OCRMemory('ocr_memory.json')

        # 尝试识别，失败后换策略
        result = memory.find_text_with_memory(bot, '幻灵挑战', scene='navigate')
        if result:
            memory.remember('navigate', '幻灵挑战', strategy)  # 记住成功策略
    """

    # 默认尝试顺序（从快到慢）
    DEFAULT_STRATEGIES = [
        OCRStrategy(min_conf=0.5),              # 1. 全屏 + 标准置信度
        OCRStrategy(min_conf=0.3),              # 2. 全屏 + 低置信度
        OCRStrategy(roi=(0.2, 0.2, 0.6, 0.6)), # 3. 中心区域
        OCRStrategy(roi=(0.0, 0.0, 1.0, 1.0)), # 4. 全屏（显式）
        OCRStrategy(min_conf=0.2),               # 5. 极低置信度
    ]

    def __init__(self, cache_path: str = 'ocr_memory.json'):
        self.cache_path = cache_path
        # {(scene, keyword): OCRStrategy}
        self.memory: Dict[Tuple[str, str], OCRStrategy] = {}
        self._load()

    def _key(self, scene: str, keyword: str) -> Tuple[str, str]:
        return (scene.lower(), keyword)

    def _load(self):
        """从文件加载记忆"""
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for key_str, strat in data.items():
                scene, keyword = key_str.split('|||', 1)
                self.memory[(scene, keyword)] = OCRStrategy(
                    roi=strat.get('roi'),
                    color_filter=strat.get('color_filter'),
                    min_conf=strat.get('min_conf', 0.5),
                    retries=strat.get('retries', 1),
                    note=strat.get('note', ''),
                )
        except Exception as e:
            print(f"[WARN] OCR记忆加载失败: {e}")

    def _save(self):
        """保存记忆到文件"""
        try:
            data = {}
            for (scene, keyword), strat in self.memory.items():
                data[f'{scene}|||{keyword}'] = asdict(strat)
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] OCR记忆保存失败: {e}")

    def remember(self, scene: str, keyword: str, strategy: OCRStrategy):
        """记住成功的策略"""
        key = self._key(scene, keyword)
        # 如果策略相同就不重复保存
        if key in self.memory:
            existing = self.memory[key]
            if (existing.roi == strategy.roi and
                abs(existing.min_conf - strategy.min_conf) < 0.05):
                return  # 相同策略，无需更新
        self.memory[key] = strategy
        self._save()
        pass  # 记忆成功，静默

    def get_strategy(self, scene: str, keyword: str) -> Optional[OCRStrategy]:
        """获取记忆的策略"""
        return self.memory.get(self._key(scene, keyword))

    def get_attempt_order(self, scene: str, keyword: str) -> List[OCRStrategy]:
        """获取尝试顺序：优先使用记忆的策略，然后是默认策略。"""
        strategies = []
        key = self._key(scene, keyword)

        # 1. 优先尝试记忆的策略
        if key in self.memory:
            strategies.append(self.memory[key])

        # 2. 添加默认策略（跳过已尝试的）
        known = strategies[0] if strategies else None
        for strat in self.DEFAULT_STRATEGIES:
            if known is None or (
                strat.roi != known.roi or
                abs(strat.min_conf - known.min_conf) > 0.1
            ):
                strategies.append(strat)

        return strategies

    def find_text_with_memory(self, bot, keyword: str, scene: str = 'default',
                             exact: bool = False, y_min_ratio: float = None) -> Optional[Tuple[int, int]]:
        """
        带记忆的 find_text：自动尝试记忆/默认策略，直到成功。

        Args:
            bot: AFKBot 实例（有 _snap, _ocr_img 等方法）
            keyword: 要查找的文字
            scene: 场景标识（如 'navigate', 'main', 'battle' 等）
            exact: 是否精确匹配

        Returns:
            (cx, cy) 绝对坐标，或 None（全部失败）
        """
        strategies = self.get_attempt_order(scene, keyword)

        for i, strat in enumerate(strategies):
            img, l, t, w, h = bot._snap()

            if strat.roi:
                rx, ry, rw, rh = strat.roi
                x = max(0, int(rx * w))
                y = max(0, int(ry * h))
                sw = min(w - x, max(1, int(rw * w)))
                sh = min(h - y, max(1, int(rh * h)))
                cropped = img.crop((x, y, x + sw, y + sh))
                items = bot._ocr_img(cropped)
                # 坐标转换回绝对坐标，保留 x_min/x_max 并平移
                abs_items = [(cy + y, cx + x, text, conf, xmn + x, xmx + x)
                             for cy, cx, text, conf, xmn, xmx in items]
            else:
                items = bot._ocr_img(img)
                abs_items = items

            # y_min_ratio 过滤：只匹配底部区域的文字
            if y_min_ratio is not None:
                y_thresh = int(y_min_ratio * h)
                abs_items = [item for item in abs_items if item[0] >= y_thresh]

            pos = bot._find_text_impl(abs_items, keyword, exact=exact,
                                     min_conf=strat.min_conf)
            if pos:
                self.remember(scene, keyword, strat)
                return pos

        return None

    def debug_dump(self) -> str:
        """打印记忆内容（调试用）"""
        if not self.memory:
            return '[OCR Memory] empty'
        lines = ['[OCR Memory] strategies:']
        for (scene, keyword), strat in sorted(self.memory.items()):
            roi_str = f'roi={strat.roi}' if strat.roi else 'fullscreen'
            lines.append(f'  {scene:12} | {keyword:12} | conf={strat.min_conf} | {roi_str}')
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════════

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# 截图优化：裁剪掉窗口装饰区域（标题栏+边框），只截游戏内容
# 设为 0 则截全窗口（包含装饰），设为 >0 则裁掉对应像素
_SNAP_TRIM = 0  # 实测 Unity 窗口没有装饰边框，设为 0 即可

# 模板匹配按钮名称 → 模板文件（放在 _templates/ 目录）
# 优先级：OCR优先，OCR失败后尝试模板匹配
# 模板匹配按钮名称 → 模板文件（已弃用，改用比例坐标）
# 保留此字典仅用于参考，新代码使用 _BTN_POS 比例坐标
_TMPL_BUTTONS = {
    'wanfa':     'btn_wanfa.png',      # 玩法目录（大世界界面）
    'shengmi':   'btn_shengmi.png',     # 神秘屋
    'huanling':  'btn_huanling.png',    # 幻灵挑战
    'challenge': 'btn_challenge.png',   # 挑战
    'auto':      'btn_auto_challenge.png',  # 自动挑战
    'options':   'btn_options.png',     # ≡选项（备战/其他界面）
    'gear':      'btn_gear.png',        # 齿轮设置
    'playbook':  'btn_playbook.png',    # 玩法目录（playbook）
    'normal':    'btn_normal_play.png', # 常规玩法
    'activity':  'btn_activity.png',    # 活动
    'world':     'btn_world.png',       # 世界（家园界面切换按钮）
    'jiayuan':   'btn_jiayuan.png',     # 家园（大世界右下角按钮）
    # idle: 已移除（改用 OCR 文字"挂机关卡"）
    # formation: 已移除（改用比例坐标）
}

# 图标按钮比例坐标（分辨率无关，比模板匹配更可靠）
# 格式: {模板名: (rx, ry)}  rx/ry 是相对于窗口的比例 0.0~1.0
_BTN_POS = {
    # 主界面
    'challenge': (0.647, 0.191),   # 挑战 - 主界面右上
    'huanling':  (0.648, 0.207),   # 幻灵挑战 - 主界面右上（略低）
    'options':   (0.967, 0.947),   # ≡选项 - 主界面右下角
    # 玩法目录（playbook state）
    'wanfa':     (0.297, 0.759),   # 玩法目录 - 底部
    'normal':    (0.500, 0.300),   # 常规玩法 - 玩法目录中部
    'activity':  (0.498, 0.116),   # 活动 - 玩法目录顶部中央
    # 选项菜单
    'gear':      (0.717, 0.944),   # ⚙设置齿轮 - 选项菜单右下
    # 备战/阵容界面
    'auto':      (0.500, 0.380),   # 自动挑战 - 备战界面
}


# ═══════════════════════════════════════════════════════════════════
#  MaaFramework 风格声明式 Pipeline
#  参考：https://deepwiki.com/MaaXYZ/MaaFramework
#  核心概念：
#    Step     — 最小操作单元（OCR点击/固定坐标/等待文字/颜色匹配）
#    Pipeline  — 步骤链 + next 跳转（成功/失败/超时 → 目标步骤）
#    ROI       — Region of Interest，减少无效 OCR 区域，加速且更准
#    freeze    — 等待画面稳定后再操作（防止点击到过渡动画）
# ═══════════════════════════════════════════════════════════════════

class Step:
    """声明式步骤基类（MaaFramework Step 参考实现）"""
    # pre_delay  : 执行前等待秒数
    # post_delay : 执行后等待秒数
    # wait_freezes: 等待画面稳定的最大秒数（OCR 连续 N 次无变化才算稳定）
    # retry      : 重试次数
    # roi        : (x, y, w, h) 截图 ROI，None = 全屏
    # on_timeout : 超时后的跳转目标，None = 停止
    # label      : 日志标签

    def __init__(self, label='', pre_delay=0.5, post_delay=1.0,
                 wait_freezes=0, retry=0, roi=None, on_timeout=None):
        self.label = label
        self.pre_delay = pre_delay
        self.post_delay = post_delay
        self.wait_freezes = wait_freezes   # 等待 N 秒画面无变化
        self.retry = retry
        self.roi = roi                      # (x_off, y_off, w, h) 相对游戏窗口
        self.on_timeout = on_timeout
        self._attempts = 0

    def _crop_roi(self, img, win_l, win_t, win_w, win_h):
        """裁剪 ROI 区域，返回 (cropped_img, offset_x, offset_y)"""
        if self.roi is None:
            return img, 0, 0
        rx, ry, rw, rh = self.roi
        x = max(0, int(rx * win_w))
        y = max(0, int(ry * win_h))
        w = min(win_w - x, max(1, int(rw * win_w)))
        h = min(win_h - y, max(1, int(rh * win_h)))
        cropped = img.crop((x, y, x + w, y + h))
        return cropped, x, y

    def _wait_freezes(self, bot, get_fingerprint_fn):
        """MaaFramework freeze 机制：等待画面稳定"""
        if self.wait_freezes <= 0:
            return True
        deadline = time.time() + self.wait_freezes
        last_fp = None
        stable_count = 0
        while time.time() < deadline:
            if bot._stop or not bot._pause.is_set():
                return False
            fp = get_fingerprint_fn()
            if fp != last_fp:
                last_fp = fp
                stable_count = 0
            else:
                stable_count += 1
                if stable_count >= 3:   # 连续 3 次采样无变化（约 1.5s）认为稳定
                    return True
            time.sleep(0.5)
        return False

    def _wait_for_text(self, bot, keyword, timeout=15):
        """等待指定文字出现（OCR 轮询）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if bot._stop or not bot._pause.is_set():
                return False
            img, l, t, w, h = bot._snap()
            items = bot._ocr_img(img)
            texts = [t for _, _, t, c, *_ in items if c >= 0.5]
            if any(keyword in t for t in texts):
                return True
            time.sleep(0.8)
        return False

    def run(self, bot):
        """执行步骤，返回 True/False"""
        raise NotImplementedError


class OCRActionStep(Step):
    """MaaFramework OCR 识别 + 点击（MaaFramework OCRTask 参考）"""
    def __init__(self, text, action='click', label='', click_offset=(0, 0),
                 exact=False, min_conf=0.5, wait_text=None,
                 **kwargs):
        super().__init__(label=label or text, **kwargs)
        self.text = text
        self.action = action           # 'click' | 'return_true' | 'return_false'
        self.click_offset = click_offset
        self.exact = exact
        self.min_conf = min_conf
        self.wait_text = wait_text     # 检测到这段文字才算成功

    def run(self, bot):
        time.sleep(self.pre_delay)
        if self.wait_text:
            # MaaFramework freeze：等待指定文字出现
            if not self._wait_for_text(bot, self.wait_text):
                return self._handle_timeout(bot)
            time.sleep(self.post_delay)
            return True

        # OCR 截图
        img, l, t, w, h = bot._snap()
        if self.roi:
            img, off_x, off_y = self._crop_roi(img, l, t, w, h)
        else:
            off_x, off_y = 0, 0

        items = bot._ocr_img(img)
        # 坐标转换回绝对坐标
        abs_items = []
        for cy, cx, txt, conf in items:
            abs_items.append((cy + off_y, cx + off_x, txt, conf))

        pos = bot._find_text_impl(abs_items, self.text, self.exact, self.min_conf)
        if not pos:
            return self._handle_timeout(bot)

        cx_abs = pos[0] + self.click_offset[0]
        cy_abs = pos[1] + self.click_offset[1]
        bot.log(f'  [{self.label}] → ({cx_abs},{cy_abs})')
        bot._click_abs(cx_abs, cy_abs)
        time.sleep(self.post_delay)
        return True



    def _handle_timeout(self, bot):
        if self.on_timeout == '__STOP__':
            return False
        if self.on_timeout:
            bot.log(f'  [{self.label}] 超时，跳转 → {self.on_timeout}')
            return '__JUMP__'
        bot.log(f'  [{self.label}] 未找到')
        return False


class RatioClickStep(Step):
    """固定比例坐标点击（MaaFramework DirectHit 参考）"""
    def __init__(self, rx, ry, label='', wait_text=None, after_ocr=False,
                 **kwargs):
        super().__init__(label=label, **kwargs)
        self.rx = rx
        self.ry = ry
        self.wait_text = wait_text
        self.after_ocr = after_ocr   # True = 点击后用 OCR 验证是否生效

    def run(self, bot):
        time.sleep(self.pre_delay)
        l, t, w, h = bot._win_dims()
        cx = int(self.rx * w)
        cy = int(self.ry * h)
        bot.log(f'  [{self.label}] 比例→ ({cx},{cy})')
        bot._click_abs(l + cx, t + cy)
        time.sleep(self.post_delay)

        if self.wait_text:
            if not self._wait_for_text(bot, self.wait_text):
                return self._handle_timeout(bot)
        return True


    def _handle_timeout(self, bot):
        if self.on_timeout == '__STOP__':
            return False
        if self.on_timeout:
            bot.log(f'  [{self.label}] 超时，跳转 → {self.on_timeout}')
            return '__JUMP__'
        return False


class WaitTextStep(Step):
    """等待指定文字出现（MaFramework OCR + DoNothing 参考）"""
    def __init__(self, text, exact=False, min_conf=0.5, label='', **kwargs):
        super().__init__(label=label or f'等待"{text}"', **kwargs)
        self.text = text
        self.exact = exact
        self.min_conf = min_conf

    def run(self, bot):
        time.sleep(self.pre_delay)
        deadline = time.time() + (self.wait_freezes or 30)
        last_fp = None
        while time.time() < deadline:
            if bot._stop or not bot._pause.is_set():
                return False
            img, l, t, w, h = bot._snap()
            if self.roi:
                img, off_x, off_y = self._crop_roi(img, l, t, w, h)
            else:
                off_x, off_y = 0, 0
            items = bot._ocr_img(img)
            abs_items = [(cy + off_y, cx + off_x, t, c) for cy, cx, t, c in items]
            pos = bot._find_text_impl(abs_items, self.text, self.exact, self.min_conf)
            if pos:
                bot.log(f'  [{self.label}] ✓')
                time.sleep(self.post_delay)
                return True
            # freeze 检测：连续 3 次采样无变化则判定为稳定
            fp = hash(tuple(sorted([t for _, _, t, c, *_ in abs_items])))
            if fp == last_fp:
                stable = getattr(self, '_stable_count', 0) + 1
                setattr(self, '_stable_count', stable)
                if stable >= 3:
                    bot.log(f'  [{self.label}] 画面稳定但未找到文字')
                    return self._handle_timeout(bot)
            else:
                setattr(self, '_stable_count', 0)
            last_fp = fp
            time.sleep(1)
        return self._handle_timeout(bot)

    def _handle_timeout(self, bot):
        if self.on_timeout == '__STOP__':
            return False
        bot.log(f'  [{self.label}] 超时未找到')
        return False


class ColorMatchStep(Step):
    """颜色匹配检测（MaaFramework ColorMatch 参考）"""
    # 用途：检测能量条、血条、按钮高亮、结算面板等 OCR 不适合的场景
    def __init__(self, hsv_range, region=None, label='',
                 action='return_true', **kwargs):
        # hsv_range: ((h_min, s_min, v_min), (h_max, s_max, v_max))
        super().__init__(label=label, **kwargs)
        self.hsv_range = hsv_range
        self.region = region    # (rx, ry, rw, rh) 比例区域
        self.action = action

    def run(self, bot):
        time.sleep(self.pre_delay)
        img, l, t, w, h = bot._snap()
        if self.region:
            rx, ry, rw, rh = self.region
            x = max(0, int(rx * w))
            y = max(0, int(ry * h))
            sw = min(w - x, max(1, int(rw * w)))
            sh = min(h - y, max(1, int(rh * h)))
            img = img.crop((x, y, x + sw, y + sh))

        matched = self._color_match(img)
        time.sleep(self.post_delay)
        if matched:
            bot.log(f'  [{self.label}] 颜色匹配 ✓')
            return True
        bot.log(f'  [{self.label}] 颜色不匹配')
        return False

    def _color_match(self, img):
        import numpy as np
        arr = np.array(img.convert('HSV'))
        (h_min, s_min, v_min), (h_max, s_max, v_max) = self.hsv_range
        mask = ((arr[:, :, 0] >= h_min) & (arr[:, :, 0] <= h_max) &
                (arr[:, :, 1] >= s_min) & (arr[:, :, 1] <= s_max) &
                (arr[:, :, 2] >= v_min) & (arr[:, :, 2] <= v_max))
        return mask.sum() > 50   # 至少 50 个像素匹配


class Pipeline:
    """MaaFramework 风格 Pipeline 执行器"""
    def __init__(self, steps, bot, label='Pipeline'):
        self.steps = steps      # dict: name -> Step
        self.order = []         # 执行顺序（可被跳转覆盖）
        self._bot = bot
        self.label = label
        # 构建跳转图（Step.on_timeout 指向另一个 Step 名称）
        self._jumps = {}
        for name, step in steps.items():
            if step.on_timeout and step.on_timeout != '__STOP__':
                self._jumps[name] = step.on_timeout

    def run(self):
        """从第一个步骤开始执行，遇到 __STOP__ 或全部完成返回"""
        bot = self._bot
        names = list(self.steps.keys())
        idx = 0
        while idx < len(names):
            name = names[idx]
            step = self.steps[name]
            step._attempts += 1
            if step.retry > 0 and step._attempts <= step.retry:
                bot.log(f'  [{step.label}] 重试 ({step._attempts}/{step.retry})')

            result = step.run(bot)
            if result == '__JUMP__':
                # 跳转到指定步骤
                target = step.on_timeout
                if target in self.steps:
                    idx = names.index(target)
                    bot.log(f'  Pipeline 跳转 → {target}')
                    continue
                else:
                    bot.log(f'  Pipeline 跳转目标不存在: {target}')
                    return False
            elif not result:
                if name in self._jumps:
                    target = self._jumps[name]
                    if target in self.steps:
                        idx = names.index(target)
                        bot.log(f'  Pipeline 超时跳转 → {target}')
                        continue
                return False
            idx += 1
        return True


# ═══════════════════════════════════════════════════════════════════
#  核心 Bot（参考 MaaFramework Agent 架构）
# ═══════════════════════════════════════════════════════════════════

class AFKBot:
    f"""AFK Journey 自动推图核心逻辑 {VERSION}

    状态机架构：
    ════════════════════════════════════════════════════════════════════
    主循环状态机（run 方法）：
      [启动] → [detect_phase] → [导航/等待] → [点挑战] → [等待战斗] → [关闭结算] → [下一轮]
                     ↓
               phase=? 分支处理：
               - battle  → wait_for_battle_end()
               - result  → step_close_result()
               - loading → 等待加载
               - login   → 停止脚本
               - options_menu → ESC关闭
               - quest_panel → ESC关闭
               - unknown  → ESC退回 → 重新detect
    ════════════════════════════════════════════════════════════════════
    导航状态机（navigate_to_idle_stage 方法）：
      [town] → 点"玩法目录" → [playbook] → 点"挂机关卡" → [main] → 完成
      [activity] → 点"常规玩法" → [playbook]
      [options_menu] → ESC → [town]
      [quest_panel] → ESC → 重新detect
      [battle] → 等待结束 → 重新detect
      [result] → 关闭 → 重新detect
      [unknown] → ESC退回 → no_progress+1 → 连续3次→停止
    ════════════════════════════════════════════════════════════════════
    detect_phase 返回值：
      main/formation → 挂机推图主界面
      town          → 主城界面（有"玩法目录"）
      playbook      → 玩法目录（有"挂机关卡"）
      activity      → 活动界面（有"常规玩法"）
      options_menu  → 选项菜单（≡）
      quest_panel   → 任务面板
      battle        → 战斗中
      result        → 结算界面
      loading       → 加载中
      login         → 登录界面
      unknown       → 无法识别
    """

    def __init__(self, config):
        self._stop = False
        self._pause = threading.Event()
        self._pause.set()
        self._hwnd = None
        self._ocr = MaaOCR()
        self._cfg = config
        self.log = config.get('log_callback') or print
        self.set_status = config.get('status_callback') or (lambda **kw: None)
        self.stats = {
            'huanling_wins': 0,
            'normal_wins': 0,
            'huanling_times': [],
            'normal_times': [],
            'total_battles': 0,
            'recovery_count': 0,
            'stage_progress': 0,   # 总推进关卡数
            'round_progress': 0,    # 本轮推进关卡数
            'current_stage': 0,    # 当前关卡（主界面红底白字）
            'huanling_stage': 0,    # 赛季幻灵关卡
            'normal_stage': 0,      # 赛季挂机关卡
            '_prev_huanling': 0,    # 进入战斗前的赛季幻灵关卡（临时）
            '_prev_normal': 0,      # 进入战斗前的赛季挂机关卡（临时）
        }
        # 调试截图目录（自动创建）
        self._debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_debug_shots')
        os.makedirs(self._debug_dir, exist_ok=True)
        self._debug_seq = 0

        # OCR 记忆系统
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self._ocr_memory = OCRMemory(os.path.join(script_dir, 'ocr_memory.json'))
        self.log(f'[{VERSION}] MaaOCR PP-OCRv5 引擎初始化')
        self.log('[OCR Memory] 已加载 ' + str(len(self._ocr_memory.memory)) + ' 条记忆')

        # 加载配置文件
        # PyInstaller 下 __file__ 指向临时解压目录，用 sys.executable 定位 exe 所在目录
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(base_dir, 'afkj_config.json')
        self.config = self._load_config()

    def _load_config(self):
        """加载配置文件，如果不存在则创建默认配置"""
        default_config = {
            "stuck": {
                "sample_interval": 0.3,
                "threshold": 10,
                "max_recovery_attempts": 3
            },
            "battle": {
                "miss_threshold": 3,
                "min_duration": 30,
                "ocr_interval_initial": 1,
                "ocr_interval_stable": 3,
                "max_diff": 10,
                "max_iter_navigate": 40,
                "max_attempts_close_result": 5
            }
        }
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                # 合并用户配置到默认配置（深度合并）
                for section in user_config:
                    if section in default_config:
                        default_config[section].update(user_config[section])
                    else:
                        default_config[section] = user_config[section]
        except Exception as e:
            self.log(f'配置文件加载失败，使用默认配置: {e}')
        # 如果配置文件不存在，保存默认配置
        if not os.path.exists(self.config_file):
            self.config = default_config
            self._save_config()
        return default_config

    def _save_config(self):
        """保存当前配置到文件（供 GUI 调用）"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f'保存配置失败: {e}')


    def click_template(self, name, conf=0.8, label=''):
        """
        【已弃用】改用 click_ratio() 比例坐标方式。
        按钮比例坐标定义在模块级 _BTN_POS 字典。
        """
        pos = _BTN_POS.get(name)
        if pos:
            return self.click_ratio(pos[0], pos[1], label=label or name)
        self.log(f'  ⚠ 按钮 [{name}] 位置未知（_BTN_POS 中无定义）')
        return False

    def ocr_then_template(self, keyword, tmpl_name, label='', exact=False, conf=0.5, verify_text=None):
        """
        统一点击方法：OCR 优先 → 失败则模板匹配兜底 → 验证。
        返回是否成功。

        Args:
            keyword: OCR 要查找的文字
            tmpl_name: OCR 失败后使用的模板名称（_TMPL_BUTTONS 的 key）
            label: 日志标签
            exact: 是否精确匹配
            conf: OCR 置信度阈值
            verify_text: 点击后验证期望出现的文字，None 则不验证
        """
        if not self._check_pause():
            return False
        label = label or keyword

        # Step 1: OCR
        pos = self._ocr_memory.find_text_with_memory(
            self, keyword, scene='navigate', exact=exact
        )
        if pos:
            self.click_at(pos[0], pos[1], f'{label} [OCR]')
            time.sleep(0.5)
        else:
            # Step 2: OCR 失败，尝试模板匹配
            self.log(f'  ⚠ OCR 未找到"{keyword}"，尝试模板匹配 [{tmpl_name}]...')
            found = self.click_template(tmpl_name, label=f'{label} [模板]')
            if not found:
                self.log(f'  ❌ OCR+模板 均未找到: {label}')
                return False
            time.sleep(1.5)

        # Step 3: 验证（如果指定了验证文字）
        if verify_text:
            if not self._wait_for_text(verify_text, timeout=5, label=f'验证[{verify_text}]'):
                self.log(f'  ⚠ 验证"{verify_text}"失败，点击可能无效')
                return False
        return True

    # ── 停止 & 暂停 ────────────────────────────────────────────────

    def stop(self):
        self._stop = True
        self._pause.set()

    def pause(self):
        if not self._pause.is_set():
            return
        self._pause.clear()
        self.log('⏸ 已暂停（按恢复继续）')

    def resume(self):
        if self._pause.is_set():
            return
        self._pause.set()
        self.log('▶ 恢复运行')

    def _check_pause(self):
        while not self._pause.wait(timeout=0.2):
            if self._stop:
                return False
        return not self._stop

    # ── 窗口管理 ──────────────────────────────────────────────────

    def find_hwnd(self):
        out = []
        def cb(h, r):
            if 'Unity' in win32gui.GetClassName(h):
                r.append(h)
        win32gui.EnumWindows(cb, out)
        return out[0] if out else None

    def find_and_focus(self):
        for attempt in range(5):
            h = self.find_hwnd()
            if h:
                try:
                    win32gui.ShowWindow(h, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(h)
                    time.sleep(0.5)
                    l, t, r, b = win32gui.GetWindowRect(h)
                    if r - l > 50 and b - t > 50:
                        return h
                except Exception as e:
                    self.log(f"[WARN] find_and_focus ShowWindow失败: {e}")
                time.sleep(2)
            else:
                self.log(f'  等待游戏窗口... ({attempt+1}/5)')
                time.sleep(3)
        return None

    def _win_dims(self):
        """获取窗口位置和尺寸，返回 (l, t, w, h)"""
        for attempt in range(3):
            try:
                l, t, r, b = win32gui.GetWindowRect(self._hwnd)
                if r - l < 50 or b - t < 50:
                    raise ValueError('too small')
                return l, t, r - l, b - t
            except Exception:
                self.log(f'  窗口异常，尝试恢复... ({attempt+1}/3)')
                self._hwnd = self.find_and_focus()
                if not self._hwnd:
                    raise RuntimeError('游戏窗口丢失！')
                self.log(f'  [hwnd refreshed] {self._hwnd}')
                l, t, r, b = win32gui.GetWindowRect(self._hwnd)
                if r - l >= 50 and b - t >= 50:
                    return l, t, r - l, b - t
                time.sleep(1)
        raise RuntimeError('游戏窗口丢失！')

    def _snap(self):
        """截图（ROI 优化），返回 (img, l, t, w, h)"""
        # 获取 client 区域尺寸（纯游戏内容，不含窗口装饰）
        cl_left, cl_top, cl_right, cl_bottom = win32gui.GetClientRect(self._hwnd)
        cl_w, cl_h = cl_right - cl_left, cl_bottom - cl_top
        l, t = win32gui.ClientToScreen(self._hwnd, (0, 0))
        self._activate_window()  # 确保游戏窗口在前台，避免截到遮挡物
        time.sleep(0.05)
        trim = _SNAP_TRIM
        img = ImageGrab.grab(bbox=(l + trim, t + trim, l + cl_w - trim, t + cl_h - trim))
        return img, l + trim, t + trim, cl_w - 2 * trim, cl_h - 2 * trim

    def _debug_screenshot(self, label='debug'):
        """保存调试截图到 _debug_shots/ 目录，文件名含时间戳和标签"""
        try:
            self._debug_seq += 1
            ts = time.strftime('%H%M%S')
            filename = f'{ts}_{self._debug_seq:03d}_{label}.png'
            filepath = os.path.join(self._debug_dir, filename)
            img, _, _, _, _ = self._snap()
            img.save(filepath)
            self.log(f'  [截图] {filepath}')
        except Exception as e:
            self.log(f"[WARN] 调试截图失败: {e}")

    def _activate_window(self):
        """激活游戏窗口到前台（操作前必须调用）"""
        try:
            # 先恢复窗口（如果被最小化）
            win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)
            # 再置前台（带 AttachThreadInput 解决焦点抢占问题）
            thread_id = win32api.GetCurrentThreadId()
            target_thread_id, _ = win32process.GetWindowThreadProcessId(self._hwnd)
            if thread_id != target_thread_id:
                win32process.AttachThreadInput(thread_id, target_thread_id, True)
                win32gui.SetForegroundWindow(self._hwnd)
                win32process.AttachThreadInput(thread_id, target_thread_id, False)
            else:
                win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.1)
        except Exception as e:
            self.log(f"[WARN] 激活窗口失败: {e}")

    # ── 点击 & 按键 ────────────────────────────────────────────────

    def _click_abs(self, cx, cy):
        """屏幕绝对坐标点击（自动激活窗口）"""
        if not self._check_pause():
            return
        self._activate_window()
        pyautogui.click(cx, cy)

    def click_ratio(self, rx, ry, label=''):
        """比例坐标点击（rx/ry 是相对于窗口的比例 0.0~1.0）"""
        if not self._check_pause():
            return False
        l, t, w, h = self._win_dims()
        cx = l + rx * w
        cy = t + ry * h
        if label:
            self.log('点击: {} → 比例({:.3f},{:.3f}) → 屏幕({:.0f},{:.0f})'.format(label, rx, ry, cx, cy))
        self._click_abs(cx, cy)
        time.sleep(0.5)
        return True

    def _screen_offset(self):
        """返回窗口左上角屏幕坐标 (l, t)"""
        l, t, _, _ = self._win_dims()
        return l, t

    def _send_esc(self):
        """发送 ESC 按键（系统级 keybd_event，Unity 有效）"""
        if not self._check_pause():
            return
        self._activate_window()
        time.sleep(0.1)  # 激活后等一下再发按键
        win32api.keybd_event(0x1B, 0, 0, 0)
        win32api.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.05)  # 按键间隔

    def click_at(self, cx, cy, label=''):
        """OCR 图像坐标 → 加上窗口偏移变成屏幕绝对坐标 → 点击"""
        if not self._check_pause():
            return
        # OCR 返回的是截图片区相对坐标（image 左上角 = 窗口左上角 = l+trim, t+trim）
        # pyautogui 需要屏幕绝对坐标，所以要加上窗口偏移
        _, img_l, img_t, _, _ = self._snap()
        abs_cx = img_l + cx
        abs_cy = img_t + cy
        self.log(f'点击: {label} → OCR({cx},{cy}) → 屏幕({abs_cx},{abs_cy})')
        self._click_abs(abs_cx, abs_cy)
        time.sleep(0.5)

    def press_esc(self, label=''):
        if not self._check_pause():
            return
        if label:
            self.log(f'ESC: {label}')
        win32api.SendMessage(self._hwnd, 0x0100, 0x1B, 0)
        time.sleep(0.05)
        win32api.SendMessage(self._hwnd, 0x0101, 0x1B, 0)

    # ── OCR ────────────────────────────────────────────────────────

    def _ocr_img(self, img, roi=None):
        """对 PIL Image 执行 OCR，返回 [(cy, cx, text, conf, x_min, x_max)]
        
        x_min/x_max 为文字块左右边界，用于 OCR 合并多个相邻文字时精确定位子词位置。

        Args:
            img: PIL Image
            roi: 可选 (x, y, w, h) 截图区域（像素坐标），MaaOCR 内部会做裁剪
        """
        results = self._ocr.recognize(img, roi=roi)
        items = []
        for text, box, score in results:
            # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys))
            x_min = min(xs)
            x_max = max(xs)
            items.append((cy, cx, text, score, x_min, x_max))
        items.sort()
        return items

    def _ocr_screen(self, img=None):
        """全屏 OCR（带缓存，60秒内相同画面不重复OCR）

        Args:
            img: 可选，外部传入的截图 PIL Image。如果为 None，则内部调用 _snap() 截图。
        """
        if img is None:
            img, l, t, w, h = self._snap()
        else:
            w, h = img.size
        # 计算画面指纹（采样20点像素），用作缓存键
        arr = np.array(img.convert('RGB'))
        sample_pts = [
            (h // 4, w // 4), (h // 4, 3 * w // 4),
            (3 * h // 4, w // 4), (3 * h // 4, 3 * w // 4),
            (h // 2, w // 2),
            (h // 3, w // 3), (h // 3, 2 * w // 3),
            (2 * h // 3, w // 3), (2 * h // 3, 2 * w // 3),
            (h // 5, w // 5), (h // 5, 4 * w // 5),
            (4 * h // 5, w // 5), (4 * h // 5, 4 * w // 5),
            (h // 6, w // 2), (5 * h // 6, w // 2),
            (h // 2, w // 6), (h // 2, 5 * w // 6),
            (h // 4, w // 2), (3 * h // 4, w // 2),
        ]
        pixels = tuple(tuple(arr[y, x].tolist()) for y, x in sample_pts)
        key = hash(pixels)

        now = time.time()
        cache = getattr(self, '_ocr_cache', {})
        if key in cache and now - cache[key][0] < 60:
            return cache[key][1]

        items = self._ocr_img(img)
        cache[key] = (now, items)
        # 限制缓存大小：超过10条时，保留最新的10条
        if len(cache) > 10:
            sorted_keys = sorted(cache.keys(), key=lambda k: cache[k][0], reverse=True)
            for k in sorted_keys[10:]:
                del cache[k]
        self._ocr_cache = cache
        return items

    def _ocr_roi(self, roi):
        """ROI OCR：只识别指定区域，加速且减少误识别"""
        img, l, t, w, h = self._snap()
        rx, ry, rw, rh = roi
        x = max(0, int(rx * w))
        y = max(0, int(ry * h))
        sw = min(w - x, max(1, int(rw * w)))
        sh = min(h - y, max(1, int(rh * h)))
        cropped = img.crop((x, y, x + sw, y + sh))
        return self._ocr_img(cropped)

    def _normalize(self, text):
        """Unicode NFKC 标准化，解决 OCR 编码差异导致的匹配失败"""
        return unicodedata.normalize('NFKC', text)

    def _find_text_impl(self, items, keyword, exact=False, min_conf=0.5):
        """在 OCR 结果中查找文字，支持 Unicode NFKC 标准化匹配。

        兼容4元组 (cy, cx, text, conf) 和6元组 (cy, cx, text, conf, x_min, x_max)。
        当 OCR 将相邻文字合并为一个块时（如"玩法目录英雄厅堂"），
        按关键词在合并文字中的字符位置比例精确估算 cx，避免点击到中间位置。

        匹配策略（按优先级）：
        1. exact=True: 精确匹配 keyword == NFKC(OCR_text)
        2. keyword 包含在 OCR_text 中（partial，正向匹配）
        3. OCR_text 包含 keyword 的主要部分（反向 fallback，应对 OCR 首字截断问题）
           - 当 OCR 识别为"键采用"而目标是"一键采用"时，走此路径
           - 仅当 keyword 长度>=2 且 OCR_text 长度>=2 时触发
           - 取 confidence >= min_conf 且最高的那个结果
        """
        norm_kw = self._normalize(keyword)
        for item in items:
            if len(item) >= 6:
                cy, cx, text, conf, x_min, x_max = item[0], item[1], item[2], item[3], item[4], item[5]
            else:
                cy, cx, text, conf = item[0], item[1], item[2], item[3]
                x_min, x_max = cx, cx
            if conf < min_conf:
                continue
            norm_text = self._normalize(text)
            if exact:
                matched = (norm_text == norm_kw or norm_text.startswith(norm_kw) or norm_kw in norm_text)
            else:
                matched = (norm_kw in norm_text)
            if matched:
                # 若 OCR 合并了多个相邻文字，按字符比例定位到关键词中心
                if norm_text != norm_kw and (x_max - x_min) > 10:
                    try:
                        idx = norm_text.index(norm_kw)
                        total_chars = len(norm_text)
                        kw_center_char = idx + len(norm_kw) / 2.0
                        ratio = kw_center_char / total_chars
                        cx = x_min + (x_max - x_min) * ratio
                    except ValueError:
                        pass
                return cx, cy

        # ── 反向 substring fallback ───────────────────────────────
        # 处理 OCR 首字截断："一键采用"可能被识别为"键采用"
        if len(keyword) >= 3:
            suffix = keyword[1:]  # 去掉首字，保留剩余部分
            norm_suffix = self._normalize(suffix)
            best_conf = min_conf
            for item in items:
                if len(item) >= 6:
                    cy, cx, text, conf, x_min, x_max = item[0], item[1], item[2], item[3], item[4], item[5]
                else:
                    cy, cx, text, conf = item[0], item[1], item[2], item[3]
                    x_min, x_max = cx, cx
                if conf < best_conf:
                    continue
                norm_text = self._normalize(text)
                if norm_suffix in norm_text:
                    # 找到了，估算关键词位置
                    if norm_text != norm_suffix and (x_max - x_min) > 10:
                        try:
                            idx = norm_text.index(norm_suffix)
                            total_chars = len(norm_text)
                            kw_center_char = idx + len(norm_suffix) / 2.0 - 1
                            ratio = max(0, min(1, kw_center_char / total_chars))
                            cx = x_min + (x_max - x_min) * ratio
                        except (ValueError, ZeroDivisionError):
                            pass
                    return cx, cy
        return None

    def find_text(self, items, keyword, min_conf=0.5):
        return self._find_text_impl(items, keyword, exact=False, min_conf=min_conf)

    def find_text_exact(self, items, keyword, min_conf=0.5):
        """精确匹配：优先 exact(NFKC)，fallback 到 partial match"""
        pos = self._find_text_impl(items, keyword, exact=True, min_conf=min_conf)
        if pos:
            return pos
        # fallback: partial match（处理 OCR 编码错误，如 "幻灵挑战" 被识别为乱码）
        return self._find_text_impl(items, keyword, exact=False, min_conf=min_conf)

    def find_text_exclude(self, items, keyword, exclude_keywords, min_conf=0.5):
        """精确匹配 keyword，但排除包含 exclude_keywords 中任意一项的结果

        用于解决 "挑战" 和 "幻灵挑战" 部分匹配冲突的问题。
        """
        norm_kw = self._normalize(keyword)
        norm_exclude = [self._normalize(e) for e in exclude_keywords]
        for cy, cx, text, conf, *_ in items:
            if conf < min_conf:
                continue
            norm_text = self._normalize(text)
            # 必须精确匹配 keyword
            if norm_kw != norm_text:
                continue
            # 不能包含任何排除词
            skip = any(ex in norm_text for ex in norm_exclude)
            if skip:
                continue
            return cx, cy
        return None

    # ── 界面检测（MaaFramework Region.json 参考）────────────────────

    def is_on_main_screen(self):
        items = self._ocr_screen()
        _, _, w, h = self._win_dims()
        for _, _, t, c, *_ in items:
            if c >= 0.5 and ('玩法目录' in t or '玩法目' in t):
                return True
        menu_only = ['背包', '商店', '社交', '编队', '邮件']
        menu_count = sum(1 for f in menu_only if self.find_text(items, f, min_conf=0.5))
        if menu_count >= 2:
            return False
        for _, _, t, c, *_ in items:
            if c >= 0.5 and t == '活动':
                return False
        if self.find_text(items, '设置', min_conf=0.6) and (self.find_text(items, '图像') or self.find_text(items, '战斗')):
            return False
        if self.find_text(items, '退出游戏'):
            return False
        if w < 900:
            return True
        return False

    def esc_to_main_screen(self, max_esc=5):
        if self.is_on_main_screen():
            return True
        exit_count = 0
        for i in range(max_esc):
            if self._stop:
                return False
            if not self._pause.is_set():
                self._pause.wait()
                if self._stop:
                    return False
            self._activate_window()
            items = self._ocr_screen()
            if self.find_text(items, '退出游戏'):
                exit_count += 1
                self.log(f'  [ESC] 退出游戏弹窗 ({exit_count})，关闭')
                self._send_esc()
                time.sleep(0.8)
                if exit_count >= 2:
                    self.log(f'  [ESC] 已在主界面')
                    return True
                continue
            if self.is_on_main_screen():
                return True
            exit_count = 0
            self.log(f'  [ESC] 返回 ({i+1}/{max_esc})')
            self._send_esc()
            time.sleep(0.8)
        return self.is_on_main_screen()

    # ── 阶段检测 ───────────────────────────────────────────────────

    def detect_phase(self):
        # detect_phase 状态说明：
        # - result      : 结算界面（自动挑战结束/战斗失败/点击空白处关闭）
        # - battle     : 战斗中（自动挑战中）
        # - loading    : 加载中（加载中/Loading/进度）
        # - login      : 登录界面（登录/账号/服务器）
        # - formation  : 备战/结算界面（通关阵容）
        # - main       : 挂机推图主界面（幻灵挑战/挑战）
        # - playbook   : 玩法目录（挂机关卡）
        # - activity   : 活动界面（活动+常规玩法）
        # - options_menu: 选项菜单（背包/邮件/社交/编队/图鉴/商城 ≥3）
        # - quest_panel: 任务面板（每日任务/AFK/离线奖励）
        # - town       : 主城界面（is_on_main_screen()）
        # - unknown    : 无法识别
        items = self._ocr_screen()
        # 统一使用 all_texts (c>=0.3) 做阶段检测，提高召回率
        all_texts = [t for _, _, t, c, *_ in items if c >= 0.3]
        # 高置信度文本 (c>=0.5) 用于需要精确匹配的场景
        high_texts = [t for _, _, t, c, *_ in items if c >= 0.5]

        # result 优先（终态，战斗结束时可能同时显示 battle+result 文字）
        if any('自动挑战结束' in t for t in all_texts) or any('自动战斗结束' in t for t in all_texts):
            return 'result'
        if any('挑战失败' in t or '战斗失败' in t or '战败' in t for t in all_texts):
            return 'result'
        # 点击空白处关闭（结算界面特征）
        if any('点击空白处关闭' in t for t in all_texts):
            return 'result'
        # 点击关闭（可能是其他弹窗如奖励弹窗，排除"领取"/"奖励"）
        if any('点击关闭' in t and '领取' not in t and '奖励' not in t for t in all_texts):
            return 'result'
        # 确认对话框："是否关闭自动挑战模式？" — 说明自动挑战还在运行
        if any('是否关闭' in t and '自动挑战' in t for t in all_texts):
            return 'battle'

        # battle 状态
        if any('自动挑战中' in t for t in all_texts):
            return 'battle'

        # 加载界面检测
        if any('加载中' in t or 'Loading' in t or '进度' in t for t in all_texts):
            return 'loading'

        # 登录界面检测
        if any('登录' in t or '账号' in t or '服务器' in t for t in all_texts):
            return 'login'

        # 备战/结算界面
        if any('通关阵容' in t for t in all_texts):
            return 'formation'
        if any('幻灵挑战' in t for t in all_texts) or any(t == '挑战' for t in all_texts):
            return 'main'
        # 选项菜单检测（≡菜单，包含背包/邮件/社交/编队/图鉴/商城等，优先于activity）
        # 必须在 activity 前面检测，因为侧边栏菜单里也有"活动"文字
        menu_items = ['背包', '邮件', '社交', '编队', '图鉴', '商城']
        found_menu = sum(1 for item in menu_items if any(item in t for t in all_texts))
        if found_menu >= 3:
            return 'options_menu'

        # 任务面板检测（每日任务/AFK离线奖励界面）
        if any('每日任务' in t for t in all_texts) or any('AFK' in t for t in all_texts) \
           or any('离线奖励' in t for t in all_texts) or any('任务详情' in t for t in all_texts):
            return 'quest_panel'

        # 玩法目录检测（主内容有"挂机关卡"按钮，优先于activity）
        # 必须在 activity 前面检测，因为 playbook 界面侧边栏也有"活动"文字
        if any('挂机关卡' in t for t in all_texts):
            return 'playbook'
        # 活动界面检测（活动界面：主内容有"常规玩法"，侧边栏"活动"高亮）
        # 此时已排除 playbook（有"挂机关卡"），所以 activity 检测不会误判 playbook
        if any('活动' in t for t in all_texts) and any('常规玩法' in t for t in all_texts):
            return 'activity'

        if self.is_on_main_screen():
            return 'town'
        self._debug_screenshot('phase_unknown')
        return 'unknown'

    # ── 导航（MaaFramework Pipeline 风格）──────────────────────────

    def _ocr_and_click_for_navigate(self, target_text, tmpl_name=None, y_min_ratio=None, max_retries=3):
        """OCR 找到文字并点击，返回是否成功（用于导航场景）"""
        if self._stop:
            return False
        for attempt in range(1, max_retries + 1):
            pos = self._ocr_memory.find_text_with_memory(
                self, target_text, scene='navigate', exact=False, y_min_ratio=y_min_ratio
            )
            if pos:
                self.log(f'点击[{attempt}]: {target_text} → OCR({pos[0]:.0f},{pos[1]:.0f})')
                self.click_at(pos[0], pos[1], target_text)
                time.sleep(1.5)
                return True
            if attempt < max_retries:
                self.log(f'  ⚠ [{attempt}] OCR 未找到"{target_text}"，1秒后重试...')
                time.sleep(1)
                continue
            if tmpl_name:
                self.log(f'  ⚠ [{attempt}] OCR 未找到"{target_text}"，尝试模板 [{tmpl_name}]...')
                if self.click_template(tmpl_name, label=f'{target_text} [模板]'):
                    time.sleep(1.5)
                    return True
            self.log(f'  ⚠ OCR+模板 均未找到: {target_text}')
        return False

    def navigate_to_idle_stage(self):
        """导航到挂机推图主界面（状态机驱动）

        循环：
        1. 检测当前画面（detect_phase）
        2. 根据画面决定操作
        3. 无法识别 → ESC退回上一层，回到1

        导航状态机（状态 → 操作 → 下一状态）：
          town         → 点"玩法目录"     → playbook
          playbook     → 点"挂机关卡"     → main
          activity     → 点"常规玩法"     → playbook
          options_menu → ESC              → 重新detect
          quest_panel  → ESC              → 重新detect
          battle       → 等待结束         → 重新detect
          result       → 关闭             → 重新detect
          unknown      → ESC (no_progress) → 重新detect (连续3次→停止)
        """
        battle_cfg = self.config['battle']
        max_iter = battle_cfg['max_iter_navigate']
        no_progress = 0
        last_phase = None

        def is_target():
            """检查是否已在目标界面（幻灵挑战/挑战 可见）"""
            items = self._ocr_screen()
            texts = [t for _, _, t, c, *_ in items if c >= 0.5]
            return (
                any('幻灵挑战' in t or t == '挑战' for t in texts) or
                any('通关阵容' in t or '自动挑战' in t for t in texts)
            )

        # 预检查：已在目标界面
        if is_target():
            self.log('✓ 已在挂机推图界面')
            return True

        for i in range(max_iter):
            if self._stop:
                return False

            phase = self.detect_phase()
            self.log(f'[nav {i}] 当前画面: {phase}')

            # ── 目标状态：已在挂机推图界面 ──
            if phase in ('main', 'formation'):
                if is_target():
                    self.log(f'✓ 导航成功（{phase}）')
                    return True
                self.log(f'  在{phase}但目标未就绪，等待...')
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 主城界面（town）：点"玩法目录" ──
            if phase == 'town':
                self.log('在主城界面，点"玩法目录"')
                if self._ocr_and_click_for_navigate('玩法目录', tmpl_name='wanfa', y_min_ratio=0.2):
                    time.sleep(1)
                    continue
                self._send_esc()
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 玩法目录（playbook）：点"挂机关卡" ──
            if phase == 'playbook':
                self.log('在玩法目录，点"挂机关卡"')
                if self._ocr_and_click_for_navigate('挂机关卡', y_min_ratio=0.2):
                    time.sleep(1)
                    continue
                self._send_esc()
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 活动菜单（activity）：点"常规玩法" ──
            if phase == 'activity':
                self.log('在活动菜单，点"常规玩法"')
                if self._ocr_and_click_for_navigate('常规玩法', tmpl_name='normal', y_min_ratio=0.2):
                    time.sleep(1)
                    continue
                self._send_esc()
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 选项菜单（options_menu）：ESC关闭 ──
            if phase == 'options_menu':
                self.log('在选项菜单，ESC关闭')
                self._send_esc()
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 任务面板（quest_panel）：ESC关闭 ──
            if phase == 'quest_panel':
                self.log('在任务面板，ESC关闭')
                self._send_esc()
                time.sleep(1)
                # 无进展，增加计数
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self.log(f'⚠ 连续{no_progress}次在{phase}无进展，停止导航')
                    return False
                continue

            # ── 战斗中（battle）：等待结束 ──
            if phase == 'battle':
                self.log('在战斗中，等待结束（最多90秒）...')
                for _ in range(90):
                    if self._stop:
                        return False
                    time.sleep(1)
                    if _ % 3 == 2 and self.detect_phase() != 'battle':
                        break
                continue

            # ── 结算界面（result）：关闭 ──
            if phase == 'result':
                self.log('在结算界面，关闭')
                self.step_close_result()
                continue

            # ── 加载中（loading）：等待 ──
            if phase == 'loading':
                self.log('加载中，等待...')
                time.sleep(2)
                continue

            # ── 登录界面（login）：停止 ──
            if phase == 'login':
                self.log('⚠ 检测到登录界面，停止脚本')
                self._stop = True
                return False

            # ── 无法识别（unknown 或未知 phase）──
            if phase == last_phase:
                no_progress += 1
            else:
                no_progress = 0
            last_phase = phase

            if no_progress >= 3:
                self.log(f'⚠ 连续{no_progress}次无法识别画面({phase})，停止导航')
                return False

            self.log(f'无法识别画面({phase})，ESC退回 (no_progress={no_progress})')
            self._send_esc()
            time.sleep(1.5)

        self.log(f'⚠ 导航超时（{max_iter}次迭代）')
        return False

    # ── 操作步骤 ───────────────────────────────────────────────────

    def wait_for_idle_stage(self, timeout=30, img=None):
        self.log('等待挂机推图主界面...')
        battle_cfg = self.config['battle']
        deadline = time.time() + timeout
        miss_count = 0
        miss_threshold = battle_cfg['miss_threshold']  # 连续 N 次无变化就 ESC
        last_fp = None

        while time.time() < deadline:
            if self._stop:
                return False
            if not self._check_pause():
                return False
            if img is None:
                items = self._ocr_screen()
            else:
                items = self._ocr_screen(img)
                img = None  # 只用一次传入的img，后续重新截图
            texts = [t for _, _, t, c, *_ in items if c >= 0.5]
            fp = '|'.join(sorted(set(texts)))

            if any('幻灵挑战' in t for t in texts) or any(t == '挑战' for t in texts):
                self.log('✓ 已在挂机推图主界面')
                return True
            if any('通关阵容' in t for t in texts) or any('自动挑战' in t for t in texts):
                self.log('✓ 已在备战界面（挂机推图内）')
                return True

            # 连续 3 次画面无变化 → ESC
            if fp == last_fp and last_fp is not None:
                miss_count += 1
                if miss_count >= miss_threshold:
                    self.log(f'  [{miss_count}s] 画面无变化，ESC...')
                    self._send_esc()
                    time.sleep(1)
                    miss_count = 0  # ESC 后重新计数
            else:
                miss_count = 0
            last_fp = fp

            time.sleep(1)
        return False

    def step_click_challenge_btn(self, mode='huanling', img=None):
        """点击挑战按钮，进入备战界面。
        Returns:
            True  — 成功进入备战界面
            False — 按钮不存在（该类挑战已通关）或恢复失败
        """
        if not self._check_pause():
            return False
        # 如果已经在备战界面，无需点击挑战按钮
        # 挂机关卡备战界面有"通关阵容"；先锋关卡备战界面只有"自动挑战"
        if img is None:
            items0 = self._ocr_screen()
        else:
            items0 = self._ocr_screen(img)
            img = None  # 只用一次
        texts0 = [t for _, _, t, c, *_ in items0 if c >= 0.4]
        if any('通关阵容' in t or '自动挑战' in t for t in texts0):
            self.log('已在备战界面，跳过点击挑战按钮')
            return True

        if mode == 'huanling':
            label = '幻灵挑战'
            find_fn = lambda its: self.find_text_exact(its, '幻灵挑战')
            fallback = 'huanling'
            expect_text = '通关阵容'
        else:
            label = '挑战'
            find_fn = lambda its: self.find_text_exclude(its, '挑战', exclude_keywords=['幻灵'])
            fallback = 'challenge'
            expect_text = '通关阵容'

        # ── OCR 3次重试 ───────────────────────────────────────────
        clicked = False
        for attempt in range(1, 4):
            items = self._ocr_screen()
            pos = find_fn(items)
            if pos:
                self.click_at(pos[0], pos[1], label)
                clicked = True
                break
            if attempt < 3:
                self.log(f'  ⚠ [{attempt}] 未找到"{label}"，1秒后重试...')
                time.sleep(1)
            else:
                # 3次全失败 → 比例坐标兜底
                self.log(f'  ⚠ [3] 未找到"{label}"，尝试比例坐标兜底 [{fallback}]...')
                if self.click_template(fallback, label=f'{label} [比例坐标]'):
                    clicked = True
                time.sleep(0.5)

        if not clicked:
            # 按钮完全找不到 → 该挑战类型可能已通关
            self.log(f'✗ 找不到"{label}"按钮，该挑战类型可能已通关')
            return False

        # 验证是否进入备战界面
        if not self._wait_for_text(expect_text, timeout=5, label=f'{label}→备战界面'):
            self._debug_screenshot(f'{label}_no_formation')
            self.log(f'⚠ 未进入备战界面，ESC后重试...')
            self._send_esc()
            time.sleep(1)
            # ESC后重试找挑战按钮
            for retry_esc in range(1, 4):
                items2 = self._ocr_screen()
                pos2 = find_fn(items2)
                if pos2:
                    self.log(f'  ✓ ESC后重试找到"{label}"，点击')
                    self.click_at(pos2[0], pos2[1], f'{label}(ESC后重试)')
                    time.sleep(2)
                    if self._wait_for_text(expect_text, timeout=5, label=f'{label}→备战界面(ESC后)'):
                        return True
                    break
                if retry_esc < 3:
                    self._send_esc()
                    time.sleep(1)
                else:
                    self.log('⚠ ESC后3次仍未找到挑战按钮，尝试全流程恢复...')
                    self._recover_to_idle_stage()
                    return False
            # ESC重试仍失败 → 全流程恢复
            self._recover_to_idle_stage()
            return False
        return True

    def step_click_formation(self, formation_idx=0, img=None):
        if not self._check_pause():
            return

        # ── 点击"通关阵容" ─────────────────────────────────────
        for attempt in range(1, 4):
            if img is None:
                items = self._ocr_screen()
            else:
                items = self._ocr_screen(img)
                img = None  # 只用一次
            pos = self.find_text(items, '通关阵容')
            if pos:
                self.click_at(pos[0], pos[1], '通关阵容')
                time.sleep(2)
                break
            if attempt < 3:
                self.log(f'  ⚠ [{attempt}] 未找到"通关阵容"，1秒后重试...')
                time.sleep(1)
            else:
                self.log('  ⚠ [3] 未找到"通关阵容"，跳过')
                return

        if formation_idx > 0:
            self._click_next_formation(formation_idx)
        else:
            # 无需切换阵容，直接点"一键采用"，3次重试
            for attempt in range(1, 4):
                items2 = self._ocr_screen()
                adopt = self.find_text(items2, '一键采用')
                if adopt:
                    self.click_at(adopt[0], adopt[1], '一键采用')
                    time.sleep(1.5)
                    return
                if attempt < 3:
                    self.log(f'  ⚠ [{attempt}] 未找到"一键采用"，1秒后重试...')
                    time.sleep(1)
                else:
                    # 3次全失败 → ESC回退
                    self.log('  ⚠ [3] 未找到"一键采用"，ESC回退...')
                    self._debug_screenshot('yijian_caiyong_missing')
                    self._send_esc()
                    time.sleep(1)
                    # 再次尝试找"一键采用"
                    items3 = self._ocr_screen()
                    adopt3 = self.find_text(items3, '一键采用')
                    if adopt3:
                        self.log('  ✓ ESC后找到"一键采用"，点击')
                        self.click_at(adopt3[0], adopt3[1], '一键采用(ESC后)')
                        time.sleep(1.5)
                    else:
                        # 检查是否已进入备战界面（"自动挑战"可见）
                        texts3 = [t for _, _, t, c, *_ in items3 if c >= 0.5]
                        if any('自动挑战' in t for t in texts3):
                            self.log('  ✓ 未找到"一键采用"，但已在备战界面，继续')
                        else:
                            self.log('  ⚠ 无法确认当前界面，尝试恢复...')
                            self._recover_to_idle_stage()

    def _click_next_formation(self, times=1, img=None):
        for i in range(times):
            # 确认阵容面板已打开（面板内有"一键采用"文字），3次重试
            panel_ok = False
            for attempt in range(1, 4):
                if img is None:
                    items = self._ocr_screen()
                else:
                    items = self._ocr_screen(img)
                    img = None  # 只用一次
                if self.find_text(items, '一键采用'):
                    panel_ok = True
                    break
                if attempt < 3:
                    time.sleep(1)
                else:
                    self.log(f'  ⚠ [3] 阵容面板未打开，跳过切换')
                    return

            # 比例坐标点击 ">" 按钮（模板匹配在分辨率切换后不可靠）
            # 箭头在阵容面板右侧，比例约 (0.631, 0.544)
            if not self.click_ratio(0.631, 0.544, label=f'切换阵容> ({i+1}/{times})'):
                self.log(f'  ❌ 点击">"按钮失败，跳过本次切换')
                return
            time.sleep(1.5)

            # 验证阵容面板仍然存在（没有被意外关闭），3次重试
            adopt_found = False
            for attempt in range(1, 4):
                items2 = self._ocr_screen()
                adopt = self.find_text(items2, '一键采用')
                if adopt:
                    self.click_at(adopt[0], adopt[1], '一键采用（换阵后）')
                    time.sleep(1)
                    adopt_found = True
                    break
                if attempt < 3:
                    self.log(f'  ⚠ [{attempt}] 未找到"一键采用"，等待...')
                    time.sleep(1)
                else:
                    # 3次全失败 → 检查当前界面
                    texts = [t for _, _, t, c, *_ in items2 if c >= 0.5]
                    if any('自动挑战' in t for t in texts):
                        self.log(f'  ✓ 阵容面板已关闭，"自动挑战"可见，继续')
                    else:
                        self.log(f'  ⚠ [3] 未找到"一键采用"，ESC回退...')
                        self._send_esc()
                        time.sleep(1)

    def step_click_auto_challenge(self, img=None):
        """点击自动挑战按钮，等待进入战斗画面。
        Returns:
            bool: True=成功进入战斗，False=未进入战斗(已尝试恢复)
        """
        if not self._check_pause():
            return False

        # ── 找"自动挑战"按钮，3次重试 ───────────────────────────
        click_made = False
        for attempt in range(1, 4):
            if img is None:
                items = self._ocr_screen()
            else:
                items = self._ocr_screen(img)
                img = None  # 只用一次
            pos = self.find_text(items, '自动挑战')
            if pos:
                self.click_at(pos[0], pos[1], f'自动挑战 [OCR #{attempt}]')
                click_made = True
                break
            if attempt < 3:
                self.log(f'⚠ [{attempt}] 未找到"自动挑战"，1秒后重试...')
                time.sleep(1)
            else:
                self.log('⚠ [3] 未找到"自动挑战"，尝试模板匹配 [auto]...')
                if self.click_template('auto', label='自动挑战 [模板]'):
                    click_made = True
                time.sleep(0.5)

        if not click_made:
            self.log('⚠ OCR+模板 均未找到"自动挑战"，尝试恢复...')
            self._recover_to_idle_stage()
            return False

        # ── 验证是否进入战斗 ─────────────────────────────────────
        if not self._wait_for_text('自动挑战中', timeout=5, label='→战斗中'):
            self.log('⚠ 5s内未进入战斗，1秒后重试OCR...')
            time.sleep(1)
            # 重试找按钮并点击
            for retry in range(1, 4):
                items2 = self._ocr_screen()
                pos2 = self.find_text(items2, '自动挑战')
                if pos2:
                    self.log(f'  ✓ 重试#{retry}找到"自动挑战"，点击')
                    self.click_at(pos2[0], pos2[1], f'自动挑战(重试#{retry})')
                    time.sleep(2)
                    if self._wait_for_text('自动挑战中', timeout=5, label=f'→战斗中(重试#{retry})'):
                        return True
                else:
                    if retry < 3:
                        self.log(f'  ⚠ 重试#{retry}未找到，1秒后继续...')
                        time.sleep(1)
                    else:
                        self.log('⚠ 重试3次仍未进入战斗，ESC回退...')
                        self._send_esc()
                        time.sleep(1)
                        # ESC后最后一次尝试
                        items3 = self._ocr_screen()
                        pos3 = self.find_text(items3, '自动挑战')
                        if pos3:
                            self.log('  ✓ ESC后找到"自动挑战"，点击')
                            self.click_at(pos3[0], pos3[1], '自动挑战(ESC后)')
                            time.sleep(2)
                            if self._wait_for_text('自动挑战中', timeout=5, label='→战斗中(ESC后)'):
                                return True
                        # 检查是否已在备战界面
                        texts3 = [t for _, _, t, c, *_ in items3 if c >= 0.5]
                        if any('通关阵容' in t for t in texts3):
                            self.log('  ⚠ 未进入战斗，但"通关阵容"可见，需要重新点挑战')
                            # 留在当前状态，由外层循环处理
                            return False
            self._debug_screenshot('no_battle')
            self.log('⚠ 未进入战斗，尝试恢复...')
            self._recover_to_idle_stage()
            return False
        return True

    def _wait_for_text(self, keyword, timeout=5, label='', img=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop:
                return False
            if not self._check_pause():
                return False
            if img is None:
                items = self._ocr_screen()
            else:
                items = self._ocr_screen(img)
                img = None  # 只用一次传入的img，后续重新截图
            texts = [t for _, _, t, c, *_ in items if c >= 0.5]
            if any(keyword in t for t in texts):
                self.log(f'✓ {label}: 检测到"{keyword}"')
                return True
            time.sleep(1)
        self.log(f'✗ {label}: {timeout}s内未检测到"{keyword}"')
        return False

    def _recover_to_idle_stage(self):
        """恢复到挂机推图流程（不要求回到主界面，检测到任意可用状态即继续）

        可用状态优先级：
        1. 挂机推图主界面（幻灵挑战/挑战可见）
        2. 备战界面（通关阵容/自动挑战可见）→ 继续推进
        3. 玩法目录界面 → 继续导航
        4. 主界面 → 全导航流程
        """
        self.stats['recovery_count'] = self.stats.get('recovery_count', 0) + 1
        self._push_stats()
        self.log('⚠ 尝试恢复...')

        # 预检测当前状态
        phase = self.detect_phase()
        if phase == 'main':
            self.log('✓ 已在挂机推图主界面')
            return True
        if phase == 'formation':
            self.log('✓ 已在备战界面（继续推进）')
            return True
        if phase == 'playbook':
            self.log('✓ 在玩法目录界面，继续导航')
            return self.navigate_to_idle_stage()
        if phase in ('battle', 'result', 'town'):
            self.log(f'✓ 当前状态={phase}，ESC回退')
            self._send_esc()
            time.sleep(1.5)

        # ESC后再次检测
        phase2 = self.detect_phase()
        if phase2 == 'main':
            self.log('✓ ESC后已在挂机推图主界面')
            return True
        if phase2 == 'formation':
            self.log('✓ ESC后已在备战界面')
            return True
        if phase2 == 'playbook':
            self.log('✓ ESC后在玩法目录，继续导航')
            return self.navigate_to_idle_stage()
        if phase2 == 'battle':
            self.log('仍在战斗中，等待...')
            return True  # 让外层循环处理战斗结束

        # 兜底：完整导航流程
        if not self.navigate_to_idle_stage():
            self.log('!! 恢复失败')
            return False
        self.log('✓ 恢复成功')
        return True

    def _detect_battle_stage(self, img=None):
        """战斗中检测赛季关卡数字（幻灵/挂机/先锋），返回数字或0"""
        if img is None:
            items = self._ocr_screen()
        else:
            items = self._ocr_screen(img)
        texts = [t for _, _, t, c, *_ in items if c >= 0.3]
        joined = ' '.join(texts)
        # 挂机关卡
        m = re.search(r'赛季幻灵关卡\s*(\d+)', joined)
        if m:
            return int(m.group(1))
        m = re.search(r'赛季挂机关卡\s*(\d+)', joined)
        if m:
            return int(m.group(1))
        # 先锋关卡
        m = re.search(r'幻灵先锋关卡\s*(\d+)', joined)
        if m:
            return int(m.group(1))
        m = re.search(r'赛季先锋关卡\s*(\d+)', joined)
        if m:
            return int(m.group(1))
        return 0

    def _detect_battle_mode(self, img=None):
        """战斗中判断是幻灵挑战还是挑战，返回 'huanling' / 'normal' / None"""
        if img is None:
            items = self._ocr_screen()
        else:
            items = self._ocr_screen(img)
        texts = [t for _, _, t, c, *_ in items if c >= 0.3]
        joined = ' '.join(texts)
        # 挂机关卡
        if '赛季幻灵关卡' in joined:
            return 'huanling'
        if '赛季挂机关卡' in joined:
            return 'normal'
        # 先锋关卡
        if '幻灵先锋关卡' in joined:
            return 'huanling'
        if '赛季先锋关卡' in joined:
            return 'normal'
        return None

    def wait_for_battle_end(self, current_mode='huanling'):
        self.log('等待战斗结束...')
        battle_cfg = self.config['battle']
        start_time = time.time()
        battle_started = False
        last_ocr_time = 0
        cached_texts = []
        miss_count = 0  # 连续漏检计数
        prev_battle_stage = 0  # 上次检测的赛季关卡
        # 优化：使用画面指纹减少无效OCR
        last_fp = None  # 上次画面指纹
        ocr_interval = battle_cfg['ocr_interval_initial']  # OCR间隔（秒），开始时快速检测战斗开始
        next_ocr_check = 0  # 下次OCR检查时间

        while True:
            if self._stop:
                return 'stopped'
            if not self._pause.is_set():
                if not self._check_pause():
                    return 'stopped'
            if not self._hwnd:
                self.log('游戏窗口丢失，等待恢复...')
                time.sleep(5)
                continue

            elapsed = int(time.time() - start_time)
            self.set_status(phase='battle')

            now = time.time()
            # 动态调整OCR间隔：战斗开始快速检测，min_duration秒后降低频率
            if elapsed < battle_cfg['min_duration']:
                ocr_interval = battle_cfg['ocr_interval_initial']
            else:
                ocr_interval = battle_cfg['ocr_interval_stable']

            if now >= next_ocr_check:
                # 计算当前画面指纹
                img, l, t, w, h = self._snap()
                fp = self._frame_fingerprint(img)

                if last_fp is None or fp != last_fp:
                    # 画面发生变化，执行OCR
                    last_fp = fp
                    items = self._ocr_screen(img)
                    cached_texts = [t for _, _, t, c, *_ in items if c >= 0.5]
                    # 低置信度文本用于检测底部小字（如"自动挑战中..."）
                    low_conf_texts = [t for _, _, t, c, *_ in items if c >= 0.3]
                    next_ocr_check = now + ocr_interval

                    # 战斗中实时检测赛季关卡，推进就+1
                    battle_stage = self._detect_battle_stage(img)  # 传入img，避免重复截图
                    if prev_battle_stage > 0 and battle_stage > prev_battle_stage:
                        diff = battle_stage - prev_battle_stage
                        # 合理性校验：单次推进超过max_diff关视为OCR异常（正常一场最多几关）
                        if diff > battle_cfg['max_diff']:
                            self.log(f'  ⚠ OCR异常：赛季关卡 {prev_battle_stage} -> {battle_stage} (diff=+{diff})，忽略')
                        else:
                            self.stats['stage_progress'] += diff
                            self.stats['round_progress'] = self.stats.get('round_progress', 0) + diff
                            # 根据当前模式只更新对应的赛季关卡
                            if current_mode == 'huanling':
                                self.stats['huanling_stage'] = battle_stage
                            else:
                                self.stats['normal_stage'] = battle_stage
                            self._push_stats()
                            self.log(f'  >> 推进 +{diff} 关 (赛季关卡 {prev_battle_stage} -> {battle_stage})')
                    # 只在增量合理时才更新基准值；异常时保留上次正确值防止连锁错误
                    if battle_stage > 0:
                        if prev_battle_stage > 0 and battle_stage < prev_battle_stage:
                            pass  # 回退说明之前读高了，不更新基准等它恢复
                        else:
                            prev_battle_stage = battle_stage
                else:
                    # 画面未变化，跳过OCR，延长检查间隔
                    next_ocr_check = now + min(ocr_interval * 2, 10)  # 最多10秒检查一次

            texts = cached_texts
            # "自动挑战中"用低置信度检测（底部小字容易被截断/低置信度）
            in_battle = any('自动挑战中' in t for t in texts) or any('自动挑战中' in t for t in low_conf_texts)
            has_close = any('点击空白处关闭' in t or '点击关闭' in t for t in texts)
            has_defeat = any('失败' in t or '战败' in t for t in texts)
            has_auto_end = any('自动挑战结束' in t or '自动战斗结束' in t for t in texts)
            # 确认框：说明自动挑战还在运行，不算战斗结束
            has_confirm_dialog = any('是否关闭' in t and '自动挑战' in t for t in texts)

            if in_battle:
                battle_started = True
                miss_count = 0  # 检测到战斗中，重置漏检计数
            if has_confirm_dialog:
                # 确认框出现说明自动挑战还在运行，不算结束，重置漏检
                battle_started = True
                miss_count = 0
                self.log(f'  [{elapsed}s] 检测到"关闭自动挑战"确认框，仍在战斗中')
                # 尝试点击×取消（保持自动挑战继续）
                self._dismiss_confirm_dialog()
            if has_auto_end:
                self.log(f'✓ [{elapsed}s] 自动挑战结束')
                return 'done'
            if has_defeat:
                self.log(f'✓ [{elapsed}s] 战斗失败')
                return 'done'
            if battle_started and not in_battle and has_close:
                self.log(f'✓ [{elapsed}s] 战斗结束（检测到结算面板）')
                return 'done'
            if battle_started and not in_battle:
                miss_count += 1
                if miss_count < battle_cfg['miss_threshold']:
                    # 漏检次数未达阈值，继续等待（不输出日志避免刷屏）
                    time.sleep(2)
                    continue
                # 连续漏检达到阈值，确认战斗结束
                # 最短战斗时间保护：30秒内不承认结束（防止早期误判）
                if elapsed < battle_cfg['min_duration']:
                    self.log(f'  [{elapsed}s] 漏检{miss_count}次但战斗时间<{battle_cfg["min_duration"]}s，重置计数继续等待')
                    miss_count = 0
                    time.sleep(2)
                    continue
                self.log(f'  [{elapsed}s] 连续{miss_count}次未检测到"自动挑战中"，确认战斗结束')
                # 最终确认：再查一次是否真的结束了
                items2 = self._ocr_screen()
                texts2 = [t for _, _, t, c, *_ in items2 if c >= 0.5]
                low_conf2 = [t for _, _, t, c, *_ in items2 if c >= 0.3]
                if any('失败' in t or '战败' in t for t in texts2):
                    self.log('✓ 确认战斗结束（失败/战败）')
                    return 'done'
                if any('点击空白处关闭' in t or '点击关闭' in t for t in texts2):
                    self.log('✓ 确认战斗结束（结算面板）')
                    return 'done'
                if any('自动战斗结束' in t or '自动挑战结束' in t for t in texts2):
                    self.log('✓ 确认战斗结束（自动结束面板）')
                    return 'done'
                # 确认框：说明还在战斗中
                if any('是否关闭' in t and '自动挑战' in t for t in texts2):
                    self.log(f'  [{elapsed}s] 最终确认：检测到确认框，仍在战斗中')
                    miss_count = 0
                    battle_started = True
                    self._dismiss_confirm_dialog()
                    time.sleep(2)
                    continue
                # 用低置信度再检查一次"自动挑战中"
                if any('自动挑战中' in t for t in texts2) or any('自动挑战中' in t for t in low_conf2):
                    # 确认仍在战斗中，之前的漏检是误判
                    self.log(f'  [{elapsed}s] 最终确认仍在战斗中，重置漏检计数')
                    miss_count = 0
                    battle_started = True
                    time.sleep(2)
                    continue
                # 既没战斗也没结算，可能画面异常，继续等待
                self._debug_screenshot('battle_end_uncertain')
                # 检查是否已回到主界面（用户手动跳过了结算）
                if any('幻灵挑战' in t for t in texts2) or any(t == '挑战' for t in texts2):
                    self.log('✓ 确认战斗结束（已回到主界面）')
                    return 'done'
                self.log('⚠ 战斗状态不确定，继续等待...')
                # 不确定时不返回done，重置计数避免立即再次触发
                miss_count = 0
                time.sleep(2)
                continue
            time.sleep(1)

    def _dismiss_confirm_dialog(self):
        """关闭"是否关闭自动挑战模式？"确认框（点×取消，保持自动挑战继续）"""
        img, l, t, w, h = self._snap()
        items = self._ocr_img(img)
        y_min = int(0.4 * h)  # 确认框在屏幕中下部
        for cy, cx, text, conf, *_ in items:
            if conf >= 0.4 and ('×' in text or 'X' in text) and cy > y_min:
                self._click_abs(l + cx, t + cy)
                self.log(f'  [确认框] 点击×关闭 → ({l+cx},{t+cy})')
                return
        # 兜底：按ESC取消
        self._send_esc()
        self.log('  [确认框] ESC取消')

    def step_close_result(self, max_attempts=None):
        self.log('关闭结算面板...')
        if max_attempts is None:
            max_attempts = self.config['battle']['max_attempts_close_result']
        for attempt in range(max_attempts):
            if self._stop:
                return False
            if not self._check_pause():
                return False
            img, l, t, w, h = self._snap()
            items = self._ocr_img(img)
            texts = [t for _, _, t, c, *_ in items if c >= 0.5]
            # 优先检查确认框（自动挑战还在运行时点击会弹出）
            if any('是否关闭' in t and '自动挑战' in t for t in texts):
                self.log('  检测到"关闭自动挑战"确认框，先取消')
                self._dismiss_confirm_dialog()
                time.sleep(1)
                continue
            if any('幻灵挑战' in t for t in texts) or any(t == '挑战' for t in texts):
                self.log('✓ 已回到挂机推图主界面')
                return True
            has_close = any('点击空白处关闭' in t or '点击关闭' in t for t in texts)
            has_defeat = any('失败' in t or '战败' in t for t in texts)
            has_auto_end = any('自动挑战结束' in t or '自动战斗结束' in t for t in texts)
            if has_close or has_defeat or has_auto_end:
                # 有文字提示"点击空白处关闭" → 直接点空白处
                blank_cx = l + int(w * 0.2)
                blank_cy = t + int(h * 0.2)
                self._click_abs(blank_cx, blank_cy)
                self.log(f'点击: 空白处关闭 → ({blank_cx},{blank_cy})')
                time.sleep(2)
                continue
            y_min = int(0.4 * h)  # 跳过顶部UI，窗口下半部才是弹窗
            closed = False
            for cy, cx, text, conf, *_ in items:
                if conf >= 0.5 and '×' in text and cy > y_min:
                    self._click_abs(l + cx, t + cy)
                    self.log(f'点击: ×关闭 → ({l+cx},{t+cy})')
                    time.sleep(2)
                    closed = True
                    break
            if not closed:
                # 兜底：空白处
                if attempt < max_attempts - 1:
                    blank_cx = l + int(w * 0.2)
                    blank_cy = t + int(h * 0.2)
                    self._click_abs(blank_cx, blank_cy)
                    self.log(f'点击: 空白处（尝试） → ({blank_cx},{blank_cy})')
                    time.sleep(2)
        return False

    # ── 卡住检测 ───────────────────────────────────────────────────

    def _frame_fingerprint(self, img=None):
        """快速画面指纹：采样少量像素的哈希值，比OCR快100倍"""
        if img is None:
            img, l, t, w, h = self._snap()
        # 使用图片尺寸（不是窗口尺寸）
        w, h = img.width, img.height
        arr = np.array(img.convert('RGB'))
        # 采样 10 个点：左上/右上/左下/右下/中心等
        sample_pts = [
            (h // 4, w // 4), (h // 4, 3 * w // 4),
            (3 * h // 4, w // 4), (3 * h // 4, 3 * w // 4),
            (h // 2, w // 2),
            (h // 3, w // 3), (h // 3, 2 * w // 3),
            (2 * h // 3, w // 3), (2 * h // 3, 2 * w // 3),
            (h // 5, w // 5),
        ]
        pixels = tuple(tuple(arr[y, x].tolist()) for y, x in sample_pts)
        return hash(pixels)

    def _detect_current_stage(self):
        """检测当前关卡数（通过红底白字识别）

        原理：关卡列表中，当前可打的关卡是红底白字（中心偏红），
        其他是白底黑字。取 OCR 数字的中心像素颜色判断。
        返回当前关卡数字（如 1973），检测失败返回 None。
        """
        img, l, t, w, h = self._snap()
        items = self._ocr_img(img)
        arr = np.array(img.convert('RGB'))

        for cy, cx, text, conf, *_ in items:
            if conf < 0.5:
                continue
            if not (text.isdigit() and 3 <= len(text) <= 5):
                continue
            # 过滤：只保留 y 在 300~700 的数字（关卡区域）
            if not (300 <= cy <= 700):
                continue
            r, g, b = arr[cy, cx]
            # 红底白字：中心颜色偏红（R > G+20 且 R > B+20）
            if (r > g + 20) and (r > b + 20):
                return int(text)
        return None

    def _detect_season_stages(self):
        """检测备战界面的赛季关卡数（幻灵/挂机/先锋）

        挂机关卡备战界面显示：赛季幻灵关卡 X / 赛季挂机关卡 X
        先锋关卡备战界面显示：幻灵先锋关卡 X / 赛季先锋关卡 X
        返回 (huanling, normal) 或 (0, 0) 如果检测失败。
        """
        items = self._ocr_screen()

        huanling = 0
        normal = 0

        for item in items:
            t, c = item[2], item[3]
            if c < 0.4:
                continue
            # 幻灵挑战关卡（挂机或先锋）
            if '赛季幻灵' in t or '幻灵先锋' in t:
                nums = re.findall(r'\d+', t)
                if nums:
                    huanling = int(nums[-1])
            # 挑战关卡（挂机或先锋）
            elif '赛季挂机' in t or '赛季先锋' in t:
                nums = re.findall(r'\d+', t)
                if nums:
                    normal = int(nums[-1])

        return huanling, normal

    def _record_pre_battle_stage(self, current_mode, mode_prev_stage):
        """记录进入战斗前的赛季关卡，写入 mode_prev_stage[current_mode]"""
        stats = self.stats
        # 备战界面 OCR 作为主数据源
        hl, nm = self._detect_season_stages()
        if hl > 0:
            stats['huanling_stage'] = hl
        if nm > 0:
            stats['normal_stage'] = nm
        if hl > 0 or nm > 0:
            # 当前关卡 = max(幻灵, 挂机)
            # OCR 读不到的模式用已保存的旧值填充，避免日志显示"挂机 0"
            display_hl = hl if hl > 0 else stats.get('huanling_stage', 0)
            display_nm = nm if nm > 0 else stats.get('normal_stage', 0)
            stats['current_stage'] = max(hl, nm, display_hl, display_nm)
            self.log(f'  赛季: 幻灵 {display_hl} + 挂机 {display_nm}（当前 {stats["current_stage"]}）')

        # 写入 mode_prev_stage[current_mode]，用于战后计算推进数
        if current_mode == 'huanling':
            mode_prev_stage['huanling'] = stats.get('huanling_stage', 0)
        else:
            mode_prev_stage['normal'] = stats.get('normal_stage', 0)

        self._push_stats()

    def _try_recover_from_stuck(self):
        self.stats['recovery_count'] = self.stats.get('recovery_count', 0) + 1
        self._push_stats()
        self._debug_screenshot('stuck')
        self.log('⚠ 检测到画面无变化，尝试恢复...')
        self.esc_to_main_screen()
        time.sleep(1)
        return self.navigate_to_idle_stage()

    def _push_stats(self):
        self.set_status(stats=dict(self.stats))

    # ── 主循环 ─────────────────────────────────────────────────────

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            self.log(f'!! 致命错误: {e}')
            traceback.print_exc()
        finally:
            self.set_status(running=False)

    def _run_inner(self):
        # 状态机图（主循环）：
        # [init] → [detect_phase] → [导航/等待] → [点挑战] → [等待战斗] → [关闭结算] → [下一轮]
        #            ↓
        #     phase=? 分支：
        #      - battle  → wait_for_battle_end()
        #      - result  → step_close_result()
        #      - loading → 等待加载
        #      - login   → 停止脚本
        #      - options_menu → ESC关闭
        #      - quest_panel → ESC关闭
        #      - unknown  → ESC退回 → 重新detect
        self._stop = False
        self._pause.set()
        self.set_status(running=True, round=0, mode='', phase='init',
                        script_start=time.time(), battle_start=0)

        cfg = self._cfg
        bot_cfg = self.config
        stuck_cfg = bot_cfg['stuck']
        battle_cfg = bot_cfg['battle']
        max_rounds = cfg.get('max_rounds', 0)
        run_mode = cfg.get('run_mode', 'alternate')

        self.log('=' * 50)
        self.log(f'AFK Journey 自动化 {VERSION}')
        self.log(f'MaaFramework Pipeline 架构')
        self.log(f'最大轮数: {"无限" if max_rounds == 0 else max_rounds}')
        self.log(f'运行模式: '
                 + {'alternate': '交替', 'huanling': '只跑幻灵挑战', 'normal': '只跑挑战'}.get(run_mode, run_mode))
        self.log('=' * 50)

        keyboard.add_hotkey('f12', self.stop)

        self._hwnd = self.find_and_focus()
        if not self._hwnd:
            self.log('未找到 AFK Journey 窗口，请先启动游戏')
            return
        self.log(f'游戏窗口 hwnd={self._hwnd}')
        cl_left, cl_top, cl_right, cl_bottom = win32gui.GetClientRect(self._hwnd)
        cl_w, cl_h = cl_right - cl_left, cl_bottom - cl_top
        l, t = win32gui.ClientToScreen(self._hwnd, (0, 0))
        self.log(f'游戏画面: {l},{t} → {cl_w}x{cl_h}')

        round_count = 0
        current_mode = 'huanling'
        mode_fail_count = {'huanling': 0, 'normal': 0}  # 连续失败计数
        mode_formation_idx = {'huanling': 0, 'normal': 0}  # 阵容索引
        # 从持久化的 stats 恢复战前关卡
        mode_prev_stage = {
            'huanling': self.stats.get('huanling_stage', 0),
            'normal': self.stats.get('normal_stage', 0),
        }
        stuck_recovery_attempts = 0

        phase = self.detect_phase()
        self.log(f'当前阶段: {phase}')

        if phase in ('town', 'playbook', 'unknown'):
            self.log('尝试导航到挂机推图...')
            if not self.navigate_to_idle_stage():
                self._debug_screenshot('nav_failed_init')
                self.log('无法导航到挂机推图，退出')
                return
            phase = 'main'
            self.log('✓ 导航成功')

        def _next_mode(cur):
            if run_mode == 'alternate':
                return 'normal' if cur == 'huanling' else 'huanling'
            return run_mode

        def _do_battle():
            nonlocal stuck_recovery_attempts, current_mode
            self.set_status(phase='battle', battle_start=time.time())
            battle_start = time.time()
            result = self.wait_for_battle_end(current_mode)
            battle_elapsed = int(time.time() - battle_start)

            prev_mode = current_mode
            closed = self.step_close_result()

            stats = self.stats
            if prev_mode == 'huanling':
                stats['huanling_wins'] += 1
            else:
                stats['normal_wins'] += 1
            stats['total_battles'] += 1

            # 战斗结束后用备战界面 OCR 重新计算当前关卡
            if closed:
                hl, nm = self._detect_season_stages()
                if hl > 0:
                    stats['huanling_stage'] = hl
                if nm > 0:
                    stats['normal_stage'] = nm
                if hl > 0 or nm > 0:
                    stats['current_stage'] = max(hl, nm)
                    self.log(f'  当前关卡: {stats["current_stage"]}（赛季: 幻灵 {hl} + 挂机 {nm}）')

            current_mode = _next_mode(current_mode)
            self.log(f'切换: {"幻灵挑战" if prev_mode=="huanling" else "挑战"} → '
                     + f'{"幻灵挑战" if current_mode=="huanling" else "挑战"}')

            # 判断本轮是否推进了关卡（比战斗时长更准确）
            prev_stage = mode_prev_stage.get(prev_mode, 0)
            curr_stage_key = 'huanling_stage' if prev_mode == 'huanling' else 'normal_stage'
            curr_stage = stats.get(curr_stage_key, 0)
            advanced = (prev_stage > 0 and curr_stage > prev_stage)
            if advanced:
                diff = curr_stage - prev_stage
                # round_progress 已在战斗循环中实时更新，此处不再重复加
                self.log(f'  ✓ [{prev_mode}] 推进 +{diff} 关（round_progress={stats["round_progress"]}）')
                # 成功后重置失败计数，恢复默认阵容
                mode_fail_count[prev_mode] = 0
                if mode_formation_idx[prev_mode] > 0:
                    lbl = '幻灵挑战' if prev_mode == 'huanling' else '挑战'
                    self.log(f'  ✓ [{lbl}] 正常通关，恢复默认阵容')
                    mode_formation_idx[prev_mode] = 0
            else:
                # 未推进，失败计数+1
                mode_fail_count[prev_mode] = mode_fail_count.get(prev_mode, 0) + 1
                cnt = mode_fail_count[prev_mode]
                lbl = '幻灵挑战' if prev_mode == 'huanling' else '挑战'
                self.log(f'  ✗ [{lbl}] 未推进关卡（失败 x{cnt}）')
                if cnt >= 2:
                    # 连续失败，切换阵容
                    mode_formation_idx[prev_mode] += 1
                    fidx = mode_formation_idx[prev_mode]
                    self.log(f'  ⚠ 连续{cnt}轮失败，切换到推荐阵容第{fidx}套')
                    if fidx >= 5:
                        mode_formation_idx[prev_mode] = 0
                        mode_fail_count[prev_mode] = 0
                        self.log(f'  ⚠ 已循环5套阵容，重置')

            self._push_stats()
            return 'done'

        # 启动时处理已在战斗/结算/备战
        if phase == 'battle':
            # 通过 HUD 文字判断当前是幻灵挑战还是挑战
            battle_mode = self._detect_battle_mode()
            if battle_mode:
                self.log(f'已在战斗中（{battle_mode}），等待结束...')
                current_mode = battle_mode
            else:
                self.log('已在战斗中（模式未知），等待结束...')
            # 战斗已在进行中，无法从备战界面获取战前关卡
            # 直接设置 mode_prev_stage 为当前实时值，战斗结束时不计入本轮推进
            mode_prev_stage[current_mode] = self.stats.get(
                'huanling_stage' if current_mode == 'huanling' else 'normal_stage', 0)
            result = _do_battle()
            if result == 'stopped' or self._stop:
                return
            # 启动时已在战斗中，战斗结束后也要切换模式
            current_mode = _next_mode(current_mode)
            round_count = 1

        elif phase == 'result':
            self.log('在结算界面，关闭...')
            self.step_close_result()
            # 结算界面说明上一轮已完成，切换模式
            current_mode = _next_mode(current_mode)
            self.log(f'切换: {"幻灵挑战" if current_mode=="huanling" else "挑战"}')
            round_count = 1

        elif phase == 'formation':
            self.log('在备战界面，开始战斗...')
            # 记录进入战斗前的赛季关卡
            self._record_pre_battle_stage(current_mode, mode_prev_stage)
            self.step_click_formation(mode_formation_idx.get(current_mode, 0))
            if self._stop or not self._check_pause():
                return
            if not self.step_click_auto_challenge():
                # 未进入战斗，已恢复到挂机推图，跳过本轮等待
                # 直接return，下次run()会重新检测phase
                self.log('⚠ 未进入战斗，已恢复，等待下次循环')
                return
            if self._stop or not self._check_pause():
                return
            result = _do_battle()
            if result == 'stopped' or self._stop:
                return
            # 启动时在备战界面，战斗结束后也要切换模式
            current_mode = _next_mode(current_mode)
            round_count = 1

        # ─── 主循环 ───
        while not self._stop:
            if max_rounds > 0 and round_count >= max_rounds:
                self.log(f'已完成 {round_count} 轮，达到上限，停止')
                break

            while not self._pause.is_set() and not self._stop:
                time.sleep(0.2)

            round_count += 1
            self.stats['round_progress'] = 0  # 每轮开始时重置
            mode_label = '幻灵挑战' if current_mode == 'huanling' else '挑战'
            self.set_status(round=round_count, mode=mode_label, phase='idle_stage',
                            stats=dict(self.stats))

            self.log(f'\n{"─"*40}')
            self.log(f'第 {round_count} 轮 [{mode_label}]')

            stuck_recovery_attempts = 0

            try:
                self.set_status(phase='navigating', battle_start=0)

                phase = self.detect_phase()
                self.log(f'当前阶段: {phase}')

                # loading 阶段：等待加载完成
                while phase == 'loading' and not self._stop:
                    self.log('加载中，等待...')
                    time.sleep(2)
                    phase = self.detect_phase()
                    self.log(f'当前阶段: {phase}')

                # login 阶段：断线，停止脚本
                if phase == 'login':
                    self.log('⚠ 检测到登录界面，游戏可能断线，停止脚本')
                    self._stop = True
                    break

                # options_menu 阶段：ESC关闭菜单，重新检测
                if phase == 'options_menu':
                    self.log('在选项菜单，ESC关闭...')
                    self._send_esc()
                    time.sleep(1)
                    phase = self.detect_phase()
                    self.log(f'关闭菜单后当前阶段: {phase}')

                # battle 阶段：等待战斗结束，不要导航（导航无效，ESC会弹确认框）
                if phase == 'battle':
                    self.log('战斗中，等待结束...')
                    self.wait_for_battle_end(current_mode)
                    self.step_close_result()
                    phase = self.detect_phase()
                    self.log(f'战斗结束，当前阶段: {phase}')

                # 非 main 界面导航
                if phase != 'main':
                    self.log(f'不在挂机推图界面（{phase}），导航中...')
                    if not self.navigate_to_idle_stage():
                        self._debug_screenshot('nav_failed_loop')
                        self.log('导航失败，ESC返回重试...')
                        self.esc_to_main_screen()
                        if not self.navigate_to_idle_stage():
                            self.log('导航再次失败，跳过')
                            continue


                self.set_status(phase='formation')

                # 点击前再次确认在 main 界面
                phase = self.detect_phase()
                # loading 阶段：等待加载完成
                while phase == 'loading' and not self._stop:
                    self.log('加载中，等待...')
                    time.sleep(2)
                    phase = self.detect_phase()
                    self.log(f'当前阶段: {phase}')
                # login 阶段：断线，停止脚本
                if phase == 'login':
                    self.log('⚠ 检测到登录界面，游戏可能断线，停止脚本')
                    self._stop = True
                    break
                # options_menu 阶段：ESC关闭菜单，重新检测
                if phase == 'options_menu':
                    self.log('在选项菜单，ESC关闭...')
                    self._send_esc()
                    time.sleep(1)
                    phase = self.detect_phase()
                    self.log(f'关闭菜单后当前阶段: {phase}')
                # 如果检测到确认框，先关闭再重试
                if any('是否关闭' in t for t in [t2 for _, _, t2, c, *_ in self._ocr_screen() if c >= 0.5]):
                    self.log('⚠ 检测到"关闭自动挑战"确认框，取消后重试')
                    self._dismiss_confirm_dialog()
                    time.sleep(1)
                    continue
                # battle 阶段：等待战斗结束，不要导航
                if phase == 'battle':
                    self.log('战斗中，等待结束...')
                    result = self.wait_for_battle_end(current_mode)
                    if result == 'stopped' or self._stop:
                        break
                    self.step_close_result()
                    phase = self.detect_phase()
                    self.log(f'战斗结束，当前阶段: {phase}')
                if phase != 'main':
                    self.log(f'⚠ 点击前检测到界面为 {phase}，重新导航...')
                    if not self.navigate_to_idle_stage():
                        self.log('导航失败，跳过本轮')
                        continue

                btn_ok = self.step_click_challenge_btn(mode=current_mode)
                if self._stop or not self._check_pause():
                    break
                if not btn_ok:
                    # 当前挑战类型找不到入口（可能已通关）
                    other_mode = 'normal' if current_mode == 'huanling' else 'huanling'
                    other_label = '挑战' if other_mode == 'normal' else '幻灵挑战'
                    cur_label = '幻灵挑战' if current_mode == 'huanling' else '挑战'
                    # 检测另一种是否存在
                    items_chk = self._ocr_screen()
                    if other_mode == 'huanling':
                        other_pos = self.find_text_exact(items_chk, '幻灵挑战')
                    else:
                        other_pos = self.find_text_exclude(items_chk, '挑战', exclude_keywords=['幻灵'])
                    if other_pos:
                        self.log(f'⚡ [{cur_label}] 入口消失（已通关），自动切换为 [{other_label}] 模式')
                        current_mode = other_mode
                        run_mode = other_mode   # 固定只跑这种，不再交替
                        continue
                    else:
                        self.log(f'🎉 [{cur_label}] 和 [{other_label}] 入口均消失，赛季挂机关卡已全通关，停止')
                        self._stop = True
                        break

                # ── 卡住检测 ──
                base_fp = self._frame_fingerprint()
                same_count = 0
                while True:
                    if self._stop:
                        break
                    if not self._pause.is_set():
                        self._pause.wait()
                        if self._stop:
                            break
                    fp = self._frame_fingerprint()
                    if fp != base_fp:
                        same_count = 0
                        base_fp = fp
                        break
                    same_count += 1
                    self.log(f'  卡住检测: {same_count}/{stuck_cfg['threshold']}')
                    if same_count >= stuck_cfg['threshold']:
                        stuck_recovery_attempts += 1
                        self.log(f'⚠ 画面已 {same_count * stuck_cfg['sample_interval']}s 无变化，第 {stuck_recovery_attempts} 次恢复')
                        recovered = self._try_recover_from_stuck()
                        if recovered:
                            self.log('✓ 恢复成功，继续')
                            stuck_recovery_attempts = 0
                            same_count = 0
                            base_fp = self._frame_fingerprint()
                            time.sleep(2)
                            break
                        else:
                            self.log(f'✗ 恢复失败（第 {stuck_recovery_attempts}/{stuck_cfg['max_recovery_attempts']} 次）')
                            if stuck_recovery_attempts >= stuck_cfg['max_recovery_attempts']:
                                self.log('!! 连续恢复失败，停止脚本')
                                self._stop = True
                                break
                            same_count = 0
                            base_fp = self._frame_fingerprint()
                            time.sleep(2)
                    else:
                        time.sleep(stuck_cfg['sample_interval'])
                if self._stop:
                    break

                time.sleep(1)

                # 点击前再次确认在 formation 界面
                phase = self.detect_phase()
                # loading 阶段：等待加载完成
                while phase == 'loading' and not self._stop:
                    self.log('加载中，等待...')
                    time.sleep(2)
                    phase = self.detect_phase()
                    self.log(f'当前阶段: {phase}')
                # login 阶段：断线，停止脚本
                if phase == 'login':
                    self.log('⚠ 检测到登录界面，游戏可能断线，停止脚本')
                    self._stop = True
                    break
                # options_menu 阶段：ESC关闭菜单，重新检测
                if phase == 'options_menu':
                    self.log('在选项菜单，ESC关闭...')
                    self._send_esc()
                    time.sleep(1)
                    phase = self.detect_phase()
                    self.log(f'关闭菜单后当前阶段: {phase}')
                # 如果检测到确认框，先关闭再重试
                if any('是否关闭' in t for t in [t2 for _, _, t2, c, *_ in self._ocr_screen() if c >= 0.5]):
                    self.log('⚠ 点击阵容前检测到确认框，取消后重试')
                    self._dismiss_confirm_dialog()
                    time.sleep(1)
                    continue
                # battle 阶段：等待战斗结束，不要导航
                if phase == 'battle':
                    self.log('战斗中，等待结束...')
                    result = self.wait_for_battle_end(current_mode)
                    if result == 'stopped' or self._stop:
                        break
                    self.step_close_result()
                    phase = self.detect_phase()
                    self.log(f'战斗结束，当前阶段: {phase}')
                if phase != 'formation':
                    self.log(f'⚠ 点击阵容前检测到界面为 {phase}，重新导航...')
                    if not self.navigate_to_idle_stage():
                        self.log('导航失败，跳过本轮')
                        continue
                    # 重新点击 challenge 按钮
                    phase = self.detect_phase()
                    if phase == 'main':
                        self.step_click_challenge_btn(mode=current_mode)
                    else:
                        self.log(f'未能进入 main 界面({phase})，跳过本轮')
                        continue

                formation_index = mode_formation_idx[current_mode]
                self.step_click_formation(formation_index)
                if self._stop or not self._check_pause():
                    break

                time.sleep(1)
                for _ in range(stuck_cfg['threshold']):
                    if self._stop:
                        break
                    if not self._check_pause():
                        break
                    fp = self._frame_fingerprint()
                    if fp != base_fp:
                        same_count = 0
                        base_fp = fp
                        break
                    same_count += 1
                    self.log(f'  卡住检测: {same_count}/{stuck_cfg['threshold']}')
                    if same_count >= stuck_cfg['threshold']:
                        stuck_recovery_attempts += 1
                        self.log(f'⚠ 画面已 {same_count * stuck_cfg['sample_interval']}s 无变化，第 {stuck_recovery_attempts} 次恢复')
                        recovered = self._try_recover_from_stuck()
                        if recovered:
                            self.log('✓ 恢复成功，从头开始本轮')
                            stuck_recovery_attempts = 0
                            break
                        else:
                            self.log(f'✗ 恢复失败（第 {stuck_recovery_attempts}/{stuck_cfg['max_recovery_attempts']} 次）')
                            if stuck_recovery_attempts >= stuck_cfg['max_recovery_attempts']:
                                self._stop = True
                                break
                            time.sleep(2)
                    else:
                        time.sleep(stuck_cfg['sample_interval'])
                if self._stop:
                    break

                # 记录进入战斗前的赛季关卡（写入 mode_prev_stage[current_mode]）
                self._record_pre_battle_stage(current_mode, mode_prev_stage)

                battle_started = self.step_click_auto_challenge()
                if self._stop or not self._check_pause():
                    break

                if not battle_started:
                    # step_click_auto_challenge 返回 False 表示未进入战斗，已恢复
                    # 跳过 wait_for_battle_end，直接进入下一轮
                    continue

                result = _do_battle()
                if result == 'stopped' or self._stop:
                    break

            except Exception as e:
                self._debug_screenshot(f'round{round_count}_error')
                self.log(f'!! 第 {round_count} 轮异常: {e}')
                traceback.print_exc()
                self.log('尝试恢复...')
                try:
                    self.esc_to_main_screen()
                    time.sleep(1)
                except Exception as e:
                    self.log(f"[WARN] esc_to_main_screen失败: {e}")

        self.log(f'\n{"="*50}')
        self.log(f'已停止  共 {round_count} 轮')
        keyboard.unhook_all()


# ═══════════════════════════════════════════════════════════════════
#  GUI 界面 — MaaEnd 风格极简布局
# ═══════════════════════════════════════════════════════════════════

# 配色 — 现代极简深蓝+青色系（参考 v1-modern.html）
BG = '#0f172a'          # 主背景 slate-900
BG_HEADER = '#0f172a'   # 顶栏 slate-900
BG_CARD = '#1e293b'     # 卡片 slate-800
BG_DRAWER = '#0f172a'   # 抽屉 slate-900
BG_INPUT = '#334155'     # 输入框 slate-700
BG_BTN = '#1e293b'      # 按钮 slate-800
BG_BTN_HOVER = '#334155' # 按钮悬停 slate-700
BG_LOG = '#0a0f1a'      # 日志背景
ACCENT = '#06b6d4'       # 主色 cyan-500
ACCENT_DIM = '#0891b2'   # 主色暗 cyan-600
TEXT = '#94a3b8'         # 正文 slate-400
TEXT_DIM = '#64748b'     # 次要 slate-500
TEXT_BRIGHT = '#f8fafc'  # 高亮 slate-50
SUCCESS = '#10b981'      # 绿 emerald-500
ORANGE = '#f59e0b'       # 橙 amber-500
PURPLE = '#8b5cf6'       # 紫 violet-500
DANGER = '#f43f5e'       # 红 rose-500
WARN = '#f59e0b'         # 黄 amber-500


class App(ctk.CTk):
    f"""MaaEnd 风格极简界面 {VERSION}"""

    def __init__(self):
        super().__init__()

        # 窗口
        self.title('AFK Journey')
        self.geometry('440x720')
        self.minsize(380, 560)
        self.configure(fg_color=BG)
        self.attributes('-topmost', True)
        self.bind('<Escape>', lambda e: self._close_drawer() if self._drawer_open else None)
        self.bind('<F10>', lambda e: self._on_pause())
        self.bind('<F11>', lambda e: self._on_start())

        # 文件日志（追加模式，UTF-8）
        _log_dir = os.path.dirname(os.path.abspath(__file__))
        _log_path = os.path.join(_log_dir, '_afk_gui_log.txt')
        self._log_file = open(_log_path, 'a', encoding='utf-8', buffering=1)  # line-buffered

        # 字体
        self._f_title = ('Microsoft YaHei UI', 16, 'bold')
        self._f_sub = ('Microsoft YaHei UI', 14)
        self._f_small = ('Microsoft YaHei UI', 12)
        self._f_mono = ('Consolas', 12)
        self._f_stats = ('Consolas', 18, 'bold')
        self._f_stats_label = ('Microsoft YaHei UI', 10)

        # 状态
        self._bot = None
        self._bot_thread = None
        self._log_queue = queue.Queue()
        self._running = False
        self._paused = False
        self._drawer_open = False

        self._status = {
            'round': 0, 'mode': '', 'phase': '', 'battle_time': 0,
            'stats': {
                'huanling_wins': 0, 'normal_wins': 0,
                'huanling_times': [], 'normal_times': [],
                'total_battles': 0, 'recovery_count': 0,
                'stage_progress': 0,      # 总推进关卡数
                'round_progress': 0,       # 本轮推进关卡数
                'current_stage': 0,        # 当前关卡
                'huanling_stage': 0,       # 赛季幻灵关卡
                'normal_stage': 0,         # 赛季挂机关卡
            }
        }

        self._last_stats = {}  # 用于检测数字变化，触发闪烁动画

        self._cfg = {
            'max_rounds': 0,
            'run_mode': 'alternate',
            'log_callback': None,
            'status_callback': None,
        }

        self._build_ui()
        self._flush_id = None
        self._timer_id = None
        self._start_flush_timer()

    # ────────────────────────────────────────────────────────────────
    #  UI 构建
    # ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_status_bar()
        self._build_stats_panel()
        self._build_control_bar()
        self._build_log_area()
        self._build_settings_drawer()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG, height=56, corner_radius=0)
        hdr.pack(fill='x', padx=0, pady=0)
        hdr.pack_propagate(False)

        left = ctk.CTkFrame(hdr, fg_color='transparent')
        left.pack(side='left', padx=20, pady=0, fill='both', expand=True)

        # Logo 图标（圆角方块背景）
        icon_box = ctk.CTkFrame(left, fg_color=ACCENT, width=36, height=36, corner_radius=10)
        icon_box.pack(side='left', pady=10, padx=(0, 10))
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text='⚔', font=('Segoe UI Symbol', 16),
                     text_color='white', anchor='center').pack(expand=True)

        ctk.CTkLabel(left, text='AFK Journey',
                     font=self._f_title, text_color=TEXT_BRIGHT,
                     anchor='w').pack(side='left', pady=12)

        # 版本号 pill
        ctk.CTkLabel(left, text=VERSION,
                     font=('Microsoft YaHei UI', 11), text_color=TEXT_DIM,
                     fg_color=BG_CARD, corner_radius=10,
                     width=44, height=22).pack(side='left', padx=8, pady=12)

        self._btn_gear = ctk.CTkButton(
            hdr, text='⚙', font=('Segoe UI Symbol', 16),
            fg_color='transparent', hover_color=BG_CARD,
            text_color=TEXT_DIM, width=40, height=40,
            corner_radius=10,
            command=self._toggle_drawer
        )
        self._btn_gear.pack(side='right', padx=(0, 12), pady=8)

    def _build_status_bar(self):
        card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=14,
                             border_width=1, border_color=BG_INPUT)
        card.pack(fill='x', padx=16, pady=(10, 6))
        self._status_card = card
        # 上行：状态文字 + 计时器
        top = ctk.CTkFrame(card, fg_color='transparent')
        top.pack(fill='x', padx=16, pady=(10, 6))

        # 状态点容器（用于呼吸灯动画）
        self._dot_frame = ctk.CTkFrame(top, fg_color='transparent', width=16, height=16)
        self._dot_frame.pack(side='left', padx=(0, 8))
        self._dot_frame.pack_propagate(False)
        self._lbl_dot = ctk.CTkLabel(
            self._dot_frame, text='●', font=('Segoe UI Symbol', 8),
            text_color=TEXT_DIM, anchor='center'
        )
        self._lbl_dot.pack(expand=True)

        self._lbl_status = ctk.CTkLabel(
            top, text='等待启动', font=('Microsoft YaHei UI', 14, 'bold'),
            text_color=TEXT_DIM, anchor='w'
        )
        self._lbl_status.pack(side='left')

        # 右侧：计时器 + 时钟
        right = ctk.CTkFrame(top, fg_color='transparent')
        right.pack(side='right')
        self._lbl_timer = ctk.CTkLabel(
            right, text='', font=('Consolas', 12),
            text_color=ACCENT, anchor='e'
        )
        self._lbl_timer.pack(side='left', padx=(12, 0))
        self._lbl_clock = ctk.CTkLabel(
            right, text='', font=('Microsoft YaHei UI', 11),
            text_color='#475569', anchor='e'
        )
        self._lbl_clock.pack(side='left', padx=(10, 0))

    def _start_battle_anim(self):
        pass  # 动画已移除

    def _stop_battle_anim(self):
        pass  # 动画已移除

    def _build_stats_panel(self):
        # 标题
        ctk.CTkLabel(
            self, text='STATS', font=('Consolas', 10, 'bold'),
            text_color='#475569', anchor='w'
        ).pack(fill='x', padx=20, pady=(8, 4))

        grid = ctk.CTkFrame(self, fg_color='transparent')
        grid.pack(fill='x', padx=16, pady=(0, 8))
        # 三列等分，窗口变窄时三个卡片同步缩小而不是截断最后一个
        grid.columnconfigure(0, weight=1, minsize=60)
        grid.columnconfigure(1, weight=1, minsize=60)
        grid.columnconfigure(2, weight=1, minsize=60)

        self._lbl_hl = self._stat_cell(grid, '幻灵挑战', '0轮', ACCENT)
        self._lbl_hl.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        self._lbl_nm = self._stat_cell(grid, '常规挑战', '0轮', SUCCESS)
        self._lbl_nm.grid(row=0, column=1, sticky='nsew', padx=4)
        self._lbl_stage = self._stat_cell(grid, '推进关卡', '0关', ORANGE)
        self._lbl_stage.grid(row=0, column=2, sticky='nsew', padx=(4, 0))

        # ── 关卡详情面板（统一 grid，两行共享列实现垂直对齐）──────
        stage_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                  border_width=1, border_color=BG_INPUT)
        stage_card.pack(fill='x', padx=16, pady=(0, 6))

        grid = ctk.CTkFrame(stage_card, fg_color='transparent')
        grid.pack(fill='x', padx=14, pady=(8, 8))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        # 第一行：主关卡 + 本轮推进
        self._lbl_current_stage = ctk.CTkLabel(
            grid, text='--', font=('Consolas', 28, 'bold'), text_color=TEXT_BRIGHT, anchor='center'
        )
        self._lbl_current_stage.grid(row=0, column=0, sticky='ew')

        self._lbl_this_round = ctk.CTkLabel(
            grid, text='本轮推进 0 关', font=('Consolas', 13, 'bold'), text_color=ORANGE, anchor='center'
        )
        self._lbl_this_round.grid(row=0, column=1, sticky='ew')

        # 第二行：幻灵 + 常规（与第一行共享 grid，自动对齐）
        self._lbl_huanling_stage = ctk.CTkLabel(
            grid, text='幻灵 --', font=('Consolas', 12), text_color=ACCENT, anchor='center'
        )
        self._lbl_huanling_stage.grid(row=1, column=0, sticky='ew', pady=(4, 0))

        self._lbl_normal_stage = ctk.CTkLabel(
            grid, text='常规 --', font=('Consolas', 12), text_color=SUCCESS, anchor='center'
        )
        self._lbl_normal_stage.grid(row=1, column=1, sticky='ew', pady=(4, 0))

    def _stat_cell(self, parent, label, value, color):
        cell = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12, height=58,
                            border_width=1, border_color=BG_INPUT)
        cell.pack_propagate(False)
        v = ctk.CTkLabel(cell, text=value, font=('Consolas', 22, 'bold'),
                         text_color=color, anchor='center')
        v.pack(pady=(8, 0))
        l = ctk.CTkLabel(cell, text=label, font=('Microsoft YaHei UI', 10),
                         text_color='#64748b', anchor='center')
        l.pack(pady=(2, 6))
        return cell

    def _build_control_bar(self):
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.pack(fill='x', padx=16, pady=(4, 6))

        self._btn_pause = ctk.CTkButton(
            bar, text='⏸', font=('Segoe UI Symbol', 14),
            fg_color=BG_BTN, hover_color=BG_BTN_HOVER,
            text_color=TEXT_DIM, width=52, height=44, corner_radius=10,
            command=self._on_pause, state='disabled'
        )
        self._btn_pause.pack(side='left', padx=(0, 6))

        self._btn_main = ctk.CTkButton(
            bar, text='▶  启动', font=('Microsoft YaHei UI', 14, 'bold'),
            fg_color=ACCENT, hover_color='#7aa3ff',
            text_color='white', height=44, corner_radius=10,
            command=self._on_start
        )
        self._btn_main.pack(side='left', expand=True, fill='x', padx=(6, 0))

    def _build_log_area(self):
        log_hdr = ctk.CTkFrame(self, fg_color='transparent')
        log_hdr.pack(fill='x', padx=20, pady=(2, 2))

        # 清空按钮（左对齐）
        self._btn_clear_log = ctk.CTkButton(
            log_hdr, text='清空', font=('Microsoft YaHei UI', 10),
            fg_color='transparent', hover_color=BG_BTN,
            text_color='#475569', width=40, height=22, corner_radius=6,
            command=self._clear_log
        )
        self._btn_clear_log.pack(side='left')

        ctk.CTkLabel(
            log_hdr, text='LOG', font=('Consolas', 10, 'bold'),
            text_color='#475569', anchor='w'
        ).pack(side='left', padx=(6, 0))

        log_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        log_card.pack(fill='both', expand=True, padx=12, pady=(0, 8))

        self._log_text = ctk.CTkTextbox(
            log_card,
            font=self._f_mono,
            fg_color='#090912',
            text_color='#9090b0',
            border_width=0,
            scrollbar_button_color=BG_BTN,
            scrollbar_button_hover_color=BG_BTN_HOVER,
            wrap='word'
        )
        self._log_text.pack(fill='both', expand=True, padx=6, pady=6)
        self._log_text.configure(state='disabled')

        self._log_text.tag_config('ts', foreground='#475569')
        self._log_text.tag_config('ok', foreground=SUCCESS)
        self._log_text.tag_config('warn', foreground=WARN)
        self._log_text.tag_config('err', foreground=DANGER)
        self._log_text.tag_config('sep', foreground=BG_BTN)
        self._log_text.tag_config('progress', foreground=ORANGE)    # 推进 / >>
        self._log_text.tag_config('season', foreground=ACCENT)      # 赛季
        self._log_text.tag_config('phase', foreground='#e2e8f0')  # 阶段文字

    # ────────────────────────────────────────────────────────────────
    #  设置抽屉
    # ────────────────────────────────────────────────────────────────

    def _build_settings_drawer(self):
        # 抽屉用 place 绝对定位，从右侧覆盖进来
        self._drawer = tk.Frame(self, bg=BG_DRAWER, width=290)
        self._drawer.place_forget()

        inner = ctk.CTkFrame(self._drawer, fg_color=BG_DRAWER, corner_radius=0)
        inner.pack(fill='both', expand=True)

        self._drawer_inner = inner

        # 标题栏
        drawer_hdr = ctk.CTkFrame(self._drawer_inner, fg_color=BG_HEADER, height=44, corner_radius=0)
        drawer_hdr.pack(fill='x', padx=0, pady=0)
        drawer_hdr.pack_propagate(False)

        ctk.CTkLabel(drawer_hdr, text='⚙ 设置',
                     font=self._f_sub, text_color=TEXT_BRIGHT,
                     anchor='w').pack(side='left', padx=14, pady=8)

        self._btn_drawer_close = ctk.CTkButton(
            drawer_hdr, text='✕', font=('Segoe UI Symbol', 16),
            fg_color='transparent', hover_color=BG_BTN,
            text_color=TEXT_DIM, width=44, height=44, corner_radius=6,
            command=self._toggle_drawer
        )
        self._btn_drawer_close.pack(side='right', padx=6, pady=4)

        # 可滚动内容区
        drawer_content = ctk.CTkScrollableFrame(
            self._drawer_inner, fg_color='transparent',
            scrollbar_button_color=BG_BTN,
            scrollbar_button_hover_color=BG_BTN_HOVER
        )
        drawer_content.pack(fill='both', expand=True, padx=0, pady=0)
        drawer_content._scrollbar.configure(width=4)

        pad = {'padx': 14, 'pady': (10, 0), 'anchor': 'w', 'fill': 'x'}

        # ── ① 最大轮数 ──
        ctk.CTkLabel(drawer_content, text='最大轮数',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)
        ctk.CTkLabel(drawer_content, text='0 = 无限循环',
                     font=('Microsoft YaHei UI', 11), text_color=TEXT_DIM, anchor='w').pack(**pad)
        self._ent_max_rounds = ctk.CTkEntry(
            drawer_content, font=self._f_small, text_color=TEXT,
            fg_color=BG_INPUT, border_color=BG_BTN,
            placeholder_text='0', width=200, height=34
        )
        self._ent_max_rounds.pack(**pad)
        self._ent_max_rounds.insert(0, '0')

        sep = ctk.CTkFrame(drawer_content, fg_color=BG_BTN, height=1)
        sep.pack(fill='x', padx=14, pady=12)

        # ── ② 运行模式 ──
        ctk.CTkLabel(drawer_content, text='运行模式',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)

        self._run_mode_var = tk.StringVar(value='alternate')
        for txt, val, color in [
            ('🔄 交替模式',    'alternate', ACCENT),
            ('🔒 只跑幻灵挑战', 'huanling', SUCCESS),
            ('🔒 只跑挑战',    'normal',   ORANGE),
        ]:
            btn = ctk.CTkButton(
                drawer_content, text=txt, font=self._f_small,
                fg_color=BG_INPUT, hover_color=BG_BTN,
                text_color=TEXT, height=36, corner_radius=8,
                command=lambda v=val: self._on_mode_change(v)
            )
            btn.pack(**pad, ipady=4)
            if val == 'alternate':
                self._btn_mode_alt = btn
            elif val == 'huanling':
                self._btn_mode_hl = btn
            else:
                self._btn_mode_nm = btn

        self._on_mode_change('alternate')

        sep2 = ctk.CTkFrame(drawer_content, fg_color=BG_BTN, height=1)
        sep2.pack(fill='x', padx=14, pady=12)

        # ── 窗口设置 ──
        ctk.CTkLabel(drawer_content, text='窗口',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)

        # 透明度
        row_alpha = ctk.CTkFrame(drawer_content, fg_color='transparent')
        row_alpha.pack(**pad)
        ctk.CTkLabel(row_alpha, text='透明度', font=self._f_small,
                     text_color=TEXT_DIM, width=54, anchor='w').pack(side='left')
        self._slider_alpha = ctk.CTkSlider(
            row_alpha, from_=0.5, to=1.0, number_of_steps=10,
            fg_color=BG_INPUT, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color='#7aa3ff',
            command=self._on_alpha_change, width=100
        )
        self._slider_alpha.pack(side='left', padx=(0, 8))
        self._slider_alpha.set(1.0)
        self._lbl_alpha_pct = ctk.CTkLabel(row_alpha, text='100%',
                                            font=self._f_small, text_color=TEXT, width=36, anchor='e')
        self._lbl_alpha_pct.pack(side='left')

        # 置顶
        row_top = ctk.CTkFrame(drawer_content, fg_color='transparent')
        row_top.pack(**pad)
        self._chk_top = ctk.CTkCheckBox(
            row_top, text='窗口置顶',
            font=self._f_small, text_color=TEXT,
            fg_color=BG_INPUT, hover_color=BG_BTN,
            border_color=BG_BTN,
            command=self._on_topmost_change
        )
        self._chk_top.pack(side='left')
        self._chk_top.select()

        # ── ⑤ 关于 ──
        sep5 = ctk.CTkFrame(drawer_content, fg_color=BG_BTN, height=1)
        sep5.pack(fill='x', padx=14, pady=12)

        ctk.CTkLabel(drawer_content, text=f'🧠 关于 {VERSION}',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)
        info_lines = [
            '• MaaOCR PP-OCRv5 引擎',
            '• MaaFramework Pipeline 架构',
            '• ROI 区域 OCR 优化',
            '• ColorMatch 颜色检测',
            '• freeze 画面稳定等待',
        ]
        for line in info_lines:
            ctk.CTkLabel(drawer_content, text=line,
                         font=('Microsoft YaHei UI', 11), text_color=TEXT_DIM,
                         anchor='w').pack(**pad)

    def _clear_log(self):
        """清空日志区"""
        self._log_text.configure(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.configure(state='disabled')
        self._append_log('— 日志已清空 —')

    # ────────────────────────────────────────────────────────────────
    #  抽屉动画
    # ────────────────────────────────────────────────────────────────

    def _toggle_drawer(self):
        if self._drawer_open:
            self._close_drawer()
        else:
            self._open_drawer()

    def _open_drawer(self):
        self._drawer_open = True
        self._btn_gear.configure(text_color=ACCENT)
        self.update_idletasks()
        # 抽屉紧贴右边，y=0，高度撑满
        self._drawer.place(relx=1.0, rely=0.0, relheight=1.0, anchor='ne', x=0, y=0)
        self._drawer.lift()

    def _close_drawer(self):
        self._drawer_open = False
        self._btn_gear.configure(text_color=TEXT_DIM)
        self._drawer.place_forget()

    # ────────────────────────────────────────────────────────────────
    #  事件
    # ────────────────────────────────────────────────────────────────

    def _on_mode_change(self, mode):
        self._run_mode_var.set(mode)
        self._highlight_mode(mode)

    def _highlight_mode(self, mode):
        colors = {
            'alternate': (ACCENT, 'white'),
            'huanling':  (SUCCESS, 'white'),
            'normal':    (ORANGE, 'white'),
        }
        bg, fg = colors[mode]
        for btn, m in [
            (self._btn_mode_alt, 'alternate'),
            (self._btn_mode_hl, 'huanling'),
            (self._btn_mode_nm, 'normal'),
        ]:
            if m == mode:
                btn.configure(fg_color=bg, text_color=fg)
            else:
                btn.configure(fg_color=BG_INPUT, text_color=TEXT)

    def _on_alpha_change(self, val):
        v = round(val, 1)
        self.attributes('-alpha', v)
        self._lbl_alpha_pct.configure(text=f'{int(v * 100)}%')

    def _on_topmost_change(self):
        self.attributes('-topmost', bool(self._chk_top.get()))

    def _on_task_enabled_change(self, task_id: str, var: tk.BooleanVar):
        TaskRegistry.set_enabled(task_id, bool(var.get()))
        TaskRegistry.save_config()

    # ────────────────────────────────────────────────────────────────
    #  日志
    # ────────────────────────────────────────────────────────────────

    def _append_log(self, msg):
        self._log_queue.put(msg)
        # 同时写文件日志
        try:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            self._log_file.write(f'[{ts}] {msg}\n')
        except Exception as e:
            print(f"[WARN] 日志文件写入失败: {e}")

    def _start_flush_timer(self):
        """启动日志+状态批量刷新定时器（每200ms一次）"""
        if self._flush_id:
            self.after_cancel(self._flush_id)
        self._flush_id = self.after(200, self._flush)

    def _flush(self):
        """批量刷新日志队列 + 合并状态更新为一次GUI更新"""
        try:
            log_msgs = []
            latest_status = None
            while True:
                try:
                    item = self._log_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, tuple) and item[0] == '__status__':
                    latest_status = item[1]
                else:
                    log_msgs.append(item)

            # 批量写日志（所有日志一次性写入，state只切换2次）
            if log_msgs:
                self._log_text.configure(state='normal')
                ts = time.strftime('%H:%M:%S')
                for msg in log_msgs:
                    if msg.startswith('✓'):
                        tag = 'ok'
                    elif msg.startswith('!!') or msg.startswith('⚠'):
                        tag = 'err' if '!!' in msg else 'warn'
                    elif msg.startswith('─') or msg.startswith('═') or msg.startswith('='):
                        tag = 'sep'
                    elif '>>' in msg or '推进' in msg:
                        tag = 'progress'
                    elif '赛季' in msg:
                        tag = 'season'
                    elif '已暂停' in msg:
                        tag = 'warn'
                    elif '恢复运行' in msg:
                        tag = 'ok'
                    elif '当前阶段' in msg or msg.startswith('第 '):
                        tag = 'phase'
                    else:
                        tag = None
                    self._log_text.insert('end', f'[{ts}] ', 'ts')
                    self._log_text.insert('end', msg + '\n', tag or '')
                self._log_text.see('end')
                line_count = int(self._log_text.index('end-1c').split('.')[0])
                if line_count > 2000:
                    self._log_text.delete('1.0', f'{line_count - 1500}.0')
                self._log_text.configure(state='disabled')

            # 状态只刷新最后一次（避免多次调用导致GUI抖动）
            if latest_status is not None:
                self._refresh_ui(latest_status)
        except Exception as e:
            print(f"[WARN] _flush失败: {e}")
        self._flush_id = self.after(200, self._flush)

    def _refresh_timer(self):
        """每秒刷新计时器（只更新timer部分，不刷新整个UI）"""
        try:
            # 更新呼吸灯动画（运行中2秒周期，战斗中0.6秒快速闪烁）
            is_battle = self._status.get('phase', '') == 'battle' and self._running
            if is_battle:
                dot_visible = (int(time.time() * 1.67) % 2 == 0)  # ~0.6s 周期
            else:
                dot_visible = (int(time.time()) % 2 == 0)  # 2s 周期
            dot_color = ACCENT if self._running else TEXT_DIM
            if is_battle and not dot_visible:
                dot_color = ORANGE  # 战斗中"灭"的状态用橙色暗光
            self._lbl_dot.configure(text='●' if dot_visible else '○', text_color=dot_color)

            # 更新时钟
            clock_txt = time.strftime('%H:%M')
            self._lbl_clock.configure(text=clock_txt)

            if not self._running:
                return
            s = self._status
            script_start = s.get('script_start', 0)
            battle_start = s.get('battle_start', 0)
            phase = s.get('phase', '')
            mode = s.get('mode', '')
            round_n = s.get('round', 0)

            # 总时间
            total_elapsed = int(time.time() - script_start) if script_start else 0
            total_txt = f'{total_elapsed // 60}:{total_elapsed % 60:02d}'
            # 战斗时间
            battle_elapsed = int(time.time() - battle_start) if battle_start else 0

            if phase == 'battle':
                timer_txt = f'⚡ {battle_elapsed}s  ⏱ {total_txt}'
            else:
                timer_txt = f'⏱ {total_txt}'
            self._lbl_timer.configure(text=timer_txt)
        except Exception as e:
            print(f"[WARN] _refresh_timer失败: {e}")
        self._timer_id = self.after(1000, self._refresh_timer)

    # ────────────────────────────────────────────────────────────────
    #  状态更新
    # ────────────────────────────────────────────────────────────────

    def _update_status(self, **kwargs):
        self._status.update(kwargs)
        # 入队，由批量定时器统一刷新GUI（避免 after(0) 洪水）
        # 注意：传入 self._status 的副本，保证 _flush 拿到的是完整状态
        self._log_queue.put(('__status__', dict(self._status)))

    def _refresh_ui(self, data=None):
        """刷新GUI（状态数据来源：参数 > self._status）"""
        s = data if data else self._status
        stats = s.get('stats', {})
        round_n = s.get('round', 0)
        mode = s.get('mode', '')
        phase = s.get('phase', '')
        running = s.get('running', False)
        battle_start = s.get('battle_start', 0)
        script_start = s.get('script_start', 0)

        hl_wins = stats.get('huanling_wins', 0)
        nm_wins = stats.get('normal_wins', 0)
        stage_progress = stats.get('stage_progress', 0)

        # 更新统计格子数字（变化时闪白反馈）
        hl_lbl = self._lbl_hl.winfo_children()[0]
        nm_lbl = self._lbl_nm.winfo_children()[0]
        stg_lbl = self._lbl_stage.winfo_children()[0]

        # 幻灵轮数
        hl_lbl.configure(text=f'{hl_wins}轮')
        if hl_wins != self._last_stats.get('hl_wins'):
            hl_lbl.configure(text_color=TEXT_BRIGHT)
            self.after(150, lambda: hl_lbl.configure(text_color=ACCENT))
        self._last_stats['hl_wins'] = hl_wins

        # 常规轮数
        nm_lbl.configure(text=f'{nm_wins}轮')
        if nm_wins != self._last_stats.get('nm_wins'):
            nm_lbl.configure(text_color=TEXT_BRIGHT)
            self.after(150, lambda: nm_lbl.configure(text_color=SUCCESS))
        self._last_stats['nm_wins'] = nm_wins

        # 推进关卡
        stg_lbl.configure(text=f'{stage_progress}关')
        if stage_progress != self._last_stats.get('stage_progress'):
            stg_lbl.configure(text_color=TEXT_BRIGHT)
            self.after(150, lambda: stg_lbl.configure(text_color=ORANGE))
        self._last_stats['stage_progress'] = stage_progress

        # 更新关卡详情
        current_stage = stats.get('current_stage', 0)
        round_progress = stats.get('round_progress', 0)
        huanling_stage = stats.get('huanling_stage', 0)
        normal_stage = stats.get('normal_stage', 0)

        # 显示总关卡数 = 已完成的幻灵 + 已完成的挂机（均为当前赛季关卡 - 1）
        total_season = max(0, huanling_stage - 1) + max(0, normal_stage - 1)
        self._lbl_current_stage.configure(text=f'{total_season}关' if total_season else '--')
        self._lbl_this_round.configure(text=f'本轮推进{round_progress}关')
        # 显示已完成数 = 赛季关卡数 - 1
        self._lbl_huanling_stage.configure(
            text=f'幻灵 {max(0, huanling_stage - 1)}' if huanling_stage > 0 else '幻灵 --'
        )
        self._lbl_normal_stage.configure(
            text=f'常规 {max(0, normal_stage - 1)}' if normal_stage > 0 else '常规 --'
        )

        if running:
            # 脚本运行总时间（计时器由 _refresh_timer 每秒更新，这里只更新状态文字）
            # 最大轮数进度
            max_rounds = self._cfg.get('max_rounds', 0)

            if phase == 'battle':
                status_txt = f'⚔  第 {round_n} 轮  [{mode}]'
                status_clr = WARN
            else:
                phase_map = {
                    'init':       ('等待启动',     TEXT_DIM),
                    'navigating': ('导航中...',    TEXT),
                    'idle_stage': ('等待挂机...',  TEXT),
                    'formation':  ('选择阵容...',  TEXT),
                }
                p_txt, status_clr = phase_map.get(phase, ('运行中...', TEXT))
                status_txt = p_txt
                if round_n > 0 and mode:
                    status_txt = f'{p_txt}  第 {round_n} 轮 [{mode}]'
                elif round_n > 0:
                    status_txt = f'{p_txt}  第 {round_n} 轮'
            # 脚本运行中始终播放动画
            self._start_battle_anim()

            self._lbl_status.configure(text=status_txt, text_color=status_clr)
            # 战斗中状态栏边框发光
            if phase == 'battle':
                self._status_card.configure(border_color=ACCENT, border_width=1)
            else:
                self._status_card.configure(border_color=BG_INPUT, border_width=1)
        else:
            phase_txt = f'已停止 · 共 {round_n} 轮' if round_n > 0 else '等待启动'
            phase_clr = SUCCESS if round_n > 0 else TEXT_DIM
            self._lbl_status.configure(text=phase_txt, text_color=phase_clr)
            self._lbl_timer.configure(text='')
            self._stop_battle_anim()

        if running:
            self._btn_pause.configure(state='normal', text_color=TEXT)
        else:
            self._btn_pause.configure(state='disabled', text_color=TEXT_DIM)

    # ────────────────────────────────────────────────────────────────
    #  控制按钮
    # ────────────────────────────────────────────────────────────────

    def _on_start(self):
        if self._running:
            self._append_log('[F11] 已在运行中')
            return
        # 等旧 bot 线程完全结束（防止新旧 bot 同时往队列塞消息导致状态乱跳）
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._append_log('⚠ 上一次任务尚未结束，请稍等...')
            return
        # 清空队列，防止旧 bot 残留的状态/日志消息干扰新 bot
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        try:
            max_r = int(self._ent_max_rounds.get().strip())
        except ValueError:
            max_r = 0
        run_m = self._run_mode_var.get()

        self._cfg = {
            'max_rounds': max_r,
            'run_mode': run_m,
            'log_callback': self._append_log,
            'status_callback': self._update_status,
        }

        self._running = True
        self._paused = False
        self._status['running'] = True
        self._btn_main.configure(text='■ 停止', fg_color=DANGER,
                                hover_color='#ff9999', command=self._on_stop)
        self._btn_pause.configure(state='normal', text_color=TEXT)

        self._log_text.configure(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.configure(state='disabled')

        self._bot = AFKBot(self._cfg)
        self._bot_thread = threading.Thread(target=self._bot_thread_wrapper, daemon=True)
        self._bot_thread.start()
        # 启动计时器刷新（每秒一次）
        self._refresh_timer()

    def _bot_thread_wrapper(self):
        self._bot.run()
        self._running = False
        self._paused = False
        def _reset_buttons():
            self._btn_main.configure(text='▶  启动', fg_color=ACCENT,
                                    hover_color='#7aa3ff', command=self._on_start)
            self._btn_pause.configure(text='⏸', fg_color=BG_BTN, text_color=TEXT_DIM, state='disabled')
            # 停止计时器刷新
            if self._timer_id:
                self.after_cancel(self._timer_id)
                self._timer_id = None
        self.after(0, _reset_buttons)

    def _on_pause(self):
        if not self._bot:
            self._append_log('[F10] 脚本未运行')
            return
        if not self._paused:
            self._bot.pause()
            self._paused = True
            self._btn_pause.configure(text='▶', fg_color=WARN, text_color='#000')
        else:
            self._bot.resume()
            self._paused = False
            self._btn_pause.configure(text='⏸', fg_color=BG_BTN, text_color=TEXT)

    def _on_stop(self):
        self._running = False
        self._paused = False
        if self._bot:
            self._bot.stop()
        self._btn_main.configure(text='▶  启动', fg_color=ACCENT,
                                  hover_color='#7aa3ff', command=self._on_start)
        self._btn_pause.configure(text='⏸', fg_color=BG_BTN, text_color=TEXT_DIM, state='disabled')
        self._update_status(running=False)
        self._append_log('[停止]')

    def _on_closing(self):
        if self._bot:
            self._bot.stop()
        try:
            self._log_file.close()
        except Exception as e:
            print(f"[WARN] 关闭日志文件失败: {e}")
        time.sleep(0.3)
        self.destroy()


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════

def _check_admin():
    """检测是否有管理员权限，没有则自动提权重启"""
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False
    if not is_admin:
        # 用 ShellExecuteW 请求管理员权限重启自己
        ctypes.windll.shell32.ShellExecuteW(
            None, 'runas',
            sys.executable, ' '.join(sys.argv),
            None, 1  # SW_SHOWNORMAL
        )
        sys.exit(0)


if __name__ == '__main__':
    _check_admin()
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('dark-blue')

    app = App()
    app.protocol('WM_DELETE_WINDOW', app._on_closing)
    app.mainloop()
