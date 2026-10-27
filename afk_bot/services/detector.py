"""
afk_bot 阶段检测模块

PhaseDetector 封装游戏阶段检测逻辑：战斗阶段、UI 阶段、导航阶段、
赛季关卡检测等。OCR 调用委托给 ScreenCapture，文本查找委托给
TextRecognizer。

从 afk_bot.py 提取，保持行为零回归。
"""

import re
from typing import Optional, Tuple, List, Any, Callable


class PhaseDetector:
    """游戏阶段检测器。

    检测当前游戏画面所处的阶段（战斗/加载/挂机关卡/备战/结算等），
    以及赛季关卡数字。

    Attributes:
        _screen: ScreenCapture 实例，提供 snap(), ocr_screen(), debug_screenshot()
        _recognizer: TextRecognizer 实例，提供 find_text() 等文本查找方法
        _cache_has_prep: 缓存标志 — 是否有通关阵容面板
        _cache_has_formation: 缓存标志 — 是否有阵容匹配面板
        _log_fn: 可选日志回调
    """

    def __init__(
        self,
        screen_capture: Any,
        recognizer: Any,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        """初始化 PhaseDetector。

        Args:
            screen_capture: ScreenCapture 实例
            recognizer: TextRecognizer 实例
            log_callback: 可选日志回调，接收消息字符串，为 None 时静默
        """
        self._screen: Any = screen_capture
        self._recognizer: Any = recognizer
        self._log_fn: Optional[Callable[[str], None]] = log_callback

        # 缓存标志（由 _detect_battle_phase 设置，供 _detect_navigation_phase 使用）
        self._cache_has_prep: bool = False
        self._cache_has_formation: bool = False

    # ── 内部日志 ─────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """输出日志（如果设置了回调）。

        Args:
            msg: 日志消息
        """
        if self._log_fn is not None:
            self._log_fn(msg)

    # ── 主检测入口 ───────────────────────────────────────────────

    def detect_phase(self) -> str:
        """识别当前游戏阶段（重构版：调用子函数）。

        截图一次 + OCR 一次（无缓存），子函数复用同一份 OCR 结果。

        注意：必须使用 _ocr_img()（无 60s 缓存）而非 ocr_screen()。
        历史教训：秒杀场景战斗结束太快，结算界面弹出时 detect_phase
        的截图指纹与旧画面碰撞命中 ocr_screen 缓存 → 返回旧 OCR 结果
        → 结算界面被误判为 unknown → 空转数秒。无缓存路径确保
        阶段检测永远基于当前真实画面。

        Returns:
            阶段标识字符串：
            'battle' | 'pause' | 'result' | 'formation' | 'idle_stage' | 'loading' |
            'login' | 'confirm_dialog' | 'options_menu' | 'notice' |
            'world' | 'home' | 'playbook' | 'labyrinth' | 'unknown'
        """
        # 截图 + OCR（各只做一次，子函数复用）
        img, l, t, w, h = self._screen.snap()
        items: List[Tuple] = self._screen._ocr_img(img)

        # 1. 检测战斗阶段（最高优先级）
        phase: Optional[str] = self._detect_battle_phase(img, items, w, h)
        if phase:
            return phase

        # 2. 检测 UI 阶段
        phase = self._detect_ui_phase(img, items, w, h)
        if phase:
            return phase

        # 3. 检测导航阶段
        phase = self._detect_navigation_phase(img, items, w, h)
        if phase and phase != 'unknown':
            return phase

        # 未知
        self._screen.debug_screenshot('phase_unknown')
        return 'unknown'

    # ── 战斗阶段检测 ─────────────────────────────────────────────

    def _detect_battle_phase(
        self, img: Any, items: List[Tuple], w: int, h: int
    ) -> Optional[str]:
        """检测战斗相关阶段。

        返回：
            'battle'：战斗中（HUD 文字 + 退出按钮）
            'result'：战斗结算（胜利/失败/自动结束）
            'formation'：备战界面
            'confirm_dialog'：确认对话框
            None：不是战斗阶段

        Args:
            img: PIL Image 截图
            items: 复用自 detect_phase 的 OCR 结果（无缓存，真实画面）
            w: 图像宽度
            h: 图像高度

        Returns:
            阶段标识字符串或 None
        """
        # OCR 结果由 detect_phase 统一执行（置信度 >= 0.3）
        all_texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]

        # 备战/阵容面板检测标志（在前面定义，供后续使用）
        has_prep: bool = any('通关阵容' in t for t in all_texts)
        has_formation: bool = any(
            '一键采用' in t or '阵容匹配' in t for t in all_texts
        )

        # 保存标志供其他函数使用
        self._cache_has_prep = has_prep
        self._cache_has_formation = has_formation

        # 0. 检查战斗暂停界面（优先级最高，防止误判为result）
        if any('暂停中' in t for t in all_texts):
            has_pause_action: bool = any(
                '继续' in t or '再次挑战' in t or '退出战斗' in t
                for t in all_texts
            )
            if has_pause_action:
                return 'pause'

        # 1. 检查 result（优先）：战斗失败/再次挑战/自动挑战结束
        if any(
            '自动挑战结束' in t or '自动战斗结束' in t for t in all_texts
        ):
            return 'result'
        if any(
            '挑战失败' in t or '战斗失败' in t or '战败' in t
            or '再次挑战' in t
            for t in all_texts
        ):
            return 'result'
        # 结算界面大标题（"战斗胜利"——艺术字 OCR 可能识别不到）
        if any('胜利' in t for t in all_texts):
            return 'result'
        # 结算界面特有副标题（"挂机收益提升"）—— 挂机关卡页/备战界面都没有，
        # 幻灵通关后挂机关卡页只剩"挑战"按钮时也不会误判
        if any('挂机收益提升' in t for t in all_texts):
            return 'result'

        # 2. 检查 formation（优先于"点击空白处关闭"）
        # 注意：不要用 '>' 做关键词 —— 结算界面常有 ">"（箭头/战力对比），
        # 会误判成备战界面
        _formation_kws: List[str] = [
            '一键采用', '阵容匹配', '只显示已拥有'
        ]
        if any(
            any(kw in t for kw in _formation_kws) for t in all_texts
        ):
            return 'formation'
        if has_prep:
            return 'formation'
        if any('点击空白处关闭' in t for t in all_texts):
            if any(
                any(kw in t for kw in _formation_kws) for t in all_texts
            ):
                return 'formation'
            return 'result'
        if any(
            '点击关闭' in t and '领取' not in t and '奖励' not in t
            for t in all_texts
        ):
            return 'result'

        # 3. 检查 confirm_dialog
        if any('退出游戏' in t for t in all_texts):
            return 'confirm_dialog'

        # 4. 检查 battle 状态（自动挑战中 或 读秒）
        if any(
            '是否关闭' in t and '自动挑战' in t for t in all_texts
        ):
            return 'battle'
        if any('自动挑战中' in t for t in all_texts) and not has_prep:
            return 'battle'
        # 战斗检测：通过读秒"XX秒"判断是否在战斗中
        has_countdown: bool = any(
            re.search(r'^\d+秒$', t.strip()) for t in all_texts
        )
        if has_countdown and not has_prep:
            return 'battle'
        # 标志：有"赛季挂机关卡"或"赛季幻灵关卡"且不在备战/挂机关卡页
        has_main_btn: bool = any(
            t == '挑战' or '幻灵挑战' in t for t in all_texts
        )
        has_battle_text: bool = any(
            ('赛季挂机关卡' in t or '赛季幻灵关卡' in t)
            and (cy < h / 4)
            for cy, _, t, c, *_ in items
        )
        if has_battle_text and not has_formation and not has_main_btn \
                and not has_prep:
            return 'battle'

        return None

    # ── UI 阶段检测 ──────────────────────────────────────────────

    def _detect_ui_phase(
        self, img: Any, items: List[Tuple], w: int, h: int
    ) -> Optional[str]:
        """检测 UI 相关阶段。

        返回：
            'login'：登录界面
            'loading'：加载中
            'confirm_dialog'：确认框
            'options_menu'：选项菜单
            'quest_panel'：任务面板
            None：不是 UI 阶段

        Args:
            img: PIL Image 截图
            items: 复用自 detect_phase 的 OCR 结果（无缓存，真实画面）
            w: 图像宽度
            h: 图像高度

        Returns:
            阶段标识字符串或 None
        """
        # OCR 结果由 detect_phase 统一执行（置信度 >= 0.3）
        all_texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]

        # 1. 检查加载界面（含 splash screen）
        _loading_keywords: List[str] = [
            '加载中', '加载游戏', '正在加载', '初始化',
            'FARLIGHT', 'LILITH',
        ]
        _has_loading_kw: bool = any(
            kw in t for kw in _loading_keywords for t in all_texts
        )
        _has_loading_progress: bool = any(
            'Loading' in t and '进度' in t for t in all_texts
        )
        if _has_loading_kw or _has_loading_progress:
            self._log('[信息] 加载中')
            return 'loading'

        # 2. 检查登录界面
        login_texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]
        if any('点击开始游戏' in t for t in login_texts):
            return 'login'

        # 3. 检查选项菜单
        menu_items: List[str] = [
            '背包', '邮件', '社交', '编队', '图鉴', '商城'
        ]
        found_menu: int = sum(
            1 for item in menu_items if any(item in t for t in all_texts)
        )
        if found_menu >= 3:
            return 'options_menu'

        # 4. 检查任务面板
        if any('每日任务' in t for t in all_texts) \
                or any('AFK' in t for t in all_texts) \
                or any('离线奖励' in t for t in all_texts) \
                or any('任务详情' in t for t in all_texts):
            return 'quest_panel'

        return None

    # ── 导航阶段检测 ─────────────────────────────────────────────

    def _detect_navigation_phase(
        self, img: Any, items: List[Tuple], w: int, h: int
    ) -> str:
        """检测导航相关阶段。

        返回：
            'idle_stage'：挂机关卡态（2级：有"幻灵挑战/挑战"按钮）
            'formation'：备战态（战斗流）
            'world' | 'home'：0 层（世界/家园，0层内两个并列子类：有"玩法目录"字样，右下角「世界」/「家园」按钮切换）
            'playbook'：玩法目录态（1级：含"挂机关卡"条目）
            'labyrinth'：异界迷宫态（2级玩法内页，v1.7.2 新增）
            'activity'：活动态（2级）
            'unknown'：未知态

        Args:
            img: PIL Image 截图
            items: 复用自 detect_phase 的 OCR 结果（无缓存，真实画面）
            w: 图像宽度
            h: 图像高度

        Returns:
            阶段标识字符串
        """
        # OCR 结果由 detect_phase 统一执行（置信度 >= 0.3）
        all_texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]

        # 获取缓存标志（由 _detect_battle_phase 设置）
        has_formation: bool = self._cache_has_formation
        has_prep: bool = self._cache_has_prep
        has_main_btn: bool = any(
            t == '挑战' or '幻灵挑战' in t for t in all_texts
        )
        # v1.8.13 赛季挂机关卡页（用户真机 9/4）：无独立「挑战/幻灵挑战」
        # 按钮（显示「已完成全部挑战」大标题 + 「英雄厅堂」），卡片标题
        # 「赛季挂机关卡」含 playbook 子串「挂机关卡」→ 曾误判 playbook
        # 死循环点标题。加赛季页正向特征词；本检查在 playbook 之前（原有
        # 顺序），命中即优先判 idle_stage。不用「英雄厅堂」（其他界面
        # 可能出现）；备战界面有 formation/prep 特征反向排除兜底。
        _season_idle: bool = any(
            '已完成全部挑战' in t or '赛季挂机关卡' in t
            for t in all_texts
        )

        # 1. 检查挂机关卡页（idle_stage）
        if (has_main_btn or _season_idle) \
                and not has_formation and not has_prep:
            return 'idle_stage'

        # 1.5 检查异界迷宫（2级玩法内页，v1.7.2 新增；v1.7.11 加冗余判据）
        # 判据：有迷宫正向特征 且 无玩法目录特征（底部菜单 + 常规玩法/活动标签页）。
        # 玩法目录里滚动看到「异界迷宫」卡片时，画面仍带「玩法目录」菜单或「常规玩法」tab，
        # 会被 playbook 特征排除 → 不会误判为已进入。
        # v1.7.8 真机校准：迷宫主页正向词 = 标题「异界迷宫」 + 「难度N」 + 「进入」 + 「探索阵容(N/12)」
        # v1.7.11 真机修复：标题「异界迷宫」是艺术字，MaaOCR 漏检率高（实测连续 4 帧全漏）
        #   → 正向 A「异界迷宫」标题 OR 正向 B「探索阵容」（迷宫主页专属普通字体、OCR 稳定）：
        #   B 兜底 A，任一命中 + 无 playbook 词即判 labyrinth。
        # 反向判据（A/B 共用）：无「玩法目录」「常规玩法」「活动」tab。
        _playbook_marks: List[str] = ['玩法目录', '常规玩法', '活动']
        _has_lab_title: bool = any('异界迷宫' in t for t in all_texts)
        _has_lab_roster: bool = any('探索阵容' in t for t in all_texts)
        if (_has_lab_title or _has_lab_roster) and not any(
            kw in t for t in all_texts for kw in _playbook_marks
        ):
            return 'labyrinth'

        # 2. 检查玩法目录（优先于 activity）
        # v1.8.14 计数式判据（用户设计 2026-09-04，卡片A/B/C + 底部UI栏
        # 四张素材 MaaOCR 实测提取词表）：命中词数 >4 即判玩法目录。
        #   修复旧单子串判据两大缺陷：①「挂机关卡」卡片滚出屏幕即失效
        #   （词表覆盖 A/B/C 三屏卡片，滚到任意区域命中都 ≥5）；②「赛季
        #   挂机关卡」2 级页标题撞词（赛季页仅 1 命中，远低于阈值）。
        #   0 层底栏（神秘屋/玩法目录/英雄厅堂/公会，常驻 chrome）不在
        #   词表 → 0 层恒 0 命中；迷宫主页仅「异界迷宫」1 命中。
        #   注意：本检查在 activity 之前——活动页含「常规玩法/活动」页签
        #   仅 2 命中，活动内容卡不在词表，不会误判。
        _pb_marks: Tuple[str, ...] = (
            # 卡片 A（首屏：挂机关卡/女神试炼/迷梦之域/竞技场/巅峰联赛）
            '挂机关卡', '女神试炼', '迷梦之域', '竞技场', '巅峰竞技场',
            '巅峰联赛',
            # 卡片 B（滚动区：异界迷宫/公会征锋/天穹试炼/沙盘演兵/荣誉对决）
            '异界迷宫', '公会征锋', '天穹试炼', '沙盘演兵', '荣誉对决',
            '公平竞技',
            # 卡片 C
            '超凡挑战',
            # 底部页签（玩法目录底部UI栏.png 实测 0.99/1.00）
            '常规玩法', '活动',
        )
        _pb_hits: int = sum(
            1 for kw in _pb_marks if any(kw in t for t in all_texts)
        )
        if _pb_hits > 4:
            return 'playbook'

        # 3. 检查活动界面
        if any('活动' in t for t in all_texts) \
                and any('常规玩法' in t for t in all_texts):
            return 'activity'

        # 4. 检查公会界面（底部有"玩法目录"，需在 0 层之前识别）
        guild_keywords: List[str] = [
            '庇护之地', '商店', '公会挑战', '公会管理'
        ]
        guild_hits: int = sum(
            1 for kw in guild_keywords
            if any(kw in t for t in all_texts)
        )
        if guild_hits >= 3:
            return 'guild'

        # 5. 检查 0 层（世界/家园）
        # 0 层是抽象层，含两个并列子类：世界（world）和家园（home），
        # 共用底菜单（玩法目录/英雄厅堂/公会等），通过右下角「世界」/「家园」按钮互切。
        sub: Optional[str] = self._evaluate_level0()
        if sub == 'world':
            return 'world'
        if sub == 'home':
            return 'home'

        return 'unknown'

    # ── 0 层判断（世界/家园）──────────────────────────────────

    def _evaluate_level0(self) -> Optional[str]:
        """核心 0 层判断（世界/家园/非 0 层），snap+ocr 一次。

        切换按钮语义：右下角按钮显示「目的地」字样（同一位置互斥显示）。
        - 当前在世界 → 显示「家园」按钮（点击可切到家园）
        - 当前在家园 → 显示「世界」按钮（点击可切到世界）

        OCR 实测：
        - 「家园」按钮文字稳定识别（0.98 置信度）
        - 「世界」按钮文字 OCR 不可靠（字色/字号问题）

        故：world 判定走正向识别「家园」按钮；home 判定走反向推断（未识别到「家园」按钮）。

        Returns:
            'world' | 'home' | None
            - 'world'：0 层 · 世界（识别到右下角「家园」按钮文字）
            - 'home'：0 层 · 家园（未识别「家园」按钮 = 当前显示「世界」按钮可切到世界）
            - None：不在 0 层
        """
        # 截图 + OCR 一次（无缓存，真实画面）
        img, _, _, w, h = self._screen.snap()
        items: List[Tuple] = self._screen._ocr_img(img)

        if not self._is_level0_core(items, w):
            return None

        # 0 层内细化：识别右下角「家园」按钮文字（OCR 实测稳定 0.98）
        # 注意：按钮文字是「目的地」而非当前所在界面——
        # 识别到「家园」按钮表示当前在世界（点击可切到家园）。
        has_home_btn: bool = self._recognizer.find_text(items, '家园', min_conf=0.6) is not None
        return 'world' if has_home_btn else 'home'

    def _is_level0_core(self, items: List[Tuple], w: int) -> bool:
        """0 层判断核心逻辑（玩法目录 + 排除项 + 宽度兜底）。"""
        for _, _, t, c, *_ in items:
            if c >= 0.5 and ('玩法目录' in t or '玩法目' in t):
                return True
        menu_only: List[str] = ['背包', '商店', '社交', '编队', '邮件']
        menu_count: int = sum(
            1 for f in menu_only
            if self._recognizer.find_text(items, f, min_conf=0.5)
        )
        if menu_count >= 2:
            return False
        for _, _, t, c, *_ in items:
            if c >= 0.5 and t == '活动':
                return False
        if self._recognizer.find_text(items, '设置', min_conf=0.6) \
                and (self._recognizer.find_text(items, '图像')
                     or self._recognizer.find_text(items, '战斗')):
            return False
        if self._recognizer.find_text(items, '退出游戏'):
            return False
        if w < 900:
            return True
        return False

    def is_on_level0(self) -> bool:
        """检查是否在 0 层（世界/家园任一）。

        0 层是抽象层，含两个并列子类：世界（world）和家园（home），
        共用底菜单（玩法目录/英雄厅堂/公会等），通过右下角「世界」/「家园」按钮互切。
        综合多个 OCR 标志判断当前画面是否为 0 层（特征：有"玩法目录"字样）。

        Returns:
            bool: True 表示在 0 层（世界或家园）
        """
        return self._evaluate_level0() is not None

    def is_on_world_screen(self) -> bool:
        """检查是否在 0 层 · 世界（world）。

        0 层内识别到右下角「家园」按钮文字时判定为世界（按钮文字=目的地，表示可切到家园）。
        """
        return self._evaluate_level0() == 'world'

    def is_on_home_screen(self) -> bool:
        """检查是否在 0 层 · 家园（home）。

        0 层内未识别「家园」按钮文字（即当前显示「世界」按钮可切到世界）时判定为家园。
        「世界」按钮文字 OCR 不可靠，故 home 判定走反向推断。
        """
        return self._evaluate_level0() == 'home'

    # ── 赛季关卡检测 ─────────────────────────────────────────────

    def _detect_season_stages(
        self, items: List[Tuple]
    ) -> Tuple[int, int]:
        """检测备战界面的赛季关卡数（幻灵/挂机/先锋）。

        挂机关卡备战界面显示：赛季幻灵关卡 X / 赛季挂机关卡 X
        先锋关卡备战界面显示：幻灵先锋关卡 X / 赛季先锋关卡 X

        Args:
            items: OCR 结果列表 [(cy, cx, text, conf, ...), ...]

        Returns:
            (huanling, normal) 幻灵关卡数和挂机关卡数，(0, 0) 表示检测失败
        """
        huanling: int = 0
        normal: int = 0

        for item in items:
            t, c = item[2], item[3]
            if c < 0.4:
                continue
            # 幻灵挑战关卡（挂机或先锋）
            if '赛季幻灵' in t or '幻灵先锋' in t:
                nums: List[str] = re.findall(r'\d+', t)
                if nums:
                    huanling = int(nums[-1])
            # 挑战关卡（挂机或先锋）
            elif '赛季挂机' in t or '赛季先锋' in t:
                nums = re.findall(r'\d+', t)
                if nums:
                    normal = int(nums[-1])

        return huanling, normal

    def detect_current_stage(self, items: List[Tuple]) -> int:
        """检测当前关卡（取幻灵和挂机关卡的最大值）。

        Args:
            items: OCR 结果列表 [(cy, cx, text, conf, ...), ...]

        Returns:
            int: 当前关卡数，0 表示检测失败
        """
        hl, nm = self._detect_season_stages(items)
        # 取两者最大值作为当前关卡
        return max(hl, nm) if (hl > 0 or nm > 0) else 0
