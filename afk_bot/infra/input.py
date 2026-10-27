"""
afk_bot 输入控制模块

InputController 封装点击、按键、模板匹配等操作。
所有坐标转换通过 CoordSystem 完成，仅 click_abs() 直接接受屏幕坐标。

设计原则：
- 公开方法接受图像相对坐标（或比例坐标），内部通过 CoordSystem 转换
- click_abs() 是唯一直接使用屏幕绝对坐标的方法
- template_match() 从 ScreenCapture 获取截图，返回屏幕绝对坐标
"""

import os
import time
from typing import Optional, Tuple, List

import numpy as np
import pyautogui
import win32api

from afk_bot.infra.coordinate import CoordSystem
from afk_bot.infra.screen import ScreenCapture

# v1.8.32 日志中文化：模板文件名 → 中文名（用户要求日志尽量不出现英文）
_TEMPLATE_ZH = {
    'btn_back_dark.png': '黑色返回按钮',
    'btn_back_purple.png': '紫色返回按钮',
    'btn_back_arrow.png': '白色返回按钮',
    'btn_confirm_check.png': '绿色确认按钮',
    'btn_confirm_cancel.png': '白色取消按钮',
    'btn_door_battle.png': '门卡战斗按钮',
    'btn_door_shop.png': '门卡小店按钮',
    'btn_sweep_max.png': '扫荡拉满按钮',
}


def _tpl_zh(path: str) -> str:
    """模板路径 → 中文名（未收录时退回中文文件名说明）。"""
    name = os.path.basename(path)
    return _TEMPLATE_ZH.get(name, f'按钮模板{name}')


def _resolve_tpl_path(tpl_path: str) -> str:
    """模板路径兜底解析（v1.8.78）。

    调用方偶尔只传裸文件名（如 'btn_skip_battle.png'），而本函数按给定
    路径直接读盘 → 裸名会被当成"相对进程 CWD"，exe 放在桌面时 CWD 就是
    桌面，必然 FileNotFoundError（真机 14:04/14:14 实测）。

    兜底顺序：原路径 → 资源根/templates/裸名 → 资源根/裸名。
    资源根：frozen 时取 sys._MEIPASS（spec datas 把 templates 解压到此），
    否则取 afk_bot/infra/input.py 上溯两级（项目根）。

    Returns:
        str: 原路径（已存在/已是绝对路径且无候选）或首个存在的候选路径
    """
    if os.path.isfile(tpl_path):
        return tpl_path
    if os.path.isabs(tpl_path):
        return tpl_path
    try:
        import sys as _sys
        base = getattr(_sys, '_MEIPASS', '') if getattr(
            _sys, 'frozen', False) else ''
    except Exception:
        base = ''
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    for cand in (
        os.path.join(base, 'templates', tpl_path),
        os.path.join(base, tpl_path),
    ):
        if os.path.isfile(cand):
            return cand
    return tpl_path


def _masked_ccoeff_map(
    src_bgr: np.ndarray,
    tpl_bgr: np.ndarray,
    mask01: np.ndarray,
) -> Optional[np.ndarray]:
    """掩码归一化互相关（v1.7.10 新增，支持透明背景模板）。

    cv2.matchTemplate 的 TM_CCOEFF_NORMED 不接受 mask；TM_CCORR_NORMED 虽
    支持 mask 但对亮度敏感易误报。本函数用 cv2.filter2D 滑动窗口积分，
    将模板 alpha 作为权重只统计有效像素，是"透明背景模板"的正确匹配算法。

    公式（窗口左上 (x,y)，跨 3 通道）：
        Tss = Σ M·T² - (Σ M·T)² / n          （模板侧常数）
        num = Σ M·T·I - (Σ M·T)·(Σ M·I) / n
        Iss = Σ M·I² - (Σ M·I)² / n          （随窗口位置变化）
        score = num / sqrt(Tss · Iss)         （理论范围 [-1, 1]）

    Args:
        src_bgr: 源图 BGR (H, W, 3)
        tpl_bgr: 模板 BGR (th, tw, 3)
        mask01:  模板权重 (th, tw) float32，0.0~1.0（如 alpha/255）

    Returns:
        (H-th+1, W-tw+1) 的分数图；模板大于源图或无效返回 None。
    """
    import cv2

    H, W = src_bgr.shape[:2]
    th, tw = tpl_bgr.shape[:2]
    if th > H or tw > W:
        return None
    M = mask01.astype(np.float32)
    n = float(M.sum())
    if n < 1.0:
        return None

    # 模板侧常数（跨通道累计）
    st_by_c: List[float] = []
    k_mt_by_c: List[np.ndarray] = []  # 每通道卷积核 = M·T
    tss_total: float = 0.0
    for c in range(3):
        T = tpl_bgr[:, :, c].astype(np.float32)
        MT = M * T
        st_c = float(MT.sum())
        tss_c = float((MT * T).sum()) - st_c * st_c / n
        st_by_c.append(st_c)
        k_mt_by_c.append(MT)
        tss_total += tss_c
    if tss_total <= 1e-9:
        return None

    valid_h = H - th + 1
    valid_w = W - tw + 1
    # cv2.filter2D 输出与 src 同尺寸；无 padding 污染的"有效区"位于
    # [th//2 : th//2+valid_h, tw//2 : tw//2+valid_w]
    ay0, ax0 = th // 2, tw // 2
    num = np.zeros((valid_h, valid_w), dtype=np.float64)
    iss_map = np.zeros((valid_h, valid_w), dtype=np.float64)
    for c in range(3):
        I = src_bgr[:, :, c].astype(np.float32)
        I2 = I * I
        r_mti = cv2.filter2D(I, cv2.CV_64F, k_mt_by_c[c])
        r_mi = cv2.filter2D(I, cv2.CV_64F, M)
        r_mi2 = cv2.filter2D(I2, cv2.CV_64F, M)
        mti = r_mti[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        mi = r_mi[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        mi2 = r_mi2[ay0:ay0 + valid_h, ax0:ax0 + valid_w]
        num += mti - st_by_c[c] * mi / n
        iss_map += mi2 - mi * mi / n

    # 数值误差兜底：Iss 理论恒 >= 0，clip 防舍入负值
    iss_map = np.maximum(iss_map, 0.0)
    den = np.sqrt(tss_total * iss_map)
    score = np.zeros_like(num)
    # 分母相对模板能量可忽略（mask 内窗口纯色/无纹理，Iss≈浮点残差）
    # → 匹配无统计意义，得分归 0，防止残差被放大成虚假高分
    floor = 1e-7 * np.sqrt(tss_total)
    np.divide(num, den, out=score, where=den > floor)
    np.clip(score, -1.0, 1.0, out=score)
    return score


def _masked_template_scan(
    screen_bgr: np.ndarray,
    tpl_bgr: np.ndarray,
    mask01: np.ndarray,
    scale_range: Tuple[float, float, float],
) -> Optional[Tuple[float, Tuple[int, int], float, Tuple[int, int]]]:
    """多尺度 masked NCC 扫描，返回 (best_val, best_loc, scale, tpl_shape)。"""
    import cv2

    screen_h, screen_w = screen_bgr.shape[:2]
    best_val: float = -1.0
    best_loc: Optional[Tuple[int, int]] = None
    best_scale: float = 1.0
    best_tpl_shape: Optional[Tuple[int, int]] = None

    min_scale, max_scale, step = scale_range
    num_scales: int = int((max_scale - min_scale) / step) + 1
    for i in range(num_scales):
        scale: float = min_scale + i * step
        tpl_scaled: np.ndarray = cv2.resize(
            tpl_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        mask_scaled: np.ndarray = cv2.resize(
            mask01, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        mask_scaled = (mask_scaled > 0.5).astype(np.float32)
        tpl_h, tpl_w = tpl_scaled.shape[:2]
        if tpl_h > screen_h or tpl_w > screen_w:
            continue
        score_map: Optional[np.ndarray] = _masked_ccoeff_map(
            screen_bgr, tpl_scaled, mask_scaled)
        if score_map is None:
            continue
        _, max_val, _, max_loc = cv2.minMaxLoc(score_map)
        if max_val > best_val:
            best_val = float(max_val)
            best_loc = (int(max_loc[0]), int(max_loc[1]))
            best_scale = scale
            best_tpl_shape = (tpl_h, tpl_w)

    if best_val < 0 or best_loc is None or best_tpl_shape is None:
        return None
    return best_val, best_loc, best_scale, best_tpl_shape


class InputController:
    """输入控制器 — 点击、按键、模板匹配。

    所有坐标转换必经 CoordSystem：
      图像坐标 → image_to_screen() → 屏幕坐标 → click_abs()
      比例坐标 → ratio_to_image() → 图像坐标 → image_to_screen() → 屏幕坐标

    Attributes:
        _coord_system: CoordSystem 实例
        _screen_capture: ScreenCapture 实例（用于截图和窗口激活）
        _log: 日志回调函数
    """

    def __init__(
        self,
        coord_system: CoordSystem,
        screen_capture: ScreenCapture,
        log_callback=None,
    ):
        """初始化 InputController。

        Args:
            coord_system: CoordSystem 实例，用于坐标转换
            screen_capture: ScreenCapture 实例，用于截图和窗口激活
            log_callback: 日志回调函数，签名: fn(level, message)
        """
        self._coord_system: CoordSystem = coord_system
        self._screen_capture: ScreenCapture = screen_capture
        self._log = log_callback or (lambda *a, **k: None)  # v1.8.49：禁止 print 兜底

    # ── 绝对坐标点击 ────────────────────────────────────────────

    def click_abs(self, cx: int, cy: int) -> None:
        """屏幕绝对坐标点击（自动激活窗口）。

        这是唯一直接接受屏幕绝对坐标的方法，所有其他点击方法
        最终都通过此方法执行。

        Args:
            cx: 屏幕 X 坐标
            cy: 屏幕 Y 坐标
        """
        self._screen_capture.activate_window()
        pyautogui.click(cx, cy)

    # ── 图像坐标点击 ────────────────────────────────────────────

    def drag_abs(self, x0: int, y0: int, x1: int, y1: int,
                  duration: float = 0.4) -> None:
        """屏幕绝对坐标拖动（按下 → 分段移动 → 松开）。"""
        self._screen_capture.activate_window()
        pyautogui.moveTo(x0, y0)
        pyautogui.mouseDown(button='left')
        steps = 6
        for i in range(1, steps + 1):
            pyautogui.moveTo(
                int(x0 + (x1 - x0) * i / steps),
                int(y0 + (y1 - y0) * i / steps),
                duration=duration / steps)
        pyautogui.mouseUp(button='left')
        time.sleep(0.3)

    def click_at(self, img_x: int, img_y: int, label: str = '') -> None:
        """图像相对坐标 → 屏幕绝对坐标 → 点击。

        Args:
            img_x: 图像 X 坐标（相对于截图左上角，像素）
            img_y: 图像 Y 坐标（相对于截图左上角，像素）
            label: 日志标签
        """
        screen_x: int
        screen_y: int
        screen_x, screen_y = self._coord_system.image_to_screen(img_x, img_y)
        off_l, off_t = self._coord_system.client_offset
        trim: int = self._coord_system._trim
        desc: str = f'标签={label}' if label else '无标签'
        self._log(
            'info',
            f'ℹ️ 点击 | 图像=({img_x},{img_y}) '
            f'→ 偏移+({off_l + trim},{off_t + trim}) '
            f'→ 屏幕=({screen_x},{screen_y}), {desc}'
        )
        self.click_abs(screen_x, screen_y)
        time.sleep(0.5)

    # ── 图像坐标长按（v1.8.62：终止确认弹窗「长按结束」）────────

    def long_press_at(
        self, img_x: int, img_y: int, duration: float = 2.5,
        label: str = '',
    ) -> None:
        """图像坐标长按：mouseDown → 保持 duration 秒 → mouseUp。

        Args:
            img_x: 图像 X 坐标（相对于截图左上角，像素）
            img_y: 图像 Y 坐标
            duration: 按住时长秒数（长按按钮进度环一般 1-2s，取 2.5 保险）
            label: 日志标签
        """
        screen_x: int
        screen_y: int
        screen_x, screen_y = self._coord_system.image_to_screen(img_x, img_y)
        self._screen_capture.activate_window()
        pyautogui.mouseDown(screen_x, screen_y)
        time.sleep(duration)
        pyautogui.mouseUp(screen_x, screen_y)
        self._log(
            'info',
            f'ℹ️ 长按 {duration}s | 图像=({img_x},{img_y}) '
            f'→ 屏幕=({screen_x},{screen_y}), 标签={label}'
        )
        time.sleep(0.5)

    def long_press_ratio(
        self, rx: float, ry: float, duration: float = 2.5,
        label: str = '',
    ) -> None:
        """比例坐标长按（OCR 漏检帧的兜底动作）。"""
        img_x: int
        img_y: int
        img_x, img_y = self._coord_system.ratio_to_image(rx, ry)
        self.long_press_at(img_x, img_y, duration=duration, label=label)

    # ── 比例坐标点击 ────────────────────────────────────────────

    def click_ratio(self, rx: float, ry: float, label: str = '') -> bool:
        """比例坐标 (0.0~1.0) → 图像坐标 → 屏幕坐标 → 点击。

        Args:
            rx: X 方向比例（0.0 左边缘 ~ 1.0 右边缘）
            ry: Y 方向比例（0.0 上边缘 ~ 1.0 下边缘）
            label: 日志标签

        Returns:
            bool: 始终返回 True
        """
        img_x: int
        img_y: int
        img_x, img_y = self._coord_system.ratio_to_image(rx, ry)
        screen_x: int
        screen_y: int
        screen_x, screen_y = self._coord_system.image_to_screen(img_x, img_y)
        desc: str = f'标签={label}' if label else '无标签'
        off_l, off_t = self._coord_system.client_offset
        trim: int = self._coord_system._trim
        cw: int
        ch: int
        cw, ch = self._coord_system.window_size
        self._log(
            'info',
            f'ℹ️ 点击 | 比例=({rx:.3f},{ry:.3f}) '
            f'×窗口={cw}x{ch} '
            f'→ 图像=({img_x},{img_y}) '
            f'→ +偏移({off_l + trim},{off_t + trim}) '
            f'→ 屏幕=({screen_x},{screen_y}), {desc}'
        )
        self.click_abs(screen_x, screen_y)
        time.sleep(0.5)
        return True

    def click_ratio_dual(
        self,
        landscape: Tuple[float, float],
        portrait: Tuple[float, float],
        label: str = '',
    ) -> bool:
        """横竖屏自适应比例坐标点击。

        根据 CoordSystem.is_portrait() 自动选择横屏或竖屏坐标。

        Args:
            landscape: (rx, ry) 横屏比例坐标
            portrait: (rx, ry) 竖屏比例坐标
            label: 日志标签

        Returns:
            bool: 始终返回 True
        """
        if self._coord_system.is_portrait():
            rx: float
            ry: float
            rx, ry = portrait
            if label:
                label = f'{label}[竖屏]'
        else:
            rx, ry = landscape
            if label:
                label = f'{label}[横屏]'
        return self.click_ratio(rx, ry, label=label)

    # ── 鼠标移动 / 滚轮（v1.7.2 新增，供可滚动列表导航用）──────

    def move_mouse_to_image(
        self, img_x: int, img_y: int, label: str = ''
    ) -> Tuple[int, int]:
        """移动鼠标到图像相对坐标（自动激活窗口）。

        滚动类操作的前提：必须先激活窗口并把鼠标移到可滚动区域内，
        否则滚轮/拖动不会作用到游戏窗口。

        Args:
            img_x: 图像 X 坐标（相对截图左上角，像素）
            img_y: 图像 Y 坐标（相对截图左上角，像素）
            label: 日志标签

        Returns:
            (screen_x, screen_y): 实际移动到的屏幕绝对坐标
        """
        self._screen_capture.activate_window()
        screen_x, screen_y = self._coord_system.image_to_screen(img_x, img_y)
        desc: str = f'标签={label}' if label else '无标签'
        self._log(
            'info',
            f'ℹ️ 移动鼠标 | 图像=({img_x},{img_y}) '
            f'→ 屏幕=({screen_x},{screen_y}), {desc}'
        )
        pyautogui.moveTo(screen_x, screen_y)
        return screen_x, screen_y

    def move_mouse_to_ratio(
        self, rx: float, ry: float, label: str = ''
    ) -> Tuple[int, int]:
        """移动鼠标到比例坐标（0.0~1.0，自动激活窗口）。

        Args:
            rx: X 方向比例（0.0 左边缘 ~ 1.0 右边缘）
            ry: Y 方向比例（0.0 上边缘 ~ 1.0 下边缘）
            label: 日志标签

        Returns:
            (screen_x, screen_y): 实际移动到的屏幕绝对坐标
        """
        img_x, img_y = self._coord_system.ratio_to_image(rx, ry)
        if label:
            label = f'{label}[比例({rx:.3f},{ry:.3f})]'
        return self.move_mouse_to_image(img_x, img_y, label=label)

    def scroll_wheel(
        self,
        clicks: int,
        rx: Optional[float] = None,
        ry: Optional[float] = None,
        label: str = '',
    ) -> bool:
        """滚轮滚动（激活窗口 + 可选先移动鼠标到指定比例坐标）。

        游戏窗口的滚动列表只有在「窗口已激活 且 鼠标位于可滚动区域内」
        时才会响应滚轮，故必须先 activate_window() + moveTo 到区域内。

        v1.7.4：clicks 绝对值较大时**内部拆成多段小滚动连发**（每段至多 10 格）。
        原因：Unity 对单次超大滚轮 delta 可能限幅（v1.7.3 真机实测单次 -3
        只滚一丝），拆段连发 = 模拟快速滚轮，位移才真正随 clicks 放大。

        Args:
            clicks: 滚动格数，正数向上、负数向下
            rx: 可选，滚动前先把鼠标移到的 X 比例坐标
            ry: 可选，滚动前先把鼠标移到的 Y 比例坐标
            label: 日志标签

        Returns:
            bool: 始终返回 True
        """
        self._screen_capture.activate_window()
        if rx is not None and ry is not None:
            self.move_mouse_to_ratio(rx, ry, label=label)
        else:
            time.sleep(0.05)
        self._scroll_impl(clicks)
        desc: str = f'标签={label}' if label else '无标签'
        self._log(
            'info',
            f'ℹ️ 滚轮 | 格数={clicks} '
            f'{"向上" if clicks > 0 else "向下"} '
            f'位置=({rx},{ry}), {desc}'
        )
        time.sleep(0.3)
        return True

    @staticmethod
    def _scroll_impl(clicks: int) -> None:
        """实际滚轮动作（v1.7.4 修复：格数 → delta 换算 + 逐格拆发）。

        根因：pyautogui._scroll() 把参数**原样**当 dwData 传给 mouse_event，
        而 Windows 滚轮一格 = 120 delta（Unity 按 delta/120 换算滚动 tick）。
        所以 pyautogui.scroll(-3) 实际只发 delta=-3 ≈ 0.025 格 → 真机"一丝丝"。

        这里把 clicks 当「格数」：每格换算 120 delta，逐格连发（段间 20ms，
        模拟真实快速滚轮），位移才真正 = clicks 格。
        """
        if clicks == 0:
            return
        direction: int = 1 if clicks > 0 else -1
        for _ in range(abs(clicks)):
            pyautogui.scroll(direction * 120)   # 一格 = 120 delta
            time.sleep(0.02)

    # ── ESC 按键 ────────────────────────────────────────────────

    def send_esc(self) -> None:
        """发送 ESC 按键（系统级 keybd_event，Unity 游戏有效）。

        先激活窗口，等待 0.1s 再发送按键，确保焦点在游戏窗口。
        """
        self._screen_capture.activate_window()
        time.sleep(0.1)
        win32api.keybd_event(0x1B, 0, 0, 0)
        win32api.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.05)

    def send_key_f(self, label: str = '') -> None:
        """发送 F 按键（系统级 keybd_event，Unity 游戏有效，v1.7.12 新增）。

        与 send_esc() 同思路：先激活窗口确保焦点，再发 keybd_event。
        用途：异界迷宫备战画面（敌我血量预览 + 底部「战斗 F」）按 F
        开始战斗（v1.7.12 用户确认的交互方式）。

        Args:
            label: 日志标签
        """
        if label:
            self._log('info', f'ℹ️ 按键 | F | {label}')
        self._screen_capture.activate_window()
        time.sleep(0.1)
        win32api.keybd_event(0x46, 0, 0, 0)
        win32api.keybd_event(0x46, 0, 2, 0)
        time.sleep(0.05)

    def press_esc(self, label: str = '') -> None:
        """发送 ESC（**v2.1.14 起改为系统级 keybd_event，与 send_esc 同源**）。

        历史教训（真机 2026-09-11）：旧实现用 `SendMessage` 投递
        WM_KEYDOWN/WM_KEYUP 到窗口消息队列——而现代游戏引擎（Unity）
        **不读取消息队列**（它直接查询键盘状态 / Raw Input），于是该 ESC
        **恒定无效**，造成两类长期问题：
          · 全局导航频繁打「ESC 已试，改用返回按钮模板」（一直靠返回按钮兜底）
          · 迷宫「退出备战」失效 → 收尾被判失败却当成本轮完成 → 连续 30 轮
            空转（真机 11:42~11:58 共 16 分钟）

        现委托 `send_esc()`（keybd_event 真实按键 + 先激活窗口确保焦点），
        保留本方法名以免改动 11 处调用点。
        """
        if label:
            self._log('info', f'ℹ️ 退出键 | {label}')
        self.send_esc()

    def press_esc_background(self, label: str = '') -> None:
        """后台投递 ESC（旧 press_esc 实现，**仅特殊场景保留**）。

        用 SendMessage 投递消息，不抢焦点；代价是对 Unity 游戏**通常无效**。
        仅当明确需要"不打扰前台窗口"且不依赖按键生效时使用。
        """
        if label:
            self._log('info', f'ℹ️ 退出键(后台) | {label}')
        hwnd: Optional[int] = self._screen_capture.hwnd
        if hwnd is None:
            return
        win32api.SendMessage(hwnd, 0x0100, 0x1B, 0)  # WM_KEYDOWN
        time.sleep(0.05)
        win32api.SendMessage(hwnd, 0x0101, 0x1B, 0)  # WM_KEYUP

    # ── 多尺度模板匹配 ──────────────────────────────────────────

    def template_match(
        self,
        tpl_path: str,
        threshold: float = 0.8,
        scale_range: Tuple[float, float, float] = (0.5, 2.0, 0.1),
        silent: bool = False,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """多尺度模板匹配，查找按钮位置。

        从 ScreenCapture 获取截图，使用 OpenCV 多尺度模板匹配
        在截图中搜索模板图片，返回匹配中心点的屏幕绝对坐标。

        v1.7.10 起支持 RGBA 透明背景模板（如 btn_confirm_check.png 圆形
        按钮，圆外 alpha=0）：检测到透明像素时自动切换 masked NCC 算法，
        只统计圆内像素，避免"纯黑 mask 角 vs 浅色背景"的虚假匹配。

        Args:
            tpl_path: 模板图片文件路径
            threshold: 匹配置信度阈值 (0.0~1.0)
            scale_range: (min_scale, max_scale, step) 缩放搜索范围
            silent: True 时不打印日志
            region: (x, y, w, h) 屏幕坐标搜索区域，None = 全屏

        Returns:
            (cx, cy): 匹配中心的屏幕绝对坐标，未找到返回 None
        """
        import cv2

        # v1.8.78：裸文件名兜底解析（exe 场景 CWD=桌面，必然找不到）
        tpl_path = _resolve_tpl_path(tpl_path)
        if not os.path.isfile(tpl_path):
            self._log(
                'warn',
                f'⚠️ 模板文件不存在：{os.path.basename(str(tpl_path))}',
            )
            return None

        try:
            img, img_l, img_t, img_w, img_h = self._screen_capture.snap()
            # v1.9.39：空截图（窗口未就绪）→ 不进入匹配（cvtColor
            # 对空数组会抛 assertion），返回 None 交由调用方重试
            if img is None or img.width <= 0 or img.height <= 0:
                return None
            screen_bgr: np.ndarray = cv2.cvtColor(
                np.array(img), cv2.COLOR_RGB2BGR)

            # 如果指定了 region，裁剪 ROI
            if region is not None:
                rx: int
                ry: int
                rw: int
                rh: int
                rx, ry, rw, rh = region
                roi_x: int = rx - img_l
                roi_y: int = ry - img_t
                screen_bgr = screen_bgr[roi_y:roi_y + rh, roi_x:roi_x + rw]
                img_l += roi_x
                img_t += roi_y

            tpl_data: np.ndarray = np.fromfile(tpl_path, dtype=np.uint8)
            # v1.7.10: UNCHANGED 保留 alpha，支持透明背景模板
            tpl_img: Optional[np.ndarray] = cv2.imdecode(
                tpl_data, cv2.IMREAD_UNCHANGED)
            if tpl_img is None:
                # v1.8.49 脱敏：只输出文件名，不泄漏本机绝对路径
                self._log('warn',
                         f'[警告] 模板图片读取失败：'
                         f'{os.path.basename(str(tpl_path))}')
                return None

            # 分流：透明模板（4通道且有 alpha<128 的像素）→ masked NCC
            tpl_bgr: np.ndarray
            use_mask: bool = False
            mask01: Optional[np.ndarray] = None
            if tpl_img.ndim == 3 and tpl_img.shape[2] == 4:
                alpha: np.ndarray = tpl_img[:, :, 3]
                tpl_bgr = tpl_img[:, :, :3]
                if bool((alpha < 128).any()):
                    use_mask = True
                    mask01 = (alpha > 0).astype(np.float32)
            else:
                tpl_bgr = tpl_img

            screen_h: int
            screen_w: int
            screen_h, screen_w = screen_bgr.shape[:2]
            best_val: float = -1.0
            best_loc: Optional[Tuple[int, int]] = None
            best_scale: float = 1.0
            best_tpl_shape: Optional[Tuple[int, int]] = None

            if use_mask and mask01 is not None:
                # 透明模板 → masked NCC（v1.7.10）
                scanned = _masked_template_scan(
                    screen_bgr, tpl_bgr, mask01, scale_range)
                if scanned is not None:
                    best_val, best_loc, best_scale, best_tpl_shape = scanned
            else:
                tpl_original: np.ndarray = tpl_bgr
                min_scale: float
                max_scale: float
                step: float
                min_scale, max_scale, step = scale_range
                num_scales: int = int((max_scale - min_scale) / step) + 1

                for i in range(num_scales):
                    scale: float = min_scale + i * step
                    tpl_scaled: np.ndarray = cv2.resize(
                        tpl_original, None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_AREA,
                    )
                    tpl_h: int
                    tpl_w: int
                    tpl_h, tpl_w = tpl_scaled.shape[:2]
                    if tpl_h > screen_h or tpl_w > screen_w:
                        continue

                    result: np.ndarray = cv2.matchTemplate(
                        screen_bgr, tpl_scaled, cv2.TM_CCOEFF_NORMED)
                    _: float
                    max_val: float
                    max_loc: Tuple[int, int]
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)

                    if max_val > best_val:
                        best_val = max_val
                        best_loc = max_loc
                        best_scale = scale
                        best_tpl_shape = (tpl_h, tpl_w)

            if best_val >= threshold and best_loc is not None \
                    and best_tpl_shape is not None:
                tpl_h, tpl_w = best_tpl_shape
                cx: int = int(img_l + best_loc[0] + tpl_w / 2)
                cy: int = int(img_t + best_loc[1] + tpl_h / 2)
                algo = 'maskedNCC' if use_mask else 'CCOEFF'
                if not silent:
                    self._log(
                        'info',
                        f'ℹ️ 多尺度模板匹配成功 | '
                        f'{os.path.basename(tpl_path)} '
                        f'置信度={best_val:.3f} '
                        f'尺度={best_scale:.1f} '
                        f'坐标=({cx},{cy}) | '
                        f'算法={algo} | '
                        f'snap: img_l={img_l} img_t={img_t} '
                        f'img_w={img_w} img_h={img_h} | '
                        f'best_loc=({best_loc[0]},{best_loc[1]}) '
                        f'tpl=({tpl_w}x{tpl_h})'
                    )
                return cx, cy
            else:
                return None

        except Exception as e:
            self._log('warn', f'⚠️ 多尺度模板匹配异常: {e}')
            return None

    def click_back_button(
        self,
        tpl_paths: List[str],
        threshold: float = 0.7,
    ) -> bool:
        """兜底返回（v1.7.5 新增）：多模板匹配任一返回按钮，命中即点击。

    ESC 在某些场景（弹窗/特殊状态）失效时使用：
    依次尝试 tpl_paths 中的模板，找到一个就点击（click_abs），未找到返回 False。

    Args:
        tpl_paths: 模板图片绝对路径列表（多外观/多主题支持）
        threshold: 模板匹配置信度（0.0~1.0），默认 0.7

    Returns:
        bool: True 表示已点击命中模板，False 表示全部未命中
    """
        for path in tpl_paths:
            if not path or not os.path.isfile(path):
                continue
            pos: Optional[Tuple[int, int]] = self.template_match(
                path, threshold=threshold, silent=True
            )
            if pos:
                self._log(
                    'info',
                    f'↩️ 返回按钮兜底 | 模板={_tpl_zh(path)} '
                    f'置信度≥{threshold:.2f} 坐标=({pos[0]},{pos[1]})'
                )
                self.click_abs(pos[0], pos[1])
                return True
        return False
