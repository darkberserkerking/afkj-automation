"""
afk_bot OCR 记忆系统

OCRMemory 记录每个关键词/场景对应的最佳识别策略，避免重复试错。
从 afk_bot.py 直接提取，保持行为零回归。

注意：find_text_with_memory() 暂时保持 bot 参数接口，
未来在 core 层通过依赖注入替换（core.py 已并入 auto_farm/loop.py）为 snap_fn + ocr_fn 回调。
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional, Tuple, List, Dict, Any


@dataclass
class OCRStrategy:
    """OCR 策略参数。

    Attributes:
        roi: 可选 (rx, ry, rw, rh) 比例坐标区域
        color_filter: MaaOCR color_filter 参数
        min_conf: 置信度阈值
        retries: 重试次数
        note: 备注说明
    """
    roi: Optional[Tuple[float, float, float, float]] = None
    color_filter: Optional[Dict[str, Any]] = None
    min_conf: float = 0.5
    retries: int = 1
    note: str = ""


class OCRMemory:
    """OCR 记忆器 — 记录每个关键词/场景对应的最佳识别策略。

    使用方式：
        memory = OCRMemory('ocr_memory.json')

        # 尝试 OCR 识别，失败后换策略
        result = memory.find_text_with_memory(bot, '幻灵挑战', scene='navigate')
        if result:
            memory.remember('navigate', '幻灵挑战', strategy)  # 记住成功策略

    Attributes:
        cache_path: 记忆持久化文件路径
        memory: {(scene, keyword): OCRStrategy} dict
    """

    # 默认尝试顺序（从快到慢）
    DEFAULT_STRATEGIES: List[OCRStrategy] = [
        OCRStrategy(min_conf=0.5),              # 1. 全屏 + 标准置信度
        OCRStrategy(min_conf=0.3),              # 2. 全屏 + 低置信度
        OCRStrategy(roi=(0.2, 0.2, 0.6, 0.6)), # 3. 中心区域
        OCRStrategy(roi=(0.0, 0.0, 1.0, 1.0)), # 4. 全屏（显式）
        OCRStrategy(min_conf=0.2),              # 5. 极低置信度
    ]

    def __init__(self, cache_path: str = 'ocr_memory.json',
                 log: 'Optional[Callable[[str], None]]' = None):
        """初始化 OCR 记忆器。

        Args:
            cache_path: 记忆持久化 JSON 文件路径
            log: 可选日志回调（v1.8.50：修复上一轮"静默化过度"——
                 禁止 print 出口但不得吞掉失败，无回调时才静默）
        """
        self.cache_path: str = cache_path
        self._log = log
        # {(scene, keyword): OCRStrategy}
        self.memory: Dict[Tuple[str, str], OCRStrategy] = {}
        self._load()

    def _key(self, scene: str, keyword: str) -> Tuple[str, str]:
        """生成记忆键（场景 + 关键词，小写化）。"""
        return (scene.lower(), keyword)

    def _load(self) -> None:
        """从文件加载记忆。"""
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                data: dict = json.load(f)
            for key_str, strat in data.items():
                scene: str
                keyword: str
                scene, keyword = key_str.split('|||', 1)
                self.memory[(scene, keyword)] = OCRStrategy(
                    roi=tuple(strat['roi']) if strat.get('roi') else None,
                    color_filter=strat.get('color_filter'),
                    min_conf=strat.get('min_conf', 0.5),
                    retries=strat.get('retries', 1),
                    note=strat.get('note', ''),
                )
        except Exception as e:
            # v1.8.50：有 logger 则留痕（规范 2.4），无 logger 才静默降级
            if self._log:
                self._log(f'[警告] OCR记忆加载失败 | {e}')

    def _save(self) -> None:
        """保存记忆到文件。"""
        try:
            data: Dict[str, dict] = {}
            for (scene, keyword), strat in self.memory.items():
                data[f'{scene}|||{keyword}'] = asdict(strat)
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # v1.8.50：同上（保存失败同样留痕）
            if self._log:
                self._log(f'[警告] OCR记忆保存失败 | {e}')

    def remember(
        self, scene: str, keyword: str, strategy: OCRStrategy
    ) -> None:
        """记住成功的策略。

        Args:
            scene: 场景标识（如 'navigate', 'idle_stage', 'battle' 等）
            keyword: 关键词
            strategy: 成功的 OCRStrategy
        """
        key: Tuple[str, str] = self._key(scene, keyword)
        # 如果策略相同就不重复保存
        if key in self.memory:
            existing: OCRStrategy = self.memory[key]
            if (existing.roi == strategy.roi and
                    abs(existing.min_conf - strategy.min_conf) < 0.05):
                return  # 相同策略，无需更新
        self.memory[key] = strategy
        self._save()

    def get_strategy(
        self, scene: str, keyword: str
    ) -> Optional[OCRStrategy]:
        """获取记忆的策略。

        Args:
            scene: 场景标识
            keyword: 关键词

        Returns:
            OCRStrategy 或 None
        """
        return self.memory.get(self._key(scene, keyword))

    def get_attempt_order(
        self, scene: str, keyword: str
    ) -> List[OCRStrategy]:
        """获取尝试顺序：优先使用记忆的策略，然后是默认策略。

        Args:
            scene: 场景标识
            keyword: 关键词

        Returns:
            按优先级排列的 OCRStrategy 列表
        """
        strategies: List[OCRStrategy] = []
        key: Tuple[str, str] = self._key(scene, keyword)

        # 1. 优先尝试记忆的策略
        if key in self.memory:
            strategies.append(self.memory[key])

        # 2. 添加默认策略（跳过已尝试的）
        known: Optional[OCRStrategy] = strategies[0] if strategies else None
        for strat in self.DEFAULT_STRATEGIES:
            if known is None or (
                strat.roi != known.roi or
                abs(strat.min_conf - known.min_conf) > 0.1
            ):
                strategies.append(strat)

        return strategies

    def find_text_with_memory(
        self,
        bot: Any,
        keyword: str,
        scene: str = 'default',
        exact: bool = False,
        y_min_ratio: Optional[float] = None,
    ) -> Optional[Tuple[int, int]]:
        """带记忆的 find_text：自动尝试记忆/默认策略，直到成功。

        注意：bot 参数暂时保持原始接口，未来重构为 snap_fn + ocr_fn 回调。

        Args:
            bot: AFKBot 实例（有 _snap, _ocr_img, _find_text_impl 等方法）
            keyword: 要查找的文字
            scene: 场景标识（如 'navigate', 'idle_stage', 'battle' 等）
            exact: 是否精确匹配
            y_min_ratio: 可选，只匹配底部比例阈值以上的文字

        Returns:
            (cx, cy) 绝对坐标，或 None（全部失败）
        """
        strategies: List[OCRStrategy] = self.get_attempt_order(scene, keyword)

        for strat in strategies:
            img, l, t, w, h = bot._snap()

            if strat.roi:
                rx: float
                ry: float
                rw: float
                rh: float
                rx, ry, rw, rh = strat.roi
                x: int = max(0, int(rx * w))
                y: int = max(0, int(ry * h))
                sw: int = min(w - x, max(1, int(rw * w)))
                sh: int = min(h - y, max(1, int(rh * h)))
                cropped = img.crop((x, y, x + sw, y + sh))
                items = bot._ocr_img(cropped)
                # 坐标转换回绝对坐标，保留 x_min/x_max 并平移
                abs_items: List[Tuple] = [
                    (cy_item + y, cx_item + x, text, conf, xmn + x, xmx + x)
                    for cy_item, cx_item, text, conf, xmn, xmx in items
                ]
            else:
                items = bot._ocr_img(img)
                abs_items = items

            # y_min_ratio 过滤：只匹配底部区域的文字
            if y_min_ratio is not None:
                y_thresh: int = int(y_min_ratio * h)
                abs_items = [
                    item for item in abs_items if item[0] >= y_thresh
                ]

            pos: Optional[Tuple[int, int]] = bot._find_text_impl(
                abs_items, keyword, exact=exact, min_conf=strat.min_conf
            )
            if pos:
                self.remember(scene, keyword, strat)
                return pos

        return None

    def debug_dump(self) -> str:
        """打印记忆内容（调试用）。

        Returns:
            str: 格式化的记忆内容字符串
        """
        if not self.memory:
            return '[OCR Memory] empty'
        lines: List[str] = ['[OCR Memory] strategies:']
        for (scene, keyword), strat in sorted(self.memory.items()):
            roi_str: str = f'roi={strat.roi}' if strat.roi else 'fullscreen'
            lines.append(
                f'  {scene:12} | {keyword:12} | '
                f'conf={strat.min_conf} | {roi_str}'
            )
        return '\n'.join(lines)
