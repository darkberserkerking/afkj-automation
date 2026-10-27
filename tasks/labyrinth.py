"""
任务：异界迷宫

v1.7.1 注册为 planned 状态（占位）。
v1.7.2 实现**第一步：导航进入异界迷宫界面**（跑到入口即停）。

⚠️ status 仍为 'planned'：等真机验证导航成功后才转 'ready'。
   真机验证走 `tests/test_labyrinth_nav.py`（直接实例化任务，绕过调度器的 planned 跳过）。

────────────────────────────────────────────
【入口路径】（真机截图确认）
  0 层（世界/家园）→ 点「玩法目录」→ 玩法目录（默认停在「常规玩法」tab）
  → 「异界迷宫」卡片不在首屏，需向下滚动
  → OCR 找文字「异界迷宫」→ 点卡片 → 进入异界迷宫界面（phase 'labyrinth'）

【玩法目录布局】（v1.7.4 用户真机确认）
  「常规玩法」tab 共 12 个卡片 = 4 行 × 3 列，首屏可见 2.5 行；
  「异界迷宫」在最后一行 → 大步滚动直到底部即可（见 _SCROLL_CLICKS 注释）。

【滚动硬约束】（用户真机确认，别违反）
  只有「游戏窗口已激活 **且** 鼠标位于可滚动区内」时，滚轮/拖动才生效。
  故每次滚动前必须 activate_window() + 把鼠标移到可滚动区中心
  —— 由 InputController.scroll_wheel() 内部保证。

【玩法主体】（v1.7.12 实现，待真机验证）
  v1.7.11 现状：启动清残留弹窗 + 导航 + 点「进入」 + 处理「是否继续探索」弹窗。
    v1.7.10 修复：OCR 找「继续」误命中文案 → 改用模板匹配绿色 ✓
            （btn_confirm_check.png，透明背景 + masked NCC 支持）
    v1.7.11 修复：① detector labyrinth 判据加「探索阵容」冗余（艺术字标题漏检兜底）；
            ② run 启动先清上轮残留弹窗（否则 unknown→ESC 空转 14s）——
            弹窗处理抽为 _dismiss_continue_dialog()，run / 点进入后两处复用
  v1.7.12 新增迷宫内部玩法循环 _play_loop（OCR 状态机，隔离 detector）：
    选门（优先级 遗物>小店>道具>精粹>精英，用户定案）→ 备战画面按 F 开战
    （已是 4 倍速不处理）→ 胜利弹层点空白关 → 烙印三选一固定点窗口正中央
    （候选随机无法 OCR/模板，用户定案）+ 点底部确认钮 → 循环至 15 层
    Boss 打完弹「难度通关」→ 点「结束探索」退出。
    战斗失败：先 ESC，连续 3 次画面不动再点「返回」按钮模板兜底，
    然后点「结束探索」结束本次探索（用户定案：失败即结束）。
    小店：进入后点「继续前进」退出，不购买任何物品。
  status 仍为 'planned'，等真机验证通过后才转 'ready'。
────────────────────────────────────────────
"""

import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

from tasks.base import BaseTask
from task_registry import TaskRegistry, TaskMeta

task_id = "labyrinth"
label = "异界迷宫"
description = "自动进入异界迷宫逐层选门战斗，默认15层通关后退出；可勾选挑战至20关深层"

# ── 可滚动区（玩法目录卡片网格）────────────────────────────
# 比例坐标 (x0, y0, x1, y1)，取自用户真机截图的红框区域。
# 鼠标必须落在此区域内滚轮才生效；找卡片时也可按此范围过滤。
_SCROLL_REGION: Tuple[float, float, float, float] = (0.435, 0.124, 0.79, 0.605)

# ── 滚动参数（v1.7.4 提速：用户实测每格只滚一丝 → 10 倍 → 直接滚到底）──
# 玩法目录「常规玩法」tab 共 12 个卡片 = 4 行 × 3 列，首屏可见 2.5 行，
# 「异界迷宫」在最后一行 → 直接大步滚到底即可，无需逐屏找卡片。
# pyautogui.scroll 内部每格 = 1 个滚轮刻度（120 delta），-30 = 30 格。
# （若引擎对单次 delta 限幅，pyautogui 也会拆成多次小滚动，位移等价 10 倍）
_SCROLL_CLICKS: int = -30       # 单次滚轮格数（负 = 向下，v1.7.4: -3→-30 提速 10 倍）
# v1.8.88（用户定案）：移除 `_SCROLL_MAX`（最多滚动次数上限）。滚动终止
# 只由画面状态决定（找到卡片 / 列表到底），不再有次数上限。
_SCROLL_SETTLE: float = 0.3     # 滚动后等画面稳定的秒数（大步滚，间隔可略短）

_NAV_MAX_ITER: int = 30         # 导航主循环最大迭代
_ENTER_TIMEOUT: float = 10.0    # 点卡片后等待进入界面的秒数
_TEXT_TIMEOUT: float = 8.0      # OCR 找文字的重试秒数

_CARD_TEXT: str = '异界迷宫'
_CARD_MIN_CONF: float = 0.6

# ── phase 中文标签（v1.7.5 用户反馈：日志原样 phase 标识符易被误读，输出中文标签）──
# phase 标识符含义见 docs/界面命名规范.md §2，仅日志输出中文标签。
_PHASE_LABELS: dict = {
    'labyrinth':        '异界迷宫',
    'playbook':         '玩法目录',
    'world':            '世界',
    'home':             '家园',
    'guild':            '公会',
    'idle_stage':       '挂机关卡态',   # 规范名 §2 row 62：idle_stage = 挂机关卡态（v1.7.6 由 main 改名）
    'activity':         '活动页',
    'formation':        '备战态',
    'battle':           '战斗态',
    'result':           '结算态',
    'confirm_dialog':   '确认弹窗',
    'pause':            '暂停态',
    'loading':          '加载中',
    'login':            '登录态',
    'options_menu':     '选项菜单',
    'quest_panel':      '任务面板',
    'unknown':          '未知态',
}

# 返回按钮模板（v1.7.5 ESC 失效兜底）
# 绝对路径解析：tasks/labyrinth.py → 项目根 → templates/
# v1.8.78：frozen（PyInstaller onefile）下 __file__ 指向 _MEIPASS 内的虚拟路径，
# 直接用 sys._MEIPASS 更可靠——模板由 spec datas 解压到该目录。
_PROJECT_ROOT: str = (
    getattr(sys, '_MEIPASS', '')
    if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_TEMPLATE_DIR: str = os.path.join(_PROJECT_ROOT, 'templates')
_BACK_BUTTON_TPLS: List[str] = [
    os.path.join(_TEMPLATE_DIR, 'btn_back_dark.png'),    # 黑底灰箭头
    os.path.join(_TEMPLATE_DIR, 'btn_back_purple.png'),  # 暗紫底白箭头
    os.path.join(_TEMPLATE_DIR, 'btn_back_arrow.png'),   # v1.8.2 实测：纯白箭头（任意形状 alpha）
]
# v1.7.7：0.7→0.75，过滤 purple 模板右下角 0.73 伪命中（dark 0.98 先命中暂安全，提阈值双保险）
_BACK_THRESHOLD: float = 0.75

# ── 弹窗确认按钮模板（v1.7.10：透明背景 RGBA，template_match 自动走 maskedNCC）──
# btn_confirm_check.png  = 绿色圆形 ✓（"是否继续探索"→继续）
# btn_confirm_cancel.png = 白色圆形 ✕（取消按钮）
#   v2.1.21（用户 16:34 定案·机制修正）：「上阵人数不满」弹窗**必须点它**——
#   阵亡后硬打会把残缺阵容记成默认阵容（上阵越少难度越高 → 恶性循环），
#   点 ✕ 取消战斗 + 结束探索才能保住满员记录。其余确认弹窗仍禁止点。
# 阈值：真机同屏模板实测 maskedNCC 命中 1.000，0.75 留足余量
_CONFIRM_CHECK_TPL: str = 'btn_confirm_check.png'
# v1.9.3：「是否继续探索」类确认弹窗判据（OR 任一命中）。真机弹窗.png
# 实测：问句结尾「…是否继续探索？」被 OCR 跨行截断为「是否继续探」+
# 「索？」，单靠全词匹配会漏判——补截断形式与首通奖励弹窗独有词
_CONTINUE_DIALOG_MARKS: Tuple[str, ...] = (
    '是否继续探索', '是否继续探', '不再获取奖励', '探索阵容尚有空缺',
)
# v1.8.92（真机 11:25 绿✓匹配 9s）：① 匹配前先等弹窗淡入动画完成
# （动画期 maskedNCC 低于阈值 → 前两轮全屏匹配白跑）；② 缩放带收窄
# 16 档 → 6 档（UI 按钮缩放波动小，v1.8.79 跳过按钮同经验：
# 全屏 16 档 ≈2.5s/次 → 6 档 ≈0.9s/次）
_CONFIRM_CHECK_PRE_WAIT: float = 1.0
_CONFIRM_CHECK_SCALE_RANGE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
_CONFIRM_CANCEL_TPL: str = 'btn_confirm_cancel.png'
_CONFIRM_THRESHOLD: float = 0.75
_TPL_RETRIES: int = 3      # 弹窗按钮模板匹配重试次数
_TPL_RETRY_WAIT: float = 0.6

# ── 迷宫内部玩法循环（v1.7.12 新增，交互方式均为用户真机确认）──
# 门卡优先级：遗物 > 小店 > 道具 > 精粹 > 精英（用户定案 遗物/商店/精粹/精英；
# 道具之门用户未提及，按价值插在小店后——真机可调）
# v1.8.11 补 BOSS/深层标题（用户真机 9/4：大量"门卡标题未识别"）：
#   15关「首领」/ 20关「深层首领」/ 16-19关「深层挑战」——find_text 为子串
#   匹配，「首领」即可同时命中「深层首领」。BOSS/深层均为单门，追加在
#   尾部不影响多门场景的遗物>小店>道具>晶粹>精英 选择优先级。
# 注：精英/BOSS 层只有唯一门（单门场景按钮命中直点）；精粹门照常战斗
_DOOR_PRIORITY: List[str] = [
    '遗物之门', '菲茨小店', '道具之门', '晶粹之门', '烙印晶球', '精英',
    '首领', '深层挑战',
]

# ── 门卡进入按钮模板（v1.8.1：真机确认需点卡片下方按钮，点标题无效）──
# 红圈标注截图抠出（RGBA 透明，maskedNCC）；图案区分门类型：
#   btn_door_battle.png = 白圆底交叉剑（战斗类门：晶粹之门实测，疑战斗门通用）
#   btn_door_shop.png   = 白圆底钱袋（菲茨小店实测）
# 遗物/道具/精英/BOSS 门按钮图案未采样——两模板都未命中时回退旧逻辑（点标题/中央）
# 阈值：自截自配 maskedNCC 1.000，0.72 留余量（跨分辨率/图案差异扣分）
_DOOR_BTN_TPLS: List[str] = [
    os.path.join(_TEMPLATE_DIR, 'btn_door_battle.png'),
    os.path.join(_TEMPLATE_DIR, 'btn_door_shop.png'),
    # v1.8.56：烙印晶球门手掌按钮（13 层双门/单门场景；源图 14关.png
    # 实测圆心 (762,725) r=36，maskedNCC 自验 score=1.000）
    os.path.join(_TEMPLATE_DIR, 'btn_door_hand.png'),
]
_DOOR_BTN_THRESHOLD: float = 0.72
# v1.8.27：门卡按钮匹配提速——缩放带收窄（16 档→9 档，按钮尺寸波动小）
_DOOR_BTN_SCALE_RANGE: Tuple[float, float, float] = (0.7, 1.5, 0.1)

# ── 战斗「跳过」按钮模板（v1.8.72 新增，v1.8.78 修路径）──
# 必须传绝对路径：template_match 只按给定路径读文件，裸文件名会按进程
# CWD 解析（exe 放在 D:\桌面 时 CWD 就是桌面 → FileNotFoundError，
# 真机 14:04/14:14 两次实测）。与 btn_back_*/btn_door_* 同样走 _TEMPLATE_DIR。
# 阈值 0.72（三张真机战斗截图 0.999+，留跨分辨率余量）
_SKIP_BTN_TPL: str = os.path.join(_TEMPLATE_DIR, 'btn_skip_battle.png')
_SKIP_BTN_THRESHOLD: float = 0.72
# v1.8.78：按钮尺寸固定，缩放带收到 5 档（9 档→5 档实测 3373ms→1883ms）
_SKIP_BTN_SCALE_RANGE: Tuple[float, float, float] = (0.9, 1.1, 0.05)
# v1.8.78：跳过按钮位置在窗口内**稳定**（三张真机战斗截图 maskedNCC
# val 0.99+，中心比例 (0.690, 0.789) ±1px）→ region 限定区域内搜索：
# 实测 5 档匹配 3373ms→64ms（52×），配合高频轮询才不拖慢整轮循环。
# 取值放宽到 x∈[0.55,0.85] y∈[0.60,0.95]，留足分辨率/UI 版本余量。
_SKIP_REGION_RATIO: Tuple[float, float, float, float] = (
    0.55, 0.60, 0.30, 0.35,     # (rx, ry, rw, rh) 相对客户区比例
)
# v1.8.78：跳过检测独立子循环间隔（原为跟 OCR 同频 5.0s/轮，11 场只中 2 场）
_SKIP_POLL: float = 0.45
# v1.8.21：烙印/门卡固定比例兜底坐标已删除（用户定案：分辨率变化后
# 盲点不可控，漏检一律重试 OCR / 主循环下一帧重试，宁可不动作不盲点）
# 稀有度词表/推图反向排除/探索完成页词表已上收 tasks/screen_registry.py
# （v1.8.25 全局界面注册表，单一真相源），此处以别名引用保持既有引用不变
from tasks.screen_registry import (  # noqa: E402
    FINAL_SETTLE_MARKS as _FINAL_SETTLE_MARKS,
    GLOBAL_SCREEN_REGISTRY,
    PUSH_MAP_MARKS as _PUSH_MAP_MARKS,
    RARITY_WORDS as _RARITY_WORDS,
    zh_screen,
)

# 注册表 id → 本域状态名归并表（fallback 规则原判据直接返回主状态名；
# push_map 映射 unknown 走脱离链）
_FALLBACK_ALIAS = {
    'door_fallback': 'door',
    'shop_fallback': 'shop',
    'imprint_fallback': 'imprint',
    'push_map': 'unknown',
    # v1.8.87：离开迷宫荣誉结算页 → 归并 final_settle（同动作：点屏幕
    # 跳过 → 探索已结束；startup_probe 与玩法循环两处入口同步生效）
    'leave_settle': 'final_settle',
}
# v1.8.20 烙印选卡优先级（用户 2026-09-04 定案）：史诗 > 精英 > 稀有。
# 传说/神话暂不在优先级表（未来扩展时落 topmost 兜底）
_RARITY_PRIORITY: Tuple[str, ...] = ('史诗', '精英', '稀有')
# v1.8.71：品质等级表（第二步品质保护：候选全部低于被替换旧烙印→跳过）
_RARITY_RANK = {'史诗': 2, '精英': 1, '稀有': 0}

# v1.8.52（用户 2026-09-07 定案）：具名烙印优先级 + 黑名单——命中具名即按
# 此序选，黑名单一律不选；未命中具名时回退稀有度逻辑（史诗>精英>稀有）
# v1.8.89（用户 2026-09-09 定案）：「坚定意志」（史诗，开战最大生命+50%）
# 加入保留意愿表（仅次于溢出防御）；v1.8.91 修正：**不加入替换目标
# 排除表**——它仍可作为被替换对象（排除表只有双盾），只是因意愿表
# 排位靠前而在排序中位于具名最后
_IMPRINT_NAMED_PRIORITY: Tuple[str, ...] = (
    '执裁之盾', '溢出防御', '坚定意志', '藏品柜', '强壮之力', '不屈意志',
)
_IMPRINT_BLACKLIST: Tuple[str, ...] = ('泰坦体魄', '献祭之盾')
# v2.2.6（用户 2026-09-15 澄清）：「执裁之盾」与「制裁之盾」是**两个不同烙印**
# —— 推翻 2026-09-08 记录的"笔误"结论（当时把用户提到的「制裁之盾」当成错字）。
# 两者名字**仅首字不同**（执／制），OCR 易混；**稀有度不同**可作判别依据：
#   执裁之盾 = 精英（收集核；多处真机记录）
#   制裁之盾 = 史诗（用户 09-11 真机记录「坚定意志/制裁之盾 实际史诗」）
# 为什么要纠正：若把「制裁之盾」读成「执裁之盾」而照单计入库存，会让
# `_core_requirement_met()` 误判"已集齐收集核" → 单/双核模式提前停或错误换烙印 ✗
_IMPRINT_ALT_NAME: str = '制裁之盾'
# 具名烙印的**预期稀有度**（用于纠正 OCR 形近字混读；不在表中=不校验）
_IMPRINT_EXPECT_QUAL: Dict[str, str] = {
    '执裁之盾': '精英',
    '制裁之盾': '史诗',
}
# v2.1.23（用户 17:11 真机·单核选错卡）：黑名单卡「同卡」判定窗口——
# 稀有度标签位于该黑名单**卡名下方**的像素区间。**必须用方向性窗口**：
# 选卡列表行距 ≈162px、同卡标签在卡名下方 ≈83px，于是「下一张卡」的卡名
# 距「上一张卡的标签」仅 ≈79px，对称容差（旧 ±150）会误判成同卡。
_BL_LABEL_DY_MIN: int = 30
_BL_LABEL_DY_MAX: int = 130

# v1.8.52（用户定案）：门卡遗物优先级 强壮 > 庇护/极速 >> 其他；
# 三种都没有 → 晶粹之门 > 菲茨小店 > 其他；双倍奖励为同级 tie-break。
# 商店只买这三种（单价 50 精粹，最多两件）
# v1.8.72（用户更正）：「极速」是本名也是唯一名（效果为急速提升），
# 无别名——v1.8.53 的别名机制删除
_DOOR_RELIC_PRIORITY: Tuple[str, ...] = ('强壮', '庇护', '极速')
_SHOP_RELICS: Tuple[str, ...] = ('强壮', '庇护', '极速')
_SHOP_PRICE: int = 50
# v2.1.22（用户 17:00）：商店循环预算与「非商店帧」容忍上限。
# 买遗物会弹烙印选择 → 一进一出要额外 2 轮 OCR，故预算从 4 提到 10；
# 连续 3 帧都认不出商店（真离店/长转场）即判离店，防死循环。
_SHOP_MAX_ROUNDS: int = 10
_SHOP_OFF_MAX: int = 3
# v1.8.88（用户定案）：移除烙印晶球轮次上限 `_IMPRINT_ORB_ROUND_MAX`——
# 一次成功替换固定耗 2 轮（step1 选中 + step2 确认），保留烙印/滚动/
# 回滚各 1 轮，真机一局就把 12 轮耗光 → for 自然结束（无日志）→ 收尾
# 点「继续前进」，表现为「滚动后目标可见却结束流程」。现在改为**无
# 次数上限**：终止只由真实业务条件决定（库存无可替换项 / 已集齐双盾 /
# 晶粹不足 / 用户停止）。
# v1.8.84：首次库存扫描参数（用户定案：仅首次进入完整遍历一次，之后
# 内存维护；界面默认在顶部只下滚；本次 OCR 与上次相同=到底立即停）
_ORB_SCAN_WAIT: float = 0.6        # 下滚后等待渲染（v1.8.85：1.0→0.6，真机渲染实测 <0.5s）
_ORB_SCAN_TOP_WAIT: float = 0.5    # 上滚后等待渲染
_IMPRINT_QUAL_TAGS: Tuple[str, ...] = ('稀有', '史诗', '精英')
# 底部按钮：未选中=「选择烙印」，选中后变「确认」（用户 2026-09-04），两者都试
_IMPRINT_BTN_KEYWORDS: Tuple[str, ...] = ('确认', '选择烙印')
# 非烙印名排除词表（v2.2.9，真机 2026-09-15 22:44 实证）：
# 真机日志出现「卡名识别「遗物之门」（精英）」——「遗物之门」是**门卡标题**
# （在 _DOOR_PRIORITY 里），根本不是烙印名 ✗。
# 根因：`_read_card_name` 只按「标签上方 0~120px 内最靠近的非保留文本」取名，
# **不校验它是不是烙印名** → 烙印三选一弹层上方/背后若还留着选门界面的门卡
# 标题，就会被当成卡名入库（库存污染：本局累计虚增、替换目标判定受扰）。
# 排除三类：①门卡标题（_DOOR_PRIORITY 全部）②界面/流程/结算词 ③含「之门」者
_NON_IMPRINT_NAME_WORDS: Tuple[str, ...] = (
    '结束探索', '进去看看', '点击空白处', '保留烙印', '商品列表', '继续前进',
    '刷新时间', '自动挑战', '探索完成', '到达层数', '荣誉点数', '新纪录',
    '冒险历程', '挑战成功', '战斗胜利', '战斗失败', '强力敌人', '难度',
) + tuple(_DOOR_PRIORITY)

# ── 迷宫内部界面自判据（v1.8.3：续局场景）──
# 一局未完成时用返回键退出 → 进度保留；下次点「异界迷宫」卡片会跳过迷宫主页
# （「进入」按钮那页）直接进内部「继续进度」界面（真机实测 2026-09-03）。
# 该界面无「异界迷宫」标题也无「探索阵容」→ detector 判 unknown（判据见
# afk_bot/services/detector.py:345-351）→ 导航超时。故由本任务自 OCR 兜底。
# 特征词：底部固定「结束探索」按钮 + 各门卡名（门卡至少一屏可见）。
_IN_PLAY_MARKS: Tuple[str, ...] = (
    '结束探索', '遗物之门', '馈赠之门', '晶粹之门', '道具之门', '精英之门',
)

# 推图反向排除词表与「探索完成」结算页词表已上收 tasks/screen_registry.py
# （v1.8.25 全局界面注册表）——历史依据详见该模块注释与 docs/界面判据清单.md

# v1.8.6：迭代上限已取消（_play_loop 改 while True，原 80/200 均被真机
# 场景突破——烙印未选中死循环/转场多轮等；终止靠 done 路径 + unknown give_up
# + check_stop 兜底）。ESC 阈值（_UNKNOWN_ESC_AFTER=3）用户定案不动。
_DOOR_TRANSITION_WAIT: float = 3.0   # 点门后转场动画等待（用户真机 2026-09-03）
_UNKNOWN_ESC_AFTER: int = 3     # 连续未知画面达此次数 → ESC 重试
_UNKNOWN_GIVE_UP: int = 10      # 连续未知画面上限 → failed
# v1.8.57：同状态空转兜底（已识别状态连续多轮无推进——如弹层点击无效/
# 商店流程卡住；unknown 分支自有脱离链不参与，转场等待帧重置计数）。
# 轮间隔约 5s（victory 分支 sleep _VICTORY_SETTLE+_EXTRA）→ 6 轮 ≈ 30s
_STALL_ESC_AFTER: int = 6       # 同状态连续轮数达此值 → 警告 + ESC/返回交替
_STALL_GIVE_UP: int = 18        # 同状态连续轮数上限 → failed
_STATE_POLL: float = 0.8        # 状态轮询/转场等待秒数
_UNKNOWN_TRANSITION_WAIT: float = 3.0   # v1.9.35：unknown=转场中，多等再 OCR
_VICTORY_SETTLE: float = 1.0    # 关闭胜利弹层后等待秒数
# v1.8.12（用户实测 9/4）：胜利后触发烙印选择前有 ~5s 变身动画，立即 OCR
# 会判 unknown → 误 ESC。胜利弹层关闭后额外等待秒数（叠加 _VICTORY_SETTLE）。
_VICTORY_EXTRA_WAIT: float = 3.0
_BATTLE_WAIT: float = 30.0      # v2.1.5（用户 21:33）：180 → 30——4 倍速下真实
# 战斗不可能 180s；此上限的真实用途是「按 F 后画面未变化」的判定窗口。
# 未开战时（角色阵亡）画面会一直停在备战页，30s 足够确认。
_NO_BATTLE_GRACE: float = 8.0   # v2.1.5：按 F 后仍停在备战页超过该秒数 →
# 直接返回 'no_battle'（无需等满上限），使「连续 2 次未开战」快速触发
_BATTLE_HEARTBEAT: float = 12.0  # v1.9.2：战斗轮询心跳间隔（长战斗不再静默）
_BATTLE_POLL: float = 1.2       # 战斗轮询间隔秒数
# v2.1.13：烙印界面就绪等待——真机 03:55:34 出现「识别到 imprint 与点击同一秒」
# → 界面转场动画未完成，点击被吞（卡未选中、按钮仍为「选择烙印」）→ 旧逻辑
# 把该按钮当确认钮点击并**记录烙印**（假成功）→ 同卡被记多次。
_IMPRINT_READY_WAIT: float = 0.9
# v1.8.88（用户定案）：移除 `_FAILURE_ESC_MAX`（失败画面 ESC 重试次数
# 上限）。ESC / 返回按钮改为无限交替，直到退出失败画面或用户停止。

# 玩法内部状态中文标签（仅日志用）
_PLAY_STATE_LABELS: dict = {
    'door':         '选门地图',
    'battle_prep':  '备战画面',
    'victory':      '战斗胜利弹层',
    'failure':      '战斗失败',
    'imprint':      '烙印选择',
    'shop':         '菲茨小店',
    'boss_cleared': '难度通关',
    'final_settle': '探索完成结算',
    'end_confirm':  '终止确认弹窗',
    'unknown':      '未知/转场',
}


class LabyrinthTask(BaseTask):
    task_id = task_id

    #: v2.1.14：跨轮「退出备战/结束探索失败」计数（类级默认，防直调 _play_loop 未初始化）
    _no_battle_rounds: int = 0

    # v2.1.9（用户 22:35 规范）循环语义声明——迷宫内部各态均可就地续跑
    loop_screens = frozenset({
        'labyrinth.door', 'labyrinth.battle_prep',
        'labyrinth.imprint', 'labyrinth.imprint_fallback',
        'labyrinth.shop', 'labyrinth.shop_fallback',
        'labyrinth.door_fallback', 'labyrinth.victory',
        'labyrinth.boss_cleared', 'labyrinth.failure',
        'labyrinth.final_settle', 'labyrinth.leave_settle',
        'labyrinth.end_confirm',
    })
    # 家门口：迷宫主页 → 点「进入」即进内部（不必退回玩法目录再点卡片）
    near_screens = frozenset({'nav.labyrinth_main'})
    label = label
    description = description

    # v1.8.59：关卡计数类级默认（实例在 _run_once 每轮探索重置为 0；
    # 直调 _pick_door/_decide_door 的干跑测试无 _run_once 时兜底）
    _floor_count: int = 0
    _ever_clicked: bool = False   # v1.8.60：是否已点过第 1 扇门

    # 迷宫参数（v1.8.9：新增「挑战深层」）：
    # deep_challenge=False（默认）：第 15 层 Boss 通关 → 点「结束探索」退出；
    # deep_challenge=True：第 15 层通关 → 点「继续探索」进入深层 16-20 层
    # 运行模式（v1.8.63 二选一 → v1.8.93 三选一，用户 2026-09-09 定案）：
    # clear15（模式1，默认）：不进烙印晶球（多门时排除），循环 1→15，
    #   15 层通关 → 点「结束探索」退出本轮 → 重开；
    # boss20（模式2，单核）：深层打完 19 关 → 首领门前（顶栏 19/20）查
    #   库存——已有执裁之盾 → 任务停止、画面停在门前（保留进度，绝不点
    #   结束探索——会清零进度）；没有 → 打掉首领（游戏自动结算）→ 重开。
    #   晶球里候选出现执裁之盾不受史诗数量限制（收集目标优先）。
    # boss20_dual（模式3，双核）：同模式 2，达标条件 = 执裁之盾+溢出防御。
    options_schema = {
        'run_mode': {
            'type': 'enum',
            'label': '运行模式',
            'choices': [
                ('clear15', '模式1：通关15结束'),
                ('boss20', '模式2：20关前停止（单核）'),
                ('boss20_dual', '模式3：20关前停止（双核）'),
            ],
            'default': 'clear15',
            'desc': '模式1：15层通关即结束重来；模式2/3：打完19关停在首领'
                    '门前，库存达标（单核/双核）即停，不达标打掉首领重来',
        },
    }

    # ── 时间上限支持（v1.8.4；v1.8.88 起不再有轮次上限）────
    # max_time 由调度器 ctx 注入（tasks_enabled.json → TaskInstance.max_time
    # → scheduler ctx → BaseTask.__init__ 的 self.max_time）。auto_farm 是
    # bot 主循环内联检查（loop.py:677）；labyrinth 此前从不评估 max_time，
    # 「时间(秒)」设置形同虚设。本任务用 check_stop() 覆写统一感知：
    # 文件内所有长等待点位（导航/进入/玩法循环/战斗轮询 ≤1.2s）早已调用
    # check_stop()，覆写后时间上限自动生效，粒度即各轮询间隔。
    # ⚠️ 结果语义：max_time 达标视为正常完成返回 'done'（与 auto_farm 一致，
    # 日志同款文案"任务指标达成"）；手动停止才返回 'stopped'——调度器收到
    # 'stopped' 会中止整个队列（tasks/scheduler.py run_all），时间上限不应中断
    # 后续任务。
    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self._time_up: bool = False         # max_time 已达标（区分手动停止）
        self._time_up_logged: bool = False  # 「任务指标达成」日志只打一次
        # v1.8.63 旧配置迁移：ctx 原始 options 无 run_mode（用户从未在
        # 新 UI 选过模式）且勾过 deep_challenge（挑战至20关）→ 迁移为
        # boss20。迁移一次性——用户在 UI 选任一模式保存后写入 run_mode，
        # 本分支永久失效（BaseTask 的 setdefault 默认值不影响判断）
        raw = ctx.get('options') or {}
        if 'run_mode' not in raw and raw.get('deep_challenge'):
            self.options['run_mode'] = 'boss20'

    def _run_mode(self) -> str:
        """运行模式（v1.8.93 三选一）：'clear15'=通关15结束（默认）；
        'boss20'=20关前停止（单核）；'boss20_dual'=20关前停止（双核）。
        旧 deep_challenge 迁移在 __init__。"""
        mode = str(self.options.get('run_mode') or '')
        return mode if mode in ('clear15', 'boss20', 'boss20_dual') \
            else 'clear15'

    def _deep_mode(self) -> bool:
        """是否深层模式（模式2/3）：15 层通关后点「继续探索」进 16-20。"""
        return self._run_mode() in ('boss20', 'boss20_dual')

    def _core_names(self) -> Tuple[str, ...]:
        """本模式要凑的核（模式2=执裁之盾；模式3=执裁之盾+溢出防御）。"""
        return ('执裁之盾', '溢出防御') \
            if self._run_mode() == 'boss20_dual' else ('执裁之盾',)

    def _core_requirement_met(self) -> Tuple[bool, str]:
        """核数达标判定（v1.8.93，模式2/3 首领门前用）。

        库存来源 = **本局获得记录**（_record_imprint_gain，方案 1：
        每次三选一/具名命中即入库）。v2.1.8 起不再依赖晶球门滚动扫描。
        本局无任何记录 → 视为未达标。

        Returns:
            (是否达标, 达标条件描述)
        """
        inv = getattr(self, '_orb_inventory', None)
        names = {n for n, _q in inv} if isinstance(inv, list) else set()
        if self._run_mode() == 'boss20_dual':
            need = ('执裁之盾', '溢出防御')
            ok = all(n in names for n in need)
            return ok, '双核（执裁之盾+溢出防御）'
        ok = '执裁之盾' in names
        return ok, '单核（执裁之盾）'

    def _record_imprint_gain(self, name: str, qual: str) -> None:
        """记录本局获得的烙印（v1.8.93 引入，v2.1.10 修）——**方案 1：获得即入库**。

        三选一/四选一选中的烙印立即入库（库存从**本局第一次获得**起累积）。
        普通卡 OCR 读不出名字 → 以「未记录·稀有度」占位（核数判定只查具名，
        占位不影响）；具名烙印（意愿表）名字来自 OCR 命中，可靠。

        v2.1.10 修复（用户 23:21 排查确认）：此前保留了方案 2 时代的前置守卫
        `if not isinstance(_orb_inventory, list): return`——而方案 1 下库存本
        就"从零累积"，未进过晶球门时它尚不存在 → **15 关前获得的烙印全部
        被静默丢弃**（真机日志：卡名识别有、烙印记录无）→ 进晶球门时
        「本局已获烙印(0)」→「替换目标耗尽」→ 双核永远凑不齐。
        现改为惰性建立列表，保证每次获得都真实入库。
        """
        # v2.2.6（用户 2026-09-15）：**执裁之盾 / 制裁之盾 是两个不同烙印**，
        # 名字仅首字不同（执／制）而稀有度不同 → 用稀有度做**交叉校验**，
        # 防止 OCR 形近字混读把「制裁之盾」当成收集核「执裁之盾」入库
        # （否则 `_core_requirement_met()` 会误判已集齐 → 提前停/错误换烙印）。
        exp = _IMPRINT_EXPECT_QUAL.get(name)
        if exp is not None and qual not in ('未知', exp):
            _alt = next((n for n, q in _IMPRINT_EXPECT_QUAL.items()
                         if q == qual and n != name), None)
            if _alt:
                self.log(f"[警告] 烙印名读作「{name}」但稀有度为「{qual}」"
                         f"——「{name}」应为「{exp}」→ 判定实为「{_alt}」"
                         f"（形近字混读，按稀有度纠正）")
                name = _alt
        if not isinstance(getattr(self, '_orb_inventory', None), list):
            self._orb_inventory = []     # v2.1.10：方案 1 下从零累积（不再丢弃）
        self._orb_inventory.append((name, qual))
        if qual == '史诗':
            self._orb_epic_count = getattr(self, '_orb_epic_count', 0) + 1
        self.log(
            f"[数据] 烙印记录 +「{name}」（{qual}），"
            f"本局累计 {len(self._orb_inventory)} 枚"
        )

    def _run_mode_label(self) -> str:
        """当前运行模式的中文标签（v1.8.95 任务启动日志用）。"""
        mode = self._run_mode()
        return next(
            (lbl for k, lbl in self.options_schema['run_mode']['choices']
             if k == mode), mode)

    def check_stop(self) -> bool:
        """覆写：手动停止/暂停超时（super）或 max_time 时间达标 → True。"""
        if super().check_stop():
            return True
        return self._time_limit_reached()

    def _time_limit_reached(self) -> bool:
        """max_time 是否已达标（None / <=0 = 不限，与 auto_farm 语义一致）。

        Returns:
            bool: True 表示已达时间上限（日志首次触发时打一次）
        """
        if not self.max_time or self.max_time <= 0:
            return False
        if self.elapsed < self.max_time:
            return False
        self._time_up = True
        if not self._time_up_logged:
            self._time_up_logged = True
            self.log(f"任务指标达成: 时间 {self.max_time}s，结束任务")
        return True

    def _stop_result(self) -> str:
        """停止时结果映射：时间达标 → 'done'（不中断队列）；手动停止 → 'stopped'。"""
        return "done" if self._time_up else "stopped"

    # ── 前置检查 ────────────────────────────────────────────

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    # ── 任务主体 ────────────────────────────────────────────

    def run(self) -> str:
        """迷宫任务主体（v1.8.20 多轮探索）。

        一次 run() 连续执行多轮探索：通关一轮后自动开启下一轮，直到
        手动停止、时间上限或某轮非 done（failed/stopped）返回。
        v1.8.19 及之前跑完一轮即返回 done（用户反馈"默认无限轮次实际
        跑一次就停"的根因之一——另一根因是深层「继续探索」后误返回
        done，见 _play_loop boss_cleared 分支）。

        Returns:
            str: 'done' | 'failed' | 'stopped'
        """
        # v1.8.95（用户定案）：任务启动即打印运行模式（真机核查用）
        self.log(f"[信息] 运行模式：{self._run_mode_label()}")
        self._rounds: int = 0
        # v2.1.14：跨轮「退出备战/结束探索失败」计数——**不在 _run_once 重置**
        # （真机 11:42~11:58：角色全灭时每轮收尾都失败，却仍被当成本轮完成
        #  → 空转 30 轮 / 16 分钟；需连续失败 2 轮即停止任务）
        self._no_battle_rounds: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            # v2.1.7（用户 22:03 真机）：**每轮重置烙印库存镜像**——旧实现
            # 只在 run() 开头重置一次（循环外），第 2 轮进晶球时仍带第 1 轮
            # 记录（真机：第 2 轮第 1 枚「泰坦之力」被记为「库存共 11 枚」）。
            # v1.9.34 的教训在此再次体现：重置点必须在**每轮入口**。
            # v2.1.21：收敛为 `_reset_orb_mirror()`，与「结束探索」处共用。
            self._reset_orb_mirror()
            result: str = self._run_once()
            if result != 'done':
                return result
            self._rounds += 1
            if self.max_turns is not None and self._rounds >= self.max_turns:
                self.log(
                    f"[信息] 已完成 {self._rounds} 轮探索"
                    f"（轮次上限 {self.max_turns}），任务结束"
                )
                return "done"
            self.log(
                f"[信息] 第 {self._rounds} 轮探索完成，开启下一轮"
                f"（轮次上限：{'无限' if self.max_turns is None else self.max_turns}）"
            )

    def _run_once(self) -> str:
        """单轮探索：启动探测 → 导航 → 点进入 → 玩法循环（原 run() 主体）。

        阶段 0：启动探测 startup_probe()（覆写 BaseTask 契约，v1.8.7；
                合并原弹窗清理）——OCR 一帧双重判定：
                ① 已在迷宫内部任意玩法界面（door/imprint/shop/battle_prep/
                victory/failure/boss_cleared）→ 'ready'：跳过导航与「进入」，
                直接 _play_loop 续跑——最优路径原则：用户在任意界面启动任务
                时直接找最近可执行点，不做无谓回退重导航（真机 2026-09-03
                烙印界面启动触发，原逻辑导航 ESC 死循环）；
                ② 探索完成结算页（v1.8.20，轮次循环上一轮残留）→ 点屏幕
                跳过再判，不得当玩法域直通（否则跳过后落迷宫主页无门卡
                → unknown ESC 空转）；
                ③ 无玩法状态 → 检查残留「是否继续探索」弹窗并关闭（v1.7.11）
        阶段 1：导航到异界迷宫主界面（phase 'labyrinth'）；
                续局（启动即已在迷宫内部）由 _is_in_labyrinth_play 兜底，
                直接置 _resumed 跳过阶段 2（续局界面无「进入」按钮）
        阶段 2：点「进入」按钮 → 探测弹窗 → 绿 ✓ 确认（v1.7.10/11）；
                续局时跳过（已在迷宫内部，界面无「进入」按钮）
        阶段 3：迷宫内部玩法循环 _play_loop（v1.7.12 新增）：
                选门 → F 开战 → 胜利/烙印/难度通关处理 → 至 15/20 层通关退出
        任意阶段达 max_time 时间上限 → 记录"任务指标达成"并以 done 正常结束
        （与 auto_farm 一致）。

        Returns:
            str: 'done' | 'failed' | 'stopped'
        """
        self.log(
            "启动（导航 + 玩法循环；任意迷宫内部界面启动可直通续跑，v1.8.7）"
        )
        self._resumed: bool = False   # 续局标志：点卡片后跳过了迷宫主页
        # v2.1.4（用户 21:14）：连续战斗等待超时计数——连续 2 次判「无法开战」
        # （角色全部阵亡/阵容不足），结束本轮而非死循环重按 F
        self._battle_timeout_streak: int = 0
        # v1.8.59：关卡计数（初始化 0，每次成功点门 +1；顶栏 X/15 时
        # 计数=X——点门后顶栏 +1，天然同步；每轮探索重置）
        self._floor_count: int = 0
        self._ever_clicked: bool = False   # v1.8.60：首扇门标记

        # 阶段 0（v1.8.7）: 启动探测 startup_probe()（覆写 BaseTask 契约，
        # 三态 'ready'/'clear'/'failed'，OCR 一帧双重判定）：
        #   'ready'  已在迷宫内部任意玩法界面（door/imprint/shop/
        #            battle_prep/victory/failure/boss_cleared）→ 最优路径
        #            = 跳过导航与「进入」，直接 _play_loop 续跑
        #   'failed' 残留「是否继续探索」弹窗且绿✓未点中 → 失败
        #   'clear'  无弹窗可导航（原阶段 0 语义）
        # 背景：用户可在任何界面启动任务（真机 2026-09-03 在烙印界面启动——
        # detector 对迷宫内部判 unknown、_is_in_labyrinth_play 只认 door 特征词
        # → 烙印界面判 False → 导航 ESC 死循环，而烙印界面 ESC 无效）。
        probe: str = self.startup_probe()
        if probe == 'ready':
            self._resumed = True
            self.log("[信息] 跳过导航与「进入」，直接进玩法循环续跑")
            return self._play_loop()
        if probe == 'failed':
            if self.check_stop():
                return self._stop_result()
            self.log("[错误] 启动时存在「是否继续探索」弹窗且绿✓未能点中")
            return "failed"

        # 阶段 1: 导航到异界迷宫主界面
        ok: bool = self._navigate_to_labyrinth()
        if self.check_stop():
            return self._stop_result()
        if not ok:
            self.log_nav_failed("异界迷宫")
            return "failed"
        self.log("导航完成，已在异界迷宫主界面")

        # 阶段 2: 点「进入」+ 处理「是否继续探索」弹窗
        if self._resumed:
            self.log("[信息] 续局（进度保留），跳过「进入」步骤，直接进玩法循环")
        else:
            if not self._enter_labyrinth():
                if self.check_stop():
                    return self._stop_result()
                self.log("[错误] 点进入或弹窗处理失败")
                return "failed"
        self.log("已进入玩法流程，开始迷宫内部玩法循环")

        # 阶段 3: 迷宫内部玩法循环（v1.7.12）
        return self._play_loop()

    def summary(self) -> str:
        return "逐层选门战斗推进（15/20 层通关）"

    # ── 导航状态机 ──────────────────────────────────────────

    def _navigate_to_labyrinth(self) -> bool:
        """导航到迷宫主页/玩法内部（v1.8.32：**全局导航系统**唯一路径）。

        判定走全局界面注册表（screen_registry），动作按「目标层 - 当前层」
        差值走最短路径：进层（点玩法目录/点卡片/滚动找卡）或脱离
        （ESC/返回按钮交替）。续局（进度保留直进玩法内部）由 arrived_cb
        判定并置 _resumed。旧 detect_phase 状态机已删除。
        """
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(
            self.bot, logger=self.log, stop_check=self.check_stop,
        )
        if nav.navigate_to('labyrinth', arrived_cb=self._nav_arrived):
            # 续局场景：全局导航在**玩法内部界面**判定到达（无「进入」按钮）
            if nav.last_screen.startswith('labyrinth.'):
                self._resumed = True
                self.log("[信息] 已在异界迷宫内部（续局），跳过「进入」")
            return True
        return False

    def _nav_arrived(self, screen_id: str, items: List[Tuple]) -> bool:
        """全局导航「已到达」回调：迷宫主页或玩法内部界面（续局）均算到达。"""
        if screen_id == 'nav.labyrinth_main':
            return True
        return screen_id.startswith('labyrinth.') or \
            self._is_in_labyrinth_play(items)

    def _esc_with_fallback(
        self, phase_before: str, label: str = ''
    ) -> None:
        """ESC 退回 + 返回按钮模板匹配兜底（v1.7.5 新增）。

        流程：
          1. 按 ESC
          2. 等待 0.6s
          3. detect_phase 检测 phase 是否已退回（即 != phase_before）
             - 是 → ESC 生效，结束
             - 否 → 模板匹配「返回按钮」按钮（两张外观），命中即点击
        """
        self.bot.press_esc(label=label)
        self._sleep(0.6)
        if self.check_stop():
            return
        if self.bot.detect_phase() != phase_before:
            return  # ESC 已生效
        self.log(
            f"[警告] ESC 未退（phase 仍为 {phase_before}），"
            f"改用返回按钮模板兜底"
        )
        if self.bot.click_back_button(
            _BACK_BUTTON_TPLS, threshold=_BACK_THRESHOLD
        ):
            self._sleep(0.8)
        else:
            self.log("[警告] 返回按钮模板未命中，继续等下一轮 ESC 重试")

    # ── 玩法目录内：滚动找卡片 ──────────────────────────────

    def _wait_enter_labyrinth(self) -> bool:
        """点卡片后等待进入异界迷宫界面。

        v1.8.3 续局支持：进度保留时点卡片会跳过迷宫主页、直接进内部
        「继续进度」界面（detector 判 unknown）→ 本任务自 OCR 兜底判定。
        两条路径都算成功，续局路径置 self._resumed=True，run() 据此
        跳过「进入」步骤（续局界面没有「进入」按钮）。

        Returns:
            bool: True 表示已进入（迷宫主页 或 续局内部界面）
        """
        deadline: float = time.time() + _ENTER_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            if self.bot.detect_phase() == 'labyrinth':
                self._resumed = False
                return True
            if self._is_in_labyrinth_play():
                self.log(
                    "[信息] 检测到迷宫内部界面（续局：进度保留，"
                    "跳过「进入」步骤）"
                )
                self._resumed = True
                return True
            self._sleep(0.8)
        return False

    # ── 玩法主体（v1.7.9 第一段：点进入 + 弹窗处理）─────────────

    def _enter_labyrinth(self) -> bool:
        """点「进入」按钮 + 处理「是否继续探索」弹窗（按绿色 ✓）。

        流程：
          1. OCR 找「进入」文字并点击（主界面底部入口按钮）
          2. 等 1.5s 让弹窗/页面稳定
          3. 探测「是否继续探索」弹窗（v1.7.11 复用 _dismiss_continue_dialog）：
             - 弹窗存在 → 模板匹配绿色 ✓ 按钮点击
             - 未弹窗 → 阵容已满（12/12）不弹，直接进下一阶段
          4. 等 1.5s

        ⚠️ 为什么不依赖 detector confirm_dialog（v1.7.9 不动 detector）：
        detector 当前 confirm_dialog 判据只有"退出游戏"，异界迷宫
        "是否继续探索"弹窗会被 detector 透过弹窗看到背后异界迷宫
        主页（"异界迷宫"标题 + 无玩法目录关键字）→ 误判成 'labyrinth'。
        本任务用 OCR 探测弹窗特征 + 模板匹配按钮，隔离 detector 误判风险。
        通用 modal dialog 识别属 detector 架构演进，本会话不展开。

        ⚠️ v1.7.10 为什么按钮用模板匹配而非 OCR 找「继续」：
        v1.7.9 真机复测暴露 bug——弹窗按钮上无任何文字，OCR 找「继续」
        命中弹窗文案"是否**继续**探索"中的"继续"二字（非按钮），点击
        文案位置不触发按钮 → √ 未点中。改用透明背景圆形按钮模板
        （btn_confirm_check.png）直接匹配按钮图形，杜绝此类误命中。

        Returns:
            bool: True 表示已进入玩法流程（弹窗已确认或未弹）
        """
        # 1. 点「进入」按钮
        if not self._click_text('进入', min_conf=0.6):
            self.log("[错误] 未找到「进入」按钮")
            return False

        self._sleep(1.5)
        if self.check_stop():
            return False

        # 2. 探测并处理「是否继续探索」弹窗（v1.7.11 抽为复用方法，run 启动也用它）
        return self._dismiss_continue_dialog(after_enter=True)

    def _dismiss_continue_dialog(self, after_enter: bool = False) -> bool:
        """探测并关闭「是否继续探索」弹窗（若存在）。

        两个调用点（v1.7.11 真机修复后统一）：
        - run() 阶段 0：清理**上轮残留**的弹窗——真机实测上轮测试停在弹窗状态，
          重启后 detector 不识别该弹窗 → unknown → ESC 空转 14s。先清再导航。
        - _enter_labyrinth：点「进入」后正常弹出的弹窗。
          after_enter=True 时未弹窗属正常（阵容满 12/12），继续流程。

        弹窗特征文字：「是否继续探索」/「探索阵容尚有空缺」。
        处理方式：模板匹配绿色 ✓ 按钮（btn_confirm_check.png，maskedNCC）——
        ✓ = 继续探索（本次目标）；白 ✕ = 放弃探索，本流程禁止点。

        Args:
            after_enter: True = 来自点「进入」之后（未弹窗视为正常）；
                         False = 来自 run 启动清理（未弹窗视为正常，两者一致）

        Returns:
            bool: 弹窗已关闭或不存在 → True；存在但绿✓未命中 → False
        """
        items = self.bot._ocr_screen()
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.4
        ]
        has_dialog: bool = any(
            any(m in t for m in _CONTINUE_DIALOG_MARKS)
            for t in texts
        )
        if not has_dialog:
            if not after_enter:
                self.log("启动检查：无残留「是否继续探索」弹窗，继续")
            else:
                # v2.1.24（用户 17:21 日志修正）：原文案「阵容可能已满 12/12」
                # 是**推测**——此处真实逻辑只是「本帧未见继续探索类提示」，
                # 阵容到底几人无从得知。改为如实描述检测到的事实。
                self.log("未弹确认框（画面无「探索阵容尚有空缺 / 是否继续探索」"
                         "提示），继续")
            return True

        # 弹窗存在 → 模板匹配绿色 ✓ 按钮（v1.7.10 替代 OCR 找「继续」）
        where: str = "点进入后" if after_enter else "启动残留"
        self.log(
            f"检测到「是否继续探索」弹窗（{where}），模板匹配绿色 ✓ 按钮"
        )
        if self._click_template(
                _CONFIRM_CHECK_TPL, _CONFIRM_THRESHOLD,
                scale_range=_CONFIRM_CHECK_SCALE_RANGE,
                pre_wait=_CONFIRM_CHECK_PRE_WAIT):
            self._sleep(1.5)
            return True
        self.log("[错误] 弹窗存在但绿色 ✓ 按钮模板未命中")
        return False

    def _is_in_labyrinth_play(
        self, items: Optional[List[Tuple]] = None
    ) -> bool:
        """判定当前是否已在迷宫**内部**（含续局直接进入的「继续进度」界面）。

        v1.8.3：续局界面 detector 判 unknown（无「异界迷宫」标题、无
        「探索阵容」），由本任务自 OCR 兜底——延续 v1.7.9 起的隔离原则，
        不改 detector。命中任一特征词即认为已在迷宫内部。

        Args:
            items: 已有 OCR 结果则复用，None 则自行 OCR 一帧

        Returns:
            bool: True 表示已在迷宫内部（选门 / 继续进度界面）
        """
        if items is None:
            items = self.bot._ocr_screen()
        texts: List[str] = [
            it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
        ]
        return any(m in t for t in texts for m in _IN_PLAY_MARKS)

    def startup_probe(self) -> str:
        """覆写 BaseTask 契约：启动探测（v1.8.7，合并原阶段 0 弹窗清理）。

        OCR 一帧双重判定，返回三态（与基类契约一致）：
          'ready'  → 已在迷宫内部**任意玩法界面** → 直通 _play_loop
          'failed' → 残留「是否继续探索」弹窗存在但绿✓未点中
          'clear'  → 干净可导航（无弹窗 / 弹窗已模板 ✓ 关闭）

        为什么合并为一帧：run() 阶段 0 原就要 OCR 一次查残留弹窗，玩法
        状态识别直接复用同一帧——不增加 OCR 消耗（干跑测试 texts 序列
        零偏移），且导航前必须先清弹窗再判断，顺序天然正确。

        玩法状态识别：复用 _detect_play_state 状态机（door/imprint/shop/
        battle_prep/victory/failure/boss_cleared 任一命中即视为已在迷宫
        内部）。背景（用户真机 2026-09-03 烙印界面启动）：detector 对迷宫
        内部判 unknown，原 _is_in_labyrinth_play 只认 door 特征词 → 烙印等
        界面判 False → 导航 ESC 死循环（烙印界面 ESC 无效）。命中玩法状态
        即最优路径直通（_play_loop 各状态分支自带对应动作，如 imprint →
        _handle_imprint 点卡确认，完全无需重新导航）。

        Returns:
            str: 'ready' | 'failed' | 'clear'（BaseTask 契约三态）
        """
        items = self.bot._ocr_screen()

        # ⓪ v1.8.20：上轮「探索完成」结算页残留（轮次循环第二轮起的第一帧）
        # → 点击屏幕跳过再判。该页含「到达层数」会被误判 boss_cleared →
        # 'ready' 直通玩法循环 → 跳过后落迷宫主页（无门卡）→ unknown ESC 空转。
        # 跳过动作在此先行，跳不动则按 unknown 交由导航兜底。
        state: str = self._detect_play_state(items)
        if state == 'final_settle':
            self.log("[信息] 检测到「探索完成」结算页，点击屏幕跳过")
            for _ in range(3):
                self.bot.click_ratio(0.5, 0.5, label='探索完成_跳过')
                self._sleep(2.0)
                if self.check_stop():
                    return 'failed'
                items = self.bot._ocr_screen()
                state = self._detect_play_state(items)
                if state != 'final_settle':
                    break
            if state == 'final_settle':
                self.log("[警告] 探索完成结算页跳过失败，交由导航兜底")
                state = 'unknown'

        # ① 玩法状态识别（先判：迷宫主页/0层/玩法目录等词不命中 → unknown）
        if state != 'unknown':
            label: str = _PLAY_STATE_LABELS.get(state, state)
            self.log(
                f"[信息] 启动即在迷宫内部玩法界面（{state}({label})），"
                "无需导航，直接续跑"
            )
            return 'ready'

        # ② 残留弹窗检测（原阶段 0 _dismiss_continue_dialog 逻辑）
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.4
        ]
        has_dialog: bool = any(
            any(m in t for m in _CONTINUE_DIALOG_MARKS)
            for t in texts
        )
        if not has_dialog:
            self.log("启动检查：无残留「是否继续探索」弹窗，继续")
            return 'clear'
        self.log("检测到「是否继续探索」弹窗（启动残留），模板匹配绿色 ✓ 按钮")
        if self._click_template(
                _CONFIRM_CHECK_TPL, _CONFIRM_THRESHOLD,
                scale_range=_CONFIRM_CHECK_SCALE_RANGE,
                pre_wait=_CONFIRM_CHECK_PRE_WAIT):
            self._sleep(1.5)
            return 'clear'
        self.log("[错误] 弹窗存在但绿色 ✓ 按钮模板未命中")
        return 'failed'

    # ── 迷宫内部玩法循环（v1.7.12）──────────────────────────
    # 设计原则（与 v1.7.9~11 弹窗处理一脉相承）：迷宫内部状态全部用
    # 本任务自 OCR 判定，不动 detector——detector 的 confirm_dialog
    # 判据盲区已证明通用 phase 判定易被弹窗/特殊界面干扰，隔离最稳。

    def _play_loop(self) -> str:
        """迷宫内部玩法主循环：选门→开战→结算→循环，至终点通关退出。

        终点（v1.8.9）：
          默认（deep_challenge=False）→ 第 15 层 Boss 通关（难度通关结算）
          点「结束探索」→ done
          深层（deep_challenge=True）→ 15 层通关点「继续探索」进入深层
          16-20（顶栏 X/20，全程单门战斗关），至第 20 层「深层首领」通关
          点「结束探索」→ done
        精英/BOSS/深层门（5/10/15/20/16-19 层）都是单门 door 界面：中间
        门卡标题为「精英/一点麻烦」「首领」「深层首领」「深层挑战」等，
        btn_door_battle 模板 1.000 命中门卡按钮 → 进门 → 备战画面 → F。

        状态判定 _detect_play_state 按 OCR 特征文字分流：
          boss_cleared → 结算退出（深层模式感知，见 _end_exploration）
          failure      → ESC×3 + 返回按钮兜底退出失败画面 → 结束探索 → done
          victory      → 点空白关闭弹层（战斗胜利 / 挑战成功）
          imprint      → 点第一张卡（三选一 0.42 / 四选一 0.327 自适应）+ 确认
          shop         → 点「继续前进」退出（不购物，用户定案）
          battle_prep  → 按 F 开战 → 等战斗结束
          door         → 按优先级点门卡（单门精英/BOSS 场景按钮命中直点）
          unknown      → 短暂转场先等待；连续多次才 ESC；超限判失败

        Returns:
            str: 'done' | 'failed' | 'stopped'
        """
        unknown_streak: int = 0
        # v1.8.87：boss_cleared 找不到「结束探索」的跳过兼底计数（防死循环）
        boss_skip_tries: int = 0
        # v1.8.57：同状态空转计数（已识别状态无推进兑底，防 victory 误判
        # 之类的循环每 5s 空转刷屏；unknown 帧重置——转场等待是合法慢场景）
        stall_state: str = ''
        stall_streak: int = 0
        # v1.8.6：迭代上限取消 → while True（原 80/200 均被真机场景突破；
        # 终止靠 done 路径 + unknown give_up + check_stop 兜底）
        while True:
            if self.check_stop():
                return self._stop_result()
            # v2.1.27（P0-1 配套）：显式迭代计数——测试的"防死循环上界"
            # 断言改读本计数，不再依赖日志条数（日志降噪后即失效）
            self.count_iter()

            items = self.bot._ocr_screen()
            # v1.9.2（用户定案全局规则）：任何画面出现「点击空白处」
            # 一律先点掉再分流（战斗结束结算/各类奖励弹层）。
            # 例外：终止确认弹窗（含「长按结束」）——点空白会取消
            # 弹窗，必须交由 end_confirm 分支长按处理
            if (not any('长按结束' in it[2] for it in items
                        if len(it) >= 4 and it[3] >= 0.5)
                    and self.dismiss_blink_popup(items=items)):
                continue
            state: str = self._detect_play_state(items)
            if state != 'unknown':
                unknown_streak = 0
            # v1.8.72（用户要求）：残局续跑计数立即对齐顶栏——每帧校准
            # （UI 是真相源），不等第一次选门才校正。点门后经 3s 转场
            # 等待顶栏已 +1，与 _count_door_entered 的 +1 天然一致，无
            # 回拉风险；转场/遮挡帧顶栏不可读（None）自动跳过
            floor_now: Optional[int] = self._read_floor(items)
            if (floor_now is not None
                    and floor_now != self._floor_count):
                # v1.8.83：校正静默（用户定案删除警告日志），照常校正
                self._floor_count = floor_now
            label: str = _PLAY_STATE_LABELS.get(state, state)

            # ── v1.8.57 同状态空转兑底（在状态分支分发前统一拦截）──
            if state == 'unknown':
                stall_state, stall_streak = '', 0
            elif state == stall_state:
                stall_streak += 1
            else:
                stall_state, stall_streak = state, 1
            if stall_streak >= _STALL_GIVE_UP:
                self.log(
                    f"[错误] {state}({label}) 已连续 {stall_streak} 轮无推进，"
                    "脱险动作无效，玩法循环终止（请截图反馈该界面）"
                )
                return "failed"
            if stall_streak >= _STALL_ESC_AFTER:
                self.log(
                    f"[警告] {state}({label}) 已连续 {stall_streak} 轮无推进，"
                    "改走 ESC/返回键交替脱离"
                )
                self.escape_alternating(stall_streak, label='玩法空转')
                continue

            if state == 'unknown' and getattr(
                self, '_last_screen', 'unknown'
            ) != 'unknown':
                # v1.8.25：全局注册表精准指名他域画面（日志可见，便于补判据）
                # v2.1.27（P0-1）：改用 log_state——独立 key，避免与普通
                # unknown 互相压制；状态未变则不再刷屏
                self.log_state(
                    'unknown:other',
                    f"玩法循环 | 未识别画面(他域："
                    f"{zh_screen(self._last_screen)}，脱离)"
                )
            elif state == 'unknown' and unknown_streak > 1:
                # v1.9.35（用户）：转场连续 unknown 静默——日志只在
                # 首帧打一次，不再每帧刷屏
                pass
            else:
                # v2.1.27（P0-1）：状态**变化时**才打印（原每帧一条，
                # 实测占全量日志 22.7%）；同状态每 50 帧补心跳
                self.log_state(state, f"玩法循环 | {state}({label})")

            if state == 'final_settle':
                # v1.8.20：探索完成结算页 → 点击屏幕跳过
                # v1.8.27（真机 9/6）：该页出现在玩法循环中 = **探索已结束**
                # （20 层 Boss 直弹此页，或结束探索被「点击空白处」提前关闭）
                # → 跳过后必须终止本轮，否则循环会对我方主页/玩法目录/世界
                # 等导航界面狂按 ESC 空转 2 分钟（真机复现）
                self.log("[信息] 探索完成结算页，点击屏幕跳过")
                self.bot.click_ratio(0.5, 0.5, label='探索完成_跳过')
                self._sleep(2.0)
                # v2.1.21（用户定案）：**正常通关 / 探索完成同样要清镜像**。
                # 该页是 20 层 BOSS 直弹的结算页，**不经过 _end_exploration**
                # → 旧实现只有「下一轮入口」兜底；此处与探索结束同点清空。
                if self._reset_orb_mirror():
                    self.log("[信息] 探索已结束 → 烙印库存镜像已清空")
                self.log("[成功] 本次探索已结束（结算页已跳过），任务完成")
                return "done"

            if state == 'boss_cleared':
                # v1.8.9：deep_challenge 时 15 层终点点「继续探索」进深层；
                # 否则点「结束探索」。20 层深层终点必结束。
                # v1.8.20：_end_exploration 改三态返回——'continue' 表示已点
                # 「继续探索」进入深层 16-20，必须**继续玩法循环**而非结束任务
                #（v1.8.19 及之前此处一律返回 done = 20 层勾选无效的根因）
                self.log("[信息] Boss 层通关（难度通关），处理结算决策")
                res: str = self._end_exploration(items)
                if res == 'continue':
                    self._sleep(_DOOR_TRANSITION_WAIT)
                    continue
                if res == 'done':
                    return "done"
                # v1.8.87 兼底：「结束探索」未找到（如「离开迷宫」荣誉结算
                # 页 OCR 只读出「到达层数」被误判，真机 21:01 事故）
                # → 点屏幕跳过重判，不直接 failed；限 2 次防死循环
                boss_skip_tries += 1
                if boss_skip_tries > 2:
                    self.log("[错误] 难度通关结算点屏幕跳过 2 次仍无效")
                    return "failed"
                self.log("[警告] 未找到「结束探索」，点屏幕跳过重判 "
                         f"({boss_skip_tries}/2)")
                self.bot.click_ratio(0.5, 0.5, label='难度通关_跳过兼底')
                self._sleep(2.0)
                continue

            if state == 'failure':
                self.log("[警告] 战斗失败 → 按用户策略结束本次探索")
                if (self._handle_failure()
                        and self._end_exploration(items) == 'done'):
                    return "done"
                self.log("[错误] 失败画面退出或结束探索失败")
                return "failed"

            if state == 'victory':
                self.bot.click_ratio(0.5, 0.5, label='胜利弹层_点空白关闭')
                # v1.8.12（用户实测）：胜利后若触发烙印选择，有约 5s 变身
                # 动画 → 立即 OCR 会判 unknown 走 ESC。关闭弹层后 +3s。
                self._sleep(_VICTORY_SETTLE + _VICTORY_EXTRA_WAIT)
                continue

            # v1.8.52/66：烙印晶球替换列表页——**必须先于 imprint 分支**：
            # 替换主界面（首行被替换烙印+三候选+保留烙印/确认选择）会被
            # imprint_fallback（确认选择含「确认」+ 稀有度标签≥2）抢判为
            # 普通 imprint → 全量选卡反复选中首行被替换烙印（真机 23:45：
            # 连点 (175,303) 确认无效空转）。入口词：
            #   「选择已有烙印」＝选中候选后的二级确认页
            #   「保留烙印」＝替换主界面独有按钮（v1.8.66 补）
            if any('选择已有烙印' in it[2] or '保留烙印' in it[2]
                   for it in items if len(it) >= 4 and it[3] >= 0.5):
                self.log("[信息] 烙印晶球替换列表，进入替换流程")
                # v2.1.8（用户 22:21）：改用方案 1（获得即入库）——
                # 不再全量滚动遍历球内列表，原「清 _orb_scanned 强制重扫」
                # 已删除（库存来源 = 本局 _record_imprint_gain 记录）
                self._imprint_orb_flow()
                self._sleep(_STATE_POLL)
                continue

            if state == 'imprint':
                self._handle_imprint(items)
                continue

            if state == 'shop':
                # v1.8.52：商店不再直接退出——购买目标遗物后退出
                self._shop_flow()
                continue

            if state == 'end_confirm':
                # v1.8.62：「是否终止本次探索？」确认弹窗（选门界面 ESC
                # 脱离触发）→ 长按「长按结束」终止探索；终止后落结算页/
                # 迷宫主页，由后续帧自然收尾
                self.log("[信息] 终止确认弹窗，长按「长按结束」")
                pos = self.bot.find_text(items, '长按结束', min_conf=0.6)
                if pos:
                    self.bot.long_press_at(
                        pos[0], pos[1], duration=2.5, label='长按结束'
                    )
                else:
                    # OCR 漏检帧兜底：实测布局 (541,637)@1283x1022
                    self.bot.long_press_ratio(
                        0.422, 0.623, duration=2.5, label='长按结束_兜底'
                    )
                self._sleep(_DOOR_TRANSITION_WAIT)
                continue

            if state == 'battle_prep':
                _battle_res = self._start_and_wait_battle()
                if _battle_res == 'formation_required':
                    # v1.8.52：旧行为 = 停止任务，等用户手动布阵。
                    # v2.1.21（用户 16:34 定案·机制修正）：改为**自动收尾**——
                    # ✕ 已在 _start_and_wait_battle 内点掉，这里执行「退出备战
                    # （回选门界面）→ 结束探索」，结束本轮而非中止整个任务。
                    # 原理（用户实测）：阵亡后只要**不开战**，游戏记录的默认
                    # 阵容仍为满员 → 新一轮满员出战；一旦硬打，残缺阵容会被
                    # 记成默认阵容 → 上阵越少难度越高 → 继续减员（恶性循环）。
                    # 复用带回执的 _abort_round()，失败同样计入跨轮保护。
                    self.log("[信息] 上阵人数不满 → 已取消战斗，"
                             "退出备战并结束探索（保住满员阵容记录）")
                    _abort_res: str = self._abort_round()
                    if _abort_res:
                        self._no_battle_rounds = 0
                        self._battle_timeout_streak = 0
                        if _abort_res == 'continue':
                            continue
                        return 'done'
                    self._no_battle_rounds += 1
                    self.log(f"[警告] 退出备战/结束探索未成功"
                             f"（连续第 {self._no_battle_rounds} 轮）")
                    if self._no_battle_rounds >= 2:
                        self.log("[错误] 连续 2 轮均无法退出备战（角色阵亡/"
                                 "阵容不可用）→ 停止任务；请调整阵容后重跑")
                        return 'stopped'
                    return 'done'
                # v2.1.4（用户 21:14 真机）：战斗等待超时**连续 2 次** →
                # 判定「无法开战」（角色全部阵亡 / 阵容为空）→ 结束本轮。
                # 旧逻辑超时无返回值 → 主循环又按 F → 又等 180s → 死循环
                # （真机实测空转 8 分钟，用户手动停止）。
                if _battle_res in ('no_battle', 'timeout'):
                    self._battle_timeout_streak += 1
                    self.log(f"[警告] 未开战（第 "
                             f"{self._battle_timeout_streak} 次）")
                    if self._battle_timeout_streak >= 2:
                        # v2.1.4/2.1.5（用户 21:14/21:33 定案）：连续 2 次按 F
                        # 均未进入战斗（角色阵亡/阵容不足）→ 退出备战 + 结束探索
                        # v2.1.14：收尾改为**带回执的** `_abort_round()`——
                        # 旧逻辑吞掉失败值（'' → 当 done）→ 开新轮 → 画面仍在
                        # 备战页 → 空转 30 轮（真机 11:42~11:58 共 16 分钟）
                        self.log("[错误] 连续 2 次按 F 均未进入战斗（疑似角色全部"
                                 "阵亡/阵容不足）→ 退出备战并结束探索")
                        self._battle_timeout_streak = 0
                        _abort_res: str = self._abort_round()
                        if _abort_res:
                            self._no_battle_rounds = 0
                            if _abort_res == 'continue':
                                continue
                            return 'done'
                        self._no_battle_rounds += 1
                        self.log(f"[警告] 退出备战/结束探索未成功"
                                 f"（连续第 {self._no_battle_rounds} 轮）")
                        if self._no_battle_rounds >= 2:
                            self.log("[错误] 连续 2 轮均无法退出备战（角色阵亡/"
                                     "阵容不可用）→ 停止任务；请调整阵容后重跑")
                            return 'stopped'
                        return 'done'
                else:
                    # 正常开战 → 两个计数都清零
                    # （v2.1.14：`_no_battle_rounds` 也必须清——否则"上轮收尾失败
                    #   1 次 + 本轮恢复正常 + 再遇到一次失败"会提前触发停止，
                    #   过于敏感）
                    self._battle_timeout_streak = 0
                    self._no_battle_rounds = 0
                continue

            # v1.8.93（用户 2026-09-09 定案）：模式2/3 首领门前判定——
            # 打完 19 关后画面顶栏 19/20（或门卡标题命中「深层首领」）
            # 且 state=door → 查库存核数：
            #   达标 → 任务停止，画面停在门前（保留进度——点「结束探索」
            #          会清零进度，绝不点）；
            #   未达标 → 不拦截，落到 door 分支点门进首领战（无掉落，
            #          打完游戏自动结算 → 重开新一轮，全程零收尾操作）。
            # 停点判定不依赖门卡标题 OCR（真机 11:39 漏读标题 → 单门兜底
            # 点进去把首领打了），顶栏 19/20 是主判据，标题为佐证。
            # 仅 state=='door' 时判定：19 关战斗中顶栏同为 19/20，但
            # 战斗期不经过主循环帧，victory/结算页也不得误停。
            if self._deep_mode() and state == 'door':
                prog = self._extract_progress(items)
                titles_hit = any(
                    ('深层首领' in it[2] or '最终一战' in it[2])
                    for it in items
                    if len(it) >= 4 and it[3] >= 0.5)
                at_gate = titles_hit or (
                    prog is not None and prog[1] == 20 and prog[0] >= 19)
                if at_gate:
                    ok, desc = self._core_requirement_met()
                    if ok:
                        self.log(
                            f"[成功] {desc}已达标，任务停止——画面停在"
                            "首领门前（保留进度，不点结束探索）"
                        )
                        return 'stopped'
                    self.log(
                        f"[信息] 已到首领门前但{desc}未达标，"
                        "打掉首领后重开（打完游戏自动结算）"
                    )

            if state == 'door':
                self._pick_door(items)
                # v1.8.6：点门后加 3s 转场等待（unknown/转场会出现在点门/商店/
                # 精英/boss 后的转场动画，用户真机 2026-09-03）→ 减少误 ESC
                self._sleep(_DOOR_TRANSITION_WAIT)
                continue

            # ── unknown：闪烁弹窗 → 转场等待 → ESC → 返回键 → 放弃 ──
            # v1.8.12 全局规则（用户）：①「点击空白处关闭/继续」闪烁弹窗
            # 先清理（查当前帧即可——主循环轮询天然多次 OCR 覆盖闪烁相位），
            # 点掉后重置计数，**绝不因弹窗走 ESC**；② v1.8.24 起降级链为
            # ESC/返回按钮交替；③ v1.8.27：全局注册表指名导航域画面
            # （迷宫主页/玩法目录/世界等）= 已离开玩法执行域 → 优雅结束
            #（轮次循环下一轮会自动重新进入，续局进度保留）
            unknown_streak += 1
            _last: str = getattr(self, '_last_screen', 'unknown')
            if _last.startswith('nav.'):
                self.log(
                    f"[成功] 已离开玩法执行域（识别为：{zh_screen(_last)}），"
                    "本次探索结束"
                )
                return "done"
            if self.dismiss_blink_popup(items):
                self.log("[信息] 已关闭闪烁弹窗，继续玩法循环")  # v1.8.49 补齐静默分支
                unknown_streak = 0
                continue
            if unknown_streak >= _UNKNOWN_GIVE_UP:
                # 放弃前先试模板返回键（btn_back_arrow 等三款式）
                self.log("[警告] 连续未知画面超限，尝试模板返回键兜底")
                if self.bot.click_back_button(
                    _BACK_BUTTON_TPLS, threshold=_BACK_THRESHOLD
                ):
                    self.log("[信息] 返回键点击成功，重置未知计数继续")
                    unknown_streak = 0
                    self._sleep(1.5)
                    continue
                self.log("[错误] 连续未知画面超限且返回键无效，玩法循环终止")
                return "failed"
            if unknown_streak >= _UNKNOWN_ESC_AFTER:
                # v1.8.24：ESC 与返回按钮模板交替（用户定案：ESC 不万能，
                # 弹窗/特殊二级页 ESC 无响应必须点返回按钮）
                self.escape_alternating(unknown_streak, label='玩法未知')
            else:
                # v1.9.35（用户）：unknown 出现时机 = 点门/结算/烙印确认
                # 后的转场动画——多等一会再 OCR，下一帧直接到目标界面，
                # 省掉门卡「标题未命中→重试」的双帧浪费
                self._sleep(_UNKNOWN_TRANSITION_WAIT)

    def _detect_play_state(self, items: List[Tuple]) -> str:
        """迷宫域状态判定（v1.8.25 起委托全局界面注册表）。

        判据全部上收至 tasks/screen_registry.py（GLOBAL_SCREEN_REGISTRY，
        单一真相源，依据来源见 docs/界面判据清单.md）。本方法只做域映射：
          labyrinth.*      → 去前缀的本域状态名（原动作分流不变）
          labyrinth.push_map / 他域 id → 'unknown'（调用方走脱离链；
            self._last_screen 保留全局指名，日志可见精准画面名）
        评估顺序与历史行为逐条对齐（push_map → final_settle → boss_cleared
        → victory → failure → imprint → shop → imprint_fallback →
        battle_prep → door → shop_fallback → door_fallback），勿在注册表
        中随意调换。

        Returns:
            str: 'door' | 'battle_prep' | 'victory' | 'failure' | 'imprint'
                 | 'shop' | 'boss_cleared' | 'final_settle' | 'unknown'
        """
        screen: str = GLOBAL_SCREEN_REGISTRY.detect(items)
        self._last_screen: str = screen
        if screen.startswith('labyrinth.'):
            local: str = screen.split('.', 1)[1]
            # 兜底规则归并回主状态（原判据 fallback 直接返回主状态名）
            local = _FALLBACK_ALIAS.get(local, local)
            return local
        return 'unknown'

    def _door_btn_region(
        self, target_pos: Optional[Tuple[int, int]]
    ) -> Optional[Tuple[int, int, int, int]]:
        """门卡按钮搜索区（v1.8.28 修正：按**选中门**定位，非上次命中位置）。

        v1.8.27 原用「上次命中位置 ±(260,90)」：横向窗口 520 只覆盖单门/相邻
        门——双门场景两门中心间距约 426px（1280 宽屏 ≈427/853），若上一扇点
        左门、这一扇选右门，右门按钮会落在区域外 → 回落全屏，交替选门时
        优化失效。改为：
          横向：选中门标题 x（图像坐标，转屏幕 ±120）——只框选中的那扇门
          纵向：上次命中按钮行 y ±90（整局内按钮行 y 恒定，真机日志 725）
        区域仅 240×180 ≈ 全屏 3% 像素；无缓存（首扇门）→ 不缩区域。

        Args:
            target_pos: 选中门标题 (cx, cy) 图像坐标；None = 标题未识别

        Returns:
            (x, y, w, h) 屏幕坐标搜索区；None = 不缩区域
        """
        if target_pos is None:
            return None
        cache: Optional[Tuple[int, int]] = getattr(self, '_door_btn_cache',
                                                   None)
        if cache is None:
            return None      # 首扇门无按钮行基准 → 全屏（缩放带已收窄）
        try:
            off_l, off_t = self.bot._screen_offset()
        except Exception as _e:
            self.log(f"[调试] _door_btn_region() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            off_l, off_t = 0, 0     # mock/无偏移接口：图像坐标即屏幕坐标
        cx_screen: int = target_pos[0] + off_l
        cy_screen: int = cache[1]
        return (
            max(0, cx_screen - 120), max(0, cy_screen - 90), 240, 180,
        )

    def _find_door_button(
        self, target_pos: Optional[Tuple[int, int]] = None
    ) -> Optional[Tuple[int, int]]:
        """门卡进入按钮模板匹配（v1.8.27 提速版）。

        提速两手：
          1. 缩放带 0.5~2.0（16 档）收窄为 0.7~1.5（9 档）——按钮实际
             尺寸波动小，宽档位纯浪费；单模板耗时约减半
          2. 命中位置区域缓存：门卡按钮行在整局内 y 基本固定（真机日志
             按钮图 y=725 恒定），上次命中屏幕坐标 ±(260, 90) 小区域
             优先匹配（像素量 ≈ 全屏 6%），未命中再回落全屏兜底
             （布局可能变化，不能只搜小区域）

        Returns:
            (cx, cy) 屏幕绝对坐标；两模板全未命中 → None
        """
        hit: Optional[Tuple[int, int]] = None
        region: Optional[Tuple[int, int, int, int]] = self._door_btn_region(
            target_pos
        )
        if region:
            for tpl in _DOOR_BTN_TPLS:
                hit = self.bot.template_match(
                    tpl, threshold=_DOOR_BTN_THRESHOLD, silent=True,
                    scale_range=_DOOR_BTN_SCALE_RANGE, region=region,
                )
                if hit:
                    break
        if hit is None:
            # 首次（无缓存）或区域未命中 → 全屏兜底搜索
            for tpl in _DOOR_BTN_TPLS:
                hit = self.bot.template_match(
                    tpl, threshold=_DOOR_BTN_THRESHOLD, silent=True,
                    scale_range=_DOOR_BTN_SCALE_RANGE,
                )
                if hit:
                    break
        if hit:
            self._door_btn_cache = hit   # 供后续门卡区域加速
        return hit

    def _count_door_entered(self) -> None:
        """v1.8.60 点门成功后的计数更新（用户 2026-09-07 定案规则）：
        进入迷宫初始 1/15；点第 1 扇门计数保持 1 不变（确立计数）；
        第 2 扇门起每进一扇门 +1。续局启动（_resumed）的首扇门并非
        游戏第 1 扇门，按后续门处理直接 +1。"""
        if self._ever_clicked or getattr(self, '_resumed', False):
            self._floor_count += 1
        else:
            self._floor_count = max(self._floor_count, 1)
        self._ever_clicked = True
        # v1.8.69（用户要求）：每次进门后提示当前关数
        self.log(f"[信息] 当前是第 {self._floor_count} 关")

    def escape_alternating(
        self, streak: int, label: str = '未知画面', wait: float = 1.5
    ) -> bool:
        """覆写（v1.8.69）：烙印晶球界面按 ESC 会触发游戏 BUG（用户
        2026-09-08 实测禁令）——orb 流程期间及结束后冷却窗口内禁 ESC，
        改用 点空白类弹窗 + 返回按钮模板。冷却期外回退基类交替逻辑。
        """
        if time.time() < getattr(self, '_no_esc_until', 0.0):
            self.log(
                f"[信息] {label}：烙印晶球禁 ESC 冷却期，点空白+返回按钮"
            )
            items = self.bot._ocr_screen()
            if self.dismiss_blink_popup(items=items):
                self._sleep(wait)
                return True
            if self.bot.click_back_button(
                _BACK_BUTTON_TPLS, threshold=_BACK_THRESHOLD
            ):
                self._sleep(wait)
                return True
            self._sleep(wait)
            return True
        return super().escape_alternating(streak, label, wait)

    def _pick_door(self, items: List[Tuple]) -> None:
        """选门（v1.8.1）：OCR 标题选门 + 模板匹配点卡片下方进入按钮。

        真机确认（2026-09-03）：点卡片标题无效，必须点卡片底部的
        圆形按钮（战斗类门=白圆底交叉剑，小店=白圆底钱袋）。

        策略（标题与按钮各司其职）：
          标题 OCR（_DOOR_PRIORITY 优先级）→ 决定「选哪扇门」（x）
          按钮模板匹配（maskedNCC）→ 决定「点哪里」（y，门卡单排等高）
        组合：click_at(标题x, 按钮y)——按钮 y 统一转回图像坐标系。

        降级链：
          标题未识别 + 按钮命中   → 单门场景，直接点命中按钮
          按钮未命中 + 标题命中   → 回退 v1.7.12 行为（点标题，真机已知无效
                                    但聊胜于无；等补采其他门按钮图案）
          双未命中               → 不动作（v1.8.21 删除固定坐标中央兜底，
                                    分辨率变化后盲点不可控；交由主循环
                                    下一帧重试，重试仍持续失败走 unknown
                                    降级链）
        """
        # 1) 收集全部门卡标题（两轮置信度扫描合并，同卡去重）
        titles: List[Tuple[str, Tuple[int, int]]] = []
        for min_c in (0.6, 0.45):
            for kw in _DOOR_PRIORITY:
                for pos in self._find_all_text(items, kw, min_c):
                    if all(
                        abs(pos[0] - tp[0]) > 80 or abs(pos[1] - tp[1]) > 80
                        for _k, tp in titles
                    ):
                        titles.append((kw, pos))
                        if min_c == 0.45:
                            self.log(
                                f"[信息] 门卡标题低置信度命中「{kw}」"
                                "（二次扫描）"
                            )
        if not titles:
            # v1.8.53：标题未命中≠不动作——继续按钮匹配，恢复 v1.8.1
            # 降级链（按钮命中 → 单门场景直接点按钮，如精英/BOSS 门）
            self.log("[信息] 本帧无门卡标题命中，仅按按钮匹配处理")
            target_kw, target_pos = '', None
        else:
            self._door_no_title = 0   # v1.8.85：标题命中则清零漏检重试计数

        # 2) 单门场景：无需比较优先级，直接点（用户 2026-09-07 定案）
        if len(titles) == 1:
            target_kw, target_pos = titles[0]
            # v2.2.7（用户 09-15「游戏中不存在单门晶球的情况」）：模式1 下
            # 若**唯一读到的标题是晶球**，该标题必为误读、或另一扇门标题被
            # 漏读（真·单门晶球不存在）→ 直选会点到非预期卡片。
            # 此处补上「模式1 不进晶球」在**单门分支**的守卫（评分分支已有
            # score=99 拦截，单门分支不比较优先级 → 原来会漏过去）。
            # 处理：本帧不选，等下一帧重读（主循环有漏检重试→unknown 降级链，
            # 不会死循环；与 18b「首帧无标题不盲点」同思路）。
            if (target_kw == '烙印晶球'
                    and self._run_mode() == 'clear15'):
                self.log("[警告] 模式1 读到唯一的「烙印晶球」标题——该标题"
                         "必为误读或另一扇门标题漏读（游戏中不存在单门晶球）"
                         "→ 本帧不选，等下一帧重读")
                return
            self.log(f"[决策] 单门场景，直接选「{target_kw}」")
        elif titles:
            target_kw, target_pos = self._decide_door(titles, items)
        else:
            # 标题全未命中：走按钮匹配降级链（18b 单门点按钮）
            target_kw, target_pos = '', None

        # 2) 按钮模板匹配（战斗剑优先——门大多为战斗类）
        # v1.8.27/28：提速——默认全屏 16 档缩放 ×2 模板曾耗时 7-8s（真机日志
        # 14:07:46 door → 14:07:54 点击）；改窄缩放带 + 小区域优先
        btn_screen: Optional[Tuple[int, int]] = self._find_door_button(
            target_pos
        )

        # 3) 组合点击：标题 x + 按钮 y（模板屏幕坐标 → 转回图像坐标系）
        if btn_screen is not None and target_pos is not None:
            btn_img: Tuple[int, int] = self.bot.screen_to_image(*btn_screen)
            self.log(
                f"[点击] 门卡「{target_kw}」进入按钮 | "
                f"标题x={target_pos[0]} 按钮图y={btn_img[1]}"
            )
            self.bot.click_at(
                target_pos[0], btn_img[1], f'门卡_{target_kw}_按钮'
            )
            self._count_door_entered()   # v1.8.60：首扇门确立计数，后续 +1
            return

        # 4) 单门场景：标题未识别但按钮命中（精英/BOSS 层）→ 直接点按钮
        if btn_screen is not None:
            # v1.8.85：标题未命中时先等一帧重试（转场期 OCR 常漏标题），
            # 连续 2 帧仍无标题才按按钮盲点（真机 19:35/38/44 单帧漏检即盲点）
            self._door_no_title = getattr(self, '_door_no_title', 0) + 1
            if self._door_no_title < 2:
                self.log("[信息] 门卡标题未命中，等一帧重试（暂不盲点按钮）")
                return
            # v1.8.11 诊断：标题漏检时打印本帧候选词（截断），便于发现
            # 词表外的新门标题（真机 9/4 曾因缺「首领/深层挑战」大量漏检）
            cand = [
                it[2].strip() for it in items
                if len(it) >= 4 and it[3] >= 0.6
            ][:12]
            self.log(
                f"[点击] 门卡标题未识别，直接点命中按钮 | 屏幕={btn_screen} | "
                f"本帧OCR候选：{cand}"
            )
            self.bot._click_abs(*btn_screen)
            self._count_door_entered()   # v1.8.60：首扇门确立计数，后续 +1
            return

        # 5) 按钮全未命中 → 回退旧行为（遗物/道具/精英/BOSS 按钮图案未采样）
        if target_pos is not None:
            self.log(
                f"[警告] 进入按钮模板未命中（图案未采样？），回退点标题"
                f"「{target_kw}」"
            )
            self.bot.click_at(
                target_pos[0], target_pos[1], f'门卡_{target_kw}'
            )
            self._count_door_entered()   # v1.8.60：首扇门确立计数，后续 +1
            return

        # 6) 双未命中（v1.8.21 用户定案：删除固定坐标中央兜底——分辨率
        # 变化后盲点不可控，宁可不动作交由主循环下一帧重试）
        self.log("[错误] 门卡标题与按钮均未识别，本轮不点击，等待下一帧重试")

    def _imprint_pick(
        self, items: List[Tuple],
        blacklist_ys: Optional[List[int]] = None
    ) -> Optional[Tuple]:
        """按优先级找烙印选卡目标（史诗>精英>稀有，同优先级取最上方）。

        v1.8.21：稀有度标签漏检时重试 OCR（最多 3 帧间隔 1s，漏检多为
        瞬时）——**已删除固定比例中央兜底**（用户定案：分辨率变化后盲点
        不可控，宁可不动作交由主循环下一帧重试）。

        v2.1.23（用户 17:11 真机·单核模式选了稀有而放着史诗不选）：
        **黑名单排除改为「同卡」方向性窗口**。旧实现用对称 `abs(Δy)≤150`，
        而真机布局（烙印四选一.png 实测）是「卡名 → 描述 → 稀有度标签」：
          卡1 名 y=293.5 / 标签 y=376.5   → 同卡 Δy = +83
          卡2 名 y=456.0                  → 距卡1标签 −79.5 ⇒ 仍在 150 内
        于是**下一张黑名单卡会把上一张卡的标签一起排除**——史诗卡被跳过，
        退而选中相邻的稀有卡。这与 `_card_quality` v2.1.11 修的是同一类
        错误（当时只改了 `_card_quality`，这里是漏改的尾巴）；现统一为
        「标签在卡名下方 30~130px 才算同卡」，与其互为逆运算。

        Returns:
            Tuple|None: (cy, cx, text, conf, x0, x1) 选卡目标；持续漏检 None
        """
        bl: List[int] = blacklist_ys or []

        def _blacklisted(lbl_y: int) -> bool:
            """该稀有度标签是否属于某张黑名单卡（同卡 = 位于卡名下方 30~130px）。"""
            return any(
                _BL_LABEL_DY_MIN <= lbl_y - by <= _BL_LABEL_DY_MAX
                for by in bl
            )

        for attempt in range(3):
            cur = items if attempt == 0 else self.bot._ocr_screen()
            rarity_items: List[Tuple] = [
                it for it in cur
                if len(it) >= 4 and it[3] >= 0.5
                and it[2].strip() in _RARITY_WORDS
            ]
            if rarity_items:
                # v2.1.23：候选诊断日志——下次"选错卡"可直接从日志定位根因
                # （是标签没识别出来，还是被黑名单窗口误排除）
                self.log(
                    f"[数据] 烙印候选 | 标签="
                    f"{[(it[2].strip(), it[0]) for it in rarity_items]}"
                    f" | 黑名单卡名y={bl}"
                )
                for kw in _RARITY_PRIORITY:
                    # v1.8.52：黑名单卡排除（v2.1.23 改为同卡方向性判定）
                    cands = [it for it in rarity_items
                             if it[2].strip() == kw
                             and not _blacklisted(it[0])]
                    if cands:
                        return min(cands, key=lambda it: it[0])
                # 优先级词全漏检（传说/神话扩展词等）→ 最上方标签兜底
                fallback = [it for it in rarity_items
                            if not _blacklisted(it[0])]
                if fallback:
                    return min(fallback, key=lambda it: it[0])
                return None   # 全为黑名单卡 → 由 _handle_imprint 兜底
            if attempt < 2:
                self.log(
                    f"[警告] 烙印稀有度标签漏检，重试 OCR（{attempt + 1}/2）"
                )
                self._sleep(1.0)
        return None

    def _imprint_card_click_pos(
        self, items: List[Tuple], pick: Tuple
    ) -> Tuple[int, int]:
        """选卡**备用**点击坐标（v2.1.13，仅重试时使用）。

        主坐标 = 稀有度标签位置（历史做法，真机数十次实测可用，**故不改**）；
        仅在"点击被吞、选中态校验失败"时改用本备用坐标：
          ① 优先取**同卡名称**位置（名称必落在卡面可点区）；
          ② 退化取固定横向比例（0.45W，与标签同 y）。
        """
        cx, cy = int(pick[1]), int(pick[0])
        best: Optional[Tuple[str, int, int]] = None
        for it in items:
            if len(it) < 4 or it[3] < 0.6:
                continue
            t, y, x = it[2].strip(), it[0], it[1]
            if (t in _IMPRINT_QUAL_TAGS or t in _IMPRINT_BTN_KEYWORDS
                    or not (2 <= len(t) <= 10)):
                continue
            if x > cx and 40 <= cy - y <= 130:
                if best is None or (cy - y) < (cy - best[1]):
                    best = (t, y, x)
        if best is not None:
            return (best[2], best[1])
        return (int(0.45 * 1280), cy)

    def _imprint_select_and_confirm(
        self, items: List[Tuple], pick: Tuple
    ) -> Tuple[bool, List[Tuple]]:
        """选卡 + 确认（**含选中态校验与重试**，v2.1.13）。

        真机现象（03:55:34、03:57:28 等）：转场刚结束就点选卡 → 点击被吞
        （卡未选中、底部按钮仍为「选择烙印」）→ 旧逻辑把该按钮当"确认钮"
        点击并**记录烙印**（假成功）→ 下一轮界面稳定后点击生效 → **同一张
        卡被记两次甚至多次**（真机见 沸腾怒火×4 / 强壮之力×3 / 炽热守护×5）；
        极端情况连续被吞 → 空转 18 轮 → 任务失败。

        现流程：点选卡 → OCR 校验按钮；命中「确认」= 已选中 → 点它并返回
        True；命中「选择烙印」= 未选中 → **换备用坐标重试一次**；两次都不
        成功 → 返回 False（**调用方不得记录烙印**，交主循环下一帧重试）。

        Returns:
            (是否已成功点击确认, 最新 OCR items)
        """
        _pos = (int(pick[1]), int(pick[0]))
        for attempt in (1, 2):
            if attempt == 2:
                # v2.1.13（独立验证结论）：重试用**同一坐标**——真机日志显示
                # 有效补救正是「同一坐标再点」（03:55:40 同坐标 → 03:55:43 选中）；
                # 备用坐标反而有"取到描述文本"的风险，故仅保留方法备将来使用。
                self.log("[警告] 选中态校验失败（按钮仍为「选择烙印」）"
                         "→ 同坐标重试点击选卡")
            self.bot.click_at(_pos[0], _pos[1],
                              '烙印_选卡' if attempt == 1 else '烙印_选卡_重试')
            self._sleep(1.0)
            items = self.bot._ocr_screen()
            _hit = None
            for kw in _IMPRINT_BTN_KEYWORDS:
                p = self.bot.find_text(items, kw, min_conf=0.6)
                if p:
                    _hit = (kw, p)
                    break
            if _hit is None:
                # v2.1.13（独立验证发现）：「读不到按钮」不能当成"已确认"——
                # 旧写法在此直接 return True，调用方随即记录烙印 → **假成功入库**
                # （与本次修复同类）。改为再 OCR 一次，仍读不到 → 按**未确认**
                # 返回（不记录，交主循环下一帧；漏记可由下一帧补，优于污染核数）。
                self._sleep(0.8)
                items = self.bot._ocr_screen()
                for kw in _IMPRINT_BTN_KEYWORDS:
                    p = self.bot.find_text(items, kw, min_conf=0.6)
                    if p:
                        _hit = (kw, p)
                        break
                if _hit is None:
                    self.log("[警告] 两次 OCR 均未读到烙印按钮"
                             "→ 按未确认处理（本次不记录）")
                    return (False, items)
            if _hit[0] == '确认':
                self.bot.click_at(_hit[1][0], _hit[1][1], '烙印_确认')
                self._sleep(1.5)
                return (True, items)
        self.log("[警告] 两次尝试均未选中烙印卡 → 本次**不记录**，交主循环重试")
        return (False, items)

    @staticmethod
    def _card_quality(items: List[Tuple], pos: Tuple[int, int]) -> str:
        """取卡片稀有度（v1.8.93 引入，**v2.1.11 修误读**）。

        v2.1.11（用户 2026-09-11 00:04 真机）：**必须取「卡名正下方最近的」
        标签**——旧实现返回「第一个 |Δy|≤120 命中的标签」，而选卡列表行距
        约 180px、同卡标签在卡名下方约 82px，于是**上方相邻卡**的标签
        （Δy≈-100，仍在 120 容差内）会被优先返回。真机实例：
          · 「坚定意志」实际史诗 → 记为精英
          · 「制裁之盾」实际史诗 → 记为精英
        品质误读直接导致「候选不如旧烙印（品质保护）」误判 → 反复保留烙印
        （真机 23:44~23:59 空转约 15 分钟）。

        现改为三级：① 优先取卡名**下方 30~130px** 的标签（同卡布局，取最近）；
        ② 下方没有 → 退化取任意方向最近（≤120px）；③ 都没有 → '未知'。
        """
        _px, py = pos
        below: Optional[Tuple[int, str]] = None
        near: Optional[Tuple[int, str]] = None
        for it in items:
            if len(it) < 4 or it[3] < 0.6 or it[2] not in _IMPRINT_QUAL_TAGS:
                continue
            delta: int = it[0] - py          # 正 = 位于卡名下方
            if 30 <= delta <= 130:
                if below is None or delta < below[0]:
                    below = (delta, it[2])
            if abs(delta) <= 120:
                _ad: int = abs(delta)
                if near is None or _ad < near[0]:
                    near = (_ad, it[2])
        if below is not None:
            return below[1]
        return near[1] if near is not None else '未知'

    @staticmethod
    def _orb_crystal_have(items: List[Tuple]) -> Optional[int]:
        """替换主界面右上角拥有的精粹数量（v1.9.35 用户需求）。

        蓝宝石图标旁纯数字（rx>0.85, ry<0.10）。"""
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            rx, ry = it[1] / 1280.0, it[0] / 1024.0
            if rx > 0.85 and ry < 0.10:
                t = it[2].strip().replace('O', '0')
                if t.isdigit():
                    return int(t)
        return None

    @staticmethod
    def _orb_replace_cost(items: List[Tuple],) -> Optional[int]:
        """确认替换按钮下方的本次替换消耗精粹（绿色徽章数字）。"""
        cr = None
        for it in items:
            if len(it) >= 4 and it[3] >= 0.6 and it[2].strip() == '确认替换':
                cr = it
                break
        if cr is None:
            return None
        cx, cy = cr[1], cr[0]
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            t = it[2].strip().replace('O', '0')
            if (t.isdigit() and abs(it[1] - cx) < 120
                    and 20 < it[0] - cy < 100):
                return int(t)
        return None

    def _read_card_name(self, items: List[Tuple],
                        tag_pos: Tuple[int, int]) -> Optional[str]:
        """读普通烙印卡的卡名（v1.9.34，用户要求：读不出就再读）。

        卡名与稀有度标签同卡（名称在标签上方，y 差 <=120px）：
        取该带内最靠近标签的非标签/按钮文本。OCR 漏检 → 重新
        OCR 一遍再找（选卡帧可能模糊），仍找不到 → None（占位）。
        """
        reserved = set(_IMPRINT_QUAL_TAGS) | set(_IMPRINT_BTN_KEYWORDS)
        for attempt in range(2):
            best: Optional[Tuple[int, str]] = None
            for it in items:
                if len(it) < 4 or it[3] < 0.6:
                    continue
                t = it[2].strip()
                if (t in reserved or len(t) < 2 or t.isdigit()
                        or '烙印' in t or '替换' in t):
                    continue
                # v2.2.9：**必须是烙印名**——排除门卡标题/界面词/含「之门」者
                # （真机 22:44 把门卡标题「遗物之门」当成了卡名）
                if t in _NON_IMPRINT_NAME_WORDS or '之门' in t:
                    continue
                # v2.0.8（真机 19:14 卡名错乱）：**名字特征约束**——
                # 效果描述（长句/带标点/残缺片段）此前被当作卡名
                # （实例：「战斗开始时，所有敌人受到队伍攻击力45%的
                # 真实伤害并眩晕3秒。」「窜。」「方英雄分别每3秒至多
                # 触发一次。」）→ 后续按错名找替换目标必然失败
                if not (2 <= len(t) <= 8):
                    continue
                if any(ch in t for ch in '。，、；：？！‘’“”（）()%·'):
                    continue
                if any(ch.isdigit() for ch in t):
                    continue
                dy = tag_pos[1] - it[0]   # 名字在标签上方
                if 0 <= dy <= 120:
                    if best is None or it[0] > best[0]:
                        best = (it[0], t)   # 最靠近标签的一条
            if best is not None:
                if attempt > 0:
                    self.log(f"[数据] 重读获得卡名「{best[1]}」")
                return best[1]
            if attempt == 0:
                items = self.bot._ocr_screen()   # 再读一遍
                self._sleep(0.5)
        return None

    def _handle_imprint(self, items: List[Tuple]) -> None:
        """烙印选择：按优先级（史诗>精英>稀有）选卡 + 重新 OCR 点「确认」。

        v1.8.20 流程（用户定案优先级，替代 v1.8.10「最上方标签」策略）：
          1. 判定：_detect_play_state 已用 稀有度+「选择烙印」/「确认」判 imprint
          2. 按 _RARITY_PRIORITY（史诗 > 精英 > 稀有）选最高优先级稀有度
             标签所在卡（OCR 实时坐标），同优先级多张取最上方——点标签即
             选中该卡，天然兼容三选一/四选一，分辨率变化也不失效
          3. 重新 OCR → 点「确认」（未选中时按钮=「选择烙印」，选中后
             变「确认」，_IMPRINT_BTN_KEYWORDS 顺序保证选中后才点确认）
        v1.8.21：稀有度持续漏检时**不动作**（固定比例兜底已删），交由
        主循环下一帧重试；终由 max_time/停止信号兜底。
        """
        # v2.1.13：转场刚结束就点击会被吞（真机 03:55:34 识别与点击同一秒、
        # 点击失效 → 卡未选中 → 假成功记录）→ 距上次处理 >8s 视为刚进入
        # 本界面，先等动画完成再操作
        _now = time.time()
        if _now - getattr(self, '_last_imprint_at', 0.0) > 8.0:
            self.log("[信息] 烙印界面就绪等待（距上次处理 >8s，视为重新进入）")
            self._sleep(_IMPRINT_READY_WAIT)
        self._last_imprint_at = time.time()
        # v1.8.52：具名优先级（执裁之盾>溢出防御>藏品柜>强壮之力>
        # 不屈意志）——OCR 命中即点该卡，天然避开黑名单
        for kw in _IMPRINT_NAMED_PRIORITY:
            pos = self.bot.find_text(items, kw, min_conf=0.6)
            if pos:
                # v1.8.93：点击前先读同卡稀有度标签（确认后帧坐标已失效）
                qual = self._card_quality(items, pos)
                # v2.1.11（用户 00:04）：**记录实际 OCR 文本**而非关键词 kw——
                # 避免把"表里的关键词"写进库存而与游戏实际库存不符。
                # v2.2.6（用户 09-15）：注意「制裁之盾」**不是**「执裁之盾」的
                # 误认，而是**另一个烙印**（二者稀有度不同：精英/史诗）——
                # 真正的纠正放在 `_record_imprint_gain` 按稀有度交叉校验。
                _actual: str = next(
                    (it[2] for it in items
                     if len(it) >= 4
                     and abs(it[1] - pos[0]) <= 3
                     and abs(it[0] - pos[1]) <= 3), kw)
                if _actual != kw:
                    self.log(f"[警告] 具名命中「{kw}」但 OCR 实读「{_actual}」"
                             f"（疑似字形误认，按实读记录）")
                self.log(
                    f"[点击] 具名优先级烙印「{_actual}」 | "
                    f"图像=({pos[0]},{pos[1]})"
                )
                # v2.1.13：与普通路径同源——选卡+确认含选中态校验；未确认不记录
                # （pick 形态 (cy, cx, 名称)，而 pos=(cx, cy)）
                _ok, items = self._imprint_select_and_confirm(
                    items, (pos[1], pos[0], _actual))
                if not _ok:
                    return
                # v1.8.93：记录获得的烙印（模式2/3 核数判定依据）
                self._record_imprint_gain(_actual, qual)
                return
        # 黑名单（泰坦体魄/献祭之盾）位置收集 → 稀有度选择时排除
        # v1.8.56 轴修正：it[0]=y——黑名单名称与稀有度标签按 y 同卡判定
        # （_imprint_pick 内 abs(it[0]-by) 比较的是 y）
        black_ys: List[int] = [
            it[0] for it in items
            if len(it) >= 4 and it[3] >= 0.6
            and it[2] in _IMPRINT_BLACKLIST
        ]
        pick: Optional[Tuple] = self._imprint_pick(
            items, blacklist_ys=black_ys
        )
        if pick is None:
            if not black_ys:
                # v1.8.21 定案保留：稀有度标签漏检（OCR 抖动）→ 不点击，
                # 交由主循环下一帧重试（无固定坐标兜底）
                self.log(
                    "[错误] 烙印稀有度标签持续漏检，本轮不点击，"
                    "交由主循环重试"
                )
                return
            # v1.8.52：有黑名单卡在（候选被排除至空）→ 点正中央兜底，
            # 最低代价继续，避免卡死
            self.log(
                "[警告] 候选全为黑名单，点正中央兜底（避免卡死）"
            )
            self.bot.click_at(640, 512, '烙印_正中央兜底')
            self._sleep(1.0)
            items = self.bot._ocr_screen()
            for kw2 in _IMPRINT_BTN_KEYWORDS:
                p2 = self.bot.find_text(items, kw2, min_conf=0.6)
                if p2:
                    self.bot.click_at(p2[0], p2[1], f'烙印_{kw2}')
                    self._sleep(1.5)
                    # v1.8.93：候选全黑名单兑底选中卡不可识别 → 占位记录
                    self._record_imprint_gain('未记录烙印', '未知')
                    return
            return
        self.log(
            f"[点击] 烙印选卡「{pick[2]}」 | "
            f"图像=({int(pick[1])},{int(pick[0])})"
        )
        # ⚠️ v2.2.10（用户 2026-09-15 22:45 时序报障，**必须在点击前读卡名**）：
        # 真机机制：战斗后获得遗物、某类遗物累计到 2/4/6/8 时会**弹出烙印选择**；
        # 旧实现是「先点选卡+点确认 → 弹窗关闭回到选门界面 → 才重读卡名」
        # ✗ → 那时烙印选择已结束，卡名信息已消失，重读只能抓到**背后选门界面
        # 残留的门卡标题**（真机 22:44 日志「卡名识别「遗物之门」」正是这么来的 ✗）。
        # 故次序改为**先读名、再选择**（与具名路径、与 `_card_quality`
        # 「点击前先读同卡稀有度标签（确认后帧坐标已失效）」同思路）。
        # 读名自带「读不出再重读一遍」，此时弹层仍在 → 重读有效 ✓
        name = self._read_card_name(
            items, (int(pick[1]), int(pick[0])))
        # v2.1.13：选卡+确认（含选中态校验与重试）；未确认成功**不记录**
        _ok, items = self._imprint_select_and_confirm(items, pick)
        if not _ok:
            return
        if name:
            self.log(f"[数据] 卡名识别「{name}」（{pick[2]}）")
            self._record_imprint_gain(name, pick[2])
        else:
            # 兜底：仍读不到 → 按稀有度占位（核数判定只查具名）
            self._record_imprint_gain(
                f'未记录·{pick[2]}', pick[2])
        return

    # ── v1.8.52：精粹/层数读取 · 门卡遗物决策 · 商店 · 烙印晶球 ──

    @staticmethod
    def _find_all_text(
        items: List[Tuple], kw: str, min_conf: float
    ) -> List[Tuple[int, int]]:
        """枚举帧中所有含 kw 的文本位置（find_text 只返回首个，
        无法覆盖同层两张同名门的场景）。

        返回 (cx, cy)——与 find_text 返回约定一致（v1.8.55 修复：
        原返回 (cy, cx) 被上层当 (cx, cy) 用，点击 x 实为标题 y
        → 真机反复点空；评分 desc 关联轴反 → 全部 rank 3）。
        """
        return [
            (it[1], it[0]) for it in items
            if len(it) >= 4 and it[3] >= min_conf and kw in it[2]
        ]

    def _read_floor(self, items: List[Tuple]) -> Optional[int]:
        """从顶部 HUD 读当前层数（'13/15' → 13；深层 '17/20' → 17）。
        识别失败返回 None。v1.8.60：正则补 /20——旧 1[58] 只认 /15，
        深层 16/20~20/20 全部漏读，UI 校正在深层失效。"""
        for it in items:
            # v1.8.73：每帧校准后本函数会在任意帧被调——防御非 str 文本
            # （测试 Mock 乱码帧 it[2] 可能是 list）
            if (len(it) < 4 or it[3] < 0.6
                    or not isinstance(it[2], str)):
                continue
            m = re.match(r'^(\d{1,2})/(?:1[58]|20)$', it[2].strip())
            if m:
                return int(m.group(1))
        return None

    def _read_essence(self, items: List[Tuple]) -> Optional[int]:
        """从右上角读晶粹数量（纯数字，x>0.78W、y<0.08H）。失败返回 None。"""
        best: Optional[Tuple[float, int]] = None
        for it in items:
            if len(it) < 4 or it[3] < 0.6:
                continue
            t = it[2].strip()
            if not t.isdigit():
                continue
            xr = it[1] / 1280.0   # v1.8.53 修正：items=(y,x,文本,conf,…)
            yr = it[0] / 1024.0
            if xr > 0.78 and yr < 0.08:
                v = int(t)
                if best is None or xr > best[0]:
                    best = (xr, v)
        return best[1] if best else None

    def _door_score(self, kw: str, items: List[Tuple],
                    title_x: int, title_y: int,
                    title_xs: Optional[List[int]] = None) -> Tuple:
        """门卡评分：遗物优先级 + 双倍 tie-break（越小越优先）。

        描述文本与标题同卡（|dx|<220、y 在标题下方 0~220px）。
        v1.8.55：多门时描述行归属「x 最近的门标题」（title_xs）——
        真机 1080p 双门间距约 245px，右门「效果：急速提升5点」距左门
        标题仅 213px，旧 |dx|<220 窗口会把右门效果行串进左门评分。

        **不评估敌人强度**（用户 2026-09-15 定案：「无需顾虑敌人强度，失败就重来」）
        —— 卡片上的「强力敌人」等风险标签**刻意不读**，勿再引入战力/敌人强度判据
        （双倍遗物门必带「强力敌人」，若按强度避让会与「双倍最优先」互相打架）。
        v2.2.8 后 rank 取值：双倍三目标 -0.5 ＜ 单倍三目标 0/1/2 ＜ 馈赠 2.5
        ＜ 晶粹 4 ＜ 小店 5 ＜ 普通遗物 5.5 ＜ 其他 6。

        Args:
            title_xs: 全部门卡标题 x 列表；None/缺省 = 单门（不判归属）
        Returns:
            (rank, no_double) —— 越小越优先；no_double=0 表示双倍（同级优先）
        """
        xs = title_xs or [title_x]
        desc = ' '.join(
            it[2] for it in items
            if len(it) >= 4 and abs(it[1] - title_x) < 220
            and 0 < it[0] - title_y < 220
            and all(abs(it[1] - title_x) <= abs(it[1] - tx) for tx in xs)
        )
        double = '双倍' in desc
        relic_rank = 3
        _named: Optional[str] = None      # 描述里命中的三目标名（无 → None）
        for rank, name in enumerate(_DOOR_RELIC_PRIORITY):
            if name in desc:
                relic_rank = rank
                _named = name
                break
        else:
            if kw == '晶粹之门':
                relic_rank = 4
            elif kw == '菲茨小店':
                relic_rank = 5
        if kw != '遗物之门':
            # v1.8.72：馈赠之门（掉落遗物额外+1 同种）插在 急速与普通
            # 遗物之间（用户定案 rank 2.5）——好于普通遗物、低于三目标
            # v2.1.12（用户 00:29 定案）：**普通遗物门降到菲茨小店之下**——
            # 三目标（强壮/庇护/极速）命中仍是 0/1/2，但"普通遗物门"
            # （描述里没有三目标名）由 3 改为 **5.5**（小店 5 之后、
            # 其他 6 之前）：实测优先开店买目标更划算
            relic_rank = {'馈赠之门': 2.5, '晶粹之门': 4,
                          '菲茨小店': 5}.get(kw, 6)
        elif relic_rank == 3:
            relic_rank = 5.5   # 普通遗物门（无三目标名）→ 低于小店
        # v2.2.8（用户 09-15 定案「当出现双倍强壮/庇护/极速时，为最优先」）：
        # **双倍 + 三目标名** → 抬到遗物类最高档 -0.5（优于单倍强壮 0）。
        # 只对「遗物之门」生效；双倍+普通遗物门（描述无三目标名）不抬高。
        # 真机素材佐证（异界迷宫/双倍遗物.png）：标题「遗物之门」、描述含
        # 「击败敌人可获得双倍奖励」+「获得遗物：极速 (1/2)」→ 实给 2 个。
        # 与烙印晶球（-1）**不会相遇**：用户 09-15 澄清「游戏中烙印晶球必定
        # 和商店共同出现」→ 晶球只与菲茨小店（5）同现，两者互不争夺 ✓
        if double and kw == '遗物之门' and _named is not None:
            relic_rank = -0.5
        return (relic_rank, 1 if not double else 0)

    def _decide_door(
        self, titles: List[Tuple[str, Tuple[int, int]]],
        items: List[Tuple]
    ) -> Tuple[str, Tuple[int, int]]:
        """多门评分决策（v1.8.52 用户定案）→ (门标题, 位置)。

        遗物之门读描述 → 强壮 > 庇护/极速（双倍为同级 tie-break）；三种
        都没有 → 晶粹之门 > 菲茨小店 > 其他；13 层起烙印晶球最优先。
        """
        floor: Optional[int] = self._read_floor(items)
        # v1.8.59：关卡计数与顶栏 UI 对照同步——UI 是真相源，偏差
        # （续局直通从 0 起算 / 点击后未转场误计）时以顶栏校正并告警。
        # 同步后计数语义 = 顶栏 X/15 的 X（点门后顶栏 +1，天然一致）
        if floor is not None and floor != self._floor_count:
            # v1.8.83：校正静默（用户定案删除警告日志），照常校正
            self._floor_count = floor
        title_xs: List[int] = [pos[0] for _k, pos in titles]
        scored: List[Tuple[Tuple, str, Tuple[int, int]]] = []
        for kw, pos in titles:
            score = self._door_score(kw, items, pos[0], pos[1], title_xs)
            # v1.8.59：触发条件改基于关卡计数（旧 floor is None or floor>=13
            # 的 None 放行 = 第 9 关误选烙印晶球根因——顶栏 OCR 波动即裸放
            # 行）。计数已在 _pick_door 入口经顶栏 UI 校正，语义=当前顶栏 X
            if kw == '烙印晶球' and self._floor_count >= 13:
                score = (-1, 0)
            # v1.8.93（用户定案）：模式1 不进烙印晶球——多门时排到最后。
            # v2.2.7（用户 09-15）：「游戏中不存在单门晶球」→ 原文"单门只剩
            # 晶球时绕不开"的情形不存在，模式1 在评分路径上必然避开晶球。
            if kw == '烙印晶球' and self._run_mode() == 'clear15':
                score = (99, 1)
            scored.append((score, kw, pos))
        # 同分 tie-break 取最左（pos=(cx,cy)，v1.8.55 轴修正后取 pos[0]）
        scored.sort(key=lambda c: (c[0], c[2][0]))
        detail = ' | '.join(f'{kw}={sc}' for sc, kw, _p in scored)
        kw_best, pos_best = scored[0][1], scored[0][2]
        self.log(
            f"[决策] 选门「{kw_best}」 计数={self._floor_count} "
            f"UI={floor} | {detail}"
        )
        return kw_best, pos_best

    def _shop_flow(self) -> None:
        """商店购买流程（v1.8.52）：只买三种目标遗物（单价 50 精粹，
        最多两件），买完或精粹不足 50 → 点「继续前进」退出。

        每轮：OCR → 找目标遗物 → 点商品 → 找「购买」点 → 轮询扣款；
        识别失败/无目标 → 直接点「继续前进」（最低代价退出，不卡死）。

        v2.1.22（用户 17:00 定案）：**买遗物后弹出烙印选择是正常环节，
        不是离开商店**。旧实现「找不到『商品列表』即判离店」在这里会误判
        （真机 16:58:42 `已离开商店界面，结束购买流程`）→ 丢掉已购计数、
        提前结束流程；随后主循环又把商店重新拉进来（`bought` 归零，可能
        超买）。现在：**离开商店只认「继续前进」**——商品列表缺席时先看
        当前是不是烙印选择/转场，是则就地处理转发后再回商店，`bought`
        与「最多两件」判定全程保留。
        """
        bought = 0
        _off_streak = 0     # 连续「非商店」帧计数（容忍转场，防死循环）
        items: List[Tuple] = []
        for _ in range(_SHOP_MAX_ROUNDS):
            if self.check_stop():
                return
            # v2.1.12：先判「已购满 2 件」（用户定案：单价 50 精粹，最多两件）
            if bought >= 2:
                self.log("[信息] 商店已购满 2 件，退出商店")
                break
            items = self.bot._ocr_screen()
            # v2.1.12（独立验证发现补强）：每轮复查商店判据词「商品列表」
            # ——删掉旧的「见『继续前进』即退出」后，若商店在流程中关闭
            # （帧漂移，如买完自动关闭跳到选门界面），循环内会把其它界面的
            # 同名词当商品误点（实测：跳到选门界面后误点门卡「强壮」3 次、
            # 无「购买」按钮时也空转 4 轮）。「商品列表」与界面识别
            # （screen_registry 的 labyrinth.shop）同源，真商店帧必含它。
            # v2.1.22：**缺它不再直接判离店** —— 先区分烙印中转/转场。
            if self.bot.find_text(items, '商品列表', min_conf=0.5) is None:
                _st: str = self._detect_play_state(items)
                if _st == 'imprint':
                    # v2.1.22（用户 17:00）：买遗物弹烙印 = 正常环节
                    self.log("[信息] 商店购买后弹出烙印选择（正常环节）"
                             "→ 处理烙印后继续购物")
                    _off_streak = 0
                    self._handle_imprint(items)
                    self._sleep(_STATE_POLL)
                    continue
                if _st == 'unknown' and _off_streak < _SHOP_OFF_MAX:
                    _off_streak += 1
                    self.log(f"[信息] 商店画面暂缺（第 {_off_streak}/"
                             f"{_SHOP_OFF_MAX} 帧，疑转场）→ 等待重判")
                    self._sleep(_STATE_POLL)
                    continue
                self.log(f"[信息] 已离开商店界面（当前："
                         f"{_PLAY_STATE_LABELS.get(_st, _st)}），结束购买流程")
                break
            _off_streak = 0
            essence = self._read_essence(items)
            target = None
            # v1.8.56：按本名查找（v1.8.72 更正：极速即本名，真机商店截图
            # 校准），别名命中商品名称行优先于描述行（items 按 y 排序）
            for kw in _SHOP_RELICS:
                pos = self.bot.find_text(items, kw, min_conf=0.6)
                if pos:
                    target = (kw, pos)
                    break
            if target is None or (
                essence is not None and essence < _SHOP_PRICE
            ):
                self.log(
                    f"[信息] 商店购买完成（已买 {bought} 件，"
                    f"精粹={essence if essence is not None else '未知'}）"
                )
                break
            kw, pos = target
            self.log(f"[点击] 商店遗物「{kw}」 | 图像=({pos[0]},{pos[1]})")
            self.bot.click_at(pos[0], pos[1], f'商店_{kw}')
            self._sleep(1.2)
            items2 = self.bot._ocr_screen()
            buy = self.bot.find_text(items2, '购买', min_conf=0.6)
            if buy:
                self.bot.click_at(buy[0], buy[1], '商店_购买')
                bought += 1
                self._sleep(1.5)
            else:
                self.log("[警告] 未找到「购买」按钮，跳过该商品")
                self._sleep(0.8)
        # 退出商店（v1.8.53：用已取帧直接找按钮——再调 _click_text 会
        # 多消耗一帧 OCR，帧序错位导致点不到「继续前进」）
        # v2.1.22：**只有这一步算离开商店**（用户定案）
        pos = self.bot.find_text(items, '继续前进', min_conf=0.6)
        if pos:
            self.bot.click_at(pos[0], pos[1], '继续前进')
            self.log(f"[信息] 商店流程结束（本次购入 {bought} 件），"
                     "点「继续前进」")
        else:
            self.bot.press_esc(label='商店_ESC兜底')

    def _collect_imprint_entries(
        self, items: List[Tuple]
    ) -> List[Tuple[str, str, Tuple[int, int]]]:
        """收集烙印条目 (名称, 品质, 点击坐标)。

        布局实测（1280x1024）：品质标签（稀有/史诗/精英）在名称下方
        ~82px、x≈0.14W；名称 x≈0.29W。通用判据：品质标签下方 40~130px
        处、x 大于标签 x 的最短文本 = 名称（描述为长文本被长度过滤）。
        """
        out: List[Tuple[str, str, Tuple[int, int]]] = []
        reserved = {'确认替换', '继续前进', '确认选择', '保留烙印',
                    '选择已有烙印进行替换', '晶粹不足。'}
        # v1.8.53 修正坐标轴：items=(y, x, 文本, conf, …)
        labels = [(it[1], it[0], it[2]) for it in items
                  if len(it) >= 4 and it[3] >= 0.7
                  and it[2] in _IMPRINT_QUAL_TAGS]
        for lx, ly, qual in labels:
            best: Optional[Tuple[str, int]] = None
            _cand_n = 0
            for it in items:
                if len(it) < 4 or it[3] < 0.6:
                    continue
                t, y, x = it[2], it[0], it[1]
                if not (2 <= len(t) <= 10) or t in _IMPRINT_QUAL_TAGS:
                    continue
                # v1.8.82：黑名单不再在收集层排除——v1.8.67 起语义是
                # 「黑名单最优先替换」（step1 排序 grp0），此处滤掉会让
                # grp0 永远空转（Z2 测试暴露）；第二步候选排除仍由
                # _imprint_best 负责
                if t in reserved:
                    continue
                if x <= lx or not (40 <= ly - y <= 130):
                    continue
                _cand_n += 1
                # v2.1.11（用户 00:04「严查品质」）：**取最靠近标签的名称**
                # （Δ=ly-y 最小），旧实现取 y 最小（最靠上）——窗口内出现
                # 多个候选时会配到更上方那张卡的名称，导致**名称与品质错配**
                if best is None or (ly - y) < (ly - best[1]):
                    best = (t, y)
            if best:
                if _cand_n > 1:
                    # 同一标签窗口内出现多个名称 → 布局异常，留痕便于核证
                    self.log(f"[调试] 标签「{qual}」上方窗口内有 {_cand_n} "
                             f"个名称候选，取最靠近者「{best[0]}」")
                out.append((best[0], qual, (lx + 190, best[1])))
        # 去重（同名称多次出现取第一次）
        seen, uniq = set(), []
        for name, qual, pos in out:
            if name not in seen:
                seen.add(name)
                uniq.append((name, qual, pos))
        return uniq

    def _imprint_best(
        self, cands: List[Tuple[str, str]], min_qual: str = None,
        always_accept: Tuple[str, ...] = (),
        strict_upgrade: bool = False,
    ) -> Optional[Tuple[str, str]]:
        """按 具名优先级 > 黑名单排除 > 稀有度 选最优候选。

        v1.8.71（用户定案）：min_qual=被替换旧烙印品质——候选全部
        低于该品质时返回 None（换是亏的，调用方点「保留烙印」跳过）；
        品质等于或高于旧烙印的候选正常参与（具名/稀有度规则不变）。
        v1.8.82（用户定案特例）：always_accept 中的名字豁免品质下限
        （已有史诗>=2 时，执裁之盾精英候选也换——集齐优先于品质）。
        v1.9.2（用户定案）：strict_upgrade=True 时 min_qual 改为
        **严格高于**旧品质才收（平级不收）——具名仍由 always_accept
        豁免。用于模式2/3「只找核/具名或品质更高项，否则保留」。
        """
        pool = [c for c in cands if c[0] not in _IMPRINT_BLACKLIST]
        if min_qual:
            floor = _RARITY_RANK.get(min_qual, 0)
            if strict_upgrade:
                pool = [c for c in pool
                        if c[0] in always_accept
                        or _RARITY_RANK.get(c[1], 0) > floor]
            else:
                pool = [c for c in pool
                        if c[0] in always_accept
                        or _RARITY_RANK.get(c[1], 0) >= floor]
        if not pool:
            return None
        for kw in _IMPRINT_NAMED_PRIORITY:
            for c in pool:
                if c[0] == kw:
                    return c
        for qual in _RARITY_PRIORITY:
            for c in pool:
                if c[1] == qual:
                    return c
        return pool[0] if pool else None

    def _click_and_confirm(
        self, pos: Tuple[int, int], name: str, confirm_kws: Tuple[str, ...]
    ) -> bool:
        """点候选 → 点确认按钮。返回 True = 确认已点。"""
        self.log(f"[点击] 烙印「{name}」 | 图像={pos}")
        self.bot.click_at(pos[0], pos[1], f'烙印_{name}')
        self._sleep(1.0)
        items = self.bot._ocr_screen()
        for kw in confirm_kws:
            p2 = self.bot.find_text(items, kw, min_conf=0.6)
            if p2:
                self.bot.click_at(p2[0], p2[1], f'烙印_{kw}')
                self._sleep(1.5)
                return True
        self.log("[警告] 确认按钮未找到")
        return False

    def _reset_orb_mirror(self) -> bool:
        """清空**本局烙印库存镜像**（v2.1.21 收敛为方法，供多处调用）。

        为什么要清：`_orb_inventory` 是**本局探索**的镜像（方案 1「获得即
        入库」，见 `_record_imprint_gain`）。探索一结束，游戏侧烙印列表即
        作废 —— 镜像若残留，后续「替换目标」会拿过期清单判定：
          · v1.9.34 真机教训：第二轮必败 / 占位名追加在过时清单上
          · v2.1.7 真机教训：跨轮残留（第 2 轮第 1 枚被记成「库存共 11 枚」）

        调用点：
          ① `run()` 每轮入口（覆盖 final_settle 等不经「结束探索」的收尾）
          ② `_end_exploration()` 成功点掉「结束探索」时（**与探索同生命周期**，
             覆盖 boss_cleared / failure / _abort_round 全部收尾路径）
        删除属性即可 —— 各读取点都是 `hasattr` 惰性重建。

        Returns:
            bool: 本次是否真的清掉了内容（False = 本来就没有，调用方据此
                  避免重复日志）
        """
        _cleared: bool = False
        for _k in ('_orb_scanned', '_orb_inventory', '_orb_downs',
                   '_orb_epic_count', '_settle_logged'):
            if hasattr(self, _k):
                delattr(self, _k)
                _cleared = True
        return _cleared

    def _scan_orb_inventory(self) -> set:
        """【v2.1.8 起**不再被调用**】滚动遍历球内列表建立库存镜像。
        保留实现供回归/参考：用户 22:21 定案改用方案 1（获得即入库，
        见 _record_imprint_gain），全量滚动遍历单次约 10s，替换流程
        每次都要付这个成本，判定不划算。
        """
        """下滚遍历替换列表一次，建立内存库存镜像（v1.8.84 用户定案）。

        v1.8.81 首版每次到第一步都全列表扫描（上滚到顶+下滚+回滚），
        真机 18:09 单次耗 ~15s 且每轮重复 → 72s 才选出第一个目标。
        v1.8.84 优化（用户三条规则）：
          ① 仅首次进入本流程时完整遍历，之后内存库存维护
             （第二步替换成功=删旧加新+打印；保留烙印=不变）；
          ② 界面进入时默认在顶部 → 不再上滚，只下滚到底；
          ③ 本次 OCR 名字集合与上次相同 → 已到底，立即停止。
        v1.8.86（用户 2026-09-08 真机定案）：扫描完成后**停在底部、
        不再回滚到顶**（真机实测多余上滚）。

        侧写：self._orb_inventory = [(name, qual), ...] 全量库存；
        self._orb_downs = 下滚屏数（替换阶段回滚到顶用）；
        self._orb_scanned = True（中断时不置位，下次重新完整扫描）。

        Returns:
            set: 扫描到的全部烙印名（中途停止则返回已收集部分）
        """
        names: set = set()
        inv: list = []
        prev: set = None
        downs = 0
        # v1.8.88：无屏数上限——终止只由「本次 OCR 名字集合与上次相同
        # = 已到底」决定（v1.8.84 规则③），另有 check_stop() 可中断
        while True:
            if self.check_stop():
                return names   # 中断：不建库存，下次重扫
            items = self.bot._ocr_screen()
            entries = self._collect_imprint_entries(items)
            cur = {n for n, _q, _p in entries}
            for n, q, _p in entries:
                if n not in names:
                    inv.append((n, q))
            if cur == prev:
                break   # v1.8.84：与上次相同=已到底，立即停（不再滚动）
            names |= cur
            prev = cur
            self.bot.scroll_wheel(
                -30, rx=0.5, ry=0.5, label='烙印列表_下滚扫描')
            downs += 1
            self._sleep(_ORB_SCAN_WAIT)
        # v1.8.86：不再回滚到顶（用户定案），停在底部，由替换阶段按需回滚
        self._orb_inventory = inv
        self._orb_downs = downs
        self._orb_scanned = True
        self.log("[信息] 已有烙印(" + str(len(inv)) + ")："
                 + "、".join(f"{n}({q})" for n, q in inv))
        return names

    def _imprint_orb_flow(self) -> None:
        """烙印晶球替换（v1.8.67 状态机重写；v1.8.52 初版）。

        真机界面（用户 2026-09-08 三截图定案）：
          第一步：标题「选择已有烙印进行替换」，底部 继续前进+确认替换
                  ——列表=已有烙印（全可选，可滚动），选要被替换掉的
                  旧烙印（黑名单最优先=最差的先换）→ 确认替换（耗晶粹）
          第二步：底部 保留烙印+确认选择（确认选择未选中时灰色禁用）
                  ——固定四行不可滚动，首行=被替换烙印展示（不可选，
                  y<0.38 过滤），三候选按最优选一个 → 确认选择
        每轮 OCR 先按按钮词校准 step（界面是真相源，状态推断其次——
        词组互斥：第一步=继续前进/确认替换；第二步=保留烙印/确认选择）。
        集齐执裁之盾+溢出防御 / 晶粹不足 / 轮次耗尽 → 退出。
        """
        tried: set = set()      # 已替换掉的旧烙印（第一步处理过）
        _orb_prev_vis = None    # v1.8.85：上一屏可见条目集合（滚动到底判定）
        _unreachable: set = set()   # v1.8.86：滚两轮都找不到的目标（跳过）
        _seen: set = set()          # v1.8.88：累计见过的条目名（到底判定用）
        old_qual: str = None    # v1.8.71：被替换旧烙印品质（None=未知不过滤）
        name: str = ''          # 当前选中旧烙印（step2 成功后更新库存用）
        # v1.8.84：内存库存镜像惰性初始化。
        # v2.1.8（方案 1，用户 22:21）：**两个 key 分开判**——此前把
        # `_orb_inventory = []` 放在 `not hasattr(_orb_scanned)` 分支内，
        # 会把门外已由 _record_imprint_gain 记录的本局烙印**清空**
        # （方案 1 下这是致命的：进晶球门时库存恒为空 → 替换目标耗尽）。
        if not hasattr(self, '_orb_scanned'):
            self._orb_scanned = False
        if not hasattr(self, '_orb_inventory'):
            self._orb_inventory = []     # [(name, qual), ...]
        scrolls = 0
        step = 1                # 初始假设第一步（特征词校准会修正）
        stopped = False         # v1.8.68：用户停止时不走收尾动作
        # v1.8.69：烙印晶球界面按 ESC 会触发游戏 BUG（用户实测禁令）——
        # orb 全程 + 余量 90s 内禁 ESC（escape_alternating 覆写感知）
        self._no_esc_until = time.time() + 90.0
        # v1.8.78：整流程异常兜底——此前 f-string 笔误（{_qual}）
        # 直接把整个任务打成 failed，且卡在烙印晶球界面不退出。
        # 这里兜住所有异常，打日志后照常走收尾（点「继续前进」）。
        try:
            # v1.8.88（用户定案）：无轮次上限。终止只由业务条件决定：
            # 库存无可替换项 / 已集齐双盾 / 晶粹不足 / 用户停止 / 内部
            # 兜底（第二步未出现、两次点击未激活）。每轮开头均有
            # check_stop()，不会失控。
            while True:
                if self.check_stop():
                    self.log("[信息] 收到停止，退出烙印晶球流程")
                    stopped = True
                    break
                items = self.bot._ocr_screen()
                texts = [it[2] for it in items
                         if len(it) >= 4 and it[3] >= 0.5]
                if any('晶粹不足' in t for t in texts):
                    self.log("[警告] 晶粹不足，结束烙印晶球流程")
                    break
                # ── 特征词校准（两步按钮词互斥）──
                has_keep = '保留烙印' in texts
                has_go = '继续前进' in texts
                if has_keep and '确认选择' in texts:
                    step = 2
                elif has_go or '确认替换' in texts:
                    step = 1

                if step == 2:
                    # 第二步：首行=被替换烙印展示（不可选）→ y<0.38 过滤；
                    # 三候选按最优规则选（黑名单排除只在此步生效）
                    cands = [(e[0], e[1]) for e in
                             self._collect_imprint_entries(items)
                             if e[2][1] / 1024.0 >= 0.38]
                    # v1.8.82 特例：已有史诗>=2 且旧烙印为史诗 → 候选出现
                    # 执裁之盾时豁免品质保护（史诗换精英执裁之盾，集齐优先；
                    # 溢出防御本身是史诗品质，不会被品质保护拦住）
                    # v1.9.2（用户定案新策略）：模式2/3 候选接受标准——
                    #   凑核前 = 缺失的核（豁免品质）或品质更高项，否则保留
                    #   凑核后 = 任意具名（豁免品质）或品质更高项，否则保留
                    _always = ()
                    # v2.2.6（用户 09-15 澄清「模式1 不进烙印晶球，没有替换
                    # 烙印这一步」）：正常路径下晶球只在深层模式出现
                    # （:367 clear15 多门时排除晶球）→ 本条件总被下面的
                    # `_deep_mode()` 分支覆盖，实际由 `_always=_missing` 决定。
                    # 故生产路径上**只有单测直调**会用到本条件（干跑默认走
                    # clear15）。v2.2.7（用户 09-15）：「**游戏中不存在单门
                    # 晶球**」→ 连"模式1 单门晶球兜底"这一例外也不存在。
                    if (old_qual == '史诗'
                            and getattr(self, '_orb_epic_count', 0) >= 2):
                        _always = ('执裁之盾',)
                    if self._deep_mode():
                        _inv_now = {n for n, _q in
                                    getattr(self, '_orb_inventory', [])}
                        _missing = tuple(n for n in self._core_names()
                                         if n not in _inv_now)
                        _always = _missing if _missing \
                            else _IMPRINT_NAMED_PRIORITY
                    best = self._imprint_best(
                        cands, min_qual=old_qual,   # v1.8.71：品质保护
                        always_accept=_always,      # v1.8.82：特例豁免
                        strict_upgrade=self._deep_mode(),  # v1.9.2：深层平级不收
                    )
                    if best is None:
                        # v1.8.85：文案修正——保留=用同一旧烙印再抽一次随机
                        # 候选（用户 19:53 定案：这是正常流程，不是异常）
                        self.log("[信息] 候选不如旧烙印（品质保护），"
                                 "点「保留烙印」再抽一次")
                        self._click_text('保留烙印', min_conf=0.6)
                        step = 1
                        self._sleep(1.2)
                        continue
                    if self._click_and_confirm(
                        self._find_entry_pos(items, best[0]), best[0],
                        ('确认选择',)
                    ):
                        if (_always and best[0] in _always
                                and _RARITY_RANK.get(best[1], 0)
                                < _RARITY_RANK.get(old_qual or '', 0)):
                            self.log(
                                "[信息] 特例：史诗≥2，换出「"
                                f"{best[0]}」（{best[1]}），集齐优先于品质")
                        self.log(f"[成功] 已替换为「{best[0]}」（{best[1]}）")
                        # v2.1.10：门控改「库存已建立」——方案1 下替换成功必须回写，否则门前核数永远不达标
                        # v1.8.84：内存库存删旧加新 + 打印（保留烙印则不动）
                        if isinstance(getattr(self, '_orb_inventory', None), list):
                            _inv = [(n, q) for n, q in self._orb_inventory
                                    if n != name]
                            if best[0] != name:
                                _inv.append((best[0], best[1]))
                            self._orb_inventory = _inv
                            self.log(
                                "[信息] 本局已获烙印(" + str(len(_inv)) + ")："
                                + "、".join(
                                    f"{n}({q})" for n, q in _inv))
                        # v1.8.69 用户确认：换上的新烙印是正常烙印，允许在
                        # 后续替换中被再次选为替换对象（不做保护排除）
                    # v1.8.68：确认后弹「成功替换」窗（点空白处关闭）→ 点掉
                    # 再回第一步；未弹则等回第一步特征词即止
                    for _ in range(6):
                        if self.check_stop():
                            stopped = True
                            break
                        it3 = self.bot._ocr_screen()
                        t3 = [x[2] for x in it3
                              if len(x) >= 4 and x[3] >= 0.5]
                        if any('成功替换' in x or '点击空白处' in x
                               for x in t3):
                            self.dismiss_blink_popup(items=it3)
                            self._sleep(1.0)
                            continue
                        if '确认替换' in t3 or '继续前进' in t3:
                            break   # 已回第一步
                        self._sleep(1.0)
                    step = 1
                    # v1.8.86：真机 20:59 替换成功后游戏会把列表视图复位
                    # 到顶部，而滚动状态未重置 → 下一轮拿旧集合比对新顶部
                    # 帧，集合相同被误判「已到底」而提前结束。每次替换成功
                    # 都重置滚动状态。
                    _orb_prev_vis = None
                    scrolls = 0
                    _unreachable.clear()
                    _seen.clear()      # v1.8.88：列表已变，重新累计
                    self._sleep(1.2)
                    continue

                # ── 第一步：选要被替换掉的旧烙印（全行可选，无 y 过滤）──
                # v2.1.8（用户 22:21 定案）：**改用方案 1（获得即入库）**——
                # 不再滚动遍历全列表（真机实测一次全量遍历约 10s，替换流程
                # 每次都要付这个成本，用户判定耗时不划算）。
                # 本局每次三选一/具名命中获得的烙印已由 _record_imprint_gain
                # 实时记录：进晶球门时该记录即本局完整库存（新一轮开始时
                # 游戏内烙印清零，故无历史盲区；每轮入口已重置记录）。
                inv = getattr(self, '_orb_inventory', None)
                if inv is None:
                    # 兜底（**仅当从未记录过任何烙印**时，即属性未建立）→
                    # 用当前屏条目填充，只取 1 帧 OCR，不做滚动遍历。
                    # 注意：显式空列表（= []）表示「本局确实没获得烙印」，
                    # 不兜底——否则会破坏「无目标即终止」的收敛逻辑。
                    _it0 = self.bot._ocr_screen()
                    inv = [(n, q) for n, q, _p in
                           self._collect_imprint_entries(_it0)]
                    self._orb_inventory = inv
                self.log("[信息] 本局已获烙印(" + str(len(inv)) + ")：" +
                         "、".join(f"{n}({q})" for n, q in inv[:10]))
                inv_names = {n for n, _q in inv}
                # v1.8.89（用户定案）：集齐双盾**不再终止流程**——继续
                # 替换寻找其他高优先级烙印（坚定意志等），终止只由
                # 「库存无可替换项 / 晶粹不足 / 用户停止」决定
                if ('执裁之盾' in inv_names and '溢出防御' in inv_names
                        and not getattr(self, '_settle_logged', False)):
                    self._settle_logged = True
                    self.log("[成功] 已集齐执裁之盾 + 溢出防御，"
                             "继续替换寻找其他高优先级烙印")
                # v1.8.82 特例条件：史诗计数（v1.8.84 起从库存数）
                self._orb_epic_count = sum(
                    1 for _n, q in inv if q == '史诗')
                items = self.bot._ocr_screen()   # 取当前帧（扫描后停底部）
                entries = self._collect_imprint_entries(items)
                # v1.8.86（用户定案）：目标选择改为**库存驱动**——
                # 旧逻辑只看当前可见屏，真机 20:59 库存里明明还有
                # 风暴中心(史诗)/蔓延行近(精英) 却判「替换目标耗尽」。
                # 先按三档排序从库存挑期望目标，再滚动去找它。
                # v1.8.91（用户定案）：排除表**只有双盾**——坚定意志虽在
                # 保留意愿表前列，但仍可作为被替换对象（排具名最后：
                # 非具名 → 不屈意志 → … → 坚定意志 → 溢出防御 → 执裁之盾）
                pool = [
                    (n, q) for n, q in inv
                    if q in ('史诗', '精英')
                    and n not in ('执裁之盾', '溢出防御')
                    and n not in tried
                ]
                # v1.9.2（用户定案）：凑核后具名全部保留 → 不再作为
                # 替换目标（pool 只剩非具名，抽核/具名靠第二步随机候选）
                if self._deep_mode() and all(
                        n in inv_names for n in self._core_names()):
                    pool = [(n, q) for n, q in pool
                            if n not in _IMPRINT_NAMED_PRIORITY]
                if not pool:
                    self.log("[警告] 替换目标耗尽（库存无史诗/精英可替换），"
                             "结束流程")
                    break
                # 排序（v1.8.82 三档）：黑名单最优先替换（最差的先换掉，
                # 用户确认不变）→ 史诗（候选至少精英，更容易出目标）→
                # 精英；组内非具名优先 → 具名按 rank 倒序（具名列表=
                # 保留意愿序，index 越大越想换掉）
                def _key(e):
                    rank = (_IMPRINT_NAMED_PRIORITY.index(e[0])
                            if e[0] in _IMPRINT_NAMED_PRIORITY else 99)
                    if e[0] in _IMPRINT_BLACKLIST:
                        grp = 0
                    elif e[1] == '史诗':    # v1.8.82：史诗优先于精英
                        grp = 1
                    else:
                        grp = 2
                    return (grp, -rank)
                pool.sort(key=_key)
                want = pool[0][0]
                wanted = [e for e in entries if e[0] == want]
                if not wanted:
                    # 期望目标不在当前屏 → 下滚找；到底则回滚到顶重来一轮
                    _vis = {e[0] for e in entries}
                    _seen_before = len(_seen)
                    _seen |= _vis
                    # v1.8.88：**到底判定改纯状态判定**（无次数上限）：
                    #   ① 连续两屏可见集合相同 = 滚不动了（真到底）
                    #   ② 本屏没再出现任何「没见过」的名字 = 列表已滚完
                    #      （OCR 抖动导致①不成立时的收敛保障，非次数上限）
                    _at_bottom = (_orb_prev_vis is not None
                                  and (_vis == _orb_prev_vis
                                       or len(_seen) == _seen_before))
                    if _at_bottom:
                        if want in _unreachable:
                            self.log(
                                f"[警告] 目标「{want}」滚两轮仍不可见，跳过")
                            tried.add(want)
                            _orb_prev_vis = None
                            scrolls = 0
                            continue
                        self.log(f"[信息] 已滚到底，回滚到顶重找「{want}」")
                        for _ in range(scrolls):
                            if self.check_stop():
                                stopped = True
                                break
                            self.bot.scroll_wheel(
                                30, rx=0.5, ry=0.5,
                                label='烙印列表_回滚到顶')
                            self._sleep(0.4)
                        if stopped:
                            break
                        _unreachable.add(want)
                        _orb_prev_vis = None
                        scrolls = 0
                        self._sleep(0.8)
                        continue
                    _orb_prev_vis = _vis
                    self.log(f"[信息] 目标「{want}」不在可视区，滚动列表")
                    self.bot.scroll_wheel(
                        -30, rx=0.5, ry=0.5, label='烙印列表_滚动'
                    )
                    scrolls += 1
                    self._sleep(1.2)
                    continue
                name, old_qual, pos = wanted[0]
                # v1.9.35（用户 09:36）：精粹监控——拥有 < 本次消耗
        # → 点确认替换也不会出第二步（安静无反应），提前退出
                _have = self._orb_crystal_have(items)
                _cost = self._orb_replace_cost(items)
                if (_have is not None and _cost is not None
                        and _cost > _have):
                    self.log(
                        f"[警告] 晶粹不足（拥有 {_have} < 本次消耗 {_cost}），"
                        "结束替换流程")
                    tried.add(name)
                    self._click_text('继续前进', min_conf=0.6)
                    break
                # 选中态验证：点旧烙印 → 验证「确认替换」可点；未激活重试
                # 点击一次（v1.8.67：00:11 真机 11s 找不到按钮 = 点击未生效）
                for attempt in (1, 2):
                    if attempt == 1:
                        self.log(f"[信息] 替换目标「{name}」（{old_qual}）")
                    else:
                        self.log(f"[警告] 未选中？重试点击「{name}」")
                    self.bot.click_at(pos[0], pos[1], f'替换_{name}')
                    self._sleep(1.0)
                    if self._click_text('确认替换', min_conf=0.6):
                        break
                    if attempt == 1:
                        dbg = self.bot._ocr_screen()
                        self.log("[警告] 未找到「确认替换」，当前画面文本："
                                 + str([it[2] for it in dbg
                                        if len(it) >= 4
                                        and it[3] >= 0.5][:14]))
                else:
                    self.log("[错误] 两次点击均未激活「确认替换」，结束流程")
                    tried.add(name)
                    break
                self._sleep(1.2)
                # 等第二步特征词（v1.9.2：4 次 × 0.8s——候选界面正常
                # 1-2s 内出现；旧 6×1s+清弹窗+复检实测最长 22s 才兜底，
                # 纯耗时无收益）
                got = False
                for _ in range(4):
                    if self.check_stop():
                        stopped = True
                        break
                    it2 = self.bot._ocr_screen()
                    t2 = [x[2] for x in it2 if len(x) >= 4 and x[3] >= 0.5]
                    if any('晶粹不足' in x for x in t2):
                        self.log("[警告] 晶粹不足（替换未成功），结束流程")
                        tried.add(name)
                        self._click_text('继续前进', min_conf=0.6)
                        return
                    if '保留烙印' in t2 or '确认选择' in t2:
                        got = True
                        break
                    self._sleep(0.8)
                if not got:
                    # v1.8.85：先清可能的残留弹窗再判定（真机 19:33 卡 35s）
                    self.dismiss_blink_popup()
                    self._sleep(0.6)
                    it2b = self.bot._ocr_screen()
                    t2b = [x[2] for x in it2b
                           if len(x) >= 4 and x[3] >= 0.5]
                    if '保留烙印' in t2b or '确认选择' in t2b:
                        pass   # 弹窗挡着，清掉后第二步已就绪
                    else:
                        self.log("[警告] 第二步界面未出现，结束流程兜底")
                        tried.add(name)
                        break
                step = 2
                continue
        except Exception as _orb_exc:
            import traceback as _tb
            self.log("[错误] 烙印晶球流程异常中断：" + str(_orb_exc))
            self.log("[错误] 堆栈：" + _tb.format_exc()[-800:])
            if self.check_stop():
                stopped = True

        # 收尾：点「继续前进」退出（v1.8.68：用户停止时不点，保留现场）
        # v1.8.69：禁 ESC（烙印晶球界面按 ESC 触发游戏 BUG）——点空白
        # 类弹窗 + 重试继续前进，仍失败保留现场直接返回
        if not stopped:
            # v1.8.78：收尾同样要兜底——异常可能来自 OCR/点击链路，此处
            # 再抛会让整个任务 failed 且卡在烙印晶球界面（真机 14:51 事故）
            try:
                self._sleep(1.0)
                for _ in range(2):
                    if self._click_text('继续前进', min_conf=0.6):
                        break
                    # v1.8.85：已不在烙印界面 → 交还玩法循环，不再空等
                    _it_end = self.bot._ocr_screen()
                    _t_end = [x[2] for x in _it_end
                              if len(x) >= 4 and x[3] >= 0.5]
                    if not any(k in _t_end for k in
                               ('继续前进', '确认替换', '保留烙印', '确认选择')):
                        self.log("[信息] 已不在烙印界面，交还玩法循环")
                        return
                    self.dismiss_blink_popup()
                    self._sleep(1.0)
                else:
                    self.log("[警告] 未找到「继续前进」，保留现场退出（禁 ESC）")
            except Exception as _finish_exc:
                self.log("[错误] 烙印晶球收尾异常（保留现场）："
                         + str(_finish_exc))
        self.log("[信息] 烙印晶球流程结束")


    def _find_entry_pos(
        self, items: List[Tuple], name: str
    ) -> Tuple[int, int]:
        for it in items:
            if len(it) >= 4 and it[2] == name:
                return (it[1], it[0])   # v1.8.53：click_at 需要 (x, y)
        return (640, 512)

    def _skip_search_region(
        self,
    ) -> Optional[Tuple[int, int, int, int]]:
        """跳过按钮搜索区域（窗口客户区右下偏中），失败返回 None。

        v1.8.78：三张真机战斗截图 maskedNCC val 0.99+，命中中心稳定在
        窗口比例 (0.690, 0.789)，故按**比例**圈定搜索框（不用死绑像素）。
        region 由 64ms 匹配代替 3373ms 全屏，是高频轮询能成立的前提。

        Returns:
            Optional[(x, y, w, h)]: 屏幕绝对坐标搜索框；拿不到窗口
            几何时返回 None（调用方退回全屏 + 低频）
        """
        rect_getter = getattr(self.bot, 'window_rect', None)
        if rect_getter is None:
            return None
        try:
            l, t, w, h = rect_getter()
        except Exception as _e:
            self.log(f"[调试] _skip_search_region() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return None
        if w <= 0 or h <= 0:
            return None
        rx, ry, rw, rh = _SKIP_REGION_RATIO
        return (
            int(l + rx * w), int(t + ry * h),
            int(rw * w), int(rh * h),
        )

    def _start_and_wait_battle(self) -> str:
        """备战画面按 F 开战，轮询等待战斗结束（胜利/失败/画面切换）。

        已是 4 倍速不处理倍速（用户确认）。战斗中画面无 HUD 特征属
        预期 unknown，不打断；见到胜利/失败/任一已知状态即返回。

        Returns:
            str: 'formation_required' = 上阵人数不满弹窗（v2.1.21 起已点 ✕
                 取消战斗）→ 调用方须「退出备战（回选门界面）+ 结束探索」，
                 以保住游戏的满员默认阵容记录；其余情况返回 ''
        """
        self.log("[信息] 备战画面 → 按 F 开始战斗")
        self.bot.press_f(label='迷宫_开始战斗')
        # v1.8.78：跳过按钮只在战斗末段出现约 2~3s（真机 11 场：命中两次
        # 分别在按 F 后 12s/11s，胜利在 14s；18s 那场全程未命中）。主循环
        # 一轮 = 模板匹配 + OCR ≈ 5s，采样太稀 → 把跳过检测拆成独立高频
        # 子循环（0.45s 一次，region 限定后单次仅 64ms），OCR 降频到 1.2s。
        region: Optional[Tuple[int, int, int, int]] = self._skip_search_region()
        poll: float = _SKIP_POLL if region else _BATTLE_POLL
        if region is None:
            self.log("[信息] 无法获取窗口几何 → 跳过按钮走全屏搜索（低频）")
        skip_clicked: bool = False
        next_ocr_at: float = 0.0      # 立即做第一轮 OCR
        next_beat_at: float = time.time() + _BATTLE_HEARTBEAT
        started: float = time.time()
        deadline: float = started + _BATTLE_WAIT
        while time.time() < deadline:
            if self.check_stop():
                return
            self._sleep(poll)
            # ⓪ v1.9.2：心跳——每 12s 打一次（v2.1.5 措辞修正：此时未必已进入战斗）
            if time.time() >= next_beat_at:
                waited = int(time.time() - started)
                self.log(f"[信息] 按 F 后等待画面变化，已等待 {waited}s"
                         f"（上限 {int(_BATTLE_WAIT)}s）")
                next_beat_at = time.time() + _BATTLE_HEARTBEAT
            # ① 高频：跳过按钮（命中后本场不再重复点）
            if not skip_clicked:
                skip_pos = self.bot.template_match(
                    _SKIP_BTN_TPL, _SKIP_BTN_THRESHOLD, silent=True,
                    scale_range=_SKIP_BTN_SCALE_RANGE, region=region,
                )
                if skip_pos:
                    self.log("[信息] 战斗中出现「跳过」，点击快进")
                    self.bot._click_abs(*skip_pos)
                    skip_clicked = True
            # ② 降频：OCR 判画面状态（每 _BATTLE_POLL 秒一次）
            if time.time() < next_ocr_at:
                # v1.9.2：心跳（高频子循环也打，长战斗不再静默）——
                # 真机 16:00 第 20 关首领战胜利结算画面 OCR 判 unknown，
                # 轮询 72s 零日志被用户误判卡死
                if time.time() >= next_beat_at:
                    waited = int(time.time() - started)
                    self.log(f"[信息] 按 F 后等待画面变化，已等待 {waited}s"
                             f"（上限 {int(_BATTLE_WAIT)}s）")
                    next_beat_at = time.time() + _BATTLE_HEARTBEAT
                continue
            next_ocr_at = time.time() + _BATTLE_POLL
            items = self.bot._ocr_screen()
            # v1.9.2（用户定案全局规则）：任何画面出现「点击空白处关闭」
            # 一律点掉——20 关首领战胜利结算（战斗结束）正是此形态；
            # 点掉后交回主循环重新分流
            if self.dismiss_blink_popup(items=items):
                return
            # v1.9.35（用户定案 09:36）：上阵人数不满（上一关战斗减员所致）。
            # v2.1.21（用户 16:34 定案·机制修正）：**改点 ✕ 取消战斗**，
            # 不再点 ✓ 硬打。用户实测机制：阵亡后只要**不开战**，游戏记录的
            # 默认阵容仍为满员 → 新一轮就能满员出战；一旦点 ✓ 硬打，残缺阵容
            # **会被记成默认阵容** → 上阵越少难度越高 → 继续减员（恶性循环
            # 直到全部阵亡）。故此处点 ✕（btn_confirm_cancel.png，真机同屏帧
            # maskedNCC 实测 0.983）并返回 'formation_required'，由调用方执行
            # 「退出备战（回选门界面）→ 结束探索」。
            if any('上阵人数不满' in it[2] for it in items
                   if len(it) >= 4 and it[3] >= 0.5):
                self.log("[信息] 上阵人数不满弹窗 → 点 ✕ 取消战斗"
                         "（保住满员阵容记录，避免下轮继续减员）")
                _clicked: bool = False
                _cpos = self.bot.template_match(
                    _CONFIRM_CANCEL_TPL, _CONFIRM_THRESHOLD,
                    silent=True, scale_range=_CONFIRM_CHECK_SCALE_RANGE,
                )
                if _cpos:
                    # 模板返回**屏幕**绝对坐标 → 用 _click_abs（同系）
                    self.bot._click_abs(_cpos[0], _cpos[1])
                    _clicked = True
                else:
                    # 兜底：该弹窗的 ✕ 会被 OCR 读成 'X'（真机实测 conf 0.64），
                    # 限定弹窗下半部中央区避免误配；OCR 给的是**图像**坐标
                    _otext = next(
                        ((it[1], it[0]) for it in items
                         if len(it) >= 4 and it[3] >= 0.4
                         and it[2].strip() in ('X', 'x', '×', '✕')
                         and 0.40 < it[1] / 1280.0 < 0.66
                         and it[0] / 1024.0 > 0.60), None)
                    if _otext:
                        self.log("[警告] ✕ 模板未命中 → OCR 定位兜底")
                        self.bot.click_at(_otext[0], _otext[1],
                                          '迷宫_人数不满_✕')
                        _clicked = True
                if not _clicked:
                    self.log("[警告] ✕ 未能定位（模板 + OCR 兜底均失败）")
                self._sleep(1.5)
                return 'formation_required'
            state: str = self._detect_play_state(items)
            if state == 'victory':
                self.log("[信息] 战斗胜利弹层已出现")
                return
            if state == 'failure':
                self.log("[警告] 战斗失败弹层已出现")
                return
            if state not in ('battle_prep', 'unknown'):
                self.log(f"[信息] 战斗结束，画面切至 {_PLAY_STATE_LABELS.get(state, state)}")
                return
            # v2.1.5（用户 21:33）：按 F 后画面**仍停在备战页**且超过宽限
            # 秒数 → 判定「未开战」（角色全部阵亡/阵容不足时按 F 无反应）
            # → 立即返回，不必等满 _BATTLE_WAIT（原 180s 白等）
            if (state == 'battle_prep'
                    and time.time() - started >= _NO_BATTLE_GRACE):
                waited = int(time.time() - started)
                self.log(f"[警告] 按 F 后 {waited}s 画面仍停在备战页（未进入战斗）")
                return 'no_battle'
            # v1.8.27（真机 9/6）：20/20 层 Boss 击杀后**直接**弹「探索完成」
            # 结算页（无战斗胜利/难度通关中间态）→ 原逻辑干等 90s 超时
            if state == 'final_settle':
                self.log("[信息] 探索完成结算页已出现（探索已结束）")
                return
        self.log(f"[警告] 战斗等待超时（{int(_BATTLE_WAIT)}s），交由主循环兜底")
        # v2.1.4（用户 21:14 真机）：返回超时标记——调用处据此计数，
        # 连续超时判「无法开战」，避免旧逻辑死循环（超时→再按 F→再等）
        return 'timeout'

    def _handle_failure(self) -> bool:
        """退出战斗失败画面：ESC 与「返回」按钮交替，直到退出或用户停止。

        用户定案（2026-09-03）：先 ESC，无效再点「返回1/返回2」按钮
        （即 v1.7.5 的 btn_back_dark/purple 模板）。
        v1.8.88（用户定案）：移除 ESC 重试次数上限——ESC 与返回按钮无限
        交替，终止只由「画面已无『战斗失败』」或「用户停止」决定；每轮
        均 check_stop()，不会失控（真机实测首次 ESC 即退出）。

        Returns:
            bool: True = 失败画面已退出（OCR 复查无「战斗失败」）
        """
        attempt: int = 0
        while True:
            if self.check_stop():
                self.log("[信息] 收到停止，退出失败画面处理")
                return False
            attempt += 1
            self.bot.press_esc(label=f'战斗失败_ESC{attempt}')
            self._sleep(1.2)
            items = self.bot._ocr_screen()
            if not any('战斗失败' in it[2] for it in items
                       if len(it) >= 4 and it[3] >= 0.5):
                self.log(f"[信息] ESC 第 {attempt} 次退出失败画面")
                return True
            # ESC 未生效 → 同轮降级点返回按钮（两条路径交替，无次数上限）
            if self.bot.click_back_button(
                _BACK_BUTTON_TPLS, threshold=_BACK_THRESHOLD
            ):
                self._sleep(1.0)
                items = self.bot._ocr_screen()
                if not any('战斗失败' in it[2] for it in items
                           if len(it) >= 4 and it[3] >= 0.5):
                    self.log("[信息] 返回按钮已退出失败画面")
                    return True

    _PREP_BACK_RATIO: Tuple[float, float] = (0.139, 0.825)
    # ↑ 备战页左下角「◀」返回按钮的比例坐标（真机截图 2026-09-11 实测；
    #   用户验证：该按钮可退出备战界面）

    def _leave_battle_prep(self) -> bool:
        """退出备战界面（v2.1.14：含**结果校验** + 返回按钮兜底）。

        真机 2026-09-11（角色全灭场景）暴露两处问题：
          ① `press_esc` 旧实现用 SendMessage，对 Unity 游戏无效
             （已改为 keybd_event，见 afk_bot/infra/input.py）
          ② 退出后**从不校验**结果 → "退不出去"被当成成功 → 开新轮 →
             画面仍在备战页 → 又按 F → 空转 30 轮 / 16 分钟

        现流程：ESC → OCR 校验是否已离开备战页 → 仍在则点**左下角「◀」返回
        按钮**（用户真机实测可退出）→ 再校验。

        Returns:
            bool: True = 已确认离开备战界面
        """
        for attempt in (1, 2):
            if attempt == 1:
                self.bot.press_esc(label='迷宫_退出备战')
            else:
                self.log("[警告] ESC 未能离开备战页 → 点左下角返回按钮兜底")
                self.bot.click_ratio(self._PREP_BACK_RATIO[0],
                                     self._PREP_BACK_RATIO[1],
                                     label='迷宫_退出备战_返回按钮')
            self._sleep(1.8)
            if self.check_stop():
                return False
            _items = self.bot._ocr_screen()
            _st: str = self._detect_play_state(_items)
            if _st == 'battle_prep' and attempt == 1:
                # v2.1.14（独立验证建议）：二次 OCR 过滤抖动/陈旧帧——
                # 避免"ESC 其实已生效、只是首帧是旧帧"时白点一次返回按钮
                self._sleep(0.8)
                _items = self.bot._ocr_screen()
                _st = self._detect_play_state(_items)
            if _st == 'unknown':
                # 转场中（离开备战页的常见中间态）→ 再等一帧确认
                self._sleep(1.0)
                _items = self.bot._ocr_screen()
                _st = self._detect_play_state(_items)
            if _st != 'battle_prep':
                self.log(f"[信息] 已离开备战界面（当前："
                         f"{_PLAY_STATE_LABELS.get(_st, _st)}）")
                return True
        self.log("[警告] ESC 与返回按钮均未能离开备战界面")
        return False

    def _abort_round(self) -> str:
        """退出备战 + 结束探索（v2.1.14，带回执版本）。

        Returns:
            'done'     = 收尾成功（本轮结束，可开新一轮）
            'continue' = 深层「继续探索」已点，交回主循环
            ''         = 收尾失败 —— 调用方**不得**当成本轮完成
        """
        if not self._leave_battle_prep():
            return ''
        _items = self.bot._ocr_screen()
        _res: str = self._end_exploration(_items)
        if _res == 'continue':
            return 'continue'
        if _res != 'done':
            self.log("[警告] 结束探索未成功")
            return ''
        # v2.1.14（独立验证 F4）：点中「结束探索」≠ 探索真的结束——旧实现只凭
        # "点击成功"就回 done 并清零失败计数；若点击被吞/确认弹窗未识别，仍会
        # 开新轮空转（与本次修复同类的"把失败当成功"）。补一次画面校验：
        # 不应再停留在备战页或选门地图。
        self._sleep(1.5)
        _after = self.bot._ocr_screen()
        _st_after: str = self._detect_play_state(_after)
        if _st_after in ('battle_prep', 'door'):
            self.log(f"[警告] 结束探索后画面仍为 "
                     f"{_PLAY_STATE_LABELS.get(_st_after, _st_after)}"
                     " → 视为未成功")
            return ''
        return 'done'

    def _end_exploration(self, items: List[Tuple]) -> str:
        """结算决策：点「结束探索」退出 / 深层模式点「继续探索」进深层。

        决策（v1.8.9，复用调用方已 OCR 的结算帧，零额外 OCR）：
          - 已到 20 层终点（进度 20/20，deep_challenge 深层）→ 点「结束探索」
          - deep_challenge 勾选且结算帧出现「继续探索」→ 点「继续探索」
            （15 层 Boss 通关后进入深层 16-20，顶栏变 X/20）
          - 其余（含失败兜底）→ 点「结束探索」（v1.8.5 行为，默认不勾）
        点击后探测确认弹窗（若有「确认」则补点）。

        Returns:
            str: 'done'     = 已点「结束探索」（本次探索结束）
                 'continue' = 已点「继续探索」（深层模式，v1.8.20 起调用方
                              须继续玩法循环，不得结束任务——v1.8.19 及
                              之前布尔返回导致 20 层勾选无效）
                 ''         = 未找到按钮（失败）
        """
        prog: Optional[Tuple[int, int]] = self._extract_progress(items)
        # v1.8.63：deep 判定迁移到运行模式（兼容旧 deep_challenge 配置）
        deep: bool = self._deep_mode()
        btn_kw: str = '结束探索'
        go_on: bool = False
        if prog and prog[1] == 20 and prog[0] >= 20:
            # 深层终点（20/20）→ 必结束（即使弹层残留「继续探索」也不继续）
            self.log("[信息] 深层已通关第 20 层（20/20），点「结束探索」")
        elif deep and any(
            '继续探索' in it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
        ):
            btn_kw = '继续探索'
            go_on = True
            self.log("[信息] 深层模式：15 层通关，点「继续探索」进入深层 16-20")
        else:
            self.log("[信息] 结束探索，退出本次迷宫")

        if not self._click_text(btn_kw, min_conf=0.55):
            self.log(f"[错误] 未找到「{btn_kw}」按钮")
            return ''
        self._sleep(1.5)
        items = self.bot._ocr_screen()
        # v1.8.87：终止确认弹窗（「是否终止本次探索？」）当场收尾——
        # 此前只查「确认」，弹窗残留到下一轮启动（startup_probe 判
        # end_confirm 再长按）才被兜住，多绕一轮；长按结束优先于确认
        pos: Optional[Tuple[int, int]] = self.bot.find_text(
            items, '长按结束', min_conf=0.6
        )
        if pos:
            self.log("[信息] 结束探索弹出终止确认，长按「长按结束」")
            self.bot.long_press_at(
                pos[0], pos[1], duration=2.5, label='结束探索_长按结束')
            self._sleep(1.5)
        else:
            pos = self.bot.find_text(
                items, '确认', min_conf=0.6
            )
            if pos:
                self.log("[信息] 结束探索弹出确认，补点「确认」")
                self.bot.click_at(pos[0], pos[1], '结束探索_确认')
                self._sleep(1.5)
        if go_on:
            self.log("[信息] 已点「继续探索」，深入深层 16-20")
            return 'continue'
        # v2.1.21（用户定案）：**结束探索 = 本局烙印作废** → 立即清空库存
        # 镜像。不依赖「下一轮入口」兜底：镜像与探索同生命周期，任何收尾
        # 路径（boss_cleared / failure / _abort_round）都从这里过。
        if self._reset_orb_mirror():
            self.log("[信息] 探索已结束 → 烙印库存镜像已清空")
        self.log("[信息] 本次探索已结束")
        return 'done'

    @staticmethod
    def _extract_progress(items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """从 OCR items 提取层数进度 X/Y（X=已通关层数，Y=总层数 15/20）。

        v1.8.9：Y 用于区分常规(15)/深层(20)模式（用户确认：通关 15 层后继续
        挑战则顶栏显示 X/20，X 仍为已通关数，当前层 = X+1）。正则只认
        分母 15/20 的分式（顶栏层数 / boss_cleared 结算「到达层数」）。
        OCR 漏检或噪声 → None（调用方走降级）。

        Returns:
            (cleared, total) 如 (14, 15) 表示已通 14 层、当前第 15 层；或 None
        """
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            m = re.search(r'(\d{1,2})/(15|20)', it[2])
            if m:
                return int(m.group(1)), int(m.group(2))
        return None

    # ── 界面判定 ────────────────────────────────────────────

    def _is_on_playbook(self) -> bool:
        """是否在玩法目录（1级）。

        判据：底部标签页「常规玩法」与「活动」**同时存在**。

        两个坑（都踩过，别改回去）：
        1. 不能用 detect_phase 的 playbook 判定 —— 它依赖"挂机关卡"条目，
           一旦向下滚动该条目就滚出屏幕，detector 会退化判成 'activity'，
           主循环若照 phase 行事就会误发 ESC 退出玩法目录。
        2. 不能用底部菜单的"玩法目录"文字 —— 0 层（世界/家园）界面底部
           同样有「玩法目录」按钮，会把 0 层误判成玩法目录。

        Returns:
            bool: True 表示在玩法目录
        """
        items = self.bot._ocr_screen()
        texts: List[str] = [t for _, _, t, c, *_ in items if c >= 0.5]
        has_normal: bool = any('常规玩法' in t for t in texts)
        has_activity: bool = any('活动' in t for t in texts)
        return has_normal and has_activity

    # ── 通用工具 ────────────────────────────────────────────

    def _click_template(
        self, tpl_name: str, threshold: float = 0.75, label: str = '',
        scale_range: Tuple[float, float, float] = (0.8, 1.3, 0.1),
        pre_wait: float = 0.0,
    ) -> bool:
        """模板匹配按钮并点击（v1.7.10 新增）。

        OCR 找文字有两个致命缺陷：①按钮无文字时找不到；②弹窗文案
        包含按钮语义文字（如"是否**继续**探索"）会误点文案而非按钮。
        模板匹配直接比图形，两者都免疫。

        透明背景模板（btn_confirm_*.png）由 template_match 自动走
        maskedNCC 路径；命中返回屏幕绝对坐标 → _click_abs 点击。
        v1.8.92：缩放带默认收窄为 6 档（UI 按钮）；pre_wait 用于先等
        弹窗淡入动画完成再匹配，避免动画期低分空跑重试。

        Args:
            tpl_name: 模板文件名（位于 templates/）
            threshold: 匹配阈值
            label: 日志标签（可选）
            scale_range: 缩放搜索范围（默认 0.8~1.3 共 6 档）
            pre_wait: 匹配前等待秒数（等弹窗动画）

        Returns:
            bool: 命中并点击返回 True
        """
        tpl_path: str = os.path.join(_TEMPLATE_DIR, tpl_name)
        tag: str = label or tpl_name
        if pre_wait > 0:
            self._sleep(pre_wait)
        for i in range(_TPL_RETRIES):
            if self.check_stop():
                return False
            pos: Optional[Tuple[int, int]] = self.bot.template_match(
                tpl_path, threshold=threshold, silent=True,
                scale_range=scale_range,
            )
            if pos:
                self.log(
                    f"[点击] 模板命中 {tag} | 阈值={threshold} "
                    f"屏幕=({pos[0]},{pos[1]})"
                )
                self.bot._click_abs(pos[0], pos[1])
                self._sleep(1.2)
                return True
            self._sleep(_TPL_RETRY_WAIT)
        self.log(f"[警告] 模板未命中 | {tag} (阈值 {threshold})")
        return False

    def _click_text(
        self, keyword: str, min_conf: float = 0.6
    ) -> bool:
        """OCR 找文字并点击，带重试（v1.7.10 加置信度日志）。

        Args:
            keyword: 目标文字
            min_conf: 最低置信度

        Returns:
            bool: 找到并点击返回 True
        """
        deadline: float = time.time() + _TEXT_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            pos: Optional[Tuple[int, int]] = self.bot.find_text(
                items, keyword, min_conf=min_conf
            )
            if pos:
                conf: float = self._best_text_conf(items, keyword)
                self.log(
                    f"[点击] 「{keyword}」 置信度={conf:.2f} | "
                    f"图像=({pos[0]},{pos[1]})"
                )
                self.bot.click_at(pos[0], pos[1], keyword)
                self._sleep(1.2)
                return True
            self._sleep(0.6)
        self.log(f"[警告] 未找到文字 | {keyword}")
        return False

    def _best_text_conf(
        self, items: List[Tuple], keyword: str
    ) -> float:
        """在 OCR 结果中找含 keyword 的最高置信度（仅日志用）。

        OCR 项结构 (cy, cx, text, conf, ...)。匹配策略与
        ocr_engine._find_text_impl 保持一致（NFKC + 包含匹配），
        找不到返回 0.0。
        """
        import unicodedata

        norm_kw: str = unicodedata.normalize('NFKC', keyword)
        best: float = 0.0
        for item in items:
            if len(item) < 4:
                continue
            text: str = item[2]
            conf: float = item[3]
            norm_text: str = unicodedata.normalize('NFKC', text)
            if norm_kw in norm_text and conf > best:
                best = conf
        return best


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🌀",
    group="游戏任务",
    status="ready",   # v1.8.0 实装：导航+玩法主体实现并验证
    options_schema=LabyrinthTask.options_schema,   # v1.8.15：GUI 抽屉渲染参数表单
    nav_target='playbook',   # v1.8.31：入口卡片在玩法目录 → 只回到玩法目录
))
