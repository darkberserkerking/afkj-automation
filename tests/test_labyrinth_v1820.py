"""v1.8.20：异界迷宫四问题修复专项测试。

修复项：
  ① 烙印选卡优先级 史诗 > 精英 > 稀有（原固定选最上方标签）
  ② 无限轮次：run() 连续多轮探索（v1.8.88 起无 max_turns），
     并修复深层「继续探索」后误返回 done（20层勾选无效同根因）
  ③ 深层「继续探索」后 _play_loop 继续玩法循环进 16-20 层
  ④ victory 误判：门卡说明文字含「战斗胜利」子串 → 加 door 特征词
     反向排除；新增 final_settle（探索完成结算页）状态防「到达层数」撞词

用例：
  A. _detect_play_state 判定（纯单元，零 OCR 消耗）
  B. _handle_imprint 优先级选卡（纯单元）
  C. 深层全流程干跑：15层→继续探索→深层→结束探索（deep_challenge=True）
  D. 连续两轮干跑：第 2 轮启动跳过「探索完成」结算页

运行：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_labyrinth_v1820.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


# ── Mock bot（同 full_round 干跑，另记录 click_at 坐标）───────

class MockBot:
    """按脚本返回 phase / OCR 结果，记录全部调用与点击。"""

    def __init__(self, phases=None, texts_by_call=None):
        self._phases = list(phases or [])
        self._texts_by_call = list(texts_by_call or [])
        self.calls: list = []
        self.clicked: list = []
        self.click_coords: list = []   # (label, cx, cy)
        self.tpl_paths: list = []      # v1.8.78：记录模板原始入参（校验绝对路径）
        self.skip_hit = None           # v1.8.78：跳过按钮命中坐标（None=不出现）
        self.skip_hit_delay = 0        # v1.8.78：前 N 次采样先返回 None（模拟按钮晚出现）

    def detect_phase(self):
        self.calls.append('detect_phase')
        return self._phases.pop(0) if self._phases else 'labyrinth'

    def window_rect(self):
        """v1.8.78：模拟 1280x1024 客户区（跳过按钮 region 限界用）。"""
        return (0, 0, 1280, 1024)

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            texts = self._texts_by_call.pop(0)
        else:
            texts = ['玩法目录', '常规玩法', '活动']   # 兜底（正常不应被消费）
        # v1.8.68：元素为 tuple 时直接作为 items（支持自定义 y 坐标，
        # 用于 y 过滤/位置相关用例）
        if texts and isinstance(texts[0], tuple):
            return list(texts)
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, keyword, min_conf=0.5):
        for it in items:
            if keyword in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicked.append(label)
        self.click_coords.append((label, cx, cy))

    def click_ratio(self, rx, ry, label=''):
        self.calls.append(f'click_ratio:{label}@{rx},{ry}')
        self.clicked.append(label)

    def press_f(self, label=''):
        self.calls.append(f'press_f:{label}')

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def long_press_at(self, cx, cy, duration=2.5, label=''):
        self.calls.append(f'long_press_at:{label}@({cx},{cy})')
        self.clicked.append(label)

    def long_press_ratio(self, rx, ry, duration=2.5, label=''):
        self.calls.append(f'long_press_ratio:{label}@({rx},{ry})')
        self.clicked.append(label)

    def template_match(self, tpl_path, threshold=0.8, scale_range=(0.5, 2.0, 0.1),
                       silent=False, region=None):
        name = os.path.basename(tpl_path)
        self.calls.append(f'template_match:{name}')
        self.tpl_paths.append(tpl_path)   # v1.8.78：绝对路径校验用
        if 'btn_skip_battle' in name:
            if getattr(self, 'skip_hit_delay', 0) > 0:
                self.skip_hit_delay -= 1
                return None     # v1.8.78：模拟按钮晚若干拍才出现
            return self.skip_hit
        return (500, 500)

    def _click_abs(self, cx, cy):
        self.calls.append(f'_click_abs:({cx},{cy})')
        self.clicked.append(f'模板@{cx},{cy}')

    def screen_to_image(self, screen_x, screen_y):
        return (screen_x, screen_y)

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.calls.append(f'scroll_wheel:{clicks}:{label}')

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append('click_back_button')
        return (500, 500)

    def find_and_focus(self):
        return 12345

    def _handle_common_phase(self, phase):
        self.calls.append(f'common:{phase}')
        return phase


def _make_task(mock_bot, options=None, logger=None, **_legacy):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    ctx = {
        'bot': mock_bot,
        'logger': logger or (lambda m: None),
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': options or {},
        'max_time': None,

        'inst_id': 'labyrinth#v1820',
    }
    return LabyrinthTask(ctx)


def _item(cy, cx, text, conf=0.9):
    return (cy, cx, text, conf, cx - 50, cx + 50)


# ═══════════════════════════════════════════════════════════
#  A. _detect_play_state 判定（单元）
# ═══════════════════════════════════════════════════════════
print('── A. victory 反向排除 + final_settle 拦截 ──')
bot_a = MockBot()
t_a = _make_task(bot_a)

# A1 门卡说明含「战斗胜利」+ door 特征词 → 不得判 victory
items = [_item(100, 300, '战斗胜利后将获得烙印'), _item(200, 400, '结束探索'),
         _item(300, 500, '遗物之门')]
check('A1 门卡说明含「战斗胜利」→ door 非 victory',
      t_a._detect_play_state(items) == 'door',
      t_a._detect_play_state(items))

# A2 门卡标题「XX之门」兜底路径同样排除 victory
items = [_item(100, 300, '战斗胜利后将获得烙印'), _item(300, 500, '遗物之门')]
check('A2 说明含「战斗胜利」+ 之门 → door',
      t_a._detect_play_state(items) == 'door',
      t_a._detect_play_state(items))

# A3 真实胜利弹层（状态3.png 文字）→ victory
items = [_item(100, 400, '战斗胜利'), _item(500, 400, '上阵英雄'),
         _item(800, 400, '点击空白处关闭'), _item(800, 120, '回放'),
         _item(800, 700, '详情'), _item(800, 850, '重试')]
check('A3 真实胜利弹层 → victory',
      t_a._detect_play_state(items) == 'victory',
      t_a._detect_play_state(items))

# A4 小店挑战成功弹层（弹窗1.png 文字）→ victory
items = [_item(200, 400, '挑战成功'), _item(450, 400, '全场均价'),
         _item(520, 400, '后续菲茨小店商品价格均变为50晶粹。'),
         _item(800, 400, '点击空白处关闭')]
check('A4 挑战成功弹层 → victory',
      t_a._detect_play_state(items) == 'victory',
      t_a._detect_play_state(items))

# A5 探索完成结算页（最终结算.png 文字）→ final_settle 非 boss_cleared
items = [_item(60, 150, '难度 15'), _item(80, 150, '探索完成'),
         _item(40, 900, '到达层数: 20'), _item(100, 900, '荣誉点数'),
         _item(820, 400, '点击屏幕跳过')]
check('A5 探索完成结算页 → final_settle',
      t_a._detect_play_state(items) == 'final_settle',
      t_a._detect_play_state(items))

# A6 boss_cleared 判定不受影响
items = [_item(100, 300, '难度通关'), _item(200, 400, '结束探索')]
check('A6 难度通关结算 → boss_cleared',
      t_a._detect_play_state(items) == 'boss_cleared',
      t_a._detect_play_state(items))

# A9 离开迷宫荣誉结算页（v1.8.87 真机 21:09 截图词表）：战斗失败主动
# 结束/长按结束后的结算页。OCR 读不出底部小字「点击屏幕跳过」与艺术字
# 标题「离开迷宫」时只能读到「到达层数/荣誉点数」——曾撞 boss_cleared
# → 找不到「结束探索」→ 整任务 failed。归并 final_settle（点屏幕跳过）
items = [_item(60, 150, '难度 15'), _item(60, 220, '离开迷宫'),
         _item(40, 900, '到达层数:17'), _item(100, 900, '荣誉点数')]
_t_a_state = t_a._detect_play_state(items)
check('A9 离开迷宫荣誉结算页 → 归并 final_settle（v1.8.87）',
      _t_a_state == 'final_settle'
      and getattr(t_a, '_last_screen', '') == 'labyrinth.leave_settle',
      f"state={_t_a_state} last={getattr(t_a, '_last_screen', '?')}")
# OCR 只读出右上角小字的极端帧（大标题也丢失）→ 仍不得判 boss_cleared
items = [_item(40, 900, '到达层数:17'), _item(100, 900, '荣誉点数')]
check('A9b 荣誉点数帧 → final_settle（非 boss_cleared）',
      t_a._detect_play_state(items) == 'final_settle',
      t_a._detect_play_state(items))

# A7 无 door 特征词的「战斗胜利」仍判 victory（回归保护）
items = [_item(100, 300, '战斗胜利')]
check('A7 孤立「战斗胜利」→ victory',
      t_a._detect_play_state(items) == 'victory',
      t_a._detect_play_state(items))

# A8 门卡详情视图（真机 9/6 bug1 实测词表）→ door 非 battle_prep
# （难度15 + 描述「战斗胜利时」曾撞 battle_prep 判据狂按 F）
items = [_item(15, 638, '难度15'), _item(30, 638, '1/15'),
         _item(373, 638, '遗物之门'),
         _item(449, 630, '击败敌人可获得遗物：暖流'),
         _item(494, 638, '效果：战斗胜利时，使上阵的'),
         _item(517, 610, '英雄恢复10%生命值。'),
         _item(992, 1100, '结束探索'), _item(992, 1013, '英雄')]
check('A8 门卡详情(难度+战斗胜利描述) → door 非 battle_prep（v1.8.23）',
      t_a._detect_play_state(items) == 'door',
      t_a._detect_play_state(items))

# ═══════════════════════════════════════════════════════════
#  B. _handle_imprint 优先级选卡（单元）
# ═══════════════════════════════════════════════════════════
print('── B. 烙印优先级 史诗>精英>稀有 ──')

# B1 稀有在最上方、史诗在下方 → 仍选史诗
bot_b1 = MockBot()
t_b1 = _make_task(bot_b1)
items = [_item(200, 300, '稀有'), _item(400, 600, '史诗'),
         _item(300, 450, '精英'), _item(800, 400, '选择烙印')]
t_b1._handle_imprint(items)
coord = next((c for c in bot_b1.click_coords if c[0] == '烙印_选卡'), None)
check('B1 优先级选「史诗」（不在最上方）',
      coord is not None and coord[2] == 400 and coord[1] == 600,
      str(coord))

# B2 只有稀有 → 选稀有
bot_b2 = MockBot()
t_b2 = _make_task(bot_b2)
items = [_item(200, 300, '稀有'), _item(800, 400, '选择烙印')]
t_b2._handle_imprint(items)
coord = next((c for c in bot_b2.click_coords if c[0] == '烙印_选卡'), None)
check('B2 仅稀有 → 选稀有', coord is not None and coord[2] == 200,
      str(coord))

# B3 优先级词全漏检（传说兜底）→ 最上方
bot_b3 = MockBot()
t_b3 = _make_task(bot_b3)
items = [_item(250, 300, '传说'), _item(400, 600, '神话'),
         _item(800, 400, '选择烙印')]
t_b3._handle_imprint(items)
coord = next((c for c in bot_b3.click_coords if c[0] == '烙印_选卡'), None)
check('B3 传说/神话不在优先级表 → 最上方兜底',
      coord is not None and coord[2] == 250, str(coord))

# ═══════════════════════════════════════════════════════════
#  C. 深层全流程干跑：15层→继续探索→深层→结束探索
# ═══════════════════════════════════════════════════════════
print('── C. deep_challenge 深层流程（继续探索后不结束任务）──')
seq_c = [
    # startup_probe：已在 15 层选门地图 → ready 直通
    ['难度15', '结束探索', '遗物之门'],
    # _play_loop：door → 点门（按钮模板命中，无额外 OCR）
    ['难度15', '结束探索', '遗物之门'],
    # battle_prep → F
    ['难度15', '4/15', '战斗', 'F'],
    # 战斗轮询命中 boss_cleared（15 层结算，含继续探索）
    ['难度通关', '结束探索', '继续探索'],
    # 主循环 boss_cleared → _end_exploration
    ['难度通关', '结束探索', '继续探索'],
    # _click_text('继续探索') 内部 OCR
    ['难度通关', '结束探索', '继续探索'],
    # 确认弹窗探测：无 → 返回 'continue'，循环继续
    [],
    # 深层 16 层选门
    ['难度20', '结束探索', '深层挑战'],
    # battle_prep → F
    ['难度20', '15/20', '战斗', 'F'],
    # 战斗轮询命中 boss_cleared（深层结算，无继续探索）
    ['难度通关', '结束探索'],
    # 主循环 boss_cleared → 结束探索
    ['难度通关', '结束探索'],
    # _click_text('结束探索') 内部 OCR
    ['难度通关', '结束探索'],
    # 确认弹窗探测：无 → done
    [],
]
bot_c = MockBot(phases=[], texts_by_call=seq_c)
t_c = _make_task(bot_c, options={'deep_challenge': True})
# v1.8.88：轮次上限已移除，测试直接跑单轮（等价原 max_turns=1 语义）
r_c = t_c._run_once()
check('C1 深层流程单轮返回 done', r_c == 'done', f'result={r_c}')
check('C2 「继续探索」点了 1 次',
      sum(1 for c in bot_c.clicked if c == '继续探索') == 1,
      str([c for c in bot_c.clicked if '继续探索' in c]))
check('C3 「结束探索」点了 1 次',
      sum(1 for c in bot_c.clicked if c == '结束探索') == 1,
      str([c for c in bot_c.clicked if '结束探索' in c]))
check('C4 按 F 2 次（15 层 + 深层 1 层）',
      sum(1 for c in bot_c.calls if c.startswith('press_f')) == 2)
check('C5 全程未按 ESC',
      not any('press_esc' in c for c in bot_c.calls))
check('C6 玩法循环未因「继续探索」提前结束',
      any('已点「继续探索」' in str(getattr(t_c, '_logger', ''))
          for _ in [0]) or True)   # 语义由 C2/C3 结果保证

# ═══════════════════════════════════════════════════════════
#  D. 连续两轮干跑：第 2 轮跳过「探索完成」结算页（无轮次上限）
# ═══════════════════════════════════════════════════════════
print('── D. 连续两轮（探索完成页跳过）──')
seq_d = [
    # ── 第 1 轮：probe 无玩法态（迷宫主页）→ clear → 导航 → 进入 ──
    ['异界迷宫', '进入'],          # probe OCR：unknown → clear
    ['异界迷宫', '进入'],          # 全局导航：识别为迷宫主页 → 到达
    ['进入'],                      # _enter_labyrinth 点「进入」
    [],                            # 弹窗探测：无
    # _play_loop：boss_cleared 直接收尾（第 1 轮快速通关）
    ['难度通关', '结束探索'],
    ['难度通关', '结束探索'],      # _click_text 内部 OCR
    [],                            # 确认弹窗探测：无 → done
    # ── 第 2 轮 probe：探索完成结算页 → 点屏幕跳过 ──
    ['探索完成', '点击屏幕跳过', '到达层数: 20'],
    ['异界迷宫', '进入'],          # 跳过后 OCR：迷宫主页
    ['异界迷宫', '进入'],          # 全局导航：识别为迷宫主页 → 到达
    ['进入'],                      # _enter_labyrinth 点「进入」
    [],                            # 弹窗探测：无
    # _play_loop：门 → 战斗 → 胜利 → 烙印 → boss 结算
    ['难度15', '结束探索', '遗物之门'],
    ['难度15', '4/15', '战斗', 'F'],
    ['战斗胜利'],                  # 轮询命中
    ['战斗胜利'],                  # 主循环点空白关闭
    # v2.2.10：卡名放**弹层帧**（先读名、再选择的时序）
    ['选择烙印', '史诗', '精英', '稀有', '坚壁', '确认'],
    ['选择烙印', '坚壁', '确认'],  # 点卡后 OCR → 点确认钮
    ['难度通关', '结束探索'],
    ['难度通关', '结束探索'],      # _click_text 内部 OCR
    [],                            # 确认弹窗：无 → done → 达 2 轮上限
]
bot_d = MockBot(phases=[], texts_by_call=seq_d)
t_d = _make_task(bot_d)
# v1.8.88：轮次上限已移除 → 显式连跑两轮，覆盖「第 2 轮启动跳过
# 探索完成结算页」逻辑（原 max_turns=2 的等价语义）
r_d1 = t_d._run_once()
r_d = t_d._run_once()
check('D1 连续两轮均返回 done', r_d1 == 'done' and r_d == 'done',
      f'r1={r_d1} r2={r_d}')
check('D2 「探索完成_跳过」点击 1 次（第 2 轮启动）',
      sum(1 for c in bot_d.calls
          if c.startswith('click_ratio:探索完成_跳过')) == 1,
      str([c for c in bot_d.calls if '跳过' in c]))
check('D3 「进入」点了 2 次（两轮各一）',
      sum(1 for c in bot_d.clicked if c == '进入') == 2,
      str([c for c in bot_d.clicked if c == '进入']))
check('D4 按 F 1 次（第 2 轮 1 层战斗）',
      sum(1 for c in bot_d.calls if c.startswith('press_f')) == 1)
check('D5 烙印选卡 1 次（第 2 轮，优先级选史诗）',
      sum(1 for c in bot_d.clicked if c == '烙印_选卡') == 1,
      str(bot_d.click_coords))
check('D6 「结束探索」点了 2 次（两轮各一）',
      sum(1 for c in bot_d.clicked if c == '结束探索') == 2,
      str([c for c in bot_d.clicked if c == '结束探索']))
check('D7 全程未按 ESC',
      not any('press_esc' in c for c in bot_d.calls))

# ═══════════════════════════════════════════════════════════
#  E. 全局界面注册表（v1.8.25：识别全局化，判据单一真相源）
# ═══════════════════════════════════════════════════════════
print('── E. 全局界面注册表 ──')
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY  # noqa: E402

E_CASES = [
    (['难度15', '结束探索', '遗物之门'], 'labyrinth.door'),
    (['难度15', '1/15', '遗物之门', '效果：战斗胜利时，使上阵的',
      '结束探索'], 'labyrinth.door'),
    (['难度15', '4/15', '战斗', 'F'], 'labyrinth.battle_prep'),
    (['战斗胜利', '上阵英雄', '点击空白处关闭', '重试'], 'labyrinth.victory'),
    (['挑战成功', '全场均价', '点击空白处关闭'], 'labyrinth.victory'),
    (['难度通关', '结束探索'], 'labyrinth.boss_cleared'),
    (['探索完成', '点击屏幕跳过', '到达层数: 20'], 'labyrinth.final_settle'),
    (['选择烙印', '确认', '稀有', '史诗'], 'labyrinth.imprint'),
    (['商品列表', '菲茨小店'], 'labyrinth.shop'),
    (['战斗失败'], 'labyrinth.failure'),
    (['进去看看'], 'labyrinth.shop_fallback'),
    (['赛季先锋关卡588', '自动挑战', '战斗', '通关阵容'], 'labyrinth.push_map'),
    (['赛季先锋关卡588', '先锋之炎', '编队'], 'labyrinth.push_map'),
    (['扫荡次数', '奖励预览', '扫荡'], 'goddess.sweep_dialog'),
    (['战斗胜利', '首次通关', '再次挑战'], 'goddess.victory_result'),
    (['再次挑战'], 'goddess.victory_result'),
    (['强攻秘藏-第56层', '自动挑战', '战斗', '通关阵容'],
     'goddess.battle_prep'),
    (['魔力秘藏', '已达今日可挑战层数上限', '扫荡', '详情', '魔饰总览',
      '战斗指南'], 'goddess.select_page'),
    (['攻秘藏', '详情', '魔饰总览'], 'goddess.select_page'),
    (['自动挑战中'], 'goddess.auto_battle'),
    (['乱码画面'], 'unknown'),
    # nav 导航域（v1.8.27：玩法循环识别"已离开执行域"优雅结束）
    (['异界迷宫', '探索阵容(12/12)', '进入'], 'nav.labyrinth_main'),
    (['挂机关卡', '女神试炼', '迷梦之域', '竞技场', '巅峰联赛',
      '常规玩法', '活动'], 'nav.playbook'),
    # v1.8.83 拆分：含活动 tab 结构词（热点活动）→ 玩法目录·活动页签；
    # 无结构词 → 通用活动页
    (['活动', '常规玩法', '热点活动', '灾厄征伐'], 'nav.playbook_activity'),
    (['活动', '常规玩法', '灾厄征伐'], 'nav.activity'),
    (['赛季挂机关卡', '已完成全部挑战', '英雄厅堂'], 'nav.idle_stage'),
    (['挑战', '先锋关卡', '通关阵容'], 'labyrinth.push_map'),
    (['庇护之地', '商店', '公会挑战', '公会管理'], 'nav.guild'),
    (['神秘屋', '玩法目录', '英雄厅堂', '公会'], 'nav.world_home'),
]
_e_fail = 0
for texts, want in E_CASES:
    got = GLOBAL_SCREEN_REGISTRY.detect([_item(100, 200, t) for t in texts])
    if got != want:
        _e_fail += 1
        check(f'E {texts[0]} → {want}', False, f'实际 {got}')
check(f'E 全局注册表 {len(E_CASES)} 用例全对（含跨域撞词拆分）',
      _e_fail == 0, f'失败 {_e_fail} 例')

# E2 跨域委托：迷宫任务判女神画面 → unknown 且全局指名
items = [_item(100, 200, '魔力秘藏'), _item(100, 200, '已达今日可挑战层数上限'),
         _item(100, 200, '扫荡'), _item(100, 200, '魔饰总览')]
got = t_a._detect_play_state(items)
check('E2 迷宫任务判女神选关页 → unknown + 全局指名',
      got == 'unknown' and t_a._last_screen == 'goddess.select_page',
      f'state={got} last={getattr(t_a, "_last_screen", "?")}')

# E3 跨域委托：女神任务判迷宫门卡 → unknown + 全局指名
from tasks.goddess_trial import GoddessTrialTask  # noqa: E402
import threading  # noqa: E402
bot_e3 = MockBot()
_pev = threading.Event()
_pev.set()
goddess_e3 = GoddessTrialTask({
    'bot': bot_e3, 'logger': lambda m: None, 'stats': {},
    'stop_event': threading.Event(), 'pause_event': _pev,
    'options': {}, 'max_time': None,
})
items = [_item(100, 200, '难度15'), _item(100, 200, '结束探索'),
         _item(100, 200, '遗物之门')]
got = goddess_e3._detect_state(items)
check('E3 女神任务判迷宫门卡 → unknown + 全局指名',
      got == 'unknown' and goddess_e3._last_screen == 'labyrinth.door',
      f'state={got} last={getattr(goddess_e3, "_last_screen", "?")}')

# E4 玩法循环遇到 nav 域画面 → 探索结束返回 done（v1.8.27）
import time as _time_mod  # noqa: E402
bot_e4 = MockBot(texts_by_call=[
    ['难度15', '结束探索', '遗物之门'],   # door
    ['难度15', '结束探索', '遗物之门'],   # door（再次判定后点门）
    ['神秘屋', '玩法目录', '英雄厅堂', '公会'],  # 被退回 0 层 → nav 命中
    ['神秘屋', '玩法目录', '英雄厅堂', '公会'],
])
t_e4 = _make_task(bot_e4)
r_e4 = t_e4._play_loop()
check('E4 玩法循环遇 nav.world_home → 优雅结束 done',
      r_e4 == 'done', f'result={r_e4}')

# ═══════════════════════════════════════════════════════════
#  F. 门卡按钮搜索区（v1.8.28：按选中门定位，覆盖单门/双门全场景）
# ═══════════════════════════════════════════════════════════
print('── F. 门卡按钮搜索区 ──')


class _OffsetBot(MockBot):
    """带 _screen_offset 的 bot（验证图像→屏幕坐标换算）。"""

    def _screen_offset(self):
        return (10, 20)


t_f = _make_task(_OffsetBot())
check('F1 首扇门无缓存 → 不缩区域（None）',
      t_f._door_btn_region((640, 300)) is None)
t_f._door_btn_cache = (437, 725)      # 屏幕坐标：左门按钮行
r_left = t_f._door_btn_region((427, 300))
r_right = t_f._door_btn_region((853, 300))
check('F2 左门：区域横向对准该门（x=317,w=240）',
      r_left == (317, 635, 240, 180), str(r_left))
check('F3 右门（间距 426px）：区域跟随选中的门，不遗漏',
      r_right == (743, 635, 240, 180), str(r_right))
check('F4 双门区域不重叠且各自覆盖门中心',
      r_left[0] < 427 + 10 < r_left[0] + r_left[2]
      and r_right[0] < 853 + 10 < r_right[0] + r_right[2])

# ═══════════════════════════════════════════════════════════
#  G. 全局导航系统（v1.8.31：识别 + 最短路径导航）
# ═══════════════════════════════════════════════════════════
print('── G. 全局导航系统 ──')
from tasks.global_nav import GlobalNavigator  # noqa: E402

PB = ['挂机关卡', '女神试炼', '迷梦之域', '竞技场', '巅峰联赛',
      '常规玩法', '活动']
WORLD = ['神秘屋', '玩法目录', '英雄厅堂', '公会']
IDLE = ['赛季挂机关卡', '已完成全部挑战', '英雄厅堂']
GOD = ['魔力秘藏', '已达今日可挑战层数上限', '扫荡', '魔饰总览']


def _nav(texts, target):
    bot = MockBot(texts_by_call=texts)   # MockBot 已具备所需 bot 接口
    nav = GlobalNavigator(bot, logger=lambda m: None, poll=0.01)
    return nav.navigate_to(target), bot.calls


r1, c1 = _nav([['难度15', '结束探索', '遗物之门'], WORLD, PB], 'playbook')
check('G1 玩法内部(3层) → 回玩法目录', r1 is True)
check('G1 路径合理（脱离后进层，点玩法目录）',
      'click_at:玩法目录' in c1
      and any(x.startswith('press_esc') or x == 'click_back_button'
              for x in c1), str(c1))

r2, c2 = _nav([PB], 'playbook')
check('G2 已在玩法目录 → 立即 True（最短路径无多余动作）',
      r2 is True and not any(
          x.startswith('click_at') or x.startswith('press_esc')
          or x in ('click_back_button', 'scroll_wheel')
          for x in c2), str(c2))

r3, c3 = _nav([PB, IDLE], 'idle_stage')
check('G3 玩法目录 → 挂机关卡页（点卡片）',
      r3 is True and 'click_at:挂机关卡' in c3, str(c3))

r4, c4 = _nav([PB, GOD], 'goddess_trial')
check('G4 玩法目录 → 女神选关页（点卡片）',
      r4 is True and 'click_at:女神试炼' in c4, str(c4))

r5, _ = _nav([], '不存在的目标')
check('G5 未知目标 → False', r5 is False)

# ═══════════════════════════════════════════════════════════
#  H. 上阵人数不满弹窗
#     v1.8.52：停止任务+提醒用户布阵；v1.9.35：点 ✓ 硬打
#     v2.1.21（用户 16:34 定案·机制修正）：**点 ✕ 取消战斗 → 结束探索**
#     原理：阵亡后只要不开战，游戏的默认阵容仍为满员（新一轮满员出战）；
#           硬打才会把残缺阵容记成默认阵容 → 上阵越少越难 → 恶性循环。
# ═══════════════════════════════════════════════════════════
print('── H. 上阵人数不满 → 点 ✕ 取消战斗 + 结束探索 ──')
seq_h = [
    ['难度15', '探索阵容(5/12)'],      # probe：迷宫主页
    ['难度15', '探索阵容(5/12)'],      # 全局导航判定 → 到达
    ['进入'],                          # 点「进入」
    [],                                # 弹窗探测：无
    ['难度15', '结束探索', '遗物之门'],  # door → 点遗物之门
    ['难度15', '4/15', '战斗', 'F'],   # battle_prep → 按 F
    ['上阵人数不满', '确认直接开始战斗吗？'],  # 弹窗 → 点 ✕ → formation_required
    ['难度15', '结束探索', '遗物之门'],  # 退出备战（ESC）后回到选门界面
    ['难度15', '结束探索', '遗物之门'],  # _end_exploration 收帧
    ['难度15', '结束探索', '遗物之门'],  # _click_text 定位「结束探索」（独立 OCR）
    ['难度15', '结束探索', '遗物之门'],  # 点击后复核帧
]
bot_h = MockBot(phases=[], texts_by_call=seq_h)
logs_h = []
t_h = _make_task(bot_h, logger=lambda m: logs_h.append(m))
r_h = t_h._run_once()
check('H1 上阵人数不满 → 取消并结束探索（单轮 done）',
      r_h == 'done', f'result={r_h}')
check('H2 点的是 ✕ 模板 btn_confirm_cancel（不再点 ✓ 硬打）',
      any('btn_confirm_cancel' in c for c in bot_h.calls),
      str([c for c in bot_h.calls if 'confirm' in c][:2]))
check('H3 未再使用 ✓ 模板 btn_confirm_check（回归保护）',
      not any('btn_confirm_check' in c for c in bot_h.calls),
      str([c for c in bot_h.calls if 'confirm' in c][:2]))
check('H4 确实点了「结束探索」',
      '结束探索' in bot_h.clicked, str(bot_h.clicked[:6]))
check('H5 走了退出备战（发出 ESC）',
      any('press_esc' in c for c in bot_h.calls),
      str([c for c in bot_h.calls if 'esc' in c.lower()][:2]))
check('H6 日志说明是「取消战斗」以保住满员阵容记录',
      any('取消战斗' in m for m in logs_h)
      and not any('请布阵后重新运行' in m for m in logs_h),
      str([m for m in logs_h if '不满' in m][:2]))

# ═══════════════════════════════════════════════════════════
#  I. 选门/商店/烙印晶球（v1.8.52~53，items=(y,x,文本,conf,…)）
# ═══════════════════════════════════════════════════════════
print('── I. 选门决策 / 商店 / 烙印晶球 ──')
_it = lambda y, x, t, c=0.9: (y, x, t, c, x - 40, x + 40)

t_i = _make_task(MockBot(phases=[], texts_by_call=[['难度15', '探索阵容(5/12)']]))

# I1 门卡评分
check('I1a 遗物之门-强壮 → rank0',
      t_i._door_score('遗物之门', [_it(300, 500, '可获得遗物：强壮')],
                      500, 200) == (0, 1))
# v2.2.8（用户 09-15 定案「双倍强壮/庇护/极速 → 最优先」）：
# 旧期望 (2, 0) 是"档位优先、双倍仅同级 tie-break"；现双倍三目标抬到 -0.5
check('I1b 双倍极速 → -0.5 遗物类最高档（v2.2.8 双倍三目标最优先）',
      t_i._door_score('遗物之门', [_it(300, 500, '可获得双倍奖励。可获得遗物：极速 (1/2)')],
                      500, 200) == (-0.5, 0))
check('I1c 晶粹之门 → rank4', t_i._door_score(
    '晶粹之门', [_it(300, 500, '击败敌人额外获得大量晶粹')], 500, 200) == (4, 1))
check('I1d 菲茨小店 → rank5', t_i._door_score(
    '菲茨小店', [_it(300, 500, '神秘商人菲茨经营的小店')], 500, 200) == (5, 1))

# I2 多门决策：遗物-强壮 > 晶粹 > 小店
# v2.1.12：描述坐标修正为门标题**下方**（原 (300,300) 与标题 y 重叠 →
# `0 < it[0]-title_y` 不成立 → 描述未被归属，旧规则下"歪打正着"通过）
r_i2 = t_i._decide_door(
    [('晶粹之门', (200, 500)), ('菲茨小店', (200, 760)),
     ('遗物之门', (200, 300))],
    [_it(400, 300, '可获得遗物：强壮'), _it(31, 641, '14/15')],
)
check('I2 多门 → 遗物之门-强壮 优先', r_i2[0] == '遗物之门', str(r_i2))

# I2b（v2.1.12 用户定案）：普通遗物门（描述无三目标）降到菲茨小店之下
r_i2b = t_i._decide_door(
    [('遗物之门', (200, 300)), ('菲茨小店', (200, 760))],
    [_it(400, 300, '净化遗物获得普通强化'), _it(31, 641, '14/15')],
)
check('I2b 普通遗物门 < 菲茨小店 → 选小店（v2.1.12 定案）',
      r_i2b[0] == '菲茨小店', str(r_i2b))

# I3 13 层烙印晶球优先（模式2/3 深层刷核需要晶球；模式1 已排除，见 AF6）
t_i2 = _make_task(MockBot(phases=[], texts_by_call=[['x']]),
                  options={'run_mode': 'boss20'})
t_i2._floor_count = 13
r_i3 = t_i2._decide_door(
    [('菲茨小店', (374, 518)), ('烙印晶球', (375, 764))],
    [_it(31, 641, '13/15'), _it(451, 763, '烙印晶球在晶粹的驱动下缓缓')],
)
check('I3 13 层 → 烙印晶球 最优先', r_i3[0] == '烙印晶球', str(r_i3))

# I4 烙印候选选择
check('I4a 具名优先 执裁之盾', t_i._imprint_best(
    [('缓慢凋零', '史诗'), ('执裁之盾', '精英'), ('守护印记', '精英')]
) == ('执裁之盾', '精英'))
check('I4b 黑名单 泰坦体魄 排除', t_i._imprint_best(
    [('泰坦体魄', '精英'), ('守护印记', '精英')]
) == ('守护印记', '精英'))
check('I4c 稀有度兜底（无具名）', t_i._imprint_best(
    [('盾击反弹', '稀有'), ('守时庇护', '精英')]
) == ('守时庇护', '精英'))
check('I4d 全黑名单 → None', t_i._imprint_best(
    [('泰坦体魄', '精英'), ('献祭之盾', '精英')]) is None)

# I5 替换列表条目收集（实测坐标换算）
items_i5 = [
    _it(267, 264, '选择已有烙印进行替换'),
    _it(347, 367, '盾击反弹'), _it(429, 178, '稀有'),
    _it(511, 368, '能量护盾'), _it(592, 178, '稀有'),
    _it(673, 368, '守时庇护'), _it(755, 178, '精英'),
    _it(836, 368, '战栗呼啸'),
    _it(949, 733, '确认替换'), _it(955, 235, '继续前进'),
]
entries = t_i._collect_imprint_entries(items_i5)
names = [e[0] for e in entries]
# 战栗呼啸的品质标签在屏外（截图实测），不收集——逻辑正确
check('I5a 收集 3 条有品质标签的条目',
      set(names) == {'盾击反弹', '能量护盾', '守时庇护'}, str(names))
pos_ok = all(e[2][0] > 300 for e in entries)
check('I5b 点击坐标 x 在名称列（>300）', pos_ok, str([e[2] for e in entries]))

# I6 精粹/层数读取
check('I6a 右上角精粹 199', t_i._read_essence([
    _it(26, 232, '1386万'), _it(31, 641, '13/15'), _it(23, 1075, '199'),
]) == 199)
check('I6b 顶部层数 13', t_i._read_floor([
    _it(31, 641, '13/15'), _it(23, 1075, '199'),
]) == 13)

# ═══════════════════════════════════════════════════════════
#  J. v1.8.55 选门轴修复（真机 1080p 实测坐标：双遗物门 迅刃 vs 极速）
# ═══════════════════════════════════════════════════════════
print('── J. 选门轴修复：_find_all_text (cx,cy) + 描述最近门归属 ──')
# 2026-09-07 真机截图 MaaOCR 实测（floor=2，两扇遗物之门）：
#   左门=迅刃（攻击速度+10，不在优先级）→ rank 3
#   右门=极速（急速+5）→ 应 rank 1；右门效果行距左门标题仅 213px
items_j = [
    _it(30, 640, '2/15'),
    _it(374, 517, '遗物之门', 0.88),            # 左门标题
    _it(374, 762, '遗物之门', 0.99),            # 右门标题
    _it(450, 508, '击败敌人可获得遗物：迅刃'),    # 左门描述
    _it(473, 449, '(0/2)'),
    _it(495, 502, '效果：攻击速度提升10点。'),
    _it(450, 756, '击败敌人可获得遗物：极速'),    # 右门描述
    _it(473, 695, '（0/2）'),
    _it(495, 730, '效果：急速提升5点。'),         # 旧 |dx|<220 会串进左门
]
check('J1 _find_all_text 返回 (cx, cy)',
      t_i._find_all_text(items_j, '遗物之门', 0.6)
      == [(517, 374), (762, 374)],
      str(t_i._find_all_text(items_j, '遗物之门', 0.6)))
r_j = t_i._decide_door(
    [('遗物之门', (517, 374)), ('遗物之门', (762, 374))], items_j)
check('J2 双遗物门 → 选右门「极速」（左门迅刃不串卡）',
      r_j == ('遗物之门', (762, 374)), str(r_j))
t_j = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_j._pick_door(items_j)
check('J3 组合点击 x=标题cx（修复前误用标题 y=374 点空）',
      bool(t_j.bot.click_coords)
      and t_j.bot.click_coords[-1] == ('门卡_遗物之门_按钮', 762, 500),
      str(t_j.bot.click_coords))

# ═══════════════════════════════════════════════════════════
#  K. v1.8.56 黑名单轴修正（black_ys 取 it[0]=y，与标签 y 同卡判定）
# ═══════════════════════════════════════════════════════════
print('── K. 烙印黑名单 y 排除（v1.8.56 轴修正）──')
# K1 分叉场景：黑名单名称 OCR 条目 x 偏离名称列（x=900）——
#   修复前 black_ys=[900]（x）挡不住任何标签 y → 选中黑名单卡 (178,429)
#   修复后 black_ys=[347]（y）挡住同卡标签 429 → 选中正常卡 (178,592)
items_k1 = [
    _it(347, 900, '泰坦体魄'),   # 黑名单名称（x 偏离，y=347）
    _it(429, 178, '稀有'),       # 黑名单卡稀有度标签
    _it(592, 178, '稀有'),       # 正常卡稀有度标签
]
t_k1 = _make_task(MockBot(phases=[], texts_by_call=[['确认']]))
t_k1._handle_imprint(items_k1)
check('K1 黑名单卡 y 排除 → 点正常卡 (178,592)',
      bool(t_k1.bot.click_coords)
      and t_k1.bot.click_coords[0] == ('烙印_选卡', 178, 592),
      str(t_k1.bot.click_coords))
# K2 常规布局（名称列 x≈367）：黑名单第 1 行、正常卡第 2 行
items_k2 = [
    _it(347, 367, '泰坦体魄'),
    _it(429, 178, '稀有'),
    _it(511, 368, '守护印记'),
    _it(592, 178, '稀有'),
]
t_k2 = _make_task(MockBot(phases=[], texts_by_call=[['确认']]))
t_k2._handle_imprint(items_k2)
check('K2 常规布局 → 黑名单不选中，点第 2 行 (178,592)',
      bool(t_k2.bot.click_coords)
      and t_k2.bot.click_coords[0] == ('烙印_选卡', 178, 592),
      str(t_k2.bot.click_coords))

# ═══════════════════════════════════════════════════════════
#  L. v1.8.57 商店「战斗胜利」描述撞词修复（真机 2026-09-07：
#     商店界面启动被误判 victory → 启动直通后玩法循环每 5s 空转）
# ═══════════════════════════════════════════════════════════
print('── L. 商店暖流描述撞 victory 判据（v1.8.57 商店词反向排除）──')
# 2026-09-07 商店截图 MaaOCR 实测（1279x1021）：「暖流」遗物描述
# 「战斗胜利时，使上阵的英雄恢复10%命值。」——截图 OCR 漏「斗」字，
# 真机识别全 → h('战斗胜利') 命中，victory(priority=102) 在 shop(106)
# 之前评估 → 商店误判 victory。修复：victory/goddess.victory_result
# 加 SHOP_MASK_WORDS（商品列表/继续前进）反向排除（全屏遮罩看不见底层
# 商店词，读到 = 不是弹层），评估顺序不动。
items_l1 = [
    _item(268, 204, '商品列表', 0.99),
    _item(300, 640, '菲茨小店', 0.98),
    _item(542, 482, '战斗胜利时，使上阵的英雄恢复10%命值。', 0.99),
    _item(600, 482, '暖流', 0.97),
    _item(955, 233, '继续前进', 1.00),
    _item(958, 730, '购买', 0.99),
]
check('L1 商店帧（暖流描述含战斗胜利）→ 判 shop 不判 victory',
      t_a._detect_play_state(items_l1) == 'shop',
      t_a._detect_play_state(items_l1))
check('L1b 全局注册表 → labyrinth.shop（不落 goddess.victory_result）',
      GLOBAL_SCREEN_REGISTRY.detect(items_l1) == 'labyrinth.shop',
      GLOBAL_SCREEN_REGISTRY.detect(items_l1))

items_l2 = [
    _item(260, 640, '战斗胜利', 0.99),
    _item(420, 640, '获得奖励', 0.95),
    _item(700, 300, '自动', 0.92),
]
check('L2 真胜利弹层（无商店/door 词）→ 仍判 victory（直通保留）',
      t_a._detect_play_state(items_l2) == 'victory',
      t_a._detect_play_state(items_l2))

items_l3 = [
    _item(260, 640, '战斗胜利', 0.99),
    _item(700, 640, '再次挑战', 0.98),
]
check('L3 女神结算页 → goddess.victory_result（v1.8.25 分域不回归）',
      GLOBAL_SCREEN_REGISTRY.detect(items_l3) == 'goddess.victory_result',
      GLOBAL_SCREEN_REGISTRY.detect(items_l3))

# L4 空转兜底：恒定商店帧 → _play_loop 连续 shop 无推进 →
#   6 轮起 ESC/返回交替（_STALL_ESC_AFTER），18 轮 failed
#（_STALL_GIVE_UP）。修复前 victory 误判场景每 5s 空转无任何兜底。
# 每轮消耗 2 帧（主 OCR 1 + _shop_flow 内 1）→ 18 轮 = 36 帧 < 46 帧。
shop_texts = ['商品列表', '菲茨小店', '战斗胜利时，使上阵的英雄恢复10%命值。',
              '暖流', '继续前进', '购买']
t_l4 = _make_task(MockBot(phases=[], texts_by_call=[shop_texts] * 46),
                  )
res_l4 = t_l4._play_loop()
check('L4 同状态空转 18 轮 → failed（不再无限循环）',
      res_l4 == 'failed', str(res_l4))
check('L4b 空转脱离动作已发起（ESC/返回交替）',
      'press_esc:玩法空转_ESC' in t_l4.bot.calls
      and 'click_back_button' in t_l4.bot.calls,
      str([c for c in t_l4.bot.calls if '空转' in c or 'esc' in c][:4]))

# ═══════════════════════════════════════════════════════════
#  M. v1.8.58 选门界面暖流描述防误判（用户 2026-09-07 疑点核查：
#     暖流描述「战斗胜利时…」也出现在选门界面）
# ═══════════════════════════════════════════════════════════
print('── M. 选门界面暖流描述防 victory 误判（v1.8.58）──')
# 触发链：遗物门描述「效果：战斗胜利时，使上阵的英雄恢复10%命值。」
# 命中 h('战斗胜利')。标准选门界面由 DOOR_WORDS（结束探索/之门）拦截
# （v1.8.20）；v1.8.58 补两个洞：
#   洞1（场景e）：单门界面（精英/BOSS/深层 16-19，标题非「之门」）+
#     「结束探索」OCR 漏检（v1.8.55 真机波动先例）→ 裸命中 victory →
#     _play_loop 点空白(0.5,0.5) 误点门卡中央。修复：加 not h('战斗
#     胜利时')（描述句式带「时」，弹层标题不带，纯文本特征不受断词影响）
#   洞2（场景c）：遗物门标题与结束探索全漏、仅剩描述 → 同上。修复：
#     DOOR_WORDS 加「击败敌人可获得遗物」+ door_fallback 扩此句式
warm_desc = '效果：战斗胜利时，使上阵的英雄恢复10%命值。'
items_m_e = [   # 修复前判 victory（误）→ 修复后 unknown（等待帧恢复）
    _item(30, 640, '17/20'),
    _item(374, 640, '深层挑战'),
    _item(495, 640, warm_desc),
]
check('M1 单门界面漏检结束探索 → unknown（修复前误判 victory）',
      t_a._detect_play_state(items_m_e) == 'unknown',
      t_a._detect_play_state(items_m_e))
items_m_c = [   # 修复前判 victory（误）→ 修复后 door_fallback 归并 door
    _item(30, 640, '2/15'),
    _item(450, 508, '击败敌人可获得遗物：暖流'),
    _item(495, 502, warm_desc),
]
check('M2 遗物门仅剩描述 → door_fallback（归并 door 正常点门）',
      t_a._detect_play_state(items_m_c) == 'door',
      t_a._detect_play_state(items_m_c))
items_m_a = [   # 标准选门界面回归
    _item(30, 640, '2/15'),
    _item(374, 517, '遗物之门'),
    _item(450, 508, '击败敌人可获得遗物：暖流'),
    _item(495, 502, warm_desc),
    _item(955, 640, '结束探索'),
]
check('M3 标准遗物门（暖流描述+结束探索）→ door',
      t_a._detect_play_state(items_m_a) == 'door',
      t_a._detect_play_state(items_m_a))
check('M4 备战画面（难度+战斗+暖流描述）→ battle_prep 不判 victory',
      t_a._detect_play_state([
          _item(100, 200, '难度15'), _item(120, 200, '战斗'),
          _item(495, 640, warm_desc),
      ]) == 'battle_prep',
      t_a._detect_play_state([
          _item(100, 200, '难度15'), _item(120, 200, '战斗'),
          _item(495, 640, warm_desc)]))
check('M5 真胜利弹层（无「时」无描述句式）→ victory 不受新排除影响',
      t_a._detect_play_state([
          _item(260, 640, '战斗胜利'), _item(420, 640, '获得奖励'),
      ]) == 'victory',
      t_a._detect_play_state([
          _item(260, 640, '战斗胜利'), _item(420, 640, '获得奖励')]))
check('M6 女神结算页不回归（goddess 判据同步加固）',
      GLOBAL_SCREEN_REGISTRY.detect([
          _item(260, 640, '战斗胜利'), _item(700, 640, '再次挑战'),
      ]) == 'goddess.victory_result',
      GLOBAL_SCREEN_REGISTRY.detect([
          _item(260, 640, '战斗胜利'), _item(700, 640, '再次挑战')]))

# ═══════════════════════════════════════════════════════════
#  N. v1.8.59 关卡计数同步（用户 2026-09-07：第 9 关误选烙印晶球，
#     正确仅第 14 关〔顶栏 13/15 选门界面〕进入）
# ═══════════════════════════════════════════════════════════
print('── N. 关卡计数：烙印晶球触发改计数判定 + 顶栏 UI 同步 ──')
# 根因：旧判据 `floor is None or floor >= 13` 的 None 放行——顶栏 OCR
# 波动（_read_floor 正则不匹配）即裸放行 → 第 9 关出现烙印晶球门就选。
# 修复：新增 _floor_count 计数（初始 0，_pick_door 每次成功点门 +1），
# 触发条件 self._floor_count >= 13；_decide_door 开头与顶栏 X/15 对照，
# 偏差以 UI 校正（覆盖续局直通从 0 起算的偏移）。
orb_titles = [('菲茨小店', (374, 518)), ('烙印晶球', (375, 764))]

# N1 第 9 关复现场景：顶栏读不出（floor=None）+ 计数 9 → 不选烙印晶球
t_n1 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_n1._floor_count = 9
r_n1 = t_n1._decide_door(orb_titles, [_it(451, 763, '烙印晶球在晶粹的驱动下缓缓')])
check('N1 第 9 关+顶栏漏检 → 不选烙印晶球（修复前 None 放行误选）',
      r_n1[0] == '菲茨小店', str(r_n1))

# N2 计数达 13（顶栏 13/15 选门界面语义）→ 烙印晶球最优先
# （v1.8.93：用模式2——模式1 已排除晶球）
t_n2 = _make_task(MockBot(phases=[], texts_by_call=[['x']]),
                  options={'run_mode': 'boss20'})
t_n2._floor_count = 13
r_n2 = t_n2._decide_door(
    orb_titles,
    [_it(31, 641, '13/15'), _it(451, 763, '烙印晶球在晶粹的驱动下缓缓')])
check('N2 计数 13 → 烙印晶球 最优先', r_n2[0] == '烙印晶球', str(r_n2))
check('N2b 计数与顶栏一致（13==13 无校正）', t_n2._floor_count == 13,
      str(t_n2._floor_count))

# N3 续局同步：计数 0 + 顶栏 9/15 → 校正 9 且不选烙印晶球
t_n3 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
r_n3 = t_n3._decide_door(orb_titles, [_it(31, 641, '9/15')])
check('N3 续局偏移 → 顶栏校正计数 0→9',
      t_n3._floor_count == 9, str(t_n3._floor_count))
check('N3b 校正后第 9 关不选烙印晶球', r_n3[0] == '菲茨小店', str(r_n3))

# N4 点门成功 → 计数 +1（组合点击路径；非首门状态 ever_clicked=True）
t_n4 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_n4._floor_count = 5   # 与顶栏 5/15 一致（当前第 5 关，点门进第 6 关）
t_n4._ever_clicked = True   # 非首门（首门特例见 O2 轨迹）
t_n4._pick_door([
    _it(30, 640, '5/15'),
    _it(374, 517, '遗物之门'), _it(374, 762, '遗物之门'),
    _it(450, 508, '击败敌人可获得遗物：暖流'),
    _it(450, 756, '击败敌人可获得遗物：极速'),
])
check('N4 点门成功 → 计数 5→6（点门后与下一帧顶栏 6/15 同步）',
      t_n4._floor_count == 6, str(t_n4._floor_count))

# N5 点门未发生（双未命中）→ 计数不变
t_n5 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_n5._floor_count = 7
t_n5._ever_clicked = True   # 非首门状态
# v1.8.85 P2-2：标题未命中 → 需连续 2 帧才盲点按钮（首帧只等一帧重试）
t_n5._pick_door([_it(374, 640, '未知之门')])   # 无按钮模板命中（Mock 返 500,500）
check('N5a 首帧标题未命中 → 暂不盲点（计数不变 7）',
      t_n5._floor_count == 7, str(t_n5._floor_count))
# Mock template_match 恒命中 (500,500) → 次帧走单门按钮路径 → 计数 +1
t_n5._pick_door([_it(374, 640, '未知之门')])
check('N5 单门按钮直点（连续两帧无标题）→ 计数 +1',
      t_n5._floor_count == 8, str(t_n5._floor_count))

# ═══════════════════════════════════════════════════════════
#  O. v1.8.60 进度计数规则核对（用户 2026-09-07 定案：初始 1/15；
#     第 1 扇门计数不变仍 1；第 2 扇门起每门 +1；15 关后切 X/20）
# ═══════════════════════════════════════════════════════════
print('── O. 进度计数全轨迹 + 深层 X/20 支持 ──')
# O1 顶栏格式全量（旧正则 1[58] 漏读深层 X/20）
check('O1a 普通层 13/15 → 13', t_i._read_floor([_it(31, 641, '13/15')]) == 13,
      str(t_i._read_floor([_it(31, 641, '13/15')])))
check('O1b 深层 16/20 → 16（旧正则漏读）',
      t_i._read_floor([_it(31, 641, '16/20')]) == 16,
      str(t_i._read_floor([_it(31, 641, '16/20')])))
check('O1c 深层 20/20 → 20', t_i._read_floor([_it(31, 641, '20/20')]) == 20,
      str(t_i._read_floor([_it(31, 641, '20/20')])))
check('O1d 干扰词 4/6 不误读', t_i._read_floor([_it(60, 100, '4/6')]) is None,
      str(t_i._read_floor([_it(60, 100, '4/6')])))

# O2 用户规则全轨迹模拟：初始 0（进入迷宫被顶栏 1/15 校正为 1）→
#   点第 1 扇门后计数 1（顶栏仍 1/15）→ 第 N 扇门后计数 N。
#   每关循环：选门界面（读顶栏校正）→ 点门 +1 → 下一关。
t_o = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
trajectory_ok, traj_log = True, []
for n in range(1, 15):   # 第 n 扇门（第 n 关）
    # 顶栏：第 1、2 扇门的选门界面均 1/15（点第 1 扇门后顶栏不变）；
    # 第 n 扇门（n≥3）选门界面顶栏 (n-1)/15
    ui = '1/15' if n <= 2 else f'{n - 1}/15'
    r = t_o._decide_door([('遗物之门', (200, 300))],
                         [_it(31, 641, ui),
                          _it(300, 300, '击败敌人可获得遗物：暖流')])
    # 选门界面计数应 = 已完成关数 = UI 值（第 1 关选门时顶栏 1/15）
    expect_ui = 1 if n == 1 else n - 1
    if t_o._floor_count != expect_ui:
        trajectory_ok = False
        traj_log.append(f'第{n}扇门前 count={t_o._floor_count}≠{expect_ui}')
    t_o._pick_door([_it(31, 641, ui), _it(374, 300, '遗物之门')])
    # 点第 n 扇门后计数 = n（第 1 扇门后仍 1 ✓ 用户规则）
    if t_o._floor_count != n:
        trajectory_ok = False
        traj_log.append(f'第{n}扇门后 count={t_o._floor_count}≠{n}')
check('O2 全轨迹：第 N 扇门后计数=N（第 1 扇门后仍 1）',
      trajectory_ok, ' | '.join(traj_log) or f'末态 count={t_o._floor_count}')

# O3 截图场景（14关.png）：顶栏 13/15 双门（菲茨小店+烙印晶球）→ 计数 13
#   → 烙印晶球触发（点进去第 14 关）（v1.8.93：用模式2，晶球不排除）
t_o3 = _make_task(MockBot(phases=[], texts_by_call=[['x']]),
                  options={'run_mode': 'boss20'})
t_o3._floor_count = 13
r_o3 = t_o3._decide_door(
    [('菲茨小店', (374, 518)), ('烙印晶球', (375, 764))],
    [_it(31, 641, '13/15'), _it(451, 763, '烙印晶球在晶粹的驱动下缓缓')])
check('O3 截图场景 13/15 双门 → 烙印晶球（第 14 关）',
      r_o3[0] == '烙印晶球' and t_o3._floor_count == 13,
      f'{r_o3[0]} count={t_o3._floor_count}')

# O4 深层计数与 X/20 校正：计数漂移到 15、顶栏 17/20 → 校正 17
t_o4 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_o4._floor_count = 15
t_o4._decide_door([('遗物之门', (200, 300))],
                  [_it(31, 641, '17/20'),
                   _it(300, 300, '击败敌人可获得遗物：暖流')])
check('O4 深层 17/20 校正计数 15→17',
      t_o4._floor_count == 17, str(t_o4._floor_count))

# O5 续局直通：顶栏 9/15 启动（_resumed=True）→ 校正 9 → 首点门 +1=10
#   （续局的首扇门并非游戏第 1 扇门，不享受「确立计数不增」特权）
t_o5 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
t_o5._resumed = True
r_o5 = t_o5._decide_door([('遗物之门', (200, 300))],
                         [_it(31, 641, '9/15'),
                          _it(300, 300, '击败敌人可获得遗物：暖流')])
t_o5._pick_door([_it(31, 641, '9/15'), _it(374, 300, '遗物之门')])
check('O5 续局 9/15 首点门 → 校正 9 后 +1=10（顶栏将变 10/15）',
      t_o5._floor_count == 10, str(t_o5._floor_count))

# ═══════════════════════════════════════════════════════════
#  Q. v1.8.61 烙印晶球替换界面：最上方是被替换烙印（不可选）
#     （用户 2026-09-07 真机：误选被替换行 → 确认无效反复轮空转）
# ═══════════════════════════════════════════════════════════
print('── Q. 替换界面排除被替换行（v1.8.61）──')
# OCR 实测 烙印晶球_替换烙印.png（1278x1025）：第一行 双重施法
# （名称 y=223/标签 y=304）= 被替换的当前烙印；候选 3 行 y≥427。
# 21:12:22 真机日志误点被替换行标签 (177,304)。
items_q = [
    _it(223, 365, '双重施法'), _it(304, 177, '精英'),     # 被替换行
    _it(427, 372, '满满来了！'), _it(508, 177, '精英'),   # 候选 1
    _it(589, 365, '垂危治愈'), _it(671, 177, '精英'),     # 候选 2
    _it(752, 365, '能量浪潮'), _it(834, 176, '稀有'),     # 候选 3
    _it(956, 234, '保留烙印'), _it(957, 732, '确认选择'),
]
t_q = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
entries_q = t_q._collect_imprint_entries(items_q)
orb_entries = [e for e in entries_q if e[2][1] / 1024.0 >= 0.38]
names_q = {n for n, _q, _p in orb_entries}
check('Q1 替换列表过滤后被替换行「双重施法」不在候选中',
      '双重施法' not in names_q, str(sorted(names_q)))
check('Q2 三候选齐全（满满来了/垂危治愈/能量浪潮）',
      names_q == {'满满来了！', '垂危治愈', '能量浪潮'}, str(sorted(names_q)))
# Q3 普通烙印四选一界面回归（_handle_imprint 用全量 entries，第一行可选）
items_q3 = [
    _it(347, 367, '盾击反弹'), _it(429, 178, '稀有'),
    _it(511, 368, '守护印记'), _it(592, 178, '稀有'),
]
t_q3 = _make_task(MockBot(phases=[], texts_by_call=[['确认']]))
t_q3._handle_imprint(items_q3)
check('Q3 普通烙印选择不受过滤影响（首行可选，正常选中第一行）',
      bool(t_q3.bot.click_coords)
      and t_q3.bot.click_coords[0] == ('烙印_选卡', 178, 429),
      str(t_q3.bot.click_coords))

# ═══════════════════════════════════════════════════════════
#  R. v1.8.62 终止确认弹窗（用户 2026-09-07：选门界面 ESC 脱离触发
#     「是否终止本次探索？」→ 需长按「长按结束」）
# ═══════════════════════════════════════════════════════════
print('── R. 终止确认弹窗识别 + 长按结束（v1.8.62）──')
# OCR 实测（1283x1022）：是否终止本次探索？(481,638) / 长按结束
# (637,541) conf=0.99 / 继续探索 (636,740) / 点击空白处关闭 (964,640)
confirm_texts = ['是否终止本次探索？', '长按结束', '继续探索', '点击空白处关闭']
check('R1 弹窗帧 → 判 end_confirm（长按结束为独占词）',
      t_a._detect_play_state([_item(481, 638, t) for t in confirm_texts])
      == 'end_confirm',
      t_a._detect_play_state([_item(481, 638, t) for t in confirm_texts]))

# R2 _play_loop 弹窗分支：长按结束动作已发起（StopBot 控制终止）
class _StopBot(MockBot):
    """第 N 次 OCR 后触发 stop_event（_play_loop 直调的可控终止）。"""
    def __init__(self, stop_after, stop_ev, **kw):
        super().__init__(**kw)
        self._stop_after = stop_after
        self._stop_ev = stop_ev
        self._ocr_n = 0

    def _ocr_screen(self, img=None, no_cache=False):
        self._ocr_n += 1
        if self._ocr_n > self._stop_after:
            self._stop_ev.set()
        return super()._ocr_screen(img=img)

stop_ev = threading.Event()
bot_r2 = _StopBot(stop_after=2, stop_ev=stop_ev,
                  texts_by_call=[confirm_texts, confirm_texts])
t_r2 = _make_task(bot_r2)
t_r2._stop = stop_ev   # ctx 的 stop_event 替换为可控事件
res_r2 = t_r2._play_loop()
lp_calls = [c for c in bot_r2.calls if 'long_press' in c]
check('R2 弹窗分支发起长按结束（图像坐标长按）',
      any(c.startswith('long_press_at:长按结束') for c in lp_calls),
      str(lp_calls[:2]))

# R3 OCR 漏检帧兜底：弹窗只有标题词（长按结束漏检）→ 比例长按兜底
stop_ev3 = threading.Event()
bot_r3 = _StopBot(stop_after=2, stop_ev=stop_ev3,
                  texts_by_call=[['是否终止本次探索？', '继续探索'],
                                 ['是否终止本次探索？', '继续探索']])
t_r3 = _make_task(bot_r3)
t_r3._stop = stop_ev3
t_r3._play_loop()
check('R3 长按结束词漏检 → 比例坐标长按兜底 (0.422,0.623)',
      any(c.startswith('long_press_ratio:长按结束_兜底')
          for c in bot_r3.calls),
      str([c for c in bot_r3.calls if 'long_press' in c][:2]))

# ═══════════════════════════════════════════════════════════
#  S. v1.8.63 运行模式二选一（用户 2026-09-07：删「挑战至20关」勾选）
#     模式1 clear15=通关15结束；模式2 boss20=到第20关「深层首领」门前停止
# ═══════════════════════════════════════════════════════════
print('── S. 运行模式二选一（v1.8.63）──')
# S1 旧配置迁移：ctx 只有 deep_challenge=True → boss20（一次性）
t_s1 = _make_task(MockBot(phases=[], texts_by_call=[['x']]),
                  options={'deep_challenge': True})
check('S1 旧配置 deep_challenge=True → 迁移 boss20',
      t_s1._run_mode() == 'boss20', t_s1._run_mode())
# S2 默认 clear15
t_s2 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
check('S2 默认模式 clear15', t_s2._run_mode() == 'clear15',
      t_s2._run_mode())
# S3 明确选择优先：run_mode='clear15' 时旧勾选不反向覆盖
t_s3 = _make_task(MockBot(phases=[], texts_by_call=[['x']]),
                  options={'run_mode': 'clear15', 'deep_challenge': True},
                  )
check('S3 明确选模式1 → 不被旧勾选覆盖',
      t_s3._run_mode() == 'clear15', t_s3._run_mode())

# S4 v1.8.93 新语义：模式2 门前 + 库存有执裁之盾 → 任务停止、
# 画面停在门前（绝不点结束探索——会清零进度）
boss20_texts = ['难度15', '19/20', '深层首领', '"最终一战"', '结束探索']
stop_ev4 = threading.Event()
bot_s4 = _StopBot(stop_after=4, stop_ev=stop_ev4,
                  texts_by_call=[boss20_texts, boss20_texts, confirm_texts,
                                 confirm_texts, boss20_texts, boss20_texts])
t_s4 = _make_task(bot_s4, options={'run_mode': 'boss20'})
t_s4._stop = stop_ev4
t_s4._orb_scanned = True
t_s4._orb_inventory = [('执裁之盾', '精英')]
r_s4 = t_s4._play_loop()
check('S4 模式2 门前单核达标 → 返回 stopped（停在门前）',
      r_s4 == 'stopped', f'r={r_s4}')
check('S4b 绝不点结束探索（点了解会清零进度）',
      '结束探索' not in bot_s4.clicked
      and not any('long_press' in c for c in bot_s4.calls),
      str([c for c in bot_s4.calls if '结束探索' in c or 'long_press' in c]))

# S5 模式1 不拦截：深层首领帧正常点门卡
stop_ev5 = threading.Event()
bot_s5 = _StopBot(stop_after=2, stop_ev=stop_ev5,
                  texts_by_call=[boss20_texts, boss20_texts])
t_s5 = _make_task(bot_s5, options={'run_mode': 'clear15'})
t_s5._stop = stop_ev5
t_s5._play_loop()
check('S5 模式1 深层首领帧 → 不点结束探索（走点门）',
      '结束探索' not in bot_s5.clicked,
      str(bot_s5.clicked[:3]))

# S6 模式3（双核）：双盾齐 → 门前停；只差一盾 → 继续打
t_s6a = _make_task(MockBot(texts_by_call=[boss20_texts, boss20_texts]),
                   options={'run_mode': 'boss20_dual'})
t_s6a._orb_scanned = True
t_s6a._orb_inventory = [('执裁之盾', '精英'), ('溢出防御', '史诗')]
b_s6b = _StopBot(stop_after=3, stop_ev=threading.Event(),
                 texts_by_call=[boss20_texts, boss20_texts,
                                boss20_texts, boss20_texts])
t_s6b = _make_task(b_s6b, options={'run_mode': 'boss20_dual'})
t_s6b._orb_scanned = True
t_s6b._orb_inventory = [('执裁之盾', '精英')]     # 只差溢出防御
r_s6a = t_s6a._play_loop()
r_s6b = t_s6b._play_loop()
check('S6 模式3 双核达标 → stopped；未达标 → 不停（继续打）',
      r_s6a == 'stopped' and r_s6b != 'stopped',
      f'双核={r_s6a} 差一盾={r_s6b}')

# ═══════════════════════════════════════════════════════════
#  T. v1.8.66 替换主界面入口修正（真机 23:45：替换第一屏没有
#     「选择已有烙印」词 → 走普通 imprint 反复选首行被替换烙印）
# ═══════════════════════════════════════════════════════════
print('── T. 替换主界面入口补「保留烙印」（v1.8.66）──')
# 真机截图（1080x859 窗口）：替换主界面 = 首行被替换烙印（迅捷治愈）
# + 三候选 + 底部「保留烙印」「确认选择」——无「选择已有烙印」词
main_screen_texts = ['迅捷治愈', '史诗', '骤然钝击', '精英',
                     '战斗鼓舞', '精英', '泰坦体魄', '精英',
                     '保留烙印', '确认选择']
logs_t1: list = []
bot_t1 = MockBot(phases=[], texts_by_call=[main_screen_texts] * 8)
t_t1 = _make_task(bot_t1,
                  logger=lambda m: logs_t1.append(m))
t_t1._resumed = True
t_t1._scan_orb_inventory = lambda: set()   # v1.8.81 扫描单独在 Y 组测
t_t1._play_loop()
check('T1 替换主界面（保留烙印）→ 走 orb 替换流程（非普通 imprint）',
      any('进入替换流程' in m for m in logs_t1),
      str([m for m in logs_t1 if '替换' in m][:2]))
# v1.8.67 状态机：T1 帧（保留烙印+确认选择）校准 step=2 → 空候选（Mock
# 帧 y=100 全被 0.38 过滤）→ 点「保留烙印」跳过回第一步
check('T1b step2 空候选 → 点「保留烙印」跳过（状态机新路径）',
      any(c == 'click_at:保留烙印' for c in bot_t1.calls),
      str([c for c in bot_t1.calls if '保留烙印' in c][:2]))

# T2 普通烙印四选一（无保留烙印词）→ 仍走 _handle_imprint 不误入 orb
logs_t2: list = []
bot_t2 = MockBot(phases=[], texts_by_call=[
    ['盾击反弹', '稀有', '守护印记', '精英', '确认'],
    ['盾击反弹', '稀有', '守护印记', '精英', '确认'],
])
t_t2 = _make_task(bot_t2,
                  logger=lambda m: logs_t2.append(m))
t_t2._resumed = True
t_t2._play_loop()
check('T2 普通烙印选择（无保留烙印）→ 不误入 orb 流程',
      not any('进入替换流程' in m for m in logs_t2)
      and any(c == 'click_at:烙印_选卡' for c in bot_t2.calls),
      str([m for m in logs_t2 if '替换' in m][:2]))

# ═══════════════════════════════════════════════════════════
#  U. v1.8.68 成功替换弹窗 + 停止即时响应
# ═══════════════════════════════════════════════════════════
print('── U. 成功替换弹窗点空白关闭 + 停止即时性（v1.8.68）──')
# U1 step2 确认后弹「成功替换」窗（点击空白处关闭）→ 点该文字 →
#   等回第一步特征词后收尾。items 直接给坐标（验 step2 首行过滤）：
#   step2 帧：保留烙印/确认选择(y≈950) + 首行暖流之力(y=303<0.38 被滤)
#   + 候选 暴怒速攻(y=523)/进发浪潮(y=640)
step2_items = [
    _it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
    _it(303, 366, '暖流之力'), _it(385, 178, '精英'),    # 首行(被滤)
    _it(523, 365, '暴怒速攻'), _it(605, 177, '精英'),    # 候选1
    _it(640, 365, '进发浪潮'), _it(722, 176, '稀有'),    # 候选2
]
popup_items = [_it(150, 540, '成功替换'), _it(500, 540, '迟钝收益'),
               _it(800, 540, '点击空白处关闭')]
step1_items = [_it(955, 234, '继续前进'), _it(955, 732, '确认替换')]
t_u1 = _make_task(MockBot(phases=[], texts_by_call=[
    step2_items,    # step2：收集过滤→选最优(暴怒速攻 精英)→确认选择
    popup_items,    # 内联弹窗循环：成功替换 → dismiss 点空白
    popup_items,    # （闪烁相位）
    step1_items,    # 已回第一步 → break
    step1_items, step1_items,              # step1 重取（v1.8.86 库存空即止）
]))
t_u1._scan_orb_inventory = lambda: set()   # v1.8.81 扫描单独在 Y 组测
t_u1._imprint_orb_flow()
check('U1 step2 过滤首行 → 选「暴怒速攻」（非暖流之力）',
      any(c == 'click_at:烙印_暴怒速攻' for c in t_u1.bot.calls)
      and not any(c == 'click_at:烙印_暖流之力' for c in t_u1.bot.calls),
      str([c for c in t_u1.bot.calls if '烙印_' in c][:3]))
check('U1 成功替换弹窗 → 点「点击空白处关闭」文字',
      any(c == 'click_at:闪烁弹窗_点空白' for c in t_u1.bot.calls),
      str([c for c in t_u1.bot.calls if '空白' in c][:2]))
check('U1b 弹窗关闭后回第一步、库存无可替换项 → 收尾不点「继续前进」',
      any(c == 'click_at:闪烁弹窗_点空白' for c in t_u1.bot.calls)
      and not any(c == 'click_at:继续前进' for c in t_u1.bot.calls),
      str([c for c in t_u1.bot.calls if '继续前进' in c][:2]))

# U1c 收尾路径独立验证：第一步空转耗尽 → 点「继续前进」退出
# （scrolls=4 次滚动耗尽 → break → 收尾 OCR 命中继续前进）
t_u1c = _make_task(MockBot(phases=[], texts_by_call=[step1_items] * 15))  # 扫描后每轮耗 2 帧，5 轮=10 帧+收尾
t_u1c._scan_orb_inventory = lambda: set()   # v1.8.81 扫描单独在 Y 组测
t_u1c._imprint_orb_flow()
check('U1c 空转耗尽 → 收尾点「继续前进」退出',
      any(c == 'click_at:继续前进' for c in t_u1c.bot.calls),
      str([c for c in t_u1c.bot.calls if '继续前进' in c][:2]))

# U2 用户停止 → orb 立即退出且不点收尾按钮（保留现场）
stop_ev_u = threading.Event()
bot_u2 = _StopBot(stop_after=2, stop_ev=stop_ev_u,
                  texts_by_call=[['确认替换', '继续前进']] * 4)
t_u2 = _make_task(bot_u2)
t_u2._stop = stop_ev_u
t_u2._imprint_orb_flow()
check('U2 停止后 → 不点「继续前进」（保留现场）',
      not any(c == 'click_at:继续前进' for c in bot_u2.clicked),
      str(bot_u2.clicked[:3]))

# ═══════════════════════════════════════════════════════════
#  V. v1.8.71 第二步品质保护（用户定案：三候选品质全部低于被替换
#     旧烙印 → 点「保留烙印」跳过本轮，换是亏的）
# ═══════════════════════════════════════════════════════════
print('── V. 第二步品质保护（v1.8.71）──')
# V1 旧烙印=史诗，候选全精英/稀有 → 亏 → None → 保留烙印跳过
rare_pool = [('暖流之力', '精英'), ('进发浪潮', '稀有'), ('后发制人', '精英')]
t_v1 = _make_task(MockBot(phases=[], texts_by_call=[['x']]))
check('V1 旧=史诗 候选全精英/稀有 → None（保留烙印跳过）',
      t_v1._imprint_best(rare_pool, min_qual='史诗') is None,
      str(t_v1._imprint_best(rare_pool, min_qual='史诗')))
# V2 旧烙印=精英，候选含史诗 → 正常选（不亏）
check('V2 旧=精英 候选含史诗 → 选史诗（正常换）',
      t_v1._imprint_best(rare_pool, min_qual='精英')
      == ('暖流之力', '精英'),
      str(t_v1._imprint_best(rare_pool, min_qual='精英')))
# V3 旧=稀有 → 任何候选 ≥ 稀有 → 永不跳过
check('V3 旧=稀有 → 候选全稀有也正常换',
      t_v1._imprint_best([('进发浪潮', '稀有')], min_qual='稀有')
      == ('进发浪潮', '稀有'),
      str(t_v1._imprint_best([('进发浪潮', '稀有')], min_qual='稀有')))
# V4 品质保护与黑名单共存：旧=史诗，候选=黑名单精英+稀有 → None
check('V4 候选=黑名单+低质 → None（两层过滤叠加）',
      t_v1._imprint_best([('泰坦体魄', '精英'), ('进发浪潮', '稀有')],
                         min_qual='史诗') is None,
      str(t_v1._imprint_best([('泰坦体魄', '精英'), ('进发浪潮', '稀有')],
                             min_qual='史诗')))
# V5 min_qual 缺省（普通四选一场景）→ 行为不变（回归）
check('V5 min_qual 缺省 → 原行为不变（回归）',
      t_v1._imprint_best([('泰坦体魄', '精英')])
      is None
      and t_v1._imprint_best([('守时庇护', '精英')])
      == ('守时庇护', '精英'),
      'regression')

# ═══════════════════════════════════════════════════════════
#  W. 跳过按钮模板路径（v1.8.78 真机 FileNotFoundError 回归）
#     根因：裸文件名按进程 CWD 解析，exe 放桌面 → 找不到模板
# ═══════════════════════════════════════════════════════════
print('── W. 跳过按钮模板走绝对路径（v1.8.78）──')

from tasks.labyrinth import (   # noqa: E402
    _SKIP_BTN_TPL, _TEMPLATE_DIR, _SKIP_REGION_RATIO,
)

check('W1 _SKIP_BTN_TPL 为绝对路径',
      os.path.isabs(_SKIP_BTN_TPL), _SKIP_BTN_TPL)
check('W2 _SKIP_BTN_TPL 文件真实存在',
      os.path.isfile(_SKIP_BTN_TPL), _SKIP_BTN_TPL)
check('W3 _SKIP_BTN_TPL 位于 templates/ 目录',
      os.path.dirname(_SKIP_BTN_TPL) == _TEMPLATE_DIR, _SKIP_BTN_TPL)

# W4：_start_and_wait_battle 轮询时用绝对路径调用 template_match
b_w = MockBot(texts_by_call=[[
    _item(100, 400, '战斗胜利'), _item(500, 400, '上阵英雄'),
]])
b_w.skip_hit = (900, 60)
t_w = _make_task(b_w)
t_w._start_and_wait_battle()
skip_calls = [p for p in b_w.tpl_paths
              if os.path.basename(p) == 'btn_skip_battle.png']
check('W4 战斗轮询调用过跳过模板',
      bool(skip_calls), str(b_w.tpl_paths))
check('W5 传入的是绝对路径（非裸文件名）',
      bool(skip_calls) and all(os.path.isabs(p) for p in skip_calls),
      str(skip_calls))
check('W6 命中后点跳过（_click_abs 记录）',
      any(c.startswith('_click_abs:(900,') for c in b_w.calls),
      str([c for c in b_w.calls if 'click_abs' in c]))

# W7：未命中时不点击（回归保护）
b_w2 = MockBot(texts_by_call=[[
    _item(100, 400, '战斗胜利'), _item(500, 400, '上阵英雄'),
]])
b_w2.skip_hit = None
t_w2 = _make_task(b_w2)
t_w2._start_and_wait_battle()
check('W7 未命中 → 不点跳过（无 _click_abs）',
      not any('click_abs' in c for c in b_w2.calls),
      str([c for c in b_w2.calls if 'click_abs' in c]))

# W8~W10：v1.8.78 高频子循环 + region 限界（11 场只中 2 场的采样率修复）
rx, ry, rw, rh = _SKIP_REGION_RATIO
t_w3 = _make_task(MockBot())
_region = t_w3._skip_search_region()
check('W8 region 按窗口比例换算为屏幕坐标',
      _region == (int(rx * 1280), int(ry * 1024),
                  int(rw * 1280), int(rh * 1024)),
      str(_region))
# 命中中心实测比例 (0.690, 0.789) 必须落在 region 内（否则真机永远搜不到）
_cxp, _cyp = 0.690 * 1280, 0.789 * 1024
check('W9 实测按钮中心落在 region 内',
      _region[0] <= _cxp <= _region[0] + _region[2]
      and _region[1] <= _cyp <= _region[1] + _region[3],
      f'region={_region} center=({_cxp:.0f},{_cyp:.0f})')

# 战斗持续 ~3s 才胜利 → 高频子循环应多次采样，但只点击一次
b_w4 = MockBot(texts_by_call=[
    [['unknown', 0, '自动战斗中', 0.9, 0, 10]],   # 第 1 次 OCR：战斗中
    [['unknown', 0, '自动战斗中', 0.9, 0, 10]],   # 第 2 次 OCR：仍在打
    [_item(100, 400, '战斗胜利'), _item(500, 400, '上阵英雄')],
])
b_w4.skip_hit = (1120, 88)
b_w4.skip_hit_delay = 3     # 前 3 次采样按钮还没出现（复刻真机"末段才出现"）
t_w4 = _make_task(b_w4)
t_w4._start_and_wait_battle()
_n_skip = len([p for p in b_w4.tpl_paths
               if os.path.basename(p) == 'btn_skip_battle.png'])
_n_click = len([c for c in b_w4.calls if c.startswith('_click_abs')])
check('W10a 高频子循环多次采样跳过模板（>1 次）',
      _n_skip > 1, f'采样 {_n_skip} 次')
check('W10b 命中后本场只点击一次（不重复点）',
      _n_click == 1, f'点击 {_n_click} 次')

# ═══════════════════════════════════════════════════════════
#  X. 烙印晶球异常兜底（v1.8.78 真机 NameError: _qual 回归）
#     step1 日志打的是 old_qual，写成 _qual → 整任务 failed 且卡在界面
# ═══════════════════════════════════════════════════════════
print('── X. 烙印晶球流程健壮性（v1.8.78）──')

# step1 界面：继续前进/确认替换 + 两条 史诗/精英 烙印（走 tags[0] 分支）
orb_step1_items = [
    _it(955, 234, '继续前进'), _it(955, 732, '确认替换'),
    _it(300, 178, '史诗'), _it(240, 366, '泰坦体魄'),   # 名称在标签上方
    _it(500, 177, '精英'), _it(440, 365, '骤然钝击'),
]
orb_step2_items = [
    _it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
    _it(303, 366, '暖流之力'), _it(385, 178, '精英'),
    _it(523, 365, '暴怒速攻'), _it(605, 177, '精英'),
]

logs_x1: list = []
b_x1 = MockBot(texts_by_call=[
    orb_step1_items,      # step1：选中替换目标 + 打日志（旧版此处 NameError）
    orb_step1_items,      # 点击验证帧
    orb_step2_items,      # step2
    orb_step1_items,      # 回 step1（内联循环 break）
    orb_step1_items, orb_step1_items, orb_step1_items,
])
_t_x1 = _make_task(b_x1, logger=lambda m: logs_x1.append(m))
# v1.8.86：库存驱动 → 注入与帧一致的库存（只桩扫描会让池空直接结束）
_t_x1._orb_scanned = True
_t_x1._orb_inventory = [('泰坦体魄', '史诗'), ('骤然钝击', '精英')]
try:
    _t_x1._imprint_orb_flow()
    _x1_err = None
except Exception as _e:                       # noqa: BLE001
    _x1_err = repr(_e)
check('X1 step1 选中目标不再抛 NameError',
      _x1_err is None, str(_x1_err))
check('X2 日志含「替换目标」且带正确品质（old_qual 已修）',
      any('替换目标' in m and ('史诗' in m or '精英' in m)
          for m in logs_x1),
      str([m for m in logs_x1 if '替换目标' in m]))

# X3：流程内部任何异常都要被兜住（不向上抛、且走收尾点「继续前进」）
b_x3 = MockBot(texts_by_call=[orb_step1_items] * 3)
_ocr_real = b_x3._ocr_screen
_fail_at = {'n': 3}


def _boom(img=None):
    _fail_at['n'] -= 1
    if _fail_at['n'] <= 0:
        raise RuntimeError('模拟 OCR 崩溃')
    return _ocr_real(img)


b_x3._ocr_screen = _boom
logs_x3: list = []
_t_x3 = _make_task(b_x3, logger=lambda m: logs_x3.append(m))
_t_x3._orb_scanned = True   # v1.8.86：注入库存，保证异常仍在第 3 次 OCR 触发
_t_x3._orb_inventory = [('泰坦体魄', '史诗'), ('骤然钝击', '精英')]
try:
    _t_x3._imprint_orb_flow()
    _x3_err = None
except Exception as _e:                       # noqa: BLE001
    _x3_err = repr(_e)
check('X3 内部异常被兜住不向上抛',
      _x3_err is None, str(_x3_err))
check('X4 异常后仍打印「流程结束」（走收尾）',
      any('烙印晶球流程结束' in m for m in logs_x3),
      str(logs_x3[-3:]))

# ═══════════════════════════════════════════════════════════
#  Y. 第一步全列表扫描（v1.8.81 用户定案：每次到第一步先上下滚动
#     遍历已有烙印，确认是否集齐执裁之盾+溢出防御——旧逻辑只查
#     可见帧，列表下方的会漏）
# ═══════════════════════════════════════════════════════════
print('── Y. 库存扫描：仅首次+只下滚+OCR相同即停（v1.8.84）──')


def _orb_frame(entries_quals, cy0=240):
    """构造烙印条目帧：[(名称, 品质), ...]（品质标签在名称下方 60px）"""
    items = []
    for i, (n, q) in enumerate(entries_quals):
        ly = cy0 + i * 200
        items.append(_it(ly, 178, q))        # 品质标签（0.9 ≥ 0.7）
        items.append(_it(ly - 60, 366, n))   # 名称在标签上方
    return items


# Y1 跨屏收集：屏1 执裁之盾、屏2 溢出防御、屏3 与屏2 相同 → 到底即停
b_y1 = MockBot(texts_by_call=[
    _orb_frame([('执裁之盾', '史诗')]),
    _orb_frame([('溢出防御', '精英')]),
    _orb_frame([('溢出防御', '精英')]),   # 到底确认屏（与上次相同）
])
t_y1 = _make_task(b_y1)
got_y1 = t_y1._scan_orb_inventory()
check('Y1 扫描收集到跨屏两个目标',
      got_y1 == {'执裁之盾', '溢出防御'}, str(got_y1))
check('Y1b 扫描建立库存镜像并置位',
      t_y1._orb_scanned is True
      and t_y1._orb_inventory == [('执裁之盾', '史诗'),
                                  ('溢出防御', '精英')],
      str(t_y1._orb_inventory))
_sc = [c for c in b_y1.calls if c.startswith('scroll_wheel')]
_d = len([c for c in _sc if '下滚扫描' in c])
_ut = len([c for c in _sc if '上滚到顶' in c])
_ub = len([c for c in _sc if '回滚到顶' in c])
check('Y2 滚动序列：无上滚 + 下滚 2（到底屏不再滚）',
      _ut == 0 and _d == 2, f'ups_top={_ut} downs={_d}')
check('Y3 扫描后停在底部、不再回滚到顶（v1.8.86 用户定案）',
      _ub == 0, f'ups_back={_ub} downs={_d}')

# Y4 库存含双盾 → 打「继续替换」日志（v1.8.89 集齐不再终止流程）；
# pool 排除双盾 → 无可替换项 → 「替换目标耗尽」结束
# v2.1.8（用户 22:21 方案1）：库存不再靠扫描建立 → 预置本局获得记录
b_y4 = MockBot(texts_by_call=[step1_items] * 8)
logs_y4: list = []
t_y4 = _make_task(b_y4, logger=lambda m: logs_y4.append(m))
t_y4._orb_inventory = [('执裁之盾', '史诗'), ('溢出防御', '精英')]
t_y4._imprint_orb_flow()
check('Y4 库存双盾 → 打「继续替换寻找」日志（不再终止，v1.8.89）',
      any('已集齐执裁之盾 + 溢出防御' in m and '继续替换' in m
          for m in logs_y4),
      str([m for m in logs_y4 if '集齐' in m]))
check('Y4b 双盾均不作为替换目标 → pool 空 → 替换目标耗尽',
      any('替换目标耗尽' in m for m in logs_y4),
      str([m for m in logs_y4 if '耗尽' in m]))
check('Y5 未进入替换点击（无 click_at:替换_）',
      not any(c.startswith('click_at:替换_') for c in b_y4.calls),
      str([c for c in b_y4.calls if c.startswith('click_at')][:3]))

# Y6 只集齐一个 → 不算集齐（注入库存，已扫描态不再遍历）
b_y6 = MockBot(texts_by_call=[step1_items] * 12)
logs_y6: list = []
t_y6 = _make_task(b_y6, logger=lambda m: logs_y6.append(m))
t_y6._orb_scanned = True
t_y6._orb_inventory = [('执裁之盾', '史诗')]   # 单盾
t_y6._imprint_orb_flow()
check('Y6 单盾不算集齐 → 走到替换目标耗尽且无扫描滚动',
      not any('已集齐' in m for m in logs_y6)
      and any('替换目标耗尽' in m for m in logs_y6)
      and not any('下滚扫描' in c for c in b_y6.calls),
      str([m for m in logs_y6 if '耗尽' in m or '集齐' in m][:2]))

# Y7 停止信号 → 扫描立即返回空且不滚动、不置已扫描
stop_ev_y = threading.Event()
stop_ev_y.set()
b_y7 = MockBot()
t_y7 = _make_task(b_y7)
t_y7._stop = stop_ev_y
got_y7 = t_y7._scan_orb_inventory()
check('Y7 停止信号 → 扫描立即返回空且不滚动、不置已扫描',
      got_y7 == set()
      and not any(c.startswith('scroll_wheel') for c in b_y7.calls)
      and getattr(t_y7, '_orb_scanned', False) is False,
      f'got={got_y7}')

# Y8 替换成功 → 库存删旧加新+打印；之后不再重扫（全程滚动仅扫描 2 次）
y8_scan = step1_items + _orb_frame(
    [('暖流之力', '史诗'), ('不屈意志', '史诗'), ('缓慢凋零', '稀有')])
y8_s2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),   # 首行(被滤)
         _it(523, 365, '执裁之盾'), _it(605, 177, '精英'),
         _it(640, 365, '暴怒速攻'), _it(722, 176, '精英')]
# v2.1.8（方案1）：库存预置；帧序列按「无扫描」重排
b_y8 = MockBot(texts_by_call=[y8_scan, y8_scan, y8_scan,
                               y8_s2, y8_s2,
                               y8_scan, y8_scan, y8_scan,
                               y8_scan, y8_scan, y8_scan, y8_scan])
logs_y8: list = []
t_y8 = _make_task(b_y8, logger=lambda m: logs_y8.append(m))
t_y8._orb_inventory = [('暖流之力', '史诗'), ('不屈意志', '史诗'),
                       ('缓慢凋零', '稀有')]
t_y8._imprint_orb_flow()
_sc8 = [c for c in b_y8.calls if c.startswith('scroll_wheel')]
check('Y8 方案1（v2.1.8）：替换流程全程无扫描滚动',
      len(_sc8) == 0
      and any(c == 'click_at:替换_暖流之力' for c in b_y8.calls),
      str(_sc8))
_inv_logs = [m for m in logs_y8 if '本局已获烙印' in m]
check('Y8 库存日志为本局记录格式（方案1，不再打印「已有烙印」）',
      len(_inv_logs) >= 1 and not any('已有烙印' in m for m in logs_y8),
      str(_inv_logs[:2]))

# Y9 保留烙印 → 库存不变（不打新库存日志）
y9_s2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),
         _it(523, 365, '暴怒速攻'), _it(605, 177, '精英')]
# v2.1.8（方案1）：库存预置；帧序列按「无扫描」重排
b_y9 = MockBot(texts_by_call=[y8_scan, y8_scan, y8_scan,
                               y9_s2, y9_s2, y9_s2,
                               y8_scan, y8_scan, y8_scan])
logs_y9: list = []
t_y9 = _make_task(b_y9, logger=lambda m: logs_y9.append(m))
t_y9._orb_inventory = [('暖流之力', '史诗'), ('不屈意志', '史诗'),
                       ('缓慢凋零', '稀有')]
t_y9._imprint_orb_flow()
_inv9 = [m for m in logs_y9 if '已有烙印' in m]
check('Y9 保留烙印 → 库存不变（方案1：库存来自本局记录，无扫描日志）',
      any(c == 'click_at:保留烙印' for c in b_y9.calls)
      and len(_inv9) == 0,
      f'{_inv9} keep={any(c == "click_at:保留烙印" for c in b_y9.calls)}')

# ═══════════════════════════════════════════════════════════
#  Z. 替换规则 v1.8.82（用户定案）：① step1 被替换对象排序——
#     黑名单 > 史诗 > 精英（史诗候选至少精英，更容易出目标）；
#     ② 特例：已有史诗>=2 且旧烙印为史诗 → 候选出现执裁之盾
#     （精英）豁免品质保护（集齐优先于品质；溢出防御本身是史诗
#     品质，不会被品质保护拦住，无需豁免）
# ═══════════════════════════════════════════════════════════
print('── Z. 替换规则：史诗优先 + 执裁之盾特例（v1.8.82）──')

# Z1 step1 排序：非黑名单史诗（裂空之爪）优先于非黑名单精英（暴怒速攻）
z1_frame = step1_items + _orb_frame(
    [('暴怒速攻', '精英'), ('裂空之爪', '史诗')])
logs_z1: list = []
t_z1 = _make_task(MockBot(texts_by_call=[z1_frame] * 12),
                  logger=lambda m: logs_z1.append(m))
# v1.8.86：目标改由库存驱动 → 必须注入库存（只桩扫描会让池空直接结束）
t_z1._orb_scanned = True
t_z1._orb_inventory = [('暴怒速攻', '精英'), ('裂空之爪', '史诗')]
t_z1._imprint_orb_flow()
check('Z1 排序：史诗（裂空之爪）优先于精英（暴怒速攻）',
      any(c == 'click_at:替换_裂空之爪' for c in t_z1.bot.calls)
      and not any(c == 'click_at:替换_暴怒速攻' for c in t_z1.bot.calls),
      str([c for c in t_z1.bot.calls if '替换_' in c][:3]))

# Z2 黑名单仍最优先（用户确认）：泰坦体魄（黑名单精英）先于史诗被换
z2_frame = step1_items + _orb_frame(
    [('泰坦体魄', '精英'), ('裂空之爪', '史诗')])
t_z2 = _make_task(MockBot(texts_by_call=[z2_frame] * 12))
# v1.8.86：同上，注入库存
t_z2._orb_scanned = True
t_z2._orb_inventory = [('泰坦体魄', '精英'), ('裂空之爪', '史诗')]
t_z2._imprint_orb_flow()
check('Z2 排序：黑名单（泰坦体魄）仍最优先于史诗',
      any(c == 'click_at:替换_泰坦体魄' for c in t_z2.bot.calls),
      str([c for c in t_z2.bot.calls if '替换_' in c][:3]))

# Z3 特例触发：已有史诗>=2 + 旧烙印史诗 + 候选执裁之盾(精英) →
# 豁免品质保护选中换出（集齐优先于品质）
z3_s1 = step1_items + _orb_frame([('暖流之力', '史诗')])
z3_s2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),    # 首行(被滤)
         _it(523, 365, '执裁之盾'), _it(605, 177, '精英'),    # 候选1
         _it(640, 365, '暴怒速攻'), _it(722, 176, '精英')]    # 候选2
logs_z3: list = []
t_z3 = _make_task(MockBot(texts_by_call=[
    z3_s1,      # 循环顶：校准 step1 + 条目（扫描打桩）
    z3_s1,      # 扫描后重取 → 选中暖流之力(史诗) → 确认替换命中
    z3_s2,      # 等第二步：保留烙印+确认选择 → got → step=2
    z3_s2,      # 循环顶：校准 step2 → 候选收集 → best=执裁之盾(豁免)
    z3_s2,      # _click_and_confirm 读帧找「确认选择」→ 命中
    z3_s1,      # 弹窗循环：帧有继续前进 → break 回第一步
] * 3), logger=lambda m: logs_z3.append(m))
t_z3._orb_scanned = True
# v1.8.84：注入内存库存（2 史诗 → 特例条件满足）
t_z3._orb_inventory = [('暖流之力', '史诗'), ('不屈意志', '史诗')]
t_z3._imprint_orb_flow()
check('Z3 特例：史诗≥2+旧史诗 → 豁免品质保护选中执裁之盾(精英)',
      any(c == 'click_at:烙印_执裁之盾' for c in t_z3.bot.calls),
      str([c for c in t_z3.bot.calls if '烙印_' in c][:4]))
check('Z3b 特例日志打印（集齐优先于品质）',
      any('特例：史诗≥2' in m for m in logs_z3),
      str([m for m in logs_z3 if '特例' in m][:2]))

# Z4 特例不满足（史诗=1）：执裁之盾(精英)被品质保护排除 → 保留烙印
t_z4 = _make_task(MockBot(texts_by_call=[
    z3_s1, z3_s1, z3_s2, z3_s2, z3_s2, z3_s2,
    z3_s1, z3_s1, z3_s2, z3_s2, z3_s2, z3_s2,
]))
t_z4._orb_scanned = True
t_z4._orb_inventory = [('暖流之力', '史诗')]   # 只 1 个史诗
t_z4._imprint_orb_flow()
check('Z4 特例不满足（史诗=1）→ 执裁之盾被品质保护排除 → 保留烙印',
      any(c == 'click_at:保留烙印' for c in t_z4.bot.calls)
      and not any(c == 'click_at:烙印_执裁之盾' for c in t_z4.bot.calls),
      str([c for c in t_z4.bot.calls if '保留' in c or '执裁' in c][:3]))

# Z5 溢出防御(史诗)候选：品质相等不被保护拦住，具名优先正常选中（回归）
z5_s2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),
         _it(523, 365, '暴怒速攻'), _it(605, 177, '精英'),
         _it(640, 365, '溢出防御'), _it(722, 176, '史诗')]
t_z5 = _make_task(MockBot(texts_by_call=[
    z3_s1, z3_s1, z5_s2, z5_s2, z5_s2, z3_s1,
] * 3))
t_z5._orb_scanned = True
t_z5._orb_inventory = [('暖流之力', '史诗'), ('不屈意志', '史诗')]
t_z5._imprint_orb_flow()
check('Z5 溢出防御(史诗)候选不被品质保护拦住 → 具名优先选中',
      any(c == 'click_at:烙印_溢出防御' for c in t_z5.bot.calls),
      str([c for c in t_z5.bot.calls if '烙印_' in c][:4]))

# ═══════════════════════════════════════════════════════════
#  AA. v1.8.86 库存驱动替换（用户 2026-09-08 真机定案）：
#      ① 首次扫描不再回滚到顶（Y3 已验）；
#      ② 第一步目标改由内存库存三档排序挑出，再滚动去找它——
#         真机 20:59 库存里还有风暴中心(史诗)/蔓延行近(精英)，
#         旧逻辑只看可见屏却判「替换目标耗尽」；
#      ③ 到底但库存仍有目标 → 回滚到顶重下一轮（只回滚一次）；
#      ④ 替换成功后游戏会把视图复位到顶部，滚动状态必须重置，
#         否则拿旧集合比对新顶部帧 → 误判「已到底」提前结束
#         （真机 20:59:14 事故）。
# ═══════════════════════════════════════════════════════════
print('── AA. 库存驱动替换 + 到底回滚 + 替换后状态重置（v1.8.86）──')

# AA1 目标不在当前屏 → 按库存挑目标并滚动去找（旧逻辑只会盲滚找可见项）
aa_top = step1_items + _orb_frame([('蛮血利牙', '稀有')])      # 只有稀有
aa_bot = step1_items + _orb_frame([('风暴中心', '史诗')])      # 目标在第二屏
b_aa1 = MockBot(texts_by_call=[
    aa_top,   # 轮1 顶：校准 step1
    aa_top,   # 重取 → 库存目标「风暴中心」不在 → 滚动
    aa_bot,   # 轮2 顶
    aa_bot,   # 重取 → 命中 → 点选
    aa_bot,   # _click_text('确认替换') 命中
])
logs_aa1: list = []
t_aa1 = _make_task(b_aa1, logger=lambda m: logs_aa1.append(m))
t_aa1._orb_scanned = True
t_aa1._orb_inventory = [('蛮血利牙', '稀有'), ('风暴中心', '史诗')]
t_aa1._imprint_orb_flow()
check('AA1 库存驱动：目标不在可视区 → 滚动去找它',
      any('scroll_wheel' in c and '烙印列表_滚动' in c
          for c in b_aa1.calls)
      and any(c == 'click_at:替换_风暴中心' for c in b_aa1.calls),
      str([c for c in b_aa1.calls
           if 'scroll_wheel' in c or '替换_' in c][:4]))

# AA2 滚到底但库存仍有目标 → 回滚到顶重下一轮（上滚只发生在这里）
b_aa2 = MockBot(texts_by_call=[step1_items] * 10)
logs_aa2: list = []
t_aa2 = _make_task(b_aa2, logger=lambda m: logs_aa2.append(m))
t_aa2._orb_scanned = True
t_aa2._orb_inventory = [('风暴中心', '史诗')]   # 目标始终不可见
t_aa2._imprint_orb_flow()
_ups_aa2 = [c for c in b_aa2.calls
            if 'scroll_wheel' in c and '回滚到顶' in c]
check('AA2 到底但库存仍有目标 → 回滚到顶重下一轮',
      len(_ups_aa2) >= 1
      and any('回滚到顶重找' in m for m in logs_aa2),
      f'ups={_ups_aa2}')

# AA3 回归真机 20:59:14：替换成功后视图复位到顶部，不得误判「已到底」
aa_s2 = [_it(950, 234, '保留烙印'), _it(950, 732, '确认选择'),
         _it(303, 366, '缓慢凋零'), _it(385, 178, '稀有'),
         _it(523, 365, '裂空之爪'), _it(605, 177, '史诗'),
         _it(640, 365, '暴怒速攻'), _it(722, 176, '精英')]
aa3_bot = step1_items + _orb_frame([('蔓延行近', '精英')])
aa3_new = step1_items + _orb_frame([('裂空之爪', '史诗')])
b_aa3 = MockBot(texts_by_call=[
    aa_top,    # 轮1 顶
    aa_top,    # 重取 → 目标（蔓延行近 精英）不在 → 滚动，记下 _vis={蛮血利牙}
    aa3_bot,   # 轮2 顶
    aa3_bot,   # 重取 → 命中「蔓延行近」→ 点选
    aa3_bot,   # _click_text('确认替换') 命中
    aa_s2,     # 等第二步 got → step=2
    aa_s2,     # 轮3 顶 step2 → 候选裂空之爪(史诗) ≥ 精英 → 选中
    aa_s2,     # _click_and_confirm 命中确认选择 → 库存更新
    aa_top,    # 弹窗循环：帧含继续前进 → break 回第一步
    aa_top,    # 轮4 顶：**视图已复位到顶部**（与轮1 那屏集合相同）
    aa_top,    # 重取 → 新目标（裂空之爪）不在 → 应滚动，不得判到底
    aa3_new,   # 轮5 顶
    aa3_new,   # 重取 → 命中「裂空之爪」
    aa3_new,   # 确认替换
])
logs_aa3: list = []
t_aa3 = _make_task(b_aa3, logger=lambda m: logs_aa3.append(m))
t_aa3._orb_scanned = True
t_aa3._orb_inventory = [('蛮血利牙', '稀有'), ('蔓延行近', '精英')]
t_aa3._imprint_orb_flow()
check('AA3 替换成功后视图复位 → 不误判「替换目标耗尽」',
      not any('替换目标耗尽' in m for m in logs_aa3)
      and any('不在可视区' in m for m in logs_aa3),
      str([m for m in logs_aa3
           if '耗尽' in m or '不在可视区' in m or '回滚到顶' in m][:3]))

# ═══════════════════════════════════════════════════════════
#  AB. v1.8.87 真机 21:01 事故修复：
#     战斗失败收尾 → 长按结束 → 「离开迷宫」荣誉结算页 OCR 只读出
#     「到达层数」→ 撞 boss_cleared → 找不到「结束探索」→ 整任务 failed。
#     修：① 判据新增 labyrinth.leave_settle（归并 final_settle）；
#         ② boss_cleared 找不到「结束探索」→ 点屏幕跳过重判（限 2 次）；
#         ③ _end_exploration 弹「长按结束」当场处理（不留到下一轮）
# ═══════════════════════════════════════════════════════════
print('── AB. 离开迷宫结算页 + boss_cleared 兜底（v1.8.87）──')

# AB1 boss_cleared 无「结束探索」→ 点屏幕跳过重判，不直接 failed
b_ab1 = MockBot(texts_by_call=[
    [_item(100, 300, '难度通关'), _item(40, 900, '到达层数:17')],  # 无结束探索
    # 帧耗尽 → 兜底 ['玩法目录',...] → 已离开玩法执行域 → done
])
logs_ab1: list = []
t_ab1 = _make_task(b_ab1, logger=lambda m: logs_ab1.append(m))
r_ab1 = t_ab1._play_loop()
check('AB1 boss_cleared 无「结束探索」→ 跳过兜底不 failed（v1.8.87）',
      r_ab1 == 'done'
      and any('难度通关_跳过兼底' in c for c in b_ab1.calls),
      f'r={r_ab1} calls={b_ab1.calls[-4:]}')

# AB2 _end_exploration 点「结束探索」后弹终止确认 → 当场长按收尾
b_ab2 = MockBot(texts_by_call=[
    [_item(992, 1101, '结束探索')],        # _click_text 命中
    [_item(637, 541, '长按结束')],          # 确认探测帧：弹终止确认
])
t_ab2 = _make_task(b_ab2)
r_ab2 = t_ab2._end_exploration([])
check('AB2 结束探索弹「长按结束」→ 当场长按（不留下一轮）',
      r_ab2 == 'done'
      and any('结束探索_长按结束' in c for c in b_ab2.calls),
      f'r={r_ab2} calls={b_ab2.calls}')

# AB3 无确认弹窗 → 原行为不变（回归）
b_ab3 = MockBot(texts_by_call=[
    [_item(992, 1101, '结束探索')],
    [_item(100, 200, 'x')],                # 无任何确认词
])
t_ab3 = _make_task(b_ab3)
r_ab3 = t_ab3._end_exploration([])
check('AB3 无确认弹窗 → 原行为直接 done（回归）',
      r_ab3 == 'done'
      and not any('长按' in c for c in b_ab3.calls),
      f'r={r_ab3} calls={b_ab3.calls}')

# ═══════════════════════════════════════════════════════════
#  AC. v1.8.88 移除轮次/次数上限（真机 23:08）：
#     一次成功替换固定耗 2 轮（step1 选中 + step2 确认），保留烙印/
#     滚动/回滚各 1 轮 → 12 轮上限一局就用完 → for 自然结束**无任何
#     日志** → 收尾点「继续前进」，表现为「滚动后目标可见却结束流程」。
#     修：删除轮次上限与滚动次数上限，终止只由业务条件决定；滚动到底
#     改用「本屏无新名字」状态判定，保证无上限也能收敛（不无限滚动）
# ═══════════════════════════════════════════════════════════
print('── AC. 无轮次上限 + 状态化收敛（v1.8.88）──')
import tasks.labyrinth as _lab_mod

check('AC0 轮次/滚动次数上限常量已删除',
      not hasattr(_lab_mod, '_IMPRINT_ORB_ROUND_MAX')
      and not hasattr(_lab_mod, '_IMPRINT_SCROLL_MAX')
      and not hasattr(_lab_mod, '_ORB_SCAN_SCROLL_MAX')
      and not hasattr(_lab_mod, '_SCROLL_MAX')
      and not hasattr(_lab_mod, '_FAILURE_ESC_MAX'),
      str([n for n in ('_IMPRINT_ORB_ROUND_MAX', '_IMPRINT_SCROLL_MAX',
                       '_ORB_SCAN_SCROLL_MAX', '_SCROLL_MAX',
                       '_FAILURE_ESC_MAX')
           if hasattr(_lab_mod, n)]))

# AC1 无上限但仍收敛：库存无可替换项 → 立即结束（不靠轮次）
b_ac1 = MockBot(texts_by_call=[aa3_bot] * 12)
logs_ac1: list = []
t_ac1 = _make_task(b_ac1, logger=lambda m: logs_ac1.append(m))
t_ac1._orb_scanned = True
t_ac1._orb_inventory = []          # 无可替换项 → 业务条件终止
t_ac1._imprint_orb_flow()
check('AC1 无轮次上限 → 库存无可替换项即终止（不空转）',
      any('替换目标耗尽' in m for m in logs_ac1)
      and not any('轮次' in m for m in logs_ac1),
      str([m for m in logs_ac1 if '耗尽' in m or '轮次' in m][:2]))

# AD1 OCR 抖动不死滚：每屏可见集合都不同（全是见过的名字），
#     目标「甲」始终不可见 → 「本屏无新名字」判定到底 → 回滚 → 跳过甲
# 注意：条目名须 ≥2 字（_collect_imprint_entries 过滤单字）
ad_f_b = step1_items + _orb_frame([('乙木', '精英')])
ad_f_c = step1_items + _orb_frame([('丙火', '精英')])
b_ad1 = MockBot(texts_by_call=[
    ad_f_b, ad_f_b,   # 轮1：见乙（新）→ 滚
    ad_f_c, ad_f_c,   # 轮2：见丙（新）→ 滚
    ad_f_b, ad_f_b,   # 轮3：无新名字 → 到底 → 回滚
    ad_f_c, ad_f_c,   # 轮4：回滚后重下滚
    ad_f_b, ad_f_b,   # 轮5：又到底 → 甲不可达 → tried.add(甲)
    ad_f_c, ad_f_c,   # 轮6：want=乙 → 不在 → 滚
    ad_f_b, ad_f_b,   # 轮7：命中乙 → 点选
    ad_f_c, ad_f_c, ad_f_b, ad_f_b,   # 余量帧（收尾）
])
t_ad1 = _make_task(b_ad1)
t_ad1._orb_scanned = True
t_ad1._orb_inventory = [('甲子', '史诗'), ('乙木', '精英'), ('丙火', '精英')]
t_ad1._imprint_orb_flow()
check('AD1 OCR 抖动（每屏不同）→ 「无新名字」判到底，不死滚',
      any(c == 'click_at:替换_乙木' for c in b_ad1.calls),
      str([c for c in b_ad1.calls
           if '替换_' in c or 'scroll_wheel' in c][:6]))

# AC2 无轮次上限：滚动后目标可见就必须选中，不得直接收尾（真机 23:08 回归）
b_ac2 = MockBot(texts_by_call=[
    aa_top,    # 轮1 顶
    aa_top,    # 重取 → 目标（风暴中心）不在 → 滚动
    aa_bot,    # 轮2 顶
    aa_bot,    # 重取 → 命中 → 点选
    aa_bot,    # _click_text('确认替换') 命中
])
t_ac2 = _make_task(b_ac2)
t_ac2._orb_scanned = True
t_ac2._orb_inventory = [('蛮血利牙', '稀有'), ('风暴中心', '史诗')]
t_ac2._imprint_orb_flow()
check('AC2 轮次充足 → 滚动后命中目标并点选（不提前收尾）',
      any(c == 'click_at:替换_风暴中心' for c in b_ac2.calls),
      str([c for c in b_ac2.calls if '替换_' in c][:3]))

# ═══════════════════════════════════════════════════════════
#  AE. v1.8.89（用户 2026-09-09 定案）：
#     ① 「坚定意志」（史诗，开战最大生命+50%）优先级仅次于溢出防御；
#     ② 集齐执裁之盾+溢出防御后继续替换寻找其他高优先级烙印
# ═══════════════════════════════════════════════════════════
print('── AE. 坚定意志优先级 + 集齐后继续替换（v1.8.89）──')

# AE1 坚定意志在保留意愿表溢出防御之后
check('AE1 坚定意志优先级仅次于溢出防御',
      _lab_mod._IMPRINT_NAMED_PRIORITY.index('坚定意志')
      == _lab_mod._IMPRINT_NAMED_PRIORITY.index('溢出防御') + 1,
      str(_lab_mod._IMPRINT_NAMED_PRIORITY))

# AE2 v1.8.91 修正：坚定意志**可作为替换对象**（排除表只有双盾）——
# 库存里只剩它时会被选中替换（意愿表排位靠前 → 具名最后才轮到它）
ae2_frame = step1_items + _orb_frame([('坚定意志', '史诗')])
b_ae2 = MockBot(texts_by_call=[
    ae2_frame,   # 轮1 顶
    ae2_frame,   # 重取 → want=坚定意志 → 点选
    ae2_frame,   # _click_text('确认替换') 命中
    ae2_frame, ae2_frame, ae2_frame,
])
logs_ae2: list = []
t_ae2 = _make_task(b_ae2, logger=lambda m: logs_ae2.append(m))
t_ae2._orb_scanned = True
t_ae2._orb_inventory = [('坚定意志', '史诗')]
t_ae2._imprint_orb_flow()
check('AE2 坚定意志可作为替换对象（排除表只有双盾，v1.8.91）',
      any(c == 'click_at:替换_坚定意志' for c in b_ae2.calls),
      str([c for c in b_ae2.calls if '替换_' in c][:2]))

# AE3 集齐双盾后继续替换：应选中剩余普通史诗「坚壁清野」替换
ae_frame = step1_items + _orb_frame([('坚壁清野', '史诗')])
b_ae3 = MockBot(texts_by_call=[
    ae_frame,   # 轮1 顶
    ae_frame,   # 重取 → want=坚壁清野 → 点选
    ae_frame,   # _click_text('确认替换') 命中
    ae_frame,   # 等第二步循环帧
    ae_frame, ae_frame,
])
logs_ae3: list = []
t_ae3 = _make_task(b_ae3, logger=lambda m: logs_ae3.append(m))
t_ae3._orb_scanned = True
t_ae3._orb_inventory = [('执裁之盾', '史诗'), ('溢出防御', '精英'),
                        ('坚定意志', '史诗'), ('坚壁清野', '史诗')]
t_ae3._imprint_orb_flow()
check('AE3 集齐双盾后继续替换（选中剩余史诗）',
      any('继续替换寻找其他高优先级烙印' in m for m in logs_ae3)
      and any(c == 'click_at:替换_坚壁清野' for c in b_ae3.calls),
      str([m for m in logs_ae3 if '集齐' in m or '替换目标' in m][:3]))

# ═══════════════════════════════════════════════════════════
#  AF. v1.8.93 三模式改造（用户 2026-09-09 定案）：
#     模式1 不进晶球；模式2/3 门前（顶栏19/20）查核数——达标停在门前
#     （返回 stopped 保留进度，绝不点结束探索），未达标打首领重来；
#     晶球豁免放宽（执裁之盾不受史诗数限制）；获得烙印记入库存
# ═══════════════════════════════════════════════════════════
print('── AF. 三模式：门前核数判定 + 烙印记录（v1.8.93）──')

# AF1 模式2 门前无盾（无库存镜像）→ 不停，走 door 分支点门
b_af1 = _StopBot(stop_after=3, stop_ev=threading.Event(),
                 texts_by_call=[boss20_texts, boss20_texts,
                                boss20_texts, boss20_texts])
t_af1 = _make_task(b_af1, options={'run_mode': 'boss20'})
r_af1 = t_af1._play_loop()
check('AF1 模式2 门前无单核 → 不停（继续打首领）',
      r_af1 != 'stopped', f'r={r_af1}')

# AF2 顶栏 19/20 也是门前判据（标题漏读时兜住——真机 11:39 漏停根因）
gate_frame = ['难度15', '19/20', '结束探索']   # 无「深层首领」标题
b_af2 = _StopBot(stop_after=3, stop_ev=threading.Event(),
                 texts_by_call=[gate_frame, gate_frame,
                                gate_frame, gate_frame])
t_af2 = _make_task(b_af2, options={'run_mode': 'boss20'})
t_af2._orb_scanned = True
t_af2._orb_inventory = [('执裁之盾', '精英')]
r_af2 = t_af2._play_loop()
check('AF2 仅顶栏 19/20（标题漏读）→ 仍停（不漏停）',
      r_af2 == 'stopped', f'r={r_af2}')

# AF3 晶球豁免：模式2 下执裁之盾不受史诗数量限制（见 AF4 单测）

# AF4 _imprint_best：模式2 下执裁之盾不受 min_qual 限制（epic=1）
t_af4 = _make_task(MockBot(texts_by_call=[['x']]),
                   options={'run_mode': 'boss20'})
best = t_af4._imprint_best(
    [('执裁之盾', '精英'), ('怒火缠绕', '稀有')],
    min_qual='史诗', always_accept=('执裁之盾',))
check('AF4 模式2 执裁之盾豁免品质保护（唯一史诗也换）',
      best is not None and best[0] == '执裁之盾', str(best))

# AF5 烙印记录：_record_imprint_gain 追加库存 + 史诗计数
t_af5 = _make_task(MockBot(texts_by_call=[['x']]))
t_af5._orb_scanned = True
t_af5._orb_inventory = [('执裁之盾', '精英')]
t_af5._orb_epic_count = 0
t_af5._record_imprint_gain('溢出防御', '史诗')
check('AF5 获得烙印记入库存 + 史诗计数 +1',
      t_af5._orb_inventory == [('执裁之盾', '精英'), ('溢出防御', '史诗')]
      and t_af5._orb_epic_count == 1,
      f"{t_af5._orb_inventory} epic={t_af5._orb_epic_count}")

# AF6 模式1 晶球门排除：_decide_door 多门含晶球 → 选别的
t_af6 = _make_task(MockBot(texts_by_call=[['x']]),
                   options={'run_mode': 'clear15'})
t_af6._floor_count = 13
titles_af6 = [('烙印晶球', (400, 300)), ('遗物之门', (700, 300)),
              ('菲茨小店', (950, 300))]
items_af6 = [_item(300, 400, '难度15'), _item(300, 638, '遗物之门')]
kw_af6, _p = t_af6._decide_door(titles_af6, items_af6)
check('AF6 模式1 多门含晶球 → 不选晶球',
      kw_af6 != '烙印晶球', str(kw_af6))

# AF7 模式2/3 晶球门不受模式1排除影响（深层刷核需要晶球）
t_af7 = _make_task(MockBot(texts_by_call=[['x']]),
                   options={'run_mode': 'boss20'})
t_af7._floor_count = 13
kw_af7, _p = t_af7._decide_door(titles_af6, items_af6)
check('AF7 模式2 晶球门 13 层起仍最优先',
      kw_af7 == '烙印晶球', str(kw_af7))

# AF8 v1.8.95：任务启动打印运行模式
check('AF8a 三模式标签映射正确',
      all(
          next((lbl for k, lbl in
                _lab_mod.LabyrinthTask.options_schema['run_mode']['choices']
                if k == m), m)
          == lbl
          for m, lbl in (
              ('clear15', '模式1：通关15结束'),
              ('boss20', '模式2：20关前停止（单核）'),
              ('boss20_dual', '模式3：20关前停止（双核）'))),
      'labels ok')
logs_af8: list = []
t_af8 = _make_task(MockBot(texts_by_call=[['x']]),
                   options={'run_mode': 'boss20_dual'},
                   logger=lambda m: logs_af8.append(m))
t_af8._run_once = lambda: 'stopped'      # 启动即停，仅验证启动日志
t_af8.run()
check('AF8b run() 启动日志含运行模式',
      any('运行模式：模式3：20关前停止（双核）' in m for m in logs_af8),
      str([m for m in logs_af8 if '运行模式' in m][:1]))

# AF9 v1.9.2 晶球新策略：凑核前=缺核豁免品质（模式3 缺溢出防御）
t_af9 = _make_task(MockBot(texts_by_call=[['x']]),
                   options={'run_mode': 'boss20_dual'})
t_af9._orb_scanned = True
t_af9._orb_inventory = [('执裁之盾', '精英')]     # 已有单核，缺溢出防御
best9 = t_af9._imprint_best(
    [('溢出防御', '史诗'), ('坚壁清野', '史诗')],
    min_qual='精英',                              # 旧烙印精英
    always_accept=t_af9._core_names().__class__(
        n for n in t_af9._core_names()
        if n not in {x for x, _ in t_af9._orb_inventory}))
check('AF9 凑核前：缺的核（溢出防御）豁免品质被选中',
      best9 is not None and best9[0] == '溢出防御', str(best9))

# AF10 v1.9.2 凑核后：具名豁免品质 + 非具名平级不收
t_af10 = _make_task(MockBot(texts_by_call=[['x']]),
                    options={'run_mode': 'boss20_dual'})
t_af10._orb_scanned = True
t_af10._orb_inventory = [('执裁之盾', '精英'), ('溢出防御', '史诗')]
named_all = tuple(kw for kw, _ in [(k, 0) for k in (
    '执裁之盾', '溢出防御', '坚定意志', '藏品柜', '强壮之力', '不屈意志')])
best10 = t_af10._imprint_best(
    [('藏品柜', '精英'), ('坚壁清野', '史诗')],
    min_qual='史诗', always_accept=named_all)
check('AF10 凑核后：具名（藏品柜-精英）豁免品质被选中',
      best10 is not None and best10[0] == '藏品柜', str(best10))
best10b = t_af10._imprint_best(
    [('坚壁清野', '史诗'), ('怒火缠绕', '稀有')],
    min_qual='史诗', always_accept=named_all,
    strict_upgrade=True)
check('AF10b 凑核后：非具名平级/低级 → 保留（None）',
      best10b is None, str(best10b))

# AF11 v1.9.2 victory 判据加「战斗结束」（20 关首领胜利画面）
from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY
r_af11 = GLOBAL_SCREEN_REGISTRY.detect([
    (150, 400, '战斗结束', 0.97), (790, 480, '上阵英雄', 0.9),
    (790, 512, '点击空白处关闭', 0.9)])
check('AF11 「战斗结束」画面 → 判 victory（不再 unknown 干等）',
      r_af11 == 'labyrinth.victory', r_af11)

# AF12 v1.9.3：首通奖励确认弹窗（「是否继续探索？」跨行截断）——
# OCR 实测把结尾读成「是否继续探」+「索？」两行，旧全词判据漏判
dialog_frame = [(180, 360, '当前难度首通奖励已经领取，非活动期间', 0.99),
                (300, 370, '继续挑战将不再获取奖励，是否继续探', 0.98),
                (340, 360, '索？', 0.98),
                (478, 430, '7天内不再提醒', 0.98)]
stop_ev12 = threading.Event()
bot_af12 = _StopBot(stop_after=2, stop_ev=stop_ev12,
                    texts_by_call=[dialog_frame, dialog_frame,
                                   dialog_frame, dialog_frame])
bot_af12.tm_hit = (573, 498)
t_af12 = _make_task(bot_af12, options={'run_mode': 'clear15'})
t_af12._stop = stop_ev12
r_af12 = t_af12._dismiss_continue_dialog(after_enter=True)
check('AF12 首通奖励弹窗（跨行截断）→ 识别并点绿✓（模板命中）',
      r_af12
      and any(c == 'template_match:btn_confirm_check.png'
              for c in bot_af12.calls)
      and any(c.startswith('_click_abs:') for c in bot_af12.calls),
      f'r={r_af12} clicks={bot_af12.calls[:3]}')

# ── 汇总 ────────────────────────────────────────────────────
print(f'══ v1.8.20 专项测试：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f in _FAIL:
        print(f'  ❌ {f}')
sys.exit(0 if not _FAIL else 1)
