"""
afk_bot 屏幕截图模块

ScreenCapture 封装窗口管理、截图、OCR 识别等底层操作。
从 afk_bot.py 中提取，保持行为零回归。

设计原则：
- snap() 返回格式 (PIL.Image, client_l, client_t, w, h) 与旧 _snap() 兼容
- 使用 CoordSystem 管理坐标缓存
- OCR 缓存基于画面指纹（采样像素哈希），TTL 60 秒
"""

import os
import time
from typing import Optional, Tuple, List, Any

import numpy as np
from PIL import ImageGrab, Image

import win32gui
import win32con
import win32api
import win32process

from afk_bot.infra.coordinate import CoordSystem


class ScreenCapture:
    """屏幕截图与窗口管理。

    封装 FindWindow、截图、窗口激活、画面指纹、调试截图、OCR 识别等
    底层操作，为 InputController 和上层模块提供统一的截图接口。

    Attributes:
        _hwnd: 目标 Unity 窗口句柄（int 或 None）
        _ocr_engine: MaaOCR 实例，用于 OCR 识别
        _coord_system: CoordSystem 实例，管理窗口坐标缓存
        _debug_dir: 调试截图保存目录（str）
        _ocr_cache: OCR 结果缓存 dict，键为指纹哈希
        _ocr_cache_ttl: OCR 缓存有效期（秒）
        _debug_seq: 调试截图序号计数器
    """

    def __init__(
        self,
        hwnd: Optional[int] = None,
        ocr_engine: Any = None,
        coord_system: Optional[CoordSystem] = None,
        debug_dir: Optional[str] = None,
        log_callback=None,
    ):
        """初始化 ScreenCapture。

        Args:
            hwnd: Unity 窗口句柄，为 None 时需调用 find_and_focus()
            ocr_engine: MaaOCR 实例，为 None 时 OCR 相关方法返回空列表
            coord_system: CoordSystem 实例，用于坐标缓存。
                          为 None 时 snap() 自行计算偏移量。
            debug_dir: 调试截图保存目录，为 None 时使用 TEMP/AFKJ_debug
            log_callback: 日志回调函数，签名: fn(level, message)
        """
        self._hwnd: Optional[int] = hwnd
        self._ocr_engine: Any = ocr_engine
        self._coord_system: Optional[CoordSystem] = coord_system
        self._debug_dir: str = debug_dir or os.path.join(
            os.environ.get('TEMP', '/tmp'), 'AFKJ_debug'
        )
        os.makedirs(self._debug_dir, exist_ok=True)
        self._debug_seq: int = 0
        # OCR 缓存
        self._ocr_cache: dict = {}
        self._ocr_cache_ttl: float = 60.0
        self._log = log_callback or (lambda level, msg: None)

    # ── 属性 ────────────────────────────────────────────────────

    @property
    def hwnd(self) -> Optional[int]:
        """返回当前窗口句柄。"""
        return self._hwnd

    # ── 窗口管理 ────────────────────────────────────────────────

    # v1.8.37：类名含 Unity 不再唯一（Endfield/明日方舟等同为 Unity 引擎），
    # 多 Unity 窗口并存时 out[0] 会抓错窗口（真机 9/6：激活了 Endfield）。
    # 改为候选收集 + 标题评分：优先 AFK Journey 关键词、排除已知其它游戏
    _PREFERRED_TITLE_KWS = ('afk journey', '剑与远征', '启程')
    _EXCLUDED_TITLE_KWS = ('endfield', '明日方舟', '终末地', 'arknights')

    def find_hwnd(self) -> Optional[int]:
        """枚举顶层窗口，查找 Unity 类名的本游戏窗口。

        评分规则：
          1. 排除标题命中其它 Unity 游戏关键词的窗口（Endfield 等）
          2. 标题含 AFK Journey 关键词（afk journey/剑与远征/启程）优先
          3. 优先可见窗口
          4. 兜底取第一个候选，并列出全部候选标题便于诊断

        Returns:
            int: 窗口句柄，未找到时返回 None。
        """
        candidates: List[Tuple[int, str]] = []

        def cb(h: int, r: List[Tuple[int, str]]) -> None:
            try:
                if 'Unity' in win32gui.GetClassName(h):
                    r.append((h, win32gui.GetWindowText(h)))
            except Exception:
                pass

        win32gui.EnumWindows(cb, candidates)
        if not candidates:
            return None

        # 排除已知其它 Unity 游戏
        pool = [
            (h, t) for h, t in candidates
            if not any(k in t.lower() for k in self._EXCLUDED_TITLE_KWS)
        ]
        pool = pool or candidates   # 全被排除时仍兜底（保旧行为）

        def score(item: Tuple[int, str]) -> Tuple[int, int, int]:
            h, t = item
            tl = t.lower()
            preferred = any(k in tl for k in self._PREFERRED_TITLE_KWS)
            # v2.0.3（真机 18:26）：尺寸无效的窗口（刚创建未渲染/隐藏）
            # 排到最后——否则截图尺寸为 0，OCR 全部跳过（ESC 是全局
            # 按键与句柄无关，故「ESC 有效」不能证明句柄正确）
            try:
                _l, _t, _r, _b = win32gui.GetWindowRect(h)
                sized = 0 if (_r - _l) > 0 and (_b - _t) > 0 else 1
            except Exception:
                sized = 1
            return (0 if preferred else 1,
                    0 if win32gui.IsWindowVisible(h) else 1,
                    sized)

        pool.sort(key=score)
        hwnd, title = pool[0]
        # v1.8.42：候选日志只在首次选择时打（每次任务启动都重查一遍会刷屏）
        if len(candidates) > 1 and not getattr(self, '_cand_logged', False):
            self._log(
                'info',
                f'窗口候选 {len(candidates)} 个：'
                + '；'.join(f'{t or "(无标题)"}' for _, t in candidates)
                + f' → 选定「{title or "(无标题)"}」'
            )
            self._cand_logged = True
        self._hwnd = hwnd
        return hwnd

    def find_and_focus(self) -> Optional[int]:
        """查找并聚焦 Unity 游戏窗口，最多重试 5 次。

        Returns:
            int: 窗口句柄，全部失败返回 None。
        """
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
                except Exception:
                    pass
                time.sleep(2)
            else:
                time.sleep(3)
        return None

    def win_dims(self) -> Tuple[int, int, int, int]:
        """获取窗口矩形（含窗口装饰），返回 (l, t, w, h)。

        多次重试：窗口异常时自动调用 find_and_focus() 恢复。

        Returns:
            (left, top, width, height): 窗口屏幕坐标与尺寸

        Raises:
            RuntimeError: 5 次重试后窗口仍然无效
        """
        for attempt in range(5):
            try:
                l, t, r, b = win32gui.GetWindowRect(self._hwnd)
                if r - l < 50 or b - t < 50:
                    raise ValueError('窗口过小')
                return l, t, r - l, b - t
            except Exception:
                self._hwnd = self.find_and_focus()
                if not self._hwnd:
                    raise RuntimeError('游戏窗口丢失！')
                l, t, r, b = win32gui.GetWindowRect(self._hwnd)
                if r - l >= 50 and b - t >= 50:
                    return l, t, r - l, b - t
                time.sleep(1)
        raise RuntimeError('游戏窗口丢失！')

    # ── 截图 ────────────────────────────────────────────────────

    def snap(self) -> Tuple[Image.Image, int, int, int, int]:
        """截取游戏客户区（纯游戏内容，不含窗口装饰）。

        自动激活窗口并刷新 CoordSystem 缓存。

        Returns:
            (img, client_l, client_t, w, h):
                img: PIL Image 截图
                client_l: 客户区左上角屏幕 X 坐标
                client_t: 客户区左上角屏幕 Y 坐标
                w: 客户区宽度（像素）
                h: 客户区高度（像素）
        """
        client_w: int = 0
        client_h: int = 0
        for _attempt in range(3):
            # v2.0.7（真机 18:53 决定性矛盾：窗口矩形正常但截图 0）——
            # CoordSystem 的 hwnd 构造后固定，游戏重启/新窗口拉起后
            # 句柄变化会导致 refresh() 恒失败、客户区恒 0 → 截图 0×0。
            # 每次截图前把当前句柄同步给它即可自愈。
            if self._coord_system is not None and self._hwnd:
                try:
                    self._coord_system.bind_hwnd(self._hwnd)
                except Exception:
                    pass
            _ok = False
            if self._coord_system is not None:
                _ok = self._coord_system.refresh()
                client_l, client_t = self._coord_system.client_offset
                client_w, client_h = self._coord_system.window_size
            if not _ok and self._hwnd:
                # 坐标系统不可用（未绑定/刷新失败）→ 直接自算（不依赖它）
                try:
                    cl_left, cl_top, cl_right, cl_bottom = (
                        win32gui.GetClientRect(self._hwnd))
                    client_l, client_t = win32gui.ClientToScreen(
                        self._hwnd, (0, 0))
                    client_w = cl_right - cl_left
                    client_h = cl_bottom - cl_top
                except Exception:
                    pass
            if client_w > 0 and client_h > 0:
                break
            # v1.9.37：客户区 0×0 = 窗口最小化/未就绪 → 激活后重取
            self.activate_window()
            time.sleep(0.3)
        # v2.0.6（真机 18:53）：客户区仍 0（激活未生效）→ **兜底用窗口
        # 矩形**（Unity 无边框窗口，窗口区≈客户区），避免整轮截图全废
        if (client_w <= 0 or client_h <= 0) and self._hwnd:
            try:
                _l, _t, _r, _b = win32gui.GetWindowRect(self._hwnd)
                if (_r - _l) > 0 and (_b - _t) > 0:
                    client_l, client_t = _l, _t
                    client_w, client_h = _r - _l, _b - _t
                    self._log('warn',
                              f"[警告] 客户区读数 0 → 回退窗口矩形 "
                              f"({client_w}x{client_h})；若截图仍空请检查"
                              "窗口是否最小化")
            except Exception:
                pass

        self.activate_window()
        time.sleep(0.05)
        # v2.2.5：激活失败且窗口仍最小化 → 明确告警（此时必然截出 0×0，
        # 让日志自解释，而不是只抛一句"截图尺寸为 0"）
        try:
            import win32gui as _wg  # type: ignore
            if self._hwnd and _wg.IsIconic(self._hwnd):
                self._log('warn',
                          '[警告] 游戏窗口仍处最小化状态 → 本次截图必为 '
                          '0×0（请还原窗口后重试；若是刚启动的游戏，'
                          '稍等其加载完成）')
        except Exception:
            pass

        trim: int = 0  # Unity 窗口无装饰边框
        bbox = (client_l + trim, client_t + trim,
                client_l + client_w - trim, client_t + client_h - trim)
        # v2.0.6（真机 18:53）：**多显示器修复**——ImageGrab 默认
        # all_screens=False 只截主显示器；窗口在副屏时（如 client_l=2089
        # > 主屏宽 1920）bbox 落在主屏外 → 返回 0×0 空图 → OCR 全废。
        # all_screens=True 截整个虚拟桌面（坐标以虚拟屏左上为原点，与
        # GetWindowRect 一致）。
        try:
            img: Image.Image = ImageGrab.grab(bbox=bbox,
                                             all_screens=True)
        except TypeError:
            # 极旧版 Pillow 无 all_screens 参数 → 退回原行为
            img = ImageGrab.grab(bbox=bbox)
        # 防御：实际尺寸与请求不符时给出诊断（DPI/跨屏异常的定位线索）
        _exp_w = max(0, bbox[2] - bbox[0])
        _exp_h = max(0, bbox[3] - bbox[1])
        if img.width != _exp_w or img.height != _exp_h:
            self._log('warn',
                      f"[诊断] 截图尺寸不符 请求={_exp_w}x{_exp_h} "
                      f"实际={img.width}x{img.height} bbox={bbox}")
        return img, client_l + trim, client_t + trim, \
            client_w - 2 * trim, client_h - 2 * trim

    # ── 窗口激活 ────────────────────────────────────────────────

    def activate_window(self, max_retries: int = 5) -> bool:
        """激活游戏窗口到前台（v1.7.3 增强：双线程 attach + 前台验证）。

        恢复最小化窗口 → AttachThreadInput（当前线程同时挂到**当前前台窗口
        线程**与**目标窗口线程**）→ BringWindowToTop → ALT 解锁前台锁 →
        SetForegroundWindow → 验证 GetForegroundWindow 是否真的抢到前台。

        为什么要双 attach：Windows 前台锁规定「非前台进程不能 SetForegroundWindow
        别的窗口」。只 attach 当前线程与目标窗口线程（旧实现）依然会被拒；
        必须把当前线程也 attach 到**正在占前台的窗口线程**，把前台让渡过来，
        SetForegroundWindow 才会成功（v1.7.2 真机实测：滚轮/点击失效 = 没激活成功）。

        激活是 Unity 窗口滚动/点击生效的前提（用户真机确认），失败返回 False。

        Args:
            max_retries: 验证失败后的重试次数

        Returns:
            bool: True 表示确认游戏窗口已是前台窗口
        """
        for attempt in range(max_retries + 1):
            try:
                if self._hwnd is None:
                    return False
                win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
                time.sleep(0.05)
                self._force_foreground(self._hwnd)
                time.sleep(0.15)
                # v2.2.5：**先确认已脱离最小化**再做前台校验。
                # 最小化窗口的客户区仍报还原尺寸 → 不校验 IsIconic 会让调用方
                # 以为"已激活"，继续截图时 bbox 落在 (-32000,…) 屏外 → 0×0
                # （真机 17:47：报就绪后连续 75s 截图 0×0）。
                if win32gui.IsIconic(self._hwnd):
                    time.sleep(0.2)
                    continue
                # 验证真的抢到前台（没抢到就重试）
                if win32gui.GetForegroundWindow() == self._hwnd:
                    return True
                # 前台锁破解：模拟一次 ALT 按键让系统放行
                win32api.keybd_event(0x12, 0, 0, 0)  # ALT down
                win32api.keybd_event(0x12, 0, 2, 0)  # ALT up
                time.sleep(0.1)
            except Exception:
                pass
            if attempt < max_retries:
                time.sleep(0.3)
        return False

    def _force_foreground(self, hwnd: int) -> None:
        """强抢前台：双线程 attach + BringWindowToTop + SetForegroundWindow。

        关键：把当前线程同时 attach 到「当前前台窗口线程」与「目标窗口线程」，
        这是绕过 Windows 前台锁（SetForegroundWindow 静默失败）的标准做法。
        """
        try:
            cur_thread: int = win32api.GetCurrentThreadId()
            fg_hwnd: int = win32gui.GetForegroundWindow()
            tgt_thread: int
            tgt_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            if cur_thread == tgt_thread:
                win32gui.SetForegroundWindow(hwnd)
                return

            attached: List[int] = []
            if fg_hwnd and fg_hwnd != hwnd:
                fg_thread: int
                fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
                if fg_thread != cur_thread and fg_thread != tgt_thread:
                    win32process.AttachThreadInput(
                        cur_thread, fg_thread, True)
                    attached.append(fg_thread)
            if tgt_thread != cur_thread:
                win32process.AttachThreadInput(
                    cur_thread, tgt_thread, True)
                attached.append(tgt_thread)

            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)

            # 分离 attach（成对释放）
            for th in reversed(attached):
                try:
                    win32process.AttachThreadInput(cur_thread, th, False)
                except Exception:
                    pass
        except Exception:
            pass

    # ── 画面指纹 ────────────────────────────────────────────────

    def frame_fingerprint(self, img: Optional[Image.Image] = None) -> int:
        """快速画面指纹：采样 10 个像素点的 RGB 值做哈希。

        比 OCR 快约 100 倍，用于卡住检测和缓存键生成。

        Args:
            img: 可选 PIL Image，为 None 时自动截图

        Returns:
            int: 画面指纹哈希值
        """
        if img is None:
            img, _, _, _, _ = self.snap()
        w: int = img.width
        h: int = img.height
        if w <= 0 or h <= 0:
            return 0   # v1.9.37：空图防御（不应到达，ocr_screen 已拦）
        arr: np.ndarray = np.array(img.convert('RGB'))
        sample_pts: List[Tuple[int, int]] = [
            (h // 4, w // 4), (h // 4, 3 * w // 4),
            (3 * h // 4, w // 4), (3 * h // 4, 3 * w // 4),
            (h // 2, w // 2),
            (h // 3, w // 3), (h // 3, 2 * w // 3),
            (2 * h // 3, w // 3), (2 * h // 3, 2 * w // 3),
            (h // 5, w // 5),
        ]
        pixels: Tuple = tuple(
            tuple(arr[y, x].tolist()) for y, x in sample_pts
        )
        return hash(pixels)

    # ── 调试截图 ────────────────────────────────────────────────

    def debug_screenshot(self, tag: str = 'debug') -> Optional[str]:
        """保存当前画面的调试截图到 _debug_dir。

        文件名格式：HHMMSS_序号_tag.png

        Args:
            tag: 标签字符串，用于区分截图场景

        Returns:
            str: 保存的文件路径，失败返回 None
        """
        try:
            self._debug_seq += 1
            ts: str = time.strftime('%H%M%S')
            filename: str = f'{ts}_{self._debug_seq:03d}_{tag}.png'
            filepath: str = os.path.join(self._debug_dir, filename)
            img, _, _, _, _ = self.snap()
            img.save(filepath)
            return filepath
        except Exception:
            return None

    # ── OCR 识别 ────────────────────────────────────────────────

    def _ocr_img(
        self, img: Image.Image, roi: Any = None
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """对 PIL Image 执行 OCR，返回文字块列表。

        Args:
            img: PIL Image
            roi: 可选 (x, y, w, h) 截图区域（像素坐标），MaaOCR 内部裁剪

        Returns:
            [(cy, cx, text, conf, x_min, x_max), ...] 按 cy 排序
            空列表：ocr_engine 未初始化时
        """
        if self._ocr_engine is None:
            return []
        results: List = self._ocr_engine.recognize(img, roi=roi)
        items: List[Tuple[int, int, str, float, int, int]] = []
        for text, box, score in results:
            # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            xs: List[float] = [pt[0] for pt in box]
            ys: List[float] = [pt[1] for pt in box]
            cx: int = int(sum(xs) / len(xs))
            cy: int = int(sum(ys) / len(ys))
            x_min: int = int(min(xs))
            x_max: int = int(max(xs))
            items.append((cy, cx, text, score, x_min, x_max))
        items.sort()
        return items

    def _ocr_fingerprint(self, img: Image.Image) -> int:
        """计算截图 OCR 缓存指纹（20 采样点）。

        与 frame_fingerprint 不同：采样点更多，包含边缘区域，
        确保 UI 微小变化能产生不同缓存键。

        Args:
            img: PIL Image

        Returns:
            int: 指纹哈希值
        """
        w: int = img.width
        h: int = img.height
        arr: np.ndarray = np.array(img.convert('RGB'))
        sample_pts: List[Tuple[int, int]] = [
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
        pixels: Tuple = tuple(
            tuple(arr[y, x].tolist()) for y, x in sample_pts
        )
        return hash(pixels)

    def ocr_screen(
        self, img: Optional[Image.Image] = None, no_cache: bool = False
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """全屏 OCR 识别（带缓存，60 秒内相同画面不重复识别）。

        Args:
            img: 可选 PIL Image，为 None 时自动截图
            no_cache: **True = 跳过结果缓存，强制重新识别**（v2.2.15）

        no_cache 为什么必须有：缓存键是**稀疏采样指纹**（10 个采样点，
        见 `_ocr_fingerprint`）——**小面积变化不会改变指纹** ✗。
        真机 2026-09-17 实例：右下角沙漏徽标 x3→x2→x1（用户截图证实数字
        确实在递减），但徽标位置离最近采样点约 100px → 指纹不变 → 每次
        轮询都命中 60s 缓存、返回**旧的 x3** → 被误判"点击未被结算" →
        反复补点（历史上甚至点到钻石付费档）✗
        ⇒ **凡是靠"画面细微变化"判断的读数（如沙漏次数轮询）必须 no_cache=True**。

        Returns:
            [(cy, cx, text, conf, x_min, x_max), ...] 按 cy 排序
        """
        if img is None:
            img, _, _, _, _ = self.snap()
        # v1.9.37：空截图（窗口最小化等）→ 跳过本帧 OCR，返回空结果
        #（任务层按未知画面走正常等待/重试链路，不崩）
        if img.width <= 0 or img.height <= 0:
            # v2.0.2：向调用方暴露「画面未就绪」状态（导航据此延长等待，
            # 而非当作未知画面消耗轮次）
            self._last_snap_empty = True
            # v2.0.5（用户 18:48）：**没有窗口 = 游戏未启动（正常状态）**
            # → 静默返回，不打警告、不做无意义截图调用；
            # 只有「有句柄但尺寸 0」才是异常（未渲染/最小化）→ 警告+诊断
            if not self._hwnd:
                return []
            self._log('warn',
                     '截图尺寸为 0（窗口未就绪或最小化），跳过本帧 OCR')
            # v2.0.3：首次出现时打一次诊断（hwnd/标题/矩形/候选），
            # 便于区分「句柄选错」与「窗口确实未渲染」——只打一次防刷屏
            if not getattr(self, '_empty_diag_logged', False):
                self._empty_diag_logged = True
                try:
                    import win32gui as _wg
                    _rect = (list(_wg.GetWindowRect(self._hwnd))
                             if self._hwnd else None)
                    _title = (_wg.GetWindowText(self._hwnd)
                              if self._hwnd else '')
                    _cli = None
                    try:
                        _cli = list(_wg.GetClientRect(self._hwnd)) \
                            if self._hwnd else None
                    except Exception:
                        _cli = None
                    self._log('warn',
                              f"[诊断] 截图句柄 hwnd={self._hwnd} "
                              f"标题={_title!r} 窗口矩形={_rect} "
                              f"客户区={_cli}（客户区 0 = 窗口最小化）")
                except Exception:
                    pass
            return []
        self._last_snap_empty = False

        # 计算画面指纹用作缓存键（no_cache=True 时整段跳过）
        key: int = 0
        now: float = time.time()
        if not no_cache:
            key = self._ocr_fingerprint(img)
            if key in self._ocr_cache:
                cached_time: float
                cached_items: List
                cached_time, cached_items = self._ocr_cache[key]
                if now - cached_time < self._ocr_cache_ttl:
                    return cached_items

        items: List = self._ocr_img(img)
        if not no_cache:
            self._ocr_cache[key] = (now, items)

        # 新OCR完成：输出识别文本摘要
        texts: List[str] = [t[2] for t in items if t[3] >= 0.5]
        if texts:
            self._log(
                'info',
                f'ℹ️ OCR扫描完成 | '
                f'共{len(items)}条: {texts[:15]}'
                f'{"..." if len(texts) > 15 else ""}'
            )

        # 限制缓存大小：超过 10 条时保留最新 10 条
        if len(self._ocr_cache) > 10:
            sorted_keys: List[int] = sorted(
                self._ocr_cache.keys(),
                key=lambda k: self._ocr_cache[k][0],
                reverse=True,
            )
            for k in sorted_keys[10:]:
                del self._ocr_cache[k]

        return items
