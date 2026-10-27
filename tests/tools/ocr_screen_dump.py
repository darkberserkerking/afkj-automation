# -*- coding: utf-8 -*-
"""游戏截图界面特征提取器：OCR 全图文字 + 中心坐标，按 y 排序输出。

用途：真机出 bug 时，把截图喂进来拿到"界面上到底有哪些字、各自在哪"，
用于反推 detector 判据为什么落空 / 设计新的判定特征词。

用法（改下面的 SHOTS 列表即可）：
    "D:/anaconda3/envs/afk_gpu/python.exe" tests/ocr_screen_dump.py

首次用于：v1.8.3 迷宫续局导航修复（定位到「继续进度」界面无「异界
迷宫」标题也无「探索阵容」→ detector labyrinth 判据落空 → 判 unknown）。

MaaOCR 三个坑（已在此脚本规避，别改回去）：
  1. 方法名是 recognize() 不是 ocr()
  2. 只能传 PIL.Image / ndarray，不能传路径字符串
  3. 小图会被内部拉伸到 736 反而识别失败 → 必须整图识别，不要切片
"""
import sys

sys.path.insert(0, r'C:\Users\x9502\WorkBuddy\20260320211537')

from PIL import Image
from maa_ocr import MaaOCR

ROOT = r'C:\Users\x9502\WorkBuddy\20260320211537'
SHOTS = [
    (r'C:\Users\x9502\Pictures\Screenshots\图1.png', '图1'),
    (r'C:\Users\x9502\Pictures\Screenshots\图2.png', '图2'),
]


def main() -> None:
    ocr = MaaOCR(model_dir=ROOT + r'\model\ocr')
    for path, tag in SHOTS:
        img = Image.open(path).convert('RGB')
        w, h = img.size
        print(f'\n{"=" * 70}\n[{tag}] {path}  尺寸={w}x{h}\n{"=" * 70}')
        res = ocr.recognize(img)
        rows = []
        for item in res:
            text, box, score = item[0], item[1], item[2]
            cx = sum(p[0] for p in box) / len(box)
            cy = sum(p[1] for p in box) / len(box)
            rows.append((cy, cx, text, score))
        rows.sort()
        print(f'{"序号":<5}{"文本":<28}{"中心(cx,cy)":<20}{"置信度"}')
        for i, (cy, cx, text, score) in enumerate(rows, 1):
            print(f'{i:<5}{text:<28}({int(cx)},{int(cy)}){" " * 8}{score:.2f}')


if __name__ == '__main__':
    main()
