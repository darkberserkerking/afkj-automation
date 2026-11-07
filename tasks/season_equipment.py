# -*- coding: utf-8 -*-
"""任务：更新装备（赛季职业装备锻造，v2.3.37 实装）。

────────────────────────────────────────────────────────────
【流程】（用户 2026-11-06 需求 + 17 张真机截图 + 项目 MaaOCR 实测）
  1. 英雄厅堂（1 层）→ 点「赛季装备」（rx≈0.600 / ry≈0.867）
  2. 「赛季职业装备」主界面：辅助/法师/战士/游侠/射手/坦克 六个圆形
     职业图标（大圆标 rx≈0.39/0.61，ry≈0.51/0.63/0.73）→ 点击进入
  3. 「XX装备」页：6 件装备，**下方有「锻造」按钮的才可锻造**
     （ry≈0.776，rx≈0.338/0.404/0.470/0.536/0.602/0.667）→ 点击
  4. 锻造弹窗（「当前装备」+「可锻造装备」）→ 点弹窗内「锻造」
     （rx≈0.580 / ry≈0.671）
  5. 几秒动画 → 「锻造成功」页 → 点「穿戴新装备」（rx≈0.501/ry≈0.927）
  6. 重复 3~5，直到本职业 6 件都无「锻造」按钮
  7. 底部 6 个纯图标职业 tab → 切下一个职业，重复 3~6
  8. 六个职业全部无可锻造 → done

【关键判据（全部来自真机 OCR，不是目测）】
  · 主界面：'待领宝箱列表' + '当前赛季'（双词 AND，避开英雄厅堂底栏）
  · 职业页：'锻造等级上限' + '当前无可替换装备'
  · 锻造弹窗：'当前装备' + '可锻造装备'（**优先级必须高于职业页**，
    否则弹窗里同时读到背景 ry≈0.867 的 6 个「锻造」会误点背景）
  · 成功页：'锻造成功' + '穿戴新装备'
  · 「锻造」按钮：ry≈0.776 一行；弹窗按钮 ry≈0.671 —— 弹窗态只取
    y 最小（最高）的那个「锻造」，避免误点背景卡片
  · 装备等级达 700（上限）后「锻造」按钮消失 → 该件已完成

【UI 定位分流（用户 2026-11-05 规则）】
  · 有文字的按钮（赛季装备/锻造/穿戴新装备/职业名）→ OCR 首选
  · 纯图案的 6 个职业圆形图标 → 模板匹配（底部 tab 无文字，
    OCR 全无输出，必须用模板）；模板为 RGBA 圆外透明 + maskedNCC

【一键装备（v2.3.40 新增场景）】
  · 从宝箱等其他途径拿到更高级装备时，职业页底部横幅会换成橙色
    「一键装备」大按钮（真机 OCR (647,856) conf 0.98，ry≈0.834）。
    该界面仍有「锻造等级上限」、六件全 700 级、无「锻造」按钮，
    **但两种完成横幅都不显示** → 必须先点它换装，再判是否可锻造。
  · 分支优先级：成功页 → 弹窗 → **一键装备** → 职业页 → 主界面 → 入口。
  · 按 UI 分流规则，有明确文字的按钮走 OCR 定位（不做模板）；
    点击后强制重识别、重新走状态机，不预设后续界面。
  · 按钮可能常驻：点过一次后仍显示，给 5 帧过渡；仍无变化则
    记明确告警、按「已换装完成」切下一职业，不误杀正常流程。

【职业页判据（v2.3.39 真机修复）】
  · 横幅文案随进度变：还有可锻造时「当前无可替换装备」，六件全 700
    后变「当前暂无可锻造的装备」。把横幅写进 AND 判据会让**完成态**
    整个界面判不出（真机 17:33 连续 20 帧 unknown → failed）。
  · 故判据只用 8/8 截图全中的「锻造等级上限」，并显式排除弹窗/
    成功页（弹窗也含该词）；两种横幅作为「本职业完成」的显式信号，
    见 _W_CLASS_DONE。

【OCR 缓存铁律（v2.3.38 真机修复）】
  · 本任务的判据完全依赖「某张卡下方『锻造』按钮是否消失」这种
    **小面积画面变化**；而 screen.py 的 OCR 缓存键是 20 点稀疏指纹，
    采不到单张卡区域 → 60s TTL 内一直返回锻造前的旧 items → 反复
    点击已消失的按钮（真机 16:59 连点 (770,797) 8 次后 failed）。
  · 故本任务所有 `_ocr_screen()` 一律 `no_cache=True`；
    新增调用点必须同样处理，见 tests/test_season_equipment_dryrun.py
    的源码静态断言。

【护栏】
  · check_stop 每轮即时响应
  · 连续未知帧上限 / 单职业点击无进展上限 / 全局点击硬上限
  · 资源不足、动画卡住、按钮点了没反应 → failed（不无限重试）
  · 自然完成返回 'done'（不中断队列），手动停止返回 'stopped'
"""

import os
from typing import List, Optional, Tuple

from task_registry import TaskRegistry, TaskMeta
from tasks.base import BaseTask

task_id = "season_equipment"
label = "更新装备"
description = "锻造并穿戴六职业赛季装备，无可锻造即止"

# ── 模板（RGBA 圆外透明，走 maskedNCC；路径必须绝对，防 CWD 漂移）──
# v2.4.5：改用 **内缩版** 模板（*_inner.png，alpha 边缘腐蚀 ~4px）。
# 真机 2026-11-07 12:31 实证：圆标边缘被「!」可装备角标 / 等级横幅遮挡时，
# 原模板 6 职业中 4 个主界面分数跌破 0.70（辅助 0.687 / 法师 0.533 /
# 射手 0.637 / 战士 0.559）→ 任务卡死「圆标模板未命中」。
# 内缩 mask 只保留中心稳定圆区：主界面 6/6 ≥0.78、底部 tab 6/6 ≥0.87
# （用户两张真机截图差分实测）。原模板保留在库中可对照复验。
_TPL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')
CLASSES: Tuple[Tuple[str, str], ...] = (
    ('辅助', 'season_class_support_inner.png'),
    ('法师', 'season_class_mage_inner.png'),
    ('战士', 'season_class_warrior_inner.png'),
    ('游侠', 'season_class_ranger_inner.png'),
    ('射手', 'season_class_marksman_inner.png'),
    ('坦克', 'season_class_tank_inner.png'),
)
CLASS_TPL: dict = {zh: os.path.join(_TPL_DIR, fn) for zh, fn in CLASSES}
CLASS_ORDER: Tuple[str, ...] = tuple(zh for zh, _ in CLASSES)

# 底部 tab 搜索区域（屏幕绝对比例，window_rect 换算）；只圈底部窄条，
# 避免主界面大圆标（scale 1.6）被误当成 tab 命中。
_TAB_REGION_RATIO = (0.24, 0.89, 0.62, 0.11)
_TPL_THRESHOLD: float = 0.82
_TPL_SCALE_RANGE: Tuple[float, float, float] = (0.8, 1.4, 0.1)

# 主界面大圆标搜索区域（仅在从主界面首次进入时用）
_MAIN_ICON_REGION_RATIO = (0.24, 0.42, 0.52, 0.42)
_MAIN_ICON_THRESHOLD: float = 0.70
_MAIN_ICON_SCALE_RANGE: Tuple[float, float, float] = (1.3, 1.9, 0.1)

# 判据词（子串，来自真机 OCR）
_W_MAIN = ('待领宝箱列表', '当前赛季')
# v2.3.39 真机：横幅文案随进度变化，不可写进 AND 判据——
#   还有可锻造 → 「当前无可替换装备」
#   六件全 700 → 「当前暂无可锻造的装备」
# 8 张实测截图共同稳定词只有「锻造等级上限：700」(8/8)；
# 「装备回收」只在两轮真机出现（用户最初 5 张职业页全无），不可入判据。
# 故职业页只认该稳定词 + 显式排除弹窗/成功态；横幅仅作完成态信号。
_W_CLASS = ('锻造等级上限',)      # 8/8 截图全中；弹窗亦含此词，靠优先级排除
_W_CLASS_DONE = ('当前无可替换装备', '当前暂无可锻造的装备')
_W_DIALOG = ('当前装备', '可锻造装备')
# v2.3.40：其他途径获得更高级装备时，底部横幅换成橙色「一键装备」按钮。
# 该界面仍含「锻造等级上限」但两种完成横幅都没有 → 必须在完成判定之前
# 先点它换装，否则旧逻辑会直接判「本职业完成」切走。
_W_ONEKEY = '一键装备'
_ONEKEY_STREAK_MAX: int = 5    # 按钮常驻时，几帧后按完成继续切职业
_W_SUCCESS = ('锻造成功', '穿戴新装备')
_W_ENTRY = '赛季装备'
_W_HERO_HALL = '英雄厅堂'
_W_FORGE = '锻造'
_W_WEAR = '穿戴新装备'

# 「锻造」卡片按钮所在行（ry），弹窗按钮更靠上
_CARD_FORGE_RY: Tuple[float, float] = (0.73, 0.83)
_DIALOG_FORGE_RY_MAX: float = 0.72
_IMG_W, _IMG_H = 1280.0, 1024.0

# 护栏
_STALL_MAX: int = 20          # 连续未知帧（动画/过渡）
_NO_PROGRESS_MAX: int = 8     # 同一职业连续点击但无成功页
_CLICK_HARD_MAX: int = 90     # 全局点击硬上限（6 职业 × 6 件 × 2 次 + 余量）
_LOOP_SLEEP: float = 1.2


class SeasonEquipmentTask(BaseTask):
    """赛季职业装备锻造（六职业逐件锻造 + 穿戴）。"""

    task_id = task_id
    label = label

    # 循环语义：主界面/职业页/弹窗/成功页都在循环内；入口页算家门口
    loop_screens = frozenset({
        'season.equip_main', 'season.equip_class',
        'season.forge_dialog', 'season.forge_success',
    })
    near_screens = frozenset({'nav.hero_hall'})
    loop_entry_strict = False
    options_schema = {}

    # ── 契约方法 ────────────────────────────────────────────

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _stop_result(self) -> str:
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def summary(self) -> str:
        return "锻造并穿戴六职业赛季装备"

    def startup_probe(self) -> str:
        """三态契约：已在装备链路 → ready；其余 clear（交给 _goto_entry）。"""
        items = self.bot._ocr_screen(no_cache=True)
        texts = self._texts(items, 0.6)
        if self._is_in_equip_flow(texts):
            self.log("[信息] 启动即在赛季装备链路，就地续跑")
            return 'ready'
        return 'clear'

    @staticmethod
    def _is_in_equip_flow(texts: List[str]) -> bool:
        blob = ' '.join(texts)
        return (all(k in blob for k in _W_MAIN)
                or all(k in blob for k in _W_CLASS)
                or _W_ONEKEY in blob
                or any(k in blob for k in _W_CLASS_DONE)
                or all(k in blob for k in _W_DIALOG)
                or all(k in blob for k in _W_SUCCESS))

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> str:
        self.log("启动（英雄厅堂 → 赛季装备 → 六职业锻造穿戴）")
        probe = self.startup_probe()
        if probe == 'clear' and not self._goto_entry():
            if self.check_stop():
                return self._stop_result()
            self.log_nav_failed("赛季装备")
            return 'failed'
        return self._forge_loop()

    def _goto_entry(self) -> bool:
        """自动导航：已在英雄厅堂则直达；否则回 1 层 → 英雄厅堂 → 赛季装备。"""
        first = self.bot._ocr_screen(no_cache=True)
        first_blob = ' '.join(self._texts(first, 0.6))
        need_nav = not (_W_HERO_HALL in first_blob and _W_ENTRY in first_blob)
        if need_nav:
            from tasks.global_nav import GlobalNavigator
            nav = GlobalNavigator(
                self.bot, logger=self.log, stop_check=self.check_stop)
            if not nav.navigate_to('playbook'):
                self._nav_last_screen = getattr(nav, 'last_screen', '')
                return False
        for _ in range(4):
            if self.check_stop():
                return False
            items = self.bot._ocr_screen(no_cache=True)
            texts = self._texts(items, 0.6)
            blob = ' '.join(texts)
            if all(k in blob for k in _W_MAIN):
                self.log("[成功] 已进入赛季职业装备主界面")
                return True
            pos = self._find_text_exact(items, _W_ENTRY)
            if pos:
                self.log(f"[点击] 「赛季装备」入口 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '赛季装备_入口')
                self._sleep(2.0)
                continue
            if any(_W_HERO_HALL in t for t in texts):
                hp = next(((it[1], it[0]) for it in items
                           if len(it) >= 4 and _W_HERO_HALL in it[2]), None)
                if hp:
                    self.log(f"[点击] 底栏「英雄厅堂」 | 图像={hp}")
                    self.bot.click_at(hp[0], hp[1], '底栏_英雄厅堂')
                    self._sleep(2.0)
                    continue
            self._sleep(1.0)
        self.log("[错误] 未找到「英雄厅堂 / 赛季装备」入口")
        return False

    # ── 状态机主循环 ────────────────────────────────────────

    def _forge_loop(self) -> str:
        stall = 0
        clicks = 0
        forged = 0
        done_classes: set = set()
        cur_class: Optional[str] = None
        no_progress = 0
        last_forge_pos: Optional[Tuple[int, int]] = None
        onekey_streak = 0
        onekey_done: set = set()
        onekey_persistent = False   # 已确认按钮常驻 → 后续职业不再重复等待

        while True:
            if self.check_stop():
                return self._stop_result()
            if clicks >= _CLICK_HARD_MAX:
                self.log(f"[错误] 点击硬上限 {_CLICK_HARD_MAX} 次，"
                         "判卡住（可能资源不足或动画异常）"
                         f" | 末次锻造点击={last_forge_pos}")
                return 'failed'

            items = self.bot._ocr_screen(no_cache=True)
            texts = self._texts(items, 0.6)
            blob = ' '.join(texts)

            # ① 锻造成功页 → 穿戴新装备（唯一出口，最高优先）
            if all(k in blob for k in _W_SUCCESS):
                pos = self._find_text_exact(items, _W_WEAR)
                if pos is None:
                    stall += 1
                else:
                    self.bot.click_at(pos[0], pos[1], '穿戴新装备')
                    forged += 1
                    clicks += 1
                    stall = 0
                    no_progress = 0
                    self.log(f"[成功] 穿戴新装备（累计 {forged} 件）")
                    self._sleep(_LOOP_SLEEP)
                continue

            # ② 锻造弹窗 → 只点最靠上的「锻造」（背景卡片按钮在 ry≈0.87）
            if all(k in blob for k in _W_DIALOG):
                pos = self._dialog_forge_pos(items)
                if pos is None:
                    stall += 1
                else:
                    self.bot.click_at(pos[0], pos[1], '弹窗_锻造')
                    clicks += 1
                    stall = 0
                    no_progress += 1
                    last_forge_pos = pos
                    self.log(f"[点击] 弹窗「锻造」 | 图像={pos}")
                    if no_progress >= _NO_PROGRESS_MAX:
                        self.log(f"[错误] 连续 {no_progress} 次点锻造"
                                 "未见锻造成功页，判卡住（资源不足？）"
                                 f" | 末次坐标={pos}")
                        return 'failed'
                    self._sleep(2.5)   # 几秒锻造动画
                continue

            # ②.5 一键装备：其他途径获得更高级装备 → 先换装再锻造
            #      必须早于 ③：该界面含「锻造等级上限」但无完成横幅，
            #      落到 ③ 会被误判「本职业已无可锻造」直接切走。
            if (_W_ONEKEY in blob
                    and not all(k in blob for k in _W_DIALOG)
                    and not all(k in blob for k in _W_SUCCESS)):
                pos = self._find_text_exact(items, _W_ONEKEY)
                if pos is None:
                    stall += 1
                else:
                    if cur_class is None:
                        cur_class = self._detect_class(items)
                    key = cur_class or 'unknown'
                    if key in onekey_done:
                        onekey_streak += 1
                        if onekey_persistent or onekey_streak >= _ONEKEY_STREAK_MAX:
                            # 按钮可能常驻（换装后不消失、只是无更高装备）。
                            # 不假设界面，按「已换装完成」继续切职业，
                            # 避免每个正常职业都被判卡住。
                            self.log(f"[信息] {key}「一键装备」已执行，"
                                     f"按钮仍显示 {onekey_streak} 帧"
                                     "（疑似常驻/无更高装备），按完成继续"
                                     f" | 末次坐标={pos}")
                            onekey_persistent = True
                            done_classes.add(key)
                            no_progress = 0
                            onekey_streak = 0
                            nxt_ok = next((c for c in CLASS_ORDER
                                           if c not in done_classes), None)
                            if nxt_ok is None:
                                self.log("[成功] 六职业全部完成，"
                                         f"累计 {forged} 件")
                                return 'done'
                            if not self._switch_class(nxt_ok):
                                self.log(f"[错误] 切换职业「{nxt_ok}」失败"
                                         "（模板未命中）")
                                return 'failed'
                            cur_class = nxt_ok
                            clicks += 1
                            stall = 0
                            self.log(f"[信息] 切换职业 → {nxt_ok}")
                            self._sleep(_LOOP_SLEEP)
                            continue
                    else:
                        onekey_done.add(key)
                        onekey_streak = 0
                    self.bot.click_at(pos[0], pos[1], '一键装备')
                    clicks += 1
                    stall = 0
                    no_progress = 0
                    self.log(f"[点击] {key}「一键装备」换装 | 图像={pos}")
                    self._sleep(2.0)   # 换装动画；下一轮强制重识别重新判界面
                continue

            # ③ 职业装备页 → 找卡片「锻造」按钮
            #    弹窗也含「锻造等级上限」，显式排除防顺序调整后抢判
            if (all(k in blob for k in _W_CLASS)
                    and not all(k in blob for k in _W_DIALOG)
                    and not all(k in blob for k in _W_SUCCESS)):
                if cur_class is None:
                    cur_class = self._detect_class(items)
                forge_pos = self._card_forge_pos(items)
                if forge_pos is not None:
                    self.bot.click_at(forge_pos[0], forge_pos[1], '卡片_锻造')
                    clicks += 1
                    stall = 0
                    no_progress += 1
                    last_forge_pos = forge_pos
                    self.log(f"[点击] {cur_class or '当前职业'}装备「锻造」"
                             f" | 图像={forge_pos}")
                    if no_progress >= _NO_PROGRESS_MAX:
                        self.log(f"[错误] 连续 {no_progress} 次点锻造"
                                 "未见锻造成功页，判卡住（资源不足？）"
                                 f" | 末次坐标={forge_pos}")
                        return 'failed'
                    self._sleep(_LOOP_SLEEP)
                    continue

                # 本职业已无可锻造 → 记完成 → 切下一个职业
                # v2.3.39：完成态横幅是明确信号，日志带出便于真机核对
                done_word = next((k for k in _W_CLASS_DONE
                                  if k in blob), '无锻造按钮')
                if cur_class:
                    done_classes.add(cur_class)
                    self.log(f"[成功] {cur_class}装备已无可锻造"
                             f"（{done_word}；{len(done_classes)}/6 职业完成）")
                no_progress = 0
                onekey_streak = 0
                nxt = next((c for c in CLASS_ORDER
                            if c not in done_classes), None)
                if nxt is None:
                    self.log(f"[成功] 六职业全部锻造完成，累计 {forged} 件")
                    return 'done'
                if self._switch_class(nxt):
                    cur_class = nxt
                    clicks += 1
                    stall = 0
                    self.log(f"[信息] 切换职业 → {nxt}")
                    self._sleep(_LOOP_SLEEP)
                    continue
                self.log(f"[错误] 切换职业「{nxt}」失败（模板未命中）")
                return 'failed'

            # ④ 赛季职业装备主界面 → 点圆形图标进入第一个待办职业
            if all(k in blob for k in _W_MAIN):
                nxt = next((c for c in CLASS_ORDER
                            if c not in done_classes), None)
                if nxt is None:
                    self.log("[成功] 六职业全部完成")
                    return 'done'
                if self._enter_class_from_main(nxt):
                    cur_class = nxt
                    clicks += 1
                    stall = 0
                    self.log(f"[信息] 从主界面进入 {nxt}装备")
                    self._sleep(1.5)
                    continue
                # 模板未命中 → 按规则不得改用 OCR 猜同一目标
                self.log(f"[错误] 主界面圆形图标模板未命中（{nxt}），"
                         "符号类元素不改用 OCR 兜底")
                stall += 1

            # ⑤ 英雄厅堂入口页 → 点「赛季装备」
            elif any(_W_ENTRY in t for t in texts) and any(
                    _W_HERO_HALL in t for t in texts):
                pos = self._find_text_exact(items, _W_ENTRY)
                if pos:
                    self.bot.click_at(pos[0], pos[1], '赛季装备_入口')
                    clicks += 1
                    stall = 0
                    self._sleep(2.0)
                    continue
                stall += 1

            # ⑥ 完成态兜底：稳定词漏读但横幅明确（真机 17:33 现场）
            elif (any(k in blob for k in _W_CLASS_DONE)
                  and any('装备' in t for t in texts)):
                if cur_class:
                    done_classes.add(cur_class)
                    self.log(f"[成功] {cur_class}装备已无可锻造"
                             f"（完成横幅；{len(done_classes)}/6 职业完成）")
                nxt = next((c for c in CLASS_ORDER
                            if c not in done_classes), None)
                if nxt is None:
                    self.log(f"[成功] 六职业全部锻造完成，累计 {forged} 件")
                    return 'done'
                if self._switch_class(nxt):
                    cur_class = nxt
                    clicks += 1
                    stall = 0
                    self.log(f"[信息] 切换职业 → {nxt}")
                    self._sleep(_LOOP_SLEEP)
                    continue
                self.log(f"[错误] 切换职业「{nxt}」失败（模板未命中）")
                return 'failed'

            # ⑦ 未知 / 动画过渡
            else:
                stall += 1

            if stall == 1 or stall % 5 == 0:
                self.log(f"[信息] 等待画面就绪…（{stall}/{_STALL_MAX}）")
            if stall >= _STALL_MAX:
                self.log(f"[错误] 连续 {_STALL_MAX} 帧未识别到可操作界面")
                return 'failed'
            self._sleep(_LOOP_SLEEP)

    # ── 定位辅助 ────────────────────────────────────────────

    @staticmethod
    def _find_text_exact(items: List[Tuple], word: str) -> Optional[Tuple[int, int]]:
        """精确词匹配，返回 (x, y) 图像坐标；避开「锻造等级上限」等长词。"""
        best = None
        for it in items:
            if len(it) < 4 or it[3] < 0.6 or str(it[2]).strip() != word:
                continue
            p = (it[1], it[0])
            if best is None or p[1] < best[1]:
                best = p
        return best

    def _detect_class(self, items: List[Tuple]) -> Optional[str]:
        """从「XX装备」标题认当前职业（OCR 常拆成「辅」「助装备」）。"""
        blob = ' '.join(self._texts(items, 0.5))
        for zh in CLASS_ORDER:
            if f'{zh}装备' in blob or (len(zh) > 1 and zh[1:] + '装备' in blob):
                return zh
        return None

    def _card_forge_pos(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """职业页 6 件装备下方的「锻造」按钮（ry 限定，取最左一件）。"""
        cands = []
        for it in items:
            if len(it) < 4 or it[3] < 0.6 or str(it[2]).strip() != _W_FORGE:
                continue
            ry = it[0] / _IMG_H
            if _CARD_FORGE_RY[0] <= ry <= _CARD_FORGE_RY[1]:
                cands.append((it[1], it[0]))
        return min(cands, key=lambda p: p[0]) if cands else None

    def _dialog_forge_pos(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """弹窗内「锻造」按钮（ry < 0.72）；背景卡片按钮 ry≈0.87 排除。"""
        cands = [(it[1], it[0]) for it in items
                 if len(it) >= 4 and it[3] >= 0.6
                 and str(it[2]).strip() == _W_FORGE
                 and it[0] / _IMG_H < _DIALOG_FORGE_RY_MAX]
        return min(cands, key=lambda p: p[1]) if cands else None

    # ── 模板定位（纯图标元素，按用户规则必须用模板）──────────

    def _region(self, ratio: Tuple[float, float, float, float]
                ) -> Optional[Tuple[int, int, int, int]]:
        try:
            fn = getattr(self.bot, 'window_rect', None)
            if fn is None:
                return None
            l, t, w, h = fn()
            if w <= 0 or h <= 0:
                return None
            rx, ry, rw, rh = ratio
            return (int(l + w * rx), int(t + h * ry),
                    int(w * rw), int(h * rh))
        except Exception:                                  # noqa: BLE001
            return None

    def _match_class(self, zh: str, main: bool = False
                     ) -> Optional[Tuple[int, int]]:
        """模板匹配职业圆形图标，返回屏幕绝对坐标（供 _click_abs）。"""
        tpl = CLASS_TPL.get(zh)
        if not tpl:
            return None
        ratio = _MAIN_ICON_REGION_RATIO if main else _TAB_REGION_RATIO
        thr = _MAIN_ICON_THRESHOLD if main else _TPL_THRESHOLD
        scale = _MAIN_ICON_SCALE_RANGE if main else _TPL_SCALE_RANGE
        region = self._region(ratio)
        for r in (region, None):        # 局部优先，未命中回退全屏
            try:
                pos = self.bot.template_match(
                    tpl, threshold=thr, silent=True,
                    scale_range=scale, region=r)
            except TypeError:           # 老 MockBot 桩无 region 参数
                pos = self.bot.template_match(
                    tpl, threshold=thr, silent=True, scale_range=scale)
            if pos:
                return pos
            if r is None:
                break
        return None

    def _switch_class(self, zh: str) -> bool:
        """底部 tab 切职业（纯图标无文字）→ 模板 + _click_abs。"""
        pos = self._match_class(zh, main=False)
        if not pos:
            self.log(f"[警告] 职业 tab 模板未命中：{zh}"
                     "（检索范围=底部 tab 区+全屏；未匹配原因=图标未出现）")
            return False
        self.bot._click_abs(pos[0], pos[1])
        return True

    def _enter_class_from_main(self, zh: str) -> bool:
        """主界面大圆标进入职业（纯图案）→ 模板 + _click_abs。"""
        pos = self._match_class(zh, main=True)
        if not pos:
            self.log(f"[警告] 主界面圆标模板未命中：{zh}"
                     "（检索范围=主界面中部+全屏；符号类不改用 OCR 猜）")
            return False
        self.bot._click_abs(pos[0], pos[1])
        return True


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="⚒️",
    group="游戏任务",
    status="ready",
    options_schema=SeasonEquipmentTask.options_schema,
    nav_target='',           # 自导航（英雄厅堂非 NAV_TARGETS 目标）
    oneshot=True,
))
