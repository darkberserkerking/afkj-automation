"""一键家园任务（v1.9.0 立项）。

功能一「愿望单」（v1.9.1，v1.9.6 起为必选项，无勾选开关）：家园左侧愿望单按钮 → 循环
「一键选择 → 交付 → 点空白关闭」，直到左上角计数达目标单数。
功能二「消耗体力」：已实装。

界面事实（真机截图 2026-09-09，家园/*.png）：
  - 家园左侧愿望单按钮：竖长木牌（金星+「愿望单」金字），模板
    templates/btn_home_wishlist.png（maskedNCC 自匹配 1.000，
    家园界面交叉验证 0.962 @(42,642)）
  - 愿望单界面：左上标题「愿望单」+ 左上进度 N/5 + 底部大按钮
    （未选择=绿色「一键选择」→ 已选择=橙色「交付」）
  - 交付后弹「完成愿望单」结算页 →「点击空白处关闭」
导航事实：
  - 判据：screen_registry `nav.world_home`（神秘屋+玩法目录+英雄厅堂
    三词同现、无「庇护之地」——真机 家园.png 实测 conf≥0.97）
  - 目标：global_nav `NAV_TARGETS['world_home'] = (0, '')`，0 层无
    卡片入口，从任意层逐层 ESC/返回按钮退回即可（最短路径）
"""

import re
import time
import numpy as np
from typing import Dict, List, Optional, Tuple

from tasks.base import BaseTask
from task_registry import TaskRegistry, TaskMeta

task_id = "home"
label = "一键家园"
description = "愿望单自动刷单；可勾选消耗体力"

# 愿望单入口按钮模板（templates/ 下，facade 自动解析路径）
_WISHLIST_BTN_TPL: str = 'btn_home_wishlist.png'
# v1.9.13：愿望单需求悬浮窗内的「>」（前往获取）按钮模板——
# maskedNCC 自匹配 1.000，点击后.png 交叉验证 1.000 @(656,449)
_WISHLIST_NEXT_TPL: str = 'btn_wishlist_next.png'
_WISHLIST_NEXT_THRESHOLD: float = 0.72
_WISHLIST_NEXT_SCALE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
# 需求图标中心相对其 N/M 标签的向上偏移（截图实测 70px）
_DEMAND_ICON_DY: int = 70
# v1.9.14：工坊消耗段常量
_WORKSHOP_NAMES: Tuple[str, ...] = ('厨房', '炼金工坊', '锻造工坊',
                                    '木材加工厂')
_WORKSHOP_START_KW: Dict[str, str] = {
    '厨房': '开始烹饪', '炼金工坊': '开始炼金', '锻造工坊': '开始锻造',
    '木材加工厂': '开始生产',   # v1.9.18：材料获取途径建筑
}
_WORKSHOP_WAIT: float = 180.0        # 点「>」后自动寻路等待上限
_WORKSHOP_TPL_THRESHOLD: float = 0.72
_WORKSHOP_TPL_SCALE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
_WORKSHOP_AUTO_ON_TPL: str = 'btn_workshop_auto_on.png'
_WORKSHOP_AUTO_OFF_TPL: str = 'btn_workshop_auto_off.png'
_WORKSHOP_X10_TPL: str = 'btn_workshop_x10.png'
_WORKSHOP_X5_TPL: str = 'btn_workshop_x5.png'
_WORKSHOP_X1_TPL: str = 'btn_workshop_x1.png'
# v1.9.17：资源不足弹窗「>」按钮（跳转获取途径）
_RES_SHORT_NEXT_TPL: str = 'btn_wishlist_res_short.png'
_RES_SHORT_THRESHOLD: float = 0.72
_RES_SHORT_SCALE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
_WORKSHOP_STALL_MAX: int = 8         # 工坊段无进展帧兜底
# v2.2.11 注：弹窗内「>」按钮**有专用模板** `btn_wishlist_res_short.png`
# （v1.9.17 采自同一弹窗，maskedNCC）→ **只用模板定位**，不加坐标兜底。
# 理由（用户 2026-09-16 提醒 + 本项目既有原则）：模板没命中就说明 UI 变了，
# 此时任何"实测偏移"同样失效 → 盲点会点到错位置；项目 v1.8.21 也正是为此
# 删掉了「固定坐标中央兜底」。定位失败就如实告警、下一轮重试，不盲点。
# 愿望单里「资源不足→点>→制作」的处理轮次上限（防材料真做不出时无限乒乓）
_WISHLIST_RES_SHORT_MAX: int = 6
_CRAFT_BATCH_WAIT: float = 300.0     # 单批制作（x10）等待上限
_WISHLIST_BTN_THRESHOLD: float = 0.70
# UI 按钮尺寸波动小 → 缩放带收窄 6 档（v1.8.92 同经验）
_WISHLIST_BTN_SCALE: Tuple[float, float, float] = (0.8, 1.3, 0.1)
_WISHLIST_TARGET_DEFAULT: int = 70
# 连续无进展帧数兜底（≈6s）：点空白关闭可能存在的弹层
_WISHLIST_STALL_MAX: int = 6


class HomeTask(BaseTask):
    task_id = task_id

    # v2.1.9（用户 22:35 规范）循环语义声明——愿望单/材料不足弹窗均可续跑
    loop_screens = frozenset({'home.wishlist', 'home.res_short'})
    label = label
    description = description

    # 参数声明 → GUI 自动生成勾选/表单（手册 §3）
    options_schema = {
        'spend_stamina': {
            'type': 'bool',
            'label': '消耗体力',
            'default': False,
            'desc': '刷完愿望单后自动消耗体力',
        },
        'keep_stamina': {
            'type': 'int',
            'label': '保留体力',
            'default': 0,
            'desc': '体力保留值（0~5000），勾选消耗体力后生效',
        },
    }

    def _on_wishlist_page_items(self, items: List[Tuple]) -> bool:
        """愿望单界面判定：标题「愿望单」+ 按钮词（一键选择/交付）
        或「愿望单需求」——家园界面的按钮金字也含「愿望单」，单看
        标题会撞词（v1.9.1 真机截图修正）。"""
        texts: List[str] = [
            it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
        ]
        return any('愿望单' in t for t in texts) and (
            any('一键选择' in t for t in texts)
            or any(t.strip() == '交付' for t in texts)
            or any('愿望单需求' in t for t in texts))

    def _dismiss_daily_reward(self, items: List[Tuple]) -> bool:
        """关闭「每日奖励」悬浮窗（点击星数位置后可能弹出）。

        窗体在画面左侧，含「每日奖励」标题与「重置倒计时」——双词
        同现防误判。关闭方式 = 点击悬浮窗外任意位置（右侧空白），
        窗体无「x」按钮。
        """
        texts: List[str] = [
            it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
        ]
        if not ('每日奖励' in texts or any('每日奖励' in t for t in texts)):
            return False
        if not any('重置倒计时' in t for t in texts):
            return False
        self.log("[信息] 检测到「每日奖励」悬浮窗，点击窗外关闭")
        self.bot.click_ratio(0.72, 0.50, label='愿望单_关每日奖励')
        return True

    # ── 启动探查（BaseTask 契约强制覆写，手册 §3）─────────────

    def startup_probe(self) -> str:
        """OCR 一帧判定是否已在家园（v1.8.7 契约：三态）。

        Returns:
            'ready'  已在家园界面（直接进入功能流程）
            'clear'  未在家园且无残留弹窗 → 走导航
        """
        items = self.bot._ocr_screen()
        from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY
        screen: str = GLOBAL_SCREEN_REGISTRY.detect(items)
        # v1.9.18：记录启动画面（run() 据此分流，不额外消耗 OCR 帧）
        self._startup_screen: str = screen
        if screen == 'nav.world_home':
            self.log("[信息] 启动即在家园界面，无需导航")
            return 'ready'
        # v1.9.10（真机 19:16）：愿望单界面内启动也应直通——此前
        # 判 clear 走全局导航，一路 unknown/脱离，绕一大圈回家园
        if self._on_wishlist_page_items(items):
            self.log("[信息] 启动即在愿望单界面，直接续跑")
            return 'ready'
        # v1.9.18（真机 23:07）：资源不足弹窗画面内启动也直通——
        # 此前判 unknown 满屏 ESC 脱离，又绕回弹窗死循环
        if screen == 'home.res_short':
            self.log("[信息] 启动即在资源不足弹窗，直接进入消耗体力")
            return 'ready'
        # v1.9.12（真机 20:01）：启动时若停在「获得奖励/点击空白处关闭」
        # 这类弹层上，清掉弹层后**必须重新判一帧**——弹层背后往往就是
        # 愿望单界面，旧逻辑清完直接返回 clear，白跑一趟全局导航。
        if self.dismiss_blink_popup(items):
            items = self.bot._ocr_screen()
            screen = GLOBAL_SCREEN_REGISTRY.detect(items)
            if screen == 'nav.world_home':
                self.log("[信息] 关闭弹层后在家园界面，无需导航")
                return 'ready'
            if screen == 'home.wishlist' or self._on_wishlist_page_items(items):
                self.log("[信息] 关闭弹层后在愿望单界面，直接续跑")
                return 'ready'
        return 'clear'

    # ── 任务主体 ───────────────────────────────────────────

    def summary(self) -> str:
        """任务行摘要（v1.9.67：核心功能；路径详情见抽屉说明）。"""
        return "刷愿望单 + 消耗体力（一键家园）"

    def run(self) -> str:
        """一键家园主体：进入家园 → 按勾选执行功能（愿望单…）。

        阶段 0：startup_probe——已在家园直接进功能；
        阶段 1：全局导航到 world_home（0 层，逐层退回）；
        阶段 2：功能分流（v1.9.1：愿望单）。
        """
        self.log("启动（一键家园）")

        probe: str = self.startup_probe()
        if probe != 'ready':
            from tasks.global_nav import GlobalNavigator
            nav = GlobalNavigator(
                self.bot, logger=self.log, stop_check=self.check_stop,
            )
            ok: bool = nav.navigate_to('world_home')
            if self.check_stop():
                return self._stop_result()
            if not ok:
                self.log_nav_failed("家园界面",
                                     reached=getattr(nav, 'last_screen', ''))
                return 'failed'
            self.log("[成功] 已进入家园界面")

        # v1.9.18（真机 23:07）：启动画面在资源不足弹窗（材料耗尽场景）
        # → 跳过愿望单（已 70 星），直接进入消耗体力流程
        # （画面标识复用 startup_probe 的探测帧，零额外 OCR）
        if getattr(self, '_startup_screen', '') == 'home.res_short':
            self.log("[信息] 当前在资源不足弹窗，直接进入消耗体力流程")
            return self._spend_stamina_flow()

        # 功能分流（v1.9.14：愿望单/消耗体力已实装）
        # v1.9.6：愿望单为必选（无开关，始终执行）
        r: str = self._wishlist_flow()
        if r != 'done':
            return r
        # v1.9.13（用户定案顺序）：先刷愿望单，再消耗体力
        if self.options.get('spend_stamina'):
            return self._spend_stamina_flow()
        return 'done'

    # ── 功能一：愿望单（v1.9.1）────────────────────────────

    def _click_wishlist_demand_icon(self, items: List[Tuple]) -> bool:
        """点击「愿望单需求」栏内的需求图标（消耗体力入口）。

        定位方案（OCR 数字标签锚定，v1.9.13）：
          1. OCR 找「愿望单需求」标题 → 需求栏 y 基准；
          2. 标题下方区域（y+20 ~ y+280）内收集 N/M 分式——需求图标
             正下方都有 N/M 标签（1/2/3 个图标各有 1/2/3 个标签）；
             标题 y 过滤天然排除左上角星数 70/70（在其上方）；
          3. 取最左一个标签，点击其正上方 _DEMAND_ICON_DY(70px)
             处 = 图标中心（截图实测偏移）。

        Returns:
            bool: 命中并点击返回 True；找不到标题/标签返回 False
                  （交由调用方下一帧重试）
        """
        title: Optional[Tuple] = None
        for it in items:
            if (len(it) >= 4 and it[3] >= 0.6
                    and '愿望单需求' in it[2]):
                title = it
                break
        if title is None:
            return False
        y0: int = int(title[0])
        tags: List[Tuple[int, int]] = []
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            t = it[2].strip().replace('O', '0').replace('o', '0')
            if not re.fullmatch(r"[0-9]{1}\s*/\s*[0-9]{1,2}", t):
                continue
            # 区域过滤：标题下方 20~280px 内（星数 70/70 在上方被排除）
            if y0 + 20 < it[0] < y0 + 280:
                tags.append((int(it[1]), int(it[0])))
        if not tags:
            return False
        tags.sort()
        tx, ty = tags[0]
        ix, iy = tx, ty - _DEMAND_ICON_DY
        self.log(f"[点击] 需求图标（标签 {tx},{ty}） | 图像=({ix},{iy})")
        self.bot.click_at(ix, iy, '愿望单_需求图标')
        return True

    def _auto_mode_is_on(self, sx: int, sy: int) -> bool:
        """判别工坊「自动」图标是否为开启（绿色）状态。

        auto_on/off 两模板形状相同仅底色不同，maskedNCC 灰度化后
        无法区分（实测 0.98 vs 0.93）→ 模板只用于定位，状态用
        颜色采样：图标中心区域绿色像素占比 > 30% = 开启
        （实测 绿自动 79~85% / 灰自动 0%）。
        """
        img, cl, ct, cw, chh = self.bot.screen_image()
        arr = np.asarray(img)[:, :, ::-1]   # PIL RGB → BGR
        half = 22
        sub = arr[max(0, sy - ct - half):sy - ct + half,
                  max(0, sx - cl - half):sx - cl + half]
        if sub.size == 0:
            return True    # 采不到样（异常）→ 视为已自动，不乱点
        b = sub[:, :, 0].astype(int)
        g = sub[:, :, 1].astype(int)
        r = sub[:, :, 2].astype(int)
        green = (g > 100) & (g > r + 20) & (g > b + 20)
        return green.mean() > 0.30

    def _read_stamina(self, items: List[Tuple]) -> Optional[int]:
        """读右上角剩余体力值（v1.9.24：实时监控到 0 收尾）。

        界面右上角显示「N/5000+」，OCR conf≈0.97；ry<0.06 区域
        内的分式即体力（剥掉尾部 + 号）。
        """
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            ry = it[0] / 1024.0
            if ry >= 0.06:
                continue
            t = it[2].strip().replace('O', '0')
            t = t.lstrip('+').rstrip('+ ').strip()
            m = re.fullmatch(r"(\d{1,5})\s*/\s*\d{3,5}", t)
            if m:
                return int(m.group(1))
        return None

    def _find_slider_pos(self) -> Optional[Tuple[int, int]]:
        """定位「消耗材料」滑块（绿色胶囊边框）屏幕坐标。

        颜色检测：滑条区域（y 0.75~0.90H，x 0.25~0.60W）内绿色
        像素列分布的质心 = 滑块中心。滑块数字会变化，不用模板。
        """
        img, cl, ct, cw, chh = self.bot.screen_image()
        arr = np.asarray(img)[:, :, ::-1]   # RGB → BGR
        y0, y1 = int(0.75 * chh), int(0.90 * chh)
        x0, x1 = int(0.25 * cw), int(0.60 * cw)
        sub = arr[y0:y1, x0:x1]
        b = sub[:, :, 0].astype(int)
        g = sub[:, :, 1].astype(int)
        r = sub[:, :, 2].astype(int)
        green = (g > 110) & (g > r + 25) & (g > b + 25)
        if green.sum() < 50:
            return None
        cols = green.sum(axis=0).astype(float)
        cx = int((cols * np.arange(len(cols))).sum()
                 / max(1, cols.sum())) + x0
        rows = green.sum(axis=1).astype(float)
        cy = int((rows * np.arange(len(rows))).sum()
                 / max(1, rows.sum())) + y0
        return (cl + cx, ct + cy)

    def _lumber_production(self) -> str:
        """木材加工厂单批材料加工（v1.9.22）。

        流程：拖滑块设数量（1万~3万）→ 点「加工」→ 弹「获得奖励
        （点击空白处关闭）」→ 关闭 → return 'recycle'（外层回愿望单
        重新点需求图标做产品，形成材料↔产品大循环）。
        """
        self.log("[信息] 木材加工厂：拖动滑块设定数量（目标 1万~3万）")
        batch: int = 0
        stall: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            # v1.9.24：体力实时监控（加工也消耗体力）
            stamina = self._read_stamina(items)
            keep3 = int(self.options.get('keep_stamina') or 0)
            if stamina is not None and stamina <= keep3:
                self.log(f"[成功] 体力已耗尽（右上角剩 {stamina}，保留 {keep3}），任务完成")
                return 'done'
            # v1.9.23：体力耗尽兜底（体力礼包弹窗）
            if any('体力七日礼包' in it[2] for it in items
                   if len(it) >= 4 and it[3] >= 0.5):
                self.log("[成功] 体力已耗尽（体力礼包弹窗），任务完成")
                self.dismiss_blink_popup(items)
                return 'done'
            # 点加工后弹「获得奖励（点击空白处关闭）」→ 关闭 → 单批完成
            if self.dismiss_blink_popup(items):
                self._sleep(1.0)
                self.log("[成功] 材料加工完成，返回愿望单继续制作")
                return 'recycle'
            # 加工按钮
            btn = self.bot.find_text(items, '加工', min_conf=0.6)
            if not btn:
                stall += 1
                if stall >= _WORKSHOP_STALL_MAX * 5:
                    self.log("[错误] 长时间未回到可加工状态")
                    return 'failed'
                self._sleep(2.0)
                continue
            stall = 0
            # v1.9.20b（用户纠正）：读数量 = **界面上显示的大字数字**——
            # ① 「获得材料」图标上的数量（y≈0.38~0.50H，白字大号）
            # ② 「加工 NNNN」气泡里的数字（y≈0.60~0.72H）
            # ③ 兜底：材料图标下方小字分式「18729/8012795」分子
            cur: Optional[int] = None
            for it in items:
                if len(it) < 4 or it[3] < 0.5:
                    continue
                t = it[2].strip().replace('O', '0').replace(',', '')
                ry = it[0] / 1024.0   # 相对 y（按 1024 高归一）
                m1 = re.fullmatch(r"(\d{4,7})", t)
                if m1 and 0.36 < ry < 0.52:
                    cur = int(m1.group(1))
                    continue
                m2 = re.search(r"加工\s*(\d{4,7})", t)
                if m2 and 0.58 < ry < 0.74:
                    cur = int(m2.group(1))
                if cur is None:
                    m3 = re.fullmatch(r"(\d{1,7})\s*/\s*\d{5,}", t)
                    if m3 and 0.55 < ry < 0.70:
                        cur = int(m3.group(1))
            if cur is None:
                # 读不到数字 → 小步拖一下触发刷新再读
                sp = self._find_slider_pos()
                if sp:
                    self.bot.drag_abs(sp[0], sp[1],
                                      sp[0] + 15, sp[1])
                stall += 1
                if stall >= _WORKSHOP_STALL_MAX * 3:
                    self.log("[错误] 无法读取滑块数量，任务失败")
                    return 'failed'
                self._sleep(1.0)
                continue
            if cur >= 10000:
                # 数量达标 → 点「加工」（下一轮检测奖励页）
                batch += 1
                self.log(f"[点击] 加工（第 {batch} 批，数量 {cur}）"
                         f" | 图像=({btn[0]},{btn[1]})")
                self.bot.click_at(btn[0], btn[1], '加工厂_加工')
                self._sleep(2.5)
                stall = 0
                continue
            # 未达标 → 拖滑块右移一步（步长随剩余差距缩小）
            sp = self._find_slider_pos()
            if sp is None:
                stall += 1
                if stall >= _WORKSHOP_STALL_MAX * 3:
                    self.log("[错误] 找不到滑块，任务失败")
                    return 'failed'
                self._sleep(1.0)
                continue
            step = 30 if cur < 5000 else 12
            self.log(f"[拖动] 滑块右移 {step}px（当前 {cur}）")
            self.bot.drag_abs(sp[0], sp[1], sp[0] + step, sp[1],
                              duration=0.3)
            self._sleep(0.8)
            stall = 0

    def _spend_stamina_flow(self) -> str:
        """消耗体力流程（v1.9.22 · 材料↔产品大循环）。

        外层循环（直到体力耗尽——判定待实装——或用户停止）：
          ① 一段：愿望单界面点需求图标 → 悬浮窗「>」（_segment1）；
          ② 二段：等寻路到达工坊（_wait_workshop）；
          ③ 木材加工厂 → 单批材料加工（_lumber_production）→
             'recycle' 回 ①（材料够了，重新点需求做产品）；
          ④ 锻造类 → 生产循环（_craft_loop：自动/x10/开始；材料
             耗尽弹窗 → 点「>」跳木材加工厂 → 加工 → 'recycle' 回 ①）。
        """
        self.log("[信息] 消耗体力流程启动")
        skip_seg1: bool = getattr(
            self, "_startup_screen", "") == "home.res_short"
        if skip_seg1:
            items = self.bot._ocr_screen()
            pos = self.bot.template_match(
                _RES_SHORT_NEXT_TPL, threshold=_RES_SHORT_THRESHOLD,
                silent=True, scale_range=_RES_SHORT_SCALE,
            )
            if pos:
                self.log(
                    f"[点击] 资源不足弹窗「>」按钮 | 屏幕=({pos[0]},{pos[1]})"
                )
                self.bot._click_abs(pos[0], pos[1])
                self._sleep(2.0)
        while True:
            if not skip_seg1:
                r1 = self._segment1_click_next()
                if r1 != 'next':
                    return r1
            skip_seg1 = False
            ws = self._wait_workshop_arrived()
            if ws in ('stopped', 'failed'):
                return ws
            self.log(f"[成功] 已到达 {ws}，开始消耗体力")
            if ws == '木材加工厂':
                r = self._lumber_production()
                if r != 'recycle':
                    return r
                continue          # 材料够了 → 回愿望单重新做产品
            r = self._craft_loop(_WORKSHOP_START_KW[ws])
            if r == 'recycle':
                continue          # 材料生产完 → 回愿望单重新做产品
            return r

    def _segment1_click_next(self) -> str:
        """一段：愿望单界面点需求图标 → 悬浮窗「>」。

        Returns:
            'next'    已点击「>」（进入寻路）
            'stopped' / 'failed'
        """
        stall: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            # 每日奖励悬浮窗可能遮挡需求栏 → 先关
            if self._dismiss_daily_reward(items):
                self._sleep(1.0)
                stall = 0
                continue
            if self._on_wishlist_page_items(items):
                if self._click_wishlist_demand_icon(items):
                    self._sleep(1.5)
                    pos = self.bot.template_match(
                        _WISHLIST_NEXT_TPL,
                        threshold=_WISHLIST_NEXT_THRESHOLD,
                        silent=True, scale_range=_WISHLIST_NEXT_SCALE,
                    )
                    if pos:
                        self.log(
                            f"[点击] 悬浮窗「>」按钮（模板 maskedNCC） | "
                            f"屏幕=({pos[0]},{pos[1]})"
                        )
                        self.bot._click_abs(pos[0], pos[1])
                        self._sleep(1.5)
                        return 'next'
                    self.log("[警告] 悬浮窗「>」按钮未命中，重试")
                stall += 1
            else:
                # 不在愿望单界面 → 模板点愿望单按钮返回
                pos = self.bot.template_match(
                    _WISHLIST_BTN_TPL,
                    threshold=_WISHLIST_BTN_THRESHOLD,
                    silent=True, scale_range=_WISHLIST_BTN_SCALE,
                )
                if pos:
                    self.bot._click_abs(pos[0], pos[1])
                    self._sleep(1.5)
                stall += 1
            if stall >= _WISHLIST_STALL_MAX:
                self.log("[错误] 消耗体力一段无进展，任务失败")
                return 'failed'
            self._sleep(1.0)

    def _wishlist_res_short(self, items: List[Tuple]) -> str:
        """愿望单「当前资源不足」弹窗 → 点「>」自动寻路去制作（v2.2.11）。

        与「消耗体力」流程**同源**（复用 `_wait_workshop_arrived` /
        `_lumber_production` / `_WORKSHOP_START_KW`），差别只在入口：
        消耗体力是开工时弹，愿望单是刷单途中弹。

        「>」定位：**只用模板** `btn_wishlist_res_short.png`（v1.9.17 采自
        同一弹窗）✓ —— 不加坐标兜底（模板没命中即 UI 已变，实测偏移同样
        失效；见上方常量处的说明）。未命中 → 告警并交下一轮重试。

        Returns:
            'ok'（已处理，回愿望单继续）| 'stopped' | 'failed'
        """
        pos = self.bot.template_match(
            _RES_SHORT_NEXT_TPL, threshold=_RES_SHORT_THRESHOLD,
            silent=True, scale_range=_RES_SHORT_SCALE,
        )
        if not pos:
            self.log("[警告] 资源不足弹窗「>」按钮未命中（模板 "
                     "btn_wishlist_res_short.png）→ 本轮跳过，等待下一轮")
            return 'ok'
        self.log(f"[点击] 资源不足弹窗「>」按钮（模板 maskedNCC）"
                 f" | 屏幕=({pos[0]},{pos[1]})")
        self.bot._click_abs(pos[0], pos[1])
        self._sleep(2.0)
        ws = self._wait_workshop_arrived()
        if ws in ('stopped', 'failed'):
            return ws
        self.log(f"[成功] 已到达 {ws}，开始制作材料")
        if ws == '木材加工厂':
            r = self._lumber_production()
            return 'ok' if r == 'recycle' else r
        btn_kw = _WORKSHOP_START_KW.get(ws)
        if btn_kw:
            p = self.bot.find_text(self.bot._ocr_screen(), btn_kw, min_conf=0.6)
            if p:
                self.log(f"[点击] 「{btn_kw}」")
                self.bot.click_at(p[0], p[1], f'工坊_{btn_kw}')
                self._sleep(2.0)
        return 'ok'

    def _wait_workshop_arrived(self) -> str:
        """二段：等自动寻路到达任一工坊/木材加工厂。

        Returns:
            工坊名（'厨房'/'炼金工坊'/'锻造工坊'/'木材加工厂'）、
            'stopped' 或 'failed'（超时）。
        """
        self.log("[信息] 自动寻路中，等待到达工坊…")
        deadline: float = time.time() + _WORKSHOP_WAIT
        while time.time() < deadline:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            texts: List[str] = [
                it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
            ]
            ws = next(
                (w for w in _WORKSHOP_NAMES if any(w in t for t in texts)),
                None,
            )
            if ws:
                return ws
            self._sleep(1.5)
        self.log(f"[错误] {_WORKSHOP_WAIT}s 内未到达任何工坊，任务失败")
        return 'failed'

    def _craft_loop(self, btn_kw: str) -> str:
        """三/四/五段：锻造类工坊生产循环（自动 → x10 → 开始XX）。

        材料耗尽弹窗 → 点「>」跳木材加工厂 → 单批加工 → 'recycle'
        （外层回愿望单重新做产品）。

        Args:
            btn_kw: 开始按钮词（开始烹饪/炼金/锻造）
        """
        batch: int = 0
        stall: int = 0
        wait_making: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            texts: List[str] = [
                it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
            ]

            # v1.9.24：体力实时监控——右上角读数为 0 → 任务完成
            stamina = self._read_stamina(items)
            keep = int(self.options.get('keep_stamina') or 0)
            if stamina is not None and stamina <= keep:
                self.log(f"[成功] 体力已耗尽（右上角剩 {stamina}，保留 {keep}），任务完成")
                return 'done'

            # 兜底：点开始后体力不足弹「体力七日礼包」充值窗
            if any('体力七日礼包' in t for t in texts):
                self.log("[成功] 体力已耗尽（体力礼包弹窗），任务完成")
                self.dismiss_blink_popup(items)
                return 'done'

            # 材料耗尽弹窗 → 点「>」→ 等到达木材加工厂 → 单批加工
            if any('当前资源不足' in t for t in texts):
                self.log("[成功] 检测到材料耗尽（资源不足弹窗）")
                pos = self.bot.template_match(
                    _RES_SHORT_NEXT_TPL, threshold=_RES_SHORT_THRESHOLD,
                    silent=True, scale_range=_RES_SHORT_SCALE,
                )
                if not pos:
                    self.log("[警告] 资源不足「>」按钮未命中，重试")
                    stall += 1
                    if stall >= _WORKSHOP_STALL_MAX:
                        self.log("[错误] 资源不足弹窗处理失败")
                        return 'failed'
                    self._sleep(1.0)
                    continue
                self.log(
                    f"[点击] 资源不足弹窗「>」按钮（模板 maskedNCC）"
                    f" | 屏幕=({pos[0]},{pos[1]})"
                )
                self.bot._click_abs(pos[0], pos[1])
                self._sleep(2.0)
                ws = self._wait_workshop_arrived()
                if ws in ('stopped', 'failed'):
                    return ws
                self.log(f"[成功] 已到达 {ws}，开始生产材料")
                if ws == '木材加工厂':
                    r = self._lumber_production()
                    if r != 'recycle':
                        return r
                    return 'recycle'   # 材料生产完 → 回愿望单做产品
                # 极少见：跳到的是锻造类工坊 → 换词继续生产
                btn_kw = _WORKSHOP_START_KW[ws]
                stall = 0
                wait_making = 0
                continue

            # 制作确认弹窗（库存够交愿望单时点开始会弹）→ 继续生产
            prod_pos = self.bot.find_text(items, '继续生产', min_conf=0.6)
            if prod_pos:
                self.log("[点击] 制作弹窗「继续生产」")
                self.bot.click_at(prod_pos[0], prod_pos[1], '工坊_继续生产')
                self._sleep(1.2)
                stall = 0
                continue

            # 三段：自动模式（auto_on 模板定位 + 颜色判状态）
            on_pos = self.bot.template_match(
                _WORKSHOP_AUTO_ON_TPL, threshold=_WORKSHOP_TPL_THRESHOLD,
                silent=True, scale_range=_WORKSHOP_TPL_SCALE,
            )
            if on_pos:
                auto_sx, auto_sy = on_pos
                if not self._auto_mode_is_on(auto_sx, auto_sy):
                    self.log("[点击] 「自动」为关闭状态，切换为自动")
                    self.bot._click_abs(auto_sx, auto_sy)
                    self._sleep(1.0)
                    stall = 0
                    continue
            else:
                stall += 1
                if stall >= _WORKSHOP_STALL_MAX:
                    self.log("[错误] 工坊界面识别异常，任务失败")
                    return 'failed'
                self._sleep(1.0)
                continue
            stall = 0

            # 四段：倍数检查（锚定 auto 同行右侧，分辨率无关）
            region = (auto_sx + 80, max(0, auto_sy - 70), 2000, 150)
            x10_pos = self.bot.template_match(
                _WORKSHOP_X10_TPL, threshold=_WORKSHOP_TPL_THRESHOLD,
                silent=True, scale_range=_WORKSHOP_TPL_SCALE, region=region,
            )
            if x10_pos:
                pass     # 已是 x10（最大），继续五段
            else:
                for _tpl in (_WORKSHOP_X5_TPL, _WORKSHOP_X1_TPL):
                    lo_pos = self.bot.template_match(
                        _tpl, threshold=_WORKSHOP_TPL_THRESHOLD,
                        silent=True, scale_range=_WORKSHOP_TPL_SCALE,
                        region=region,
                    )
                    if lo_pos:
                        self.log("[点击] 倍数非 x10，切换档位")
                        self.bot._click_abs(lo_pos[0], lo_pos[1])
                        self._sleep(0.8)
                        break
                else:
                    self.log("[警告] 倍数图标 x10/x5/x1 均未命中，重试")
                    stall += 1
                    if stall >= _WORKSHOP_STALL_MAX:
                        self.log("[错误] 倍数图标识别失败，任务失败")
                        return 'failed'
                    self._sleep(1.0)
                    continue
                continue     # 切档后重新确认

            # 五段：点「开始XX」
            btn_pos = self.bot.find_text(items, btn_kw, min_conf=0.6)
            if btn_pos:
                batch += 1
                self.log(f"[点击] {btn_kw}（第 {batch} 批） | "
                         f"图像=({btn_pos[0]},{btn_pos[1]})")
                self.bot.click_at(btn_pos[0], btn_pos[1], '工坊_开始')
                self._sleep(2.0)
                # 等待制作完成：按钮消失（制作中）→ 等它重现
                gone: bool = False
                wait_deadline = time.time() + _CRAFT_BATCH_WAIT
                while time.time() < wait_deadline:
                    if self.check_stop():
                        return self._stop_result()
                    it2 = self.bot._ocr_screen()
                    t2 = [x[2] for x in it2 if len(x) >= 4 and x[3] >= 0.5]
                    if any('体力七日礼包' in x for x in t2):
                        self.log("[成功] 体力已耗尽，任务完成")
                        self.dismiss_blink_popup(it2)
                        return 'done'
                    # v1.9.24：体力实时监控（制作中也读右上角）
                    st2 = self._read_stamina(it2)
                    keep2 = int(self.options.get('keep_stamina') or 0)
                    if st2 is not None and st2 <= keep2:
                        self.log(f"[成功] 体力已耗尽（右上角剩 {st2}，保留 {keep2}），任务完成")
                        return 'done'
                    if any('当前资源不足' in x for x in t2):
                        break   # 材料耗尽 → 交回主循环处理
                    if any('继续生产' in x for x in t2):
                        p2 = self.bot.find_text(it2, '继续生产', min_conf=0.6)
                        if p2:
                            self.bot.click_at(p2[0], p2[1], '工坊_继续生产')
                        self._sleep(1.2)
                        continue
                    if not any(btn_kw in x for x in t2):
                        gone = True       # 按钮消失 = 制作中
                    else:
                        if gone:
                            break         # 制作完成，按钮重现
                    self._sleep(2.0)
                stall = 0
                continue
            # 开始按钮不在（制作中）→ 等待重现
            wait_making += 1
            if wait_making % 5 == 1:
                self.log("[信息] 制作中，等待「" + btn_kw + "」按钮重现…")
            if wait_making >= _WORKSHOP_STALL_MAX * 4:
                self.log("[错误] 长时间未回到可制作状态，任务失败")
                return 'failed'
            self._sleep(2.0)

    def _wishlist_flow(self) -> str:
        """循环「一键选择 → 交付 → 点空白关闭」直到左上角计数达标。

        状态机（每帧 OCR 分流，全部为文字判据）：
          ① 「完成愿望单」结算页 → 点空白关闭（+1 单）
          ② 左上角进度 N/X ≥ 目标 → done
          ③ 底部大按钮「一键选择」（子串）/「交付」（全等，防撞
             「交付完成」类词）→ 点击
          ④ 无愿望单标题（还在家园）→ 模板点左侧愿望单按钮
          ⑤ 连续无进展兜底：点空白关闭可能的弹层
        """
        target: int = int(self.options.get('wishlist_target')
                          or _WISHLIST_TARGET_DEFAULT)
        self.log(f"[信息] 愿望单流程启动（目标 {target} 星）")
        stall: int = 0
        _res_short_tries: int = 0     # v2.2.11：资源不足弹窗处理轮次上限
        done_count: int = -1
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            texts: List[str] = [
                it[2] for it in items if len(it) >= 4 and it[3] >= 0.5
            ]

            # ① 完成结算页 → 点空白关闭
            if any('完成愿望单' in t for t in texts):
                done_count += 1 if done_count < 0 else 1
                self.log("[成功] 完成一单愿望单，点空白关闭")
                self.bot.click_ratio(0.5, 0.9, label='愿望单_点空白关闭')
                self._sleep(1.2)
                stall = 0
                continue

            # 界面判定（②⑤共用，v1.9.10 提为方法供 startup_probe 复用）
            on_wishlist_page = self._on_wishlist_page_items(items)

            # ② 进度判定——**仅在愿望单界面内读**（v1.9.7 真机 18:21 修复：
            # 家园界面右上角的体力 5000/5000 曾被当成进度 → 1s 假完成；
            # 目标为星数：完成愿望单得星，左上角 70/X（X 随进度 5→70））
            if on_wishlist_page:
                _pg_item = self._wishlist_progress_item(items)
                if _pg_item is not None:
                    progress: Optional[int] = int(
                        _pg_item[2].strip().replace("O", "0")
                        .replace("o", "0").split("/")[0].strip())
                    if progress >= target:
                        # v1.9.9（用户定案）：达标后点击星数所在位置
                        _pos = (_pg_item[1], _pg_item[0])
                        self.log(
                            f"[成功] 愿望单星数已达 {progress}"
                            f"（目标 {target}），点击星数位置"
                        )
                        self.bot.click_at(_pos[0], _pos[1],
                                          '愿望单_星数')
                        self._sleep(1.5)
                        # v1.9.10：点击星数会弹「获得奖励」页
                        self.dismiss_blink_popup()
                        # v1.9.16（真机 22:00）：也可能弹「每日奖励」
                        # 悬浮窗（今日累计交付列表）——点窗外关闭
                        it_r = self.bot._ocr_screen()
                        self._dismiss_daily_reward(it_r)
                        return 'done'

            # ③ 底部大按钮
            if any('一键选择' in t for t in texts):
                pos = self.bot.find_text(items, '一键选择', min_conf=0.6)
                if pos:
                    self.log(f"[点击] 「一键选择」 | 图像={pos}")
                    self.bot.click_at(pos[0], pos[1], '愿望单_一键选择')
                    self._sleep(1.2)
                    stall = 0
                    continue
            exact_deliver = next(
                ((it[1], it[0]) for it in items
                 if len(it) >= 4 and it[3] >= 0.6
                 and it[2].strip() == '交付'),
                None,
            )
            if exact_deliver:
                self.log(f"[点击] 「交付」 | 图像={exact_deliver}")
                self.bot.click_at(exact_deliver[0], exact_deliver[1],
                                  '愿望单_交付')
                self._sleep(1.5)
                stall = 0
                continue

            # ④ 非愿望单界面：
            # ④-a v1.9.47（用户 13:00 截图）：世界界面没有愿望单按钮
            # ——右下角「家园」按钮（rx>0.85, ry 0.80~0.95，OCR 0.99）
            # → 点它切到家园场景再找愿望单
            if not on_wishlist_page:
                home_btn = next(
                    ((it[1], it[0]) for it in items
                     if len(it) >= 4 and it[3] >= 0.8
                     and it[2].strip() == '家园'
                     and it[1] / 1280.0 > 0.85
                     and 0.80 < it[0] / 1024.0 < 0.95), None)
                if home_btn:
                    self.log("[点击] 右下角「家园」按钮（世界 → 家园）")
                    self.bot.click_at(home_btn[0], home_btn[1],
                                      '家园_切换按钮')
                    self._sleep(2.0)
                    stall = 0
                    continue
                pos = self.bot.template_match(
                    _WISHLIST_BTN_TPL,
                    threshold=_WISHLIST_BTN_THRESHOLD,
                    silent=True, scale_range=_WISHLIST_BTN_SCALE,
                )
                if pos:
                    self.log(
                        f"[点击] 愿望单按钮（模板 maskedNCC） | "
                        f"屏幕=({pos[0]},{pos[1]})"
                    )
                    self.bot._click_abs(pos[0], pos[1])
                    self._sleep(1.5)
                    stall = 0
                    continue
                ocr_pos = self.bot.find_text(items, '愿望单', min_conf=0.6)
                if ocr_pos:
                    self.log(
                        f"[点击] 愿望单按钮（OCR 兜底） | "
                        f"图像=({ocr_pos[0]},{ocr_pos[1]})"
                    )
                    self.bot.click_at(ocr_pos[0], ocr_pos[1],
                                      '愿望单_按钮_OCR')
                    self._sleep(1.5)
                    stall = 0
                    continue

            # ⑥ v2.2.11（用户 2026-09-16 08:24 真机）：**愿望单材料耗尽弹窗**
            # 「当前资源不足，可从以下途径获取」（行如「19级厨房制作获得」+
            # 右侧圆形「>」）→ 点「>」，由游戏自动寻路去制作。
            # 旧实现：本循环没有该分支 → 落到下面「无进展→点空白」，而
            # **空白关不掉这个弹窗** → 反复空转（真机 08:25:24）✗
            # 该弹窗的处理已存在于「消耗体力」流程 → 这里复用同一套助手。
            if any(len(it) >= 4 and it[3] >= 0.8 and '当前资源不足' in it[2]
                   for it in items):
                if _res_short_tries >= _WISHLIST_RES_SHORT_MAX:
                    self.log(f"[警告] 资源不足弹窗已处理 {_res_short_tries} "
                             "次仍复现（材料确实做不出？）→ 转常规兜底")
                else:
                    _res_short_tries += 1
                    r = self._wishlist_res_short(items)
                    if r in ('stopped', 'failed'):
                        return r
                    stall = 0
                    continue

            # ⑤ 无进展兜底：点空白关闭可能的弹层
            stall += 1
            if stall >= _WISHLIST_STALL_MAX:
                self.log("[警告] 愿望单界面无进展，点空白重试")
                self.bot.click_ratio(0.5, 0.9, label='愿望单_无进展_点空白')
                stall = 0
            self._sleep(1.0)

    @staticmethod
    def _wishlist_progress_item(items: List[Tuple]) -> Optional[Tuple]:
        """愿望单界面左上角的进度分式所在 OCR 项（含坐标）。

        v1.9.9：达标后需点击该位置（左上角 70/X）——返回 (y, x, text,
        conf)，供调用方取点击坐标；未找到返回 None。
        """
        cands: List[Tuple[int, int, Tuple]] = []
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            t = it[2].strip().replace("O", "0").replace("o", "0")
            if not re.fullmatch(r"[0-9]{1,4}\s*/\s*[0-9]{1,4}", t):
                continue
            try:
                num = int(t.split("/")[0].strip())
            except ValueError:
                continue
            cands.append((it[0], num, it))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0])
        return cands[0][2]

    @classmethod
    def _wishlist_progress(cls, items: List[Tuple]) -> Optional[int]:
        """读左上角进度分式 N/X 的分子（已完成单数）。

        取画面**最上方**的分式（左上角进度 N/5 在 y≈97，愿望单需求
        N/4 在 y≈571，卡片数字不带 / 不会撞）。OCR 把 0 读成 O 的
        情况一并容错。读不到 → None（不判停，安全方向）。
        """
        cands: List[Tuple[int, int]] = []
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            t = it[2].strip().replace('O', '0').replace('o', '0')
            if not re.fullmatch(r'[0-9]{1,4}\s*/\s*[0-9]{1,4}', t):
                continue
            try:
                num = int(t.split('/')[0].strip())
            except ValueError:
                continue
            cands.append((it[0], num))
        if not cands:
            return None
        cands.sort()
        return cands[0][1]

    # ── 停止结果语义 ──────────────────────────────────────
    # v1.9.0：暂无时间指标逻辑（后续扩展时按 labyrinth 的 max_time
    # 覆写模式补齐）

    def _stop_result(self) -> str:
        return "stopped"

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🏠",
    group="游戏任务",
    status="ready",
    options_schema=HomeTask.options_schema,   # GUI 抽屉渲染勾选/表单
    nav_target='',    # 0 层任务，无玩法目录入口卡片
    oneshot=True,     # v1.9.5：单次任务——GUI 隐藏时间/轮次输入（用户定案）
))
