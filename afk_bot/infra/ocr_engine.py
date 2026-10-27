"""
afk_bot OCR 引擎模块

TextRecognizer 封装文本规范化、OCR 结果查找、OCR 记忆查找等逻辑。
OCR 底层调用委托给 ScreenCapture，文本查找逻辑集中在此模块。

从 afk_bot.py 提取，保持行为零回归。
"""

import unicodedata
from typing import Optional, Tuple, List, Any


class TextRecognizer:
    """OCR 文本识别引擎。

    封装 Unicode NFKC 标准化、OCR 结果查找（精确/模糊/排除）、
    带记忆的 OCR 查找等文本处理逻辑。底层 OCR 调用委托给 ScreenCapture。

    Attributes:
        _screen: ScreenCapture 实例，提供 _ocr_img() 和 ocr_screen()
        _ocr_memory: OCRMemory 实例，提供策略记忆与自动尝试
    """

    def __init__(self, screen_capture: Any, ocr_memory: Any,
                 log_callback=None):
        """初始化 TextRecognizer。

        Args:
            screen_capture: ScreenCapture 实例，需提供 _ocr_img(img, roi)
                            和 ocr_screen(img) 方法
            ocr_memory: OCRMemory 实例，提供 get_attempt_order(),
                        remember() 等方法
            log_callback: 日志回调函数，签名: fn(level, message)
        """
        self._screen: Any = screen_capture
        self._ocr_memory: Any = ocr_memory
        self._log = log_callback or (lambda level, msg: None)

    # ── OCR 委托 ─────────────────────────────────────────────────

    def _ocr_img(
        self, img: Any, roi: Any = None
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """对 PIL Image 执行 OCR，返回文字块列表。

        委托给 ScreenCapture._ocr_img()。

        Args:
            img: PIL Image
            roi: 可选 (x, y, w, h) 截图区域（像素坐标）

        Returns:
            [(cy, cx, text, conf, x_min, x_max), ...] 按 cy 排序
        """
        return self._screen._ocr_img(img, roi=roi)

    def ocr_screen(
        self, img: Any = None
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """全屏 OCR 识别（带缓存，60 秒内相同画面不重复识别）。

        委托给 ScreenCapture.ocr_screen()。

        Args:
            img: 可选 PIL Image，为 None 时 ScreenCapture 内部截图

        Returns:
            [(cy, cx, text, conf, x_min, x_max), ...] 按 cy 排序
        """
        return self._screen.ocr_screen(img=img)

    # ── 文本标准化 ───────────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """Unicode NFKC 标准化，解决 OCR 编码差异导致的匹配失败。

        Args:
            text: 原始文本

        Returns:
            str: NFKC 标准化后的文本
        """
        return unicodedata.normalize('NFKC', text)

    # ── 核心文本查找 ─────────────────────────────────────────────

    def _find_text_impl(
        self,
        items: List[Tuple],
        keyword: str,
        exact: bool = False,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """在 OCR 结果中查找文字，支持 Unicode NFKC 标准化匹配。

        兼容 4 元组 (cy, cx, text, conf) 和 6 元组
        (cy, cx, text, conf, x_min, x_max)。
        当 OCR 将相邻文字合并为一个块时（如"玩法目录英雄厅堂"），
        按关键词在合并文字中的字符位置比例精确估算 cx，避免点击到中间位置。

        匹配策略（按优先级）：
        1. exact=True: 精确匹配 keyword == NFKC(OCR_text)
        2. keyword 包含在 OCR_text 中（partial，正向匹配）
        3. OCR_text 包含 keyword 的主要部分
           （反向 fallback，应对 OCR 首字截断问题）
           - 当 OCR 识别为"键采用"而目标是"一键采用"时，走此路径
           - 仅当 keyword 长度>=3 时触发
           - 取 confidence >= min_conf 且最高的那个结果

        Args:
            items: OCR 结果列表 [(cy, cx, text, conf, ...), ...]
            keyword: 要查找的关键词
            exact: 是否精确匹配（NFKC 全等或包含关系）
            min_conf: 最低置信度阈值

        Returns:
            (cx, cy) 或 None
        """
        norm_kw: str = self._normalize(keyword)
        for item in items:
            if len(item) >= 6:
                cy, cx, text, conf, x_min, x_max = (
                    item[0], item[1], item[2], item[3], item[4], item[5]
                )
            else:
                cy, cx, text, conf = item[0], item[1], item[2], item[3]
                x_min, x_max = cx, cx
            if conf < min_conf:
                continue
            norm_text: str = self._normalize(text)
            if exact:
                matched: bool = (
                    norm_text == norm_kw
                    or norm_text.startswith(norm_kw)
                    or norm_kw in norm_text
                )
            else:
                matched = (norm_kw in norm_text)
            if matched:
                # 若 OCR 合并了多个相邻文字，按字符比例定位到关键词中心
                if norm_text != norm_kw and (x_max - x_min) > 10:
                    try:
                        idx: int = norm_text.index(norm_kw)
                        total_chars: int = len(norm_text)
                        kw_center_char: float = idx + len(norm_kw) / 2.0
                        ratio: float = kw_center_char / total_chars
                        cx = int(x_min + (x_max - x_min) * ratio)
                    except ValueError:
                        pass
                self._log(
                    'info',
                    f'ℹ️ OCR命中 | 关键词="{keyword}" '
                    f'→ OCR文本="{text}" '
                    f'置信度={conf:.2f} '
                    f'坐标=({cx},{cy}), '
                    f'共扫描{len(items)}条'
                )
                return cx, cy

        # ── 反向 substring fallback ───────────────────────────────
        # 处理 OCR 首字截断："一键采用"可能被识别为"键采用"
        if len(keyword) >= 3:
            suffix: str = keyword[1:]  # 去掉首字，保留剩余部分
            norm_suffix: str = self._normalize(suffix)
            best_conf: float = min_conf
            for item in items:
                if len(item) >= 6:
                    cy, cx, text, conf, x_min, x_max = (
                        item[0], item[1], item[2], item[3], item[4], item[5]
                    )
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
                            cx = int(x_min + (x_max - x_min) * ratio)
                        except (ValueError, ZeroDivisionError):
                            pass
                    return cx, cy
        return None

    # ── 公开文本查找 ─────────────────────────────────────────────

    def find_text(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """模糊查找：keyword 包含在 OCR 文字中即匹配。

        Args:
            items: OCR 结果列表
            keyword: 关键词
            min_conf: 最低置信度

        Returns:
            (cx, cy) 或 None
        """
        return self._find_text_impl(
            items, keyword, exact=False, min_conf=min_conf
        )

    def find_text_exact(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配：优先 exact (NFKC)，fallback 到 partial match。

        处理 OCR 编码错误，如 "幻灵挑战" 被识别为乱码。

        Args:
            items: OCR 结果列表
            keyword: 关键词
            min_conf: 最低置信度

        Returns:
            (cx, cy) 或 None
        """
        pos: Optional[Tuple[int, int]] = self._find_text_impl(
            items, keyword, exact=True, min_conf=min_conf
        )
        if pos:
            return pos
        # fallback: partial match
        return self._find_text_impl(
            items, keyword, exact=False, min_conf=min_conf
        )

    def find_text_exclude(
        self,
        items: List[Tuple],
        keyword: str,
        exclude_keywords: List[str],
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配 keyword，但排除包含 exclude_keywords 中任意一项的结果。

        用于解决 "挑战" 和 "幻灵挑战" 部分匹配冲突的问题。

        Args:
            items: OCR 结果列表
            keyword: 关键词（必须精确匹配）
            exclude_keywords: 排除词列表（结果不能包含这些词）
            min_conf: 最低置信度

        Returns:
            (cx, cy) 或 None
        """
        norm_kw: str = self._normalize(keyword)
        norm_exclude: List[str] = [
            self._normalize(e) for e in exclude_keywords
        ]
        for item in items:
            cy, cx, text, conf = item[0], item[1], item[2], item[3]
            if conf < min_conf:
                continue
            norm_text: str = self._normalize(text)
            # 必须精确匹配 keyword
            if norm_kw != norm_text:
                continue
            # 不能包含任何排除词
            skip: bool = any(ex in norm_text for ex in norm_exclude)
            if skip:
                continue
            return cx, cy
        return None

    # ── 带 OCR 记忆的查找 ────────────────────────────────────────

    def find_with_memory(
        self,
        keyword: str,
        scene: str = 'default',
        exact: bool = False,
        y_min_ratio: Optional[float] = None,
    ) -> Optional[Tuple[int, int]]:
        """带 OCR 记忆的查找：自动尝试记忆/默认策略，直到成功。

        每次尝试可能使用不同 ROI 重新截图+OCR，找到后自动记住成功策略。

        Args:
            keyword: 要查找的文字
            scene: 场景标识（如 'navigate', 'idle_stage', 'battle' 等）
            exact: 是否精确匹配
            y_min_ratio: 可选，只匹配底部比例阈值以上的文字

        Returns:
            (cx, cy) 绝对坐标，或 None（全部失败）
        """
        strategies: List[Any] = self._ocr_memory.get_attempt_order(
            scene, keyword
        )

        for strat in strategies:
            img, l, t, w, h = self._screen.snap()

            if strat.roi:
                rx, ry, rw, rh = strat.roi
                x: int = max(0, int(rx * w))
                y: int = max(0, int(ry * h))
                sw: int = min(w - x, max(1, int(rw * w)))
                sh: int = min(h - y, max(1, int(rh * h)))
                cropped = img.crop((x, y, x + sw, y + sh))
                items: List[Tuple] = self._screen._ocr_img(cropped)
                # 坐标转换回绝对坐标，保留 x_min/x_max 并平移
                abs_items: List[Tuple] = [
                    (
                        cy_item + y, cx_item + x, text, conf,
                        xmn + x, xmx + x
                    )
                    for cy_item, cx_item, text, conf, xmn, xmx in items
                ]
            else:
                items = self._screen.ocr_screen(img)
                abs_items = items  # noqa

            # y_min_ratio 过滤：只匹配底部区域的文字
            if y_min_ratio is not None:
                y_thresh: int = int(y_min_ratio * h)
                abs_items = [
                    item for item in abs_items if item[0] >= y_thresh
                ]

            pos: Optional[Tuple[int, int]] = self._find_text_impl(
                abs_items, keyword, exact=exact, min_conf=strat.min_conf
            )
            if pos:
                self._ocr_memory.remember(scene, keyword, strat)
                return pos

        return None
