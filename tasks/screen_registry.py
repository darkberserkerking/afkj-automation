# -*- coding: utf-8 -*-
"""全局界面注册表（v1.8.25，用户定案：界面识别全局化，不要各任务分开）

架构（v1.8.25 起）：
  ┌─────────────────────────────────────────────────────┐
  │ GLOBAL_SCREEN_REGISTRY（本模块，单一真相源）           │
  │   register(screen_id, matcher, priority, desc)      │
  │   detect(items) -> screen_id（如 'labyrinth.door'）  │
  └─────────────────────────────────────────────────────┘
            │ 本域 id → 原状态动作
            │ 他域 id → 精准指名 + 脱离链（ESC/返回按钮交替）
            ▼
  各任务（labyrinth / goddess_trial）保留动作映射，
  `_detect_play_state`/`_detect_state` 变为注册表委托。

设计原则：
  1. screen_id 带 域 前缀（'labyrinth.' / 'goddess.'），同名词各域独立
  2. 规则按 priority 升序评估，首个命中即返回；迷宫域 100 段、女神域 200 段
  3. 跨域撞词用反向排除拆分（如 再次挑战 归女神结算页，迷宫胜利弹层排除之）
  4. 判据全部来自真机截图 OCR 实测（依据来源见 docs/界面判据清单.md）
"""

from typing import Callable, List, Tuple

# ── 判据词表（单一真相源；任务模块从这里导入使用）──────────────
# v1.8.8：推图副本特征词（命中任一 → 非迷宫内部，脱离处理）
PUSH_MAP_MARKS: Tuple[str, ...] = (
    '自动挑战', '通关阵容', '编队', '赛季先锋关卡', '先锋之炎',
)
# v1.8.20：「探索完成」最终结算页（含「到达层数」撞 boss_cleared，须先拦截）
FINAL_SETTLE_MARKS: Tuple[str, ...] = ('探索完成', '点击屏幕跳过')
# v1.8.87：「离开迷宫」荣誉结算页（战斗失败主动结束/长按结束后的结算，
# 与「探索完成」页是两个不同 UI。大标题是艺术字 + 底部「点击屏幕跳过」
# 字太小 OCR 常读不出，final_settle 不命中；而右上角「到达层数:17」
# 能读出 → 撞 boss_cleared 判据 → 玩法循环去找不存在的「结束探索」
# → 整个任务 failed（真机 2026-09-08 21:01 实录）。判据用同屏的
# 普通字体词，截图实测可读）
LEAVE_SETTLE_MARKS: Tuple[str, ...] = ('离开迷宫', '荣誉点数')
# 稀有度词表（用户 2026-09-04 确认迷宫内只有 稀有/精英/史诗 三种，
# 保留 传说/神话 作未来扩展）
RARITY_WORDS: Tuple[str, ...] = ('稀有', '史诗', '传说', '神话', '精英')
# door 特征词（v1.8.20/23 反向排除：胜利/挑战成功弹层与备战画面均为
# 全屏遮罩，底层门卡地图特征词不可见；门卡详情说明含「战斗胜利」撞词的
# 修复也依赖此词组）
DOOR_WORDS: Tuple[str, ...] = ('结束探索', '进去看看', '之门',
                               '击败敌人可获得遗物')
# 商店特征词（v1.8.57 反向排除：胜利弹层是全屏遮罩，底层商店词不可见；
# 商店「暖流」遗物描述含「战斗胜利时，使上阵的英雄恢复10%命值」（真机
# OCR conf=0.99）撞 victory/goddess.victory_result 判据 → 商店被误判
# victory → 启动直通后玩法循环每 5s 空转。判据优先级不动，两处判据
# 各加此排除——读到商品列表/继续前进 = 画面不是弹层遮罩）
SHOP_MASK_WORDS: Tuple[str, ...] = ('商品列表', '继续前进')
# 烙印选择界面特征词（v2.2.1 反向排除）：真机 2026-09-12 14:31 报障——
# 烙印选择界面被判成「胜利弹层」→ ESC/返回键交替空转 18 轮 → 任务失败
# （耗时 5444 秒）。根因：烙印卡「加速叠伤」描述含
# 「…这名英雄**直至战斗结束**增加10点攻击速度…」，子串「战斗结束」撞
# victory 宽泛判据；且 victory(102) 先于 imprint(105) 评估 → 烙印界面被吃掉。
# 实证：同一张真机截图喂检测器，OCR 读全（含「直至战斗结束」）→ victory（错），
# OCR 丢字（读成「直战结束」）→ imprint（对）——即同一界面两种结果。
# 与 v1.8.57「商店暖流遗物描述撞战斗胜利」同类问题：
# **判据优先级不动，victory 加此排除**（读到烙印界面主按钮 = 画面不是胜利弹层）。
# 两词分工：①「选择烙印」= 界面主按钮（最强证据）；
#           ②「直至战斗结束」= 已知撞词句式（主按钮漏读时兜底）
IMPRINT_MASK_WORDS: Tuple[str, ...] = ('选择烙印', '直至战斗结束')
# 主城特征词（v1.8.61 反向排除：家园建筑「阿瓦隆之门」含「之门」撞
# labyrinth.door_fallback 判据 → 主城被判选门地图 → 启动直通后
# _pick_door 空转。OCR 实测 2026-09-07 家园.png：英雄厅堂/神秘屋/
# 先锋关卡 均 conf≥0.97，选门地图绝不会出现这些词）
HOME_MASK_WORDS: Tuple[str, ...] = ('英雄厅堂', '神秘屋', '先锋关卡')

# 女神试炼判据词（原 goddess_trial 常量上收全局）
GODDESS_SELECT_MARK: str = '今日可挑战层数上限'
GODDESS_SELECT_TITLE_MARKS: Tuple[str, ...] = ('攻秘藏', '力秘藏')
GODDESS_SWEEP_DIALOG_MARKS: Tuple[str, ...] = ('扫荡次数', '奖励预览')
GODDESS_AUTOCHECK_MARK: str = '自动挑战'
GODDESS_SECRET_MARK: str = '秘藏'


def _texts(items: List[Tuple], conf: float = 0.5) -> List[str]:
    """OCR items → 文本列表（6 元组 (cy, cx, text, conf, x0, x1)）。"""
    return [
        it[2] for it in items if len(it) >= 4 and it[3] >= conf
    ]




def _has_home_btn(items: List[Tuple]) -> bool:
    """世界场景右下角「家园」切换按钮（rx>0.85, ry 0.80~0.95）。"""
    for it in items:
        if (len(it) >= 4 and it[3] >= 0.8
                and it[2].strip() == "家园"
                and it[1] / 1280.0 > 0.85
                and 0.80 < it[0] / 1024.0 < 0.95):
            return True
    return False
def _mk_has(items: List[Tuple]) -> Callable[[str], bool]:
    ts = _texts(items)

    def has(kw: str) -> bool:
        return any(kw in t for t in ts)

    return has


class ScreenRegistry:
    """全局界面注册表：screen_id → 判据匹配器，按优先级评估。"""

    def __init__(self) -> None:
        self._rules: List[Tuple[int, int, str, Callable, str]] = []
        self._seq: int = 0

    def register(self, screen_id: str, matcher: Callable[[List[Tuple]], bool],
                 priority: int = 100, desc: str = '') -> None:
        """注册一条界面判据（同 priority 按注册先后评估）。"""
        self._seq += 1
        self._rules.append((priority, self._seq, screen_id, matcher, desc))

    def detect(self, items: List[Tuple]) -> str:
        """识别画面：返回首个命中的 screen_id；全不命中 → 'unknown'。"""
        for _, _, screen_id, matcher, _ in sorted(
            self._rules, key=lambda r: (r[0], r[1])
        ):
            try:
                if matcher(items):
                    return screen_id
            except Exception:   # 单条判据异常不拖垮全局识别
                continue
        return 'unknown'

    def domain(self, screen_id: str) -> str:
        """screen_id → 域名（'labyrinth'/'goddess'/'unknown'）。"""
        if screen_id == 'unknown' or '.' not in screen_id:
            return 'unknown'
        return screen_id.split('.', 1)[0]

    def screens(self) -> List[str]:
        """已录入的全部界面 id（按评估顺序）。"""
        return [sid for _, _, sid, _, _ in sorted(
            self._rules, key=lambda r: (r[0], r[1])
        )]


def build_default_registry() -> ScreenRegistry:
    """构建全局注册表：录入全部已实测界面。

    评估顺序（v1.8.25 自测校准）：**具体判据在前、宽泛词表（push_map）
    在最后**——女神备战页含「通关阵容」、战斗 HUD 含「自动挑战」，若
    push_map 先评会抢先命中（自测曾 FAIL 2 例）。跨域区分全靠反向排除：
      labyrinth.victory 排除「再次挑战」（归女神结算页）
      labyrinth.victory/battle_prep 排除 door 词（门卡详情/选门地图）
      labyrinth.push_map 排除「秘藏」（女神备战）
    """
    reg = ScreenRegistry()

    # ═══ 迷宫域（顺序 = 原 _detect_play_state，勿随意调换）═══
    reg.register(
        'labyrinth.final_settle',
        lambda items: any(_mk_has(items)(kw) for kw in FINAL_SETTLE_MARKS),
        priority=100,
        desc='探索完成结算页（含「到达层数」须先于 boss_cleared 拦截）',
    )
    reg.register(
        'labyrinth.leave_settle',
        lambda items: any(_mk_has(items)(kw) for kw in LEAVE_SETTLE_MARKS),
        priority=100,
        desc='离开迷宫荣誉结算页（战斗失败/长按结束后的结算，v1.8.87；'
             '「到达层数」撞 boss_cleared 须在其前拦截；'
             '动作归并 final_settle=点屏幕跳过）',
    )
    reg.register(
        'labyrinth.boss_cleared',
        lambda items: (lambda h: h('难度通关') or h('到达层数')
                       or (h('继续探索') and h('结束探索')))(_mk_has(items)),
        priority=101,
        desc='难度通关结算（通关15层.png 实测；v1.8.93 补：「继续探索」+'
             '「结束探索」两按钮同现 = 深层结算页——真机 11:36 该页艺术字'
             '标题漏读时曾撞 door 兜底词「结束探索」被判选门画面空转 15s）',
    )
    reg.register(
        'labyrinth.victory',
        lambda items: (lambda h: (h('战斗胜利') or h('挑战成功')
                                  or h('战斗结束'))
                       and not any(h(kw) for kw in DOOR_WORDS)
                       and not h('再次挑战')
                       and not h('战斗胜利时')
                       and not any(h(kw) for kw in SHOP_MASK_WORDS)
                       and not any(h(kw) for kw in IMPRINT_MASK_WORDS))(
            _mk_has(items)),
        priority=102,
        desc='胜利/挑战成功弹层（全屏遮罩，door 词反向排除；'
             '「再次挑战」归女神结算页 v1.8.25；'
             '商店词排除——暖流遗物描述含「战斗胜利」撞词 v1.8.57；'
             '「战斗胜利时」排除——描述句式带「时」，弹层标题不带，'
             '防单门界面漏检结束探索时误判 v1.8.58；'
             '烙印词排除——烙印卡描述含「直至战斗结束」撞词 v2.2.1）',
    )
    reg.register(
        'goddess.victory_result',
        lambda items: (lambda h: (h('战斗胜利') or h('再次挑战'))
                       and not any(h(kw) for kw in DOOR_WORDS)
                       and not h('战斗胜利时')
                       and not any(h(kw) for kw in SHOP_MASK_WORDS))(
            _mk_has(items)),
        priority=103,
        desc='女神最后一关通关结算页（需 ESC；连打中间层不弹仅最终弹；'
             'door 词排除——门卡详情说明含「战斗胜利」归迷宫域 v1.8.25；'
             '商店词/描述句式排除同理 v1.8.57/58）',
    )
    reg.register(
        'labyrinth.failure',
        lambda items: _mk_has(items)('战斗失败'),
        priority=104,
        desc='战斗失败弹层',
    )
    reg.register(
        'labyrinth.imprint',
        lambda items: _mk_has(items)('选择烙印'),
        priority=105,
        desc='烙印选择（art 标题）',
    )
    reg.register(
        'labyrinth.shop',
        lambda items: _mk_has(items)('商品列表'),
        priority=106,
        desc='菲茨小店商品面板',
    )
    reg.register(
        'labyrinth.end_confirm',
        lambda items: (lambda h: h('长按结束')
                       or h('是否终止本次探索'))(_mk_has(items)),
        priority=107,
        desc='「是否终止本次探索？」确认弹窗（选门界面 ESC 脱离触发；'
             'OCR 实测 2026-09-07：长按结束 conf=0.99 独占词；'
             '动作=长按结束终止探索 v1.8.62）',
    )
    reg.register(
        'labyrinth.imprint_fallback',
        lambda items: (lambda h: h('确认') and sum(
            1 for t in _texts(items) if t.strip() in RARITY_WORDS
        ) >= 2)(_mk_has(items)),
        priority=107,
        desc='烙印四选一兜底（art 标题漏检，稀有度标签出现 ≥2 次）',
    )
    reg.register(
        'labyrinth.battle_prep',
        lambda items: (lambda h: h('难度') and h('战斗')
                       and not h('亿')
                       and not any(h(kw) for kw in DOOR_WORDS))(_mk_has(items)),
        priority=108,
        desc='迷宫备战（难度15 顶栏专属；亿=推图战力排除；'
             'door 词排除门卡详情 v1.8.23）',
    )
    reg.register(
        'labyrinth.door',
        lambda items: _mk_has(items)('结束探索'),
        priority=109,
        desc='选门地图（底部固定按钮）',
    )
    reg.register(
        'labyrinth.shop_fallback',
        lambda items: _mk_has(items)('进去看看'),
        priority=110,
        desc='小店入口卡兜底（须在 door 之后：状态9 门卡地图同含此词）',
    )
    reg.register(
        'labyrinth.door_fallback',
        lambda items: (lambda h: (h('之门') or h('击败敌人可获得遗物'))
                       and not any(h(kw) for kw in HOME_MASK_WORDS))(
            _mk_has(items)),
        priority=111,
        desc='门卡标题兜底（遗物之门/晶粹之门/道具之门…；'
             '遗物描述句式兜底——极端帧标题全部漏检时凭描述归门 v1.8.58；'
             '主城词排除——家园「阿瓦隆之门」撞「之门」v1.8.61）',
    )

    # ═══ 女神域（顺序 = 原 _detect_state，勿随意调换）═══
    reg.register(
        'goddess.sweep_dialog',
        lambda items: (lambda h: h('扫荡次数')
                       and h('奖励预览'))(_mk_has(items)),
        priority=120,
        desc='扫荡弹窗（双词，弹窗盖在选关页上）',
    )
    reg.register(
        'goddess.battle_prep',
        lambda items: (lambda h: h(GODDESS_AUTOCHECK_MARK) and h('战斗')
                       and h(GODDESS_SECRET_MARK))(_mk_has(items)),
        priority=121,
        desc='女神备战（「秘藏」防撞推图备战 v1.8.8 教训）',
    )
    reg.register(
        'goddess.auto_battle',
        lambda items: _mk_has(items)('自动挑战中'),
        priority=122,
        desc='「自动挑战中...」HUD（战斗中正向识别）',
    )
    reg.register(
        'goddess.select_page',
        lambda items: (lambda h: h(GODDESS_SELECT_MARK) or (
            any(h(kw) for kw in GODDESS_SELECT_TITLE_MARKS)
            and (h('详情') or h('魔饰总览'))
        ))(_mk_has(items)),
        priority=123,
        desc='选关页（主判据今日可挑战层数上限；攻/力秘藏+详情/魔饰总览兜底）',
    )

    # ═══ 导航域（priority 140：v1.8.27 录入 detector 已知的导航界面，
    #     使玩法循环能识别"已离开执行域"并优雅结束，而非 ESC 空转）═══
    _PB_MARKS: Tuple[str, ...] = (
        '玩法目录', '挂机关卡', '女神试炼', '迷梦之域', '竞技场',
        '巅峰竞技场', '巅峰联赛', '异界迷宫', '公会征锋', '天穹试炼',
        '沙盘演兵', '荣誉对决', '公平竞技', '超凡挑战', '常规玩法',
        '活动',
    )

    def _r_playbook(items: List[Tuple]) -> bool:
        texts = _texts(items)
        hits = sum(1 for kw in _PB_MARKS if any(kw in t for t in texts))
        return hits > 4

    def _r_labyrinth_main(items: List[Tuple]) -> bool:
        h = _mk_has(items)
        return (h('异界迷宫') or h('探索阵容')) and not (
            h('玩法目录') or h('常规玩法') or h('活动')
        )

    def _r_idle_stage(items: List[Tuple]) -> bool:
        texts = _texts(items)
        h = _mk_has(items)
        main_btn = any(t.strip() == '挑战' or '幻灵挑战' in t for t in texts)
        season = h('已完成全部挑战') or h('赛季挂机关卡')
        return (main_btn or season) and not h('通关阵容') \
            and not h(GODDESS_AUTOCHECK_MARK)

    def _r_guild(items: List[Tuple]) -> bool:
        texts = _texts(items)
        hits = sum(1 for kw in ('庇护之地', '商店', '公会挑战', '公会管理')
                   if any(kw in t for t in texts))
        return hits >= 3

    reg.register(
        'nav.labyrinth_main',
        _r_labyrinth_main,
        priority=140,
        desc='异界迷宫主页（标题/探索阵容；被玩法循环视为探索已结束）',
    )
    reg.register(
        'nav.playbook',
        _r_playbook,
        priority=141,
        desc='玩法目录（卡片+底栏词表计数 >4，detector 同款判据）',
    )
    # v1.8.83：玩法目录·活动页签——用户 2026-09-08 17:58 实测定案：
    # 此画面识别为活动页没错，但导航点「玩法目录」命中的是左上角
    # 标题（点击无效），正确动作=点「常规玩法」切页签。判据在
    # nav.activity 之前评估（同 priority 按注册序），用活动 tab 独有
    # 结构词（热点活动/当前活动）与通用活动页区分
    reg.register(
        'nav.playbook_activity',
        lambda items: (lambda h: h('活动') and h('常规玩法')
                       and (h('热点活动') or h('当前活动')))(_mk_has(items)),
        priority=142,
        desc='玩法目录·活动页签（活动+常规玩法页签同现+活动 tab 结构词；'
             '动作=点「常规玩法」切页签）',
    )
    reg.register(
        'nav.activity',
        lambda items: (lambda h: h('活动')
                       and h('常规玩法'))(_mk_has(items)),
        priority=142,
        desc='活动页（活动+常规玩法页签同现）',
    )
    reg.register(
        'nav.idle_stage',
        _r_idle_stage,
        priority=143,
        desc='挂机关卡页（挑战按钮/赛季页；排通关阵容与自动挑战）',
    )
    reg.register(
        'nav.guild',
        _r_guild,
        priority=144,
        desc='公会主页（4 词命中 ≥3）',
    )
    reg.register(
        'nav.world_home',
        lambda items: (lambda h: h('神秘屋') and h('玩法目录')
                       and h('英雄厅堂') and not h('庇护之地'))(_mk_has(items)),
        priority=145,
        desc='0 层世界/家园（底栏三件套，排公会页）',
    )
    # v1.9.48：0 层两场景区分（用户 13:00 双截图定案）——世界/家园
    # 共用底栏三件套，靠互切按钮词区分：世界有「家园」按钮、
    # 家园有「建设」按钮（OCR 0.98/0.99，互斥）。priority=144 先于
    # nav.world_home(145) 兜底评估。
    reg.register(
        'nav.world_scene',
        lambda items: (lambda h: h("神秘屋") and h("玩法目录")
                       and h("英雄厅堂") and _has_home_btn(items))(
            _mk_has(items)),
        priority=144,
        desc='0 层·世界场景（三件套+右下「家园」按钮）',
    )
    reg.register(
        'nav.home_scene',
        lambda items: (lambda h: h("神秘屋") and h("玩法目录")
                       and h("英雄厅堂") and h("建设"))(_mk_has(items)),
        priority=144,
        desc='0 层·家园场景（三件套+左下「建设」按钮）',
    )

    # v1.9.12：一键家园·愿望单界面（家园内子页面）。此前未注册 → 启动
    # 探查/全局导航都把它判 unknown（真机 20:01 在愿望单界面启动无法
    # 识别，只能 ESC 脱离回主城再重新点进去）。
    # 判据：标题「愿望单」+ 底部大按钮（一键选择/交付）或「愿望单需求」
    # ——仅看标题会与家园界面按钮上的「愿望单」金字撞词。
    reg.register(
        'home.wishlist',
        lambda items: (lambda h: h('愿望单') and (h('一键选择')
                       or h('交付') or h('愿望单需求')))(_mk_has(items)),
        priority=146,
        desc='一键家园·愿望单界面（一键选择/交付大按钮）',
    )
    # v1.9.18：材料耗尽弹窗（工坊点开始后材料不足会弹，真机截图：
    # 「当前资源不足，可从以下途径获取」conf 1.00）
    # v2.1.15（用户 12:28 真机）：**家园工坊内部**此前未注册 → 家园任务收尾后
    # 全局导航连续 6 轮「未识别画面」才靠返回按钮脱困（12:15:54~12:16:19）
    reg.register(
        'home.workshop',
        lambda items: (lambda h: h('开始锻造') or h('开始烹饪')
                       or h('开始炼金'))(_mk_has(items)),
        priority=147,
        desc='一键家园·工坊内部生产页（开始锻造/开始烹饪/开始炼金）',
    )
    # v2.1.15：**竞技场**此前未注册 → 竞技场任务收尾后导航同样判 unknown
    # （真机 12:17:54~12:18:07 连续 5 轮）。判据来自 arena.py 头部注释实测值。
    reg.register(
        'arena.main',
        lambda items: (lambda h: h('开始挑战') and h('防守阵容'))(
            _mk_has(items)),
        priority=148,
        desc='竞技场主界面（开始挑战 + 防守阵容；MaaOCR 实测 0.99/0.97）',
    )
    reg.register(
        'arena.select',
        lambda items: (lambda h: h('选择对') and not h('防守阵容'))(
            _mk_has(items)),
        priority=149,
        desc='竞技场·选择对手页（标题常被截断为「选择对」）',
    )

    # v2.1.16（用户 12:57 规范 + 4 张截图）：1 层补充「神秘屋 / 英雄厅堂」
    # 与「主菜单」。判据刻意**避开底部 UI 栏的通用词**（底部栏四个入口
    # 「神秘屋/玩法目录/英雄厅堂/公会」在所有 1 层画面都出现，单词判定会串）。
    reg.register(
        'nav.mystery_house',
        lambda items: (lambda h: h('神秘珍藏') or h('金玫瑰高定')
                       or h('月桂酒馆'))(_mk_has(items)),
        priority=138,   # v2.1.16b：必须早于 nav.world_home(145)——后者的判据
                        # 是「神秘屋+玩法目录+英雄厅堂」三词 AND，而这三词正是
                        # 1 层底部 UI 栏的常驻文字，会抢判本画面（独立验证 F1）
        desc='神秘屋（1 层；内部横匾 神秘珍藏/金玫瑰高定/月桂酒馆）',
    )
    reg.register(
        'nav.hero_hall',
        lambda items: (lambda h: h('赛季共鸣等级')
                       or (h('幻灵之契') and h('共鸣之手')))(_mk_has(items)),
        priority=139,   # v2.1.16b：同上，须早于 nav.world_home(145)（F2）
        desc='英雄厅堂（1 层；赛季共鸣等级/幻灵之契/共鸣之手）',
    )
    reg.register(
        'nav.main_menu',
        # v2.1.16b（独立验证 F3）：「修行之路」是**活动页卡片词**
        # （docs/界面判据清单.md），单词作判会把活动页判成主菜单 →
        # menu_nav 会跳过点主菜单按钮直接在活动页点菜单项 → 入口导航失败。
        # 改为纯多词 AND 组合（宫格/横幅独有），并反向排除活动页。
        lambda items: (lambda h: (
            (h('剧情任务') and h('星夜逸闻'))
            or (h('旅行指南') and h('月桂之旅'))
            or (h('背包') and h('邮件') and h('社区'))
        ) and not (h('活动') and h('常规玩法')))(_mk_has(items)),
        priority=131.5,  # v2.1.17：主菜单面板覆盖在其它界面之上（含好友界面，
                         # ESC 从好友页回到主菜单面板）→ 必须先于 nav.friend(132)
                         # 评估，否则同帧同时含好友词+主菜单词时会误判为好友页
                         # （独立验证 F4）
        desc='主菜单面板（1 层；无底部 UI 栏，修行之路/剧情任务/宫格）',
    )

    reg.register(
        'home.res_short',
        lambda items: (lambda h: h('当前资源不足'))(_mk_has(items)),
        priority=146,
        desc='一键家园·资源不足弹窗（材料耗尽获取途径）',
    )

    # ═══ 导航域补充 ═══
    # v2.0.4（用户 18:38）：退出确认弹窗（ESC 陷阱）——最高优先级画面
    # （真机 18:26 ESC 误触后出现；OCR 实测「确定要退出游戏吗？」conf=0.99）
    reg.register(
        'nav.quit_confirm',
        lambda items: (lambda h: h('确定要退出游戏'))(_mk_has(items)),
        priority=200,   # 最高：优先于一切画面
        desc='退出确认弹窗（ESC 陷阱；动作=点 X 取消，绝不点确定）',
    )
    # v2.0.4：加载界面（登录前/切场景）
    reg.register(
        'nav.loading',
        lambda items: (lambda h: h('正在加载游戏'))(_mk_has(items)),
        priority=131,
        desc='加载界面（OCR 实测「正在加载游戏。」conf=0.98/0.99；动作=等待）',
    )
    reg.register(
        'nav.login',
        lambda items: (lambda h: h('点击开始游戏')
                       or (h('网络诊断') and h('账户')))(_mk_has(items)),
        priority=130,
        desc='游戏登录界面（重启/掉线后停此页。OCR 实测 2026-09-07：'
             '「点击开始游戏」conf=0.99；双保险=右侧栏 网络诊断+账户）',
    )
    # v2.1.3（用户 21:00）：好友界面此前**未注册** → 收赠友情点任务结束
    # 后导航判 unknown（真机 20:55：连续 8 轮未识别才靠返回按钮脱离）。
    # 判据：好友页「收礼进度N/10」或大按钮「一键赠礼」。
    reg.register(
        'nav.friend',
        lambda items: (lambda h: h('收礼进度') or h('一键赠礼'))(_mk_has(items)),
        priority=132,
        desc='好友界面（主菜单·社交进入；OCR 实测「收礼进度 10/10」）',
    )
    # v2.1.26（用户 18:57 真机）：**「任务」界面此前完全未注册** ——
    # 停在非「每日任务」标签（翻翻乐/成长挑战/赛季任务/家园目标）时，
    # 全局导航识别为 unknown → 每日任务任务反复「未识别超限→脱离→回玩法目录」，
    # 两轮后导航失败（真机 18:58:29~18:59:20）。
    # 判据 = 底部**一级标签行**（界面内常驻，与当前选中哪个标签无关）。
    reg.register(
        'nav.task_page',
        lambda items: (lambda h: sum(
            1 for t in ('日常任务', '翻翻乐', '成长挑战', '赛季任务',
                        '家园目标') if h(t)) >= 3)(_mk_has(items)),
        priority=131.6,   # 紧邻主菜单面板(131.5)：任务界面是它的子页，
                          # 面板覆盖时面板优先（131.5 先评估）
        desc='任务界面（主菜单·任务 进入；底部一级标签行命中 ≥3）',
    )
    reg.register(
        'nav.monthly_reward',        lambda items: (lambda h: h('伊索米亚月刊')
                       and h('领取奖励'))(_mk_has(items)),
        priority=129,
        desc='伊索米亚月刊弹窗（登录后随机弹出，今日奖励。OCR 实测 '
             '2026-09-08：标题+按钮双词 conf=0.99；动作=点领取奖励）',
    )
    # ═══ 宽泛词表最后评估（v1.8.25 自测校准：必须排在全部具体判据之后）═══
    reg.register(
        'labyrinth.push_map',
        lambda items: (lambda h: any(h(kw) for kw in PUSH_MAP_MARKS)
                       and not h(GODDESS_SECRET_MARK))(_mk_has(items)),
        priority=150,
        desc='推图副本特征词（任务侧映射为 unknown → 脱离；'
             '排「秘藏」防抢女神备战/战斗 HUD）',
    )

    return reg


# 全局单例（任务模块 import 即用）
GLOBAL_SCREEN_REGISTRY: ScreenRegistry = build_default_registry()


# ── v1.8.32 日志中文化：界面中文名（日志尽量不出现英文）────────
ZH_SCREEN = {
    'nav.world_home': '世界/家园', 'nav.guild': '公会',
    'nav.world_scene': '世界场景', 'nav.home_scene': '家园场景',
    'nav.playbook': '玩法目录', 'nav.activity': '活动页',
    'nav.playbook_activity': '玩法目录·活动页签',
    'nav.idle_stage': '挂机关卡页', 'nav.labyrinth_main': '异界迷宫主页',
    'nav.login': '登录界面',
    'nav.friend': '好友界面',
    'nav.task_page': '任务界面',
    'nav.mystery_house': '神秘屋',
    'nav.hero_hall': '英雄厅堂',
    'nav.main_menu': '主菜单',
    'home.workshop': '家园工坊内部',
    'arena.main': '竞技场',
    'arena.select': '竞技场·选对手',
    'nav.loading': '加载界面',
    'nav.quit_confirm': '退出确认弹窗',
    'nav.monthly_reward': '伊索米亚月刊弹窗',
    'goddess.select_page': '女神选关页', 'goddess.battle_prep': '女神备战页',
    'goddess.victory_result': '女神结算页', 'goddess.sweep_dialog': '扫荡弹窗',
    'goddess.auto_battle': '自动挑战中',
    'labyrinth.door': '选门地图', 'labyrinth.battle_prep': '迷宫备战',
    'labyrinth.victory': '战斗胜利弹层', 'labyrinth.failure': '战斗失败',
    # v1.9.11：补齐日志用中文名（此前缺失 → 日志退回英文后缀）
    'labyrinth.end_confirm': '终止确认弹窗',
    'home.wishlist': '愿望单界面',
    'home.res_short': '资源不足弹窗',
    'labyrinth.leave_settle': '离开迷宫结算',
    'labyrinth.imprint_fallback': '烙印选择兜底',
    'labyrinth.shop_fallback': '菲茨小店兜底',
    'labyrinth.door_fallback': '门卡标题兜底',
    'labyrinth.imprint': '烙印选择', 'labyrinth.shop': '菲茨小店',
    'labyrinth.boss_cleared': '难度通关', 'labyrinth.final_settle': '探索完成',
    'labyrinth.leave_settle': '离开迷宫结算',
    'labyrinth.push_map': '推图副本', 'unknown': '未识别画面',
}


def zh_screen(screen_id: str) -> str:
    """screen_id → 中文界面名（未收录时去掉域前缀兜底）。"""
    return ZH_SCREEN.get(screen_id, screen_id.split('.')[-1])
