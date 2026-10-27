"""
afk_bot 坐标系统模块

CoordSystem 是坐标转换的唯一入口，封装 ClientToScreen 缓存、
比例坐标转换、横竖屏判断等功能。

设计原则：
- 所有内部坐标统一为图像相对坐标（相对于 _snap() 返回的截图）
- image_to_screen() 是唯一的向屏幕坐标的转换路径
- 比例坐标通过 ratio_to_image() 转为图像坐标后，再经 image_to_screen() 转屏幕坐标
"""

from typing import Tuple

import win32gui


class CoordSystem:
    """坐标系统 — 统一管理窗口坐标与比例坐标的相互转换。

    缓存 ClientToScreen 偏移量，提供图像坐标、屏幕坐标、比例坐标三者之间的
    唯一转换路径。外部使用者无需关心底层窗口偏移细节。

    Attributes:
        _hwnd: 目标窗口句柄（int），可为 None（离线模式）
        _trim: 截图裁剪像素数（int）
        _client_offset_l: 客户区左上角屏幕 X 坐标（缓存值）
        _client_offset_t: 客户区左上角屏幕 Y 坐标（缓存值）
        _client_w: 客户区宽度（像素，缓存值）
        _client_h: 客户区高度（像素，缓存值）
    """

    def __init__(self, hwnd: int = None, trim: int = 0, log_callback=None):
        """初始化坐标系统。

        Args:
            hwnd: Win32 窗口句柄。为 None 时 refresh() 返回 False，
                  所有转换退化到零偏移。
            trim: 截图裁剪像素数，与 _snap() 的 trim 保持一致。
            log_callback: 日志回调函数，签名: fn(level, message)
        """
        self._hwnd: int = hwnd
        self._trim: int = trim
        self._log = log_callback or (lambda level, msg: None)
        # 缓存值初始化为 0
        self._client_offset_l: int = 0
        self._client_offset_t: int = 0
        self._client_w: int = 0
        self._client_h: int = 0
        # 上次日志输出的窗口尺寸（用于去重）
        self._last_logged_size: Tuple[int, int] = (0, 0)
        self._last_logged_offset: Tuple[int, int] = (0, 0)

    # ── 属性访问器 ──────────────────────────────────────────────

    @property
    def client_offset(self) -> Tuple[int, int]:
        """返回缓存的客户区屏幕偏移 (client_l, client_t)。"""
        return (self._client_offset_l, self._client_offset_t)

    @property
    def window_size(self) -> Tuple[int, int]:
        """返回缓存的客户区尺寸 (width, height)。"""
        return (self._client_w, self._client_h)

    # ── 核心方法 ─────────────────────────────────────────────────

    def bind_hwnd(self, hwnd: int) -> None:
        """绑定/更新窗口句柄（v2.0.7）。

        CoordSystem 构造时固定 hwnd，游戏重启/新窗口拉起后句柄变化
        会导致 refresh() 恒失败（客户区恒 0 → 截图 0×0）。调用方
        （ScreenCapture.snap）每次截图前同步当前句柄即可自愈。
        """
        if hwnd and hwnd != self._hwnd:
            self._hwnd = hwnd
            self._last_logged_size = (0, 0)   # 触发一次新窗口尺寸日志
            self._last_logged_offset = (0, 0)

    def refresh(self) -> bool:
        """刷新窗口位置缓存。

        通过 GetClientRect 获取客户区尺寸，通过 ClientToScreen 获取
        客户区左上角的屏幕绝对坐标。应在每次截图前调用以确保坐标有效。

        Returns:
            bool: 刷新成功返回 True，窗口无效或无 hwnd 时返回 False。
        """
        if not self._hwnd:
            return False
        try:
            cl_left, cl_top, cl_right, cl_bottom = win32gui.GetClientRect(
                self._hwnd)
            self._client_w = cl_right - cl_left
            self._client_h = cl_bottom - cl_top
            if self._client_w <= 0 or self._client_h <= 0:
                return False
            self._client_offset_l, self._client_offset_t = (
                win32gui.ClientToScreen(self._hwnd, (0, 0)))

            # 窗口尺寸或偏移变化时记录日志
            cur_size = (self._client_w, self._client_h)
            cur_off = (self._client_offset_l + self._trim,
                       self._client_offset_t + self._trim)
            if cur_size != self._last_logged_size \
                    or cur_off != self._last_logged_offset:
                self._last_logged_size = cur_size
                self._last_logged_offset = cur_off
                self._log(
                    'info',
                    f'ℹ️ 坐标窗口信息 | '
                    f'尺寸={self._client_w}x{self._client_h}, '
                    f'偏移=({cur_off[0]},{cur_off[1]}), '
                    f'trim={self._trim}'
                )
            return True
        except Exception:
            return False

    def image_to_screen(self, img_x: int, img_y: int) -> Tuple[int, int]:
        """图像相对坐标 → 屏幕绝对坐标。

        图像坐标原点为截图（含 trim 偏移）的左上角。加上缓存的
        ClientToScreen 偏移和 trim 值得到屏幕绝对坐标。

        Args:
            img_x: 图像 X 坐标（像素，相对于截图左上角）
            img_y: 图像 Y 坐标（像素，相对于截图左上角）

        Returns:
            (screen_x, screen_y): 屏幕绝对坐标
        """
        screen_x = self._client_offset_l + self._trim + img_x
        screen_y = self._client_offset_t + self._trim + img_y
        return (int(screen_x), int(screen_y))

    def screen_to_image(self, screen_x: int, screen_y: int) -> Tuple[int, int]:
        """屏幕绝对坐标 → 图像相对坐标。

        用于 template_match 等返回屏幕绝对坐标的场景，将其转换回
        图像相对坐标以便内部统一处理。

        Args:
            screen_x: 屏幕绝对 X 坐标
            screen_y: 屏幕绝对 Y 坐标

        Returns:
            (img_x, img_y): 图像相对坐标
        """
        img_x = screen_x - self._client_offset_l - self._trim
        img_y = screen_y - self._client_offset_t - self._trim
        return (int(img_x), int(img_y))

    def ratio_to_image(self, rx: float, ry: float) -> Tuple[int, int]:
        """比例坐标 (0.0~1.0) → 图像相对像素坐标。

        比例坐标相对于客户区尺寸（不含 trim），转换为整数像素后即为
        图像坐标。由 InputController 再经 image_to_screen() 转为屏幕坐标。

        Args:
            rx: X 方向比例（0.0 ~ 1.0，0.0 为左边缘，1.0 为右边缘）
            ry: Y 方向比例（0.0 ~ 1.0，0.0 为上边缘，1.0 为下边缘）

        Returns:
            (img_x, img_y): 图像相对像素坐标（整数）
        """
        img_x = int(rx * self._client_w)
        img_y = int(ry * self._client_h)
        return (img_x, img_y)

    def is_portrait(self) -> bool:
        """判断当前窗口是否为竖屏模式。

        Returns:
            bool: 高度 > 宽度时为 True（竖屏），否则为 False（横屏）。
                  窗口尺寸为 0 时返回 False。
        """
        return self._client_h > self._client_w
