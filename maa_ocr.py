"""
MaaStyle OCR Engine — 借鉴 MaaFramework OCR 架构

核心设计（对标 MaaFramework）：
  - PP-OCRv5 mobile det + rec（ONNX Runtime CPU，无 PaddlePaddle 依赖）
  - ROI 裁剪：只扫描目标区域，减少 90%+ 计算量
  - color_filter：颜色预过滤，消除背景干扰
  - expected 正则：声明式文字匹配（支持多语言）
  - box_index：多文本框中选择第 N 个
  - only_rec：纯识别不点击

依赖：pip install onnxruntime numpy Pillow opencv-python pyclipper shapely
模型：model/ocr/ 目录下 det.onnx + rec.onnx + keys.txt
"""

import os
import re
import time
import numpy as np
import cv2
import pyclipper
from shapely.geometry import Polygon
import onnxruntime as ort
from PIL import Image

# 默认模型路径（相对于脚本所在目录）
_DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model', 'ocr')


class MaaOCR:
    """MaaFramework 风格 OCR 引擎

    PP-OCRv5 mobile det + rec，通过 ONNX Runtime CPU 推理。
    预处理和后处理完全对标 RapidOCR / PaddleOCR 官方实现。

    用法::

        ocr = MaaOCR()
        results = ocr.recognize(image, roi=(100, 200, 300, 40))
        # results = [(text, box, score), ...]
        # box = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    """

    # DB 算法参数（完全对标 RapidOCR PP-OCRv5 默认值）
    DET_DB_THRESH = 0.3           # 二值化阈值
    DET_DB_BOX_THRESH = 0.7       # box 置信度阈值
    DET_DB_UNCLIP_RATIO = 2.0     # Vatti clipping 扩展比例
    DET_LIMIT_SIDE_LEN = 736      # 输入最小边长（RapidOCR 默认）
    DET_LIMIT_TYPE = 'min'        # 'min'（RapidOCR 默认）
    MAX_CANDIDATES = 1000         # 最大候选框数
    # 最小文本框短边（像素）
    MIN_BOX_SIDE = 3

    def __init__(self, model_dir=None):
        model_dir = model_dir or _DEFAULT_MODEL_DIR
        det_path = os.path.join(model_dir, 'det.onnx')
        rec_path = os.path.join(model_dir, 'rec.onnx')
        keys_path = os.path.join(model_dir, 'keys.txt')

        if not all(os.path.exists(p) for p in [det_path, rec_path, keys_path]):
            raise FileNotFoundError(
                f"OCR 模型文件不完整，请确保以下文件存在:\n"
                f"  {det_path}\n  {rec_path}\n  {keys_path}")

        # 加载字符表（对标 RapidOCR CTCLabelDecode）
        with open(keys_path, 'rb') as f:
            self.char_list = [
                line.decode('utf-8').strip('\n').strip('\r\n')
                for line in f.readlines()
            ]
        # 插入特殊字符（和 RapidOCR 一致）
        # 空格插到末尾，blank 插到索引 0
        self.char_list.insert(len(self.char_list), ' ')
        self.char_list.insert(0, 'blank')
        self.num_classes = len(self.char_list)

        # 归一化参数（对标 RapidOCR DetPreProcess 默认值）
        self.det_mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self.det_std = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self.det_scale = 1.0 / 255.0

        # rec 归一化
        self.rec_mean = 0.5
        self.rec_std = 0.5

        # 创建 ONNX Runtime 推理会话
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = 2
        sess_options.inter_op_num_threads = 2

        self.det_session = ort.InferenceSession(det_path, sess_options)
        self.rec_session = ort.InferenceSession(rec_path, sess_options)

        # 预热
        self._warmup()

    def _warmup(self):
        """预热模型"""
        try:
            dummy_det = np.zeros((1, 3, 32, 32), dtype=np.float32)
            self.det_session.run(None, {self.det_session.get_inputs()[0].name: dummy_det})
            dummy_rec = np.zeros((1, 3, 48, 32), dtype=np.float32)
            self.rec_session.run(None, {self.rec_session.get_inputs()[0].name: dummy_rec})
        except Exception:
            pass

    def recognize(self, image, roi=None, color_filter=None, expected=None,
                  threshold=0.3, box_index=None, only_rec=False):
        """识别图像中的文字

        Args:
            image: PIL.Image 或 numpy.ndarray (H,W,3)
            roi: (x, y, w, h) 感兴趣区域（绝对坐标），None=全图
            color_filter: dict with 'lower' and 'upper' RGB arrays for pre-filtering
            expected: str or list of str — 正则表达式匹配
            threshold: 置信度阈值 (0.0-1.0)
            box_index: 选择第 N 个匹配框（0-based）
            only_rec: 只返回识别结果

        Returns:
            list of (text, box, score)
            如果 only_rec=True 且 expected 不为空，返回第一个匹配的文字（str 或 None）
        """
        img = self._to_numpy(image)

        # ROI 裁剪
        roi_offset = (0, 0)
        if roi:
            x, y, w, h = roi
            img_cropped = img[y:y+h, x:x+w]
            roi_offset = (x, y)
        else:
            img_cropped = img

        if img_cropped.size == 0:
            return None if only_rec and expected else []

        # color_filter 预过滤
        if color_filter is not None:
            img_cropped = self._apply_color_filter(img_cropped, color_filter)

        # 文本检测
        boxes, scores = self._detect(img_cropped)

        # 文本识别
        results = self._recognize_boxes(img_cropped, boxes, scores)

        # 坐标转换回原图
        ox, oy = roi_offset
        adjusted = []
        for text, box, score in results:
            if box and isinstance(box[0], (list, np.ndarray)):
                new_box = [[p[0]+ox, p[1]+oy] for p in box]
            else:
                new_box = [[box[i]+ox, box[i+1]+oy] for i in range(0, len(box), 2)]
            adjusted.append((text, new_box, score))
        results = adjusted

        # 置信度过滤
        results = [(t, b, s) for t, b, s in results if s >= threshold]

        # expected 正则匹配
        if expected is not None:
            results = self._filter_by_expected(results, expected)

        # box_index
        if box_index is not None and results and box_index < len(results):
            results = [results[box_index]]

        # only_rec 模式
        if only_rec and expected:
            if results:
                return results[0][0]
            return None

        return results

    def recognize_full(self, image, roi=None):
        """全量 OCR — 返回所有文字

        Returns:
            (results, txts, boxes)
        """
        results = self.recognize(image, roi=roi)
        if not results:
            return results, [], []
        txts = [r[0] for r in results]
        boxes = [r[1] for r in results]
        return results, txts, boxes

    # ==================== 内部方法 ====================

    def _to_numpy(self, image):
        if isinstance(image, Image.Image):
            return np.array(image.convert('RGB'))
        elif isinstance(image, np.ndarray):
            if image.shape[2] == 4:
                image = image[:, :, :3]
            return image
        raise ValueError(f"不支持的图像类型: {type(image)}")

    def _apply_color_filter(self, img, color_filter):
        """颜色预过滤 — 不在范围内的像素替换为白色"""
        result = img.copy().astype(np.uint8)
        lower = np.array(color_filter['lower'], dtype=np.uint8)
        upper = np.array(color_filter['upper'], dtype=np.uint8)
        if lower.ndim > 1:
            lower = lower[0]
        if upper.ndim > 1:
            upper = upper[0]
        mask = np.all((result >= lower) & (result <= upper), axis=2)
        result[~mask] = [255, 255, 255]
        return result

    def _detect(self, img):
        """文本检测 — PP-OCRv5 mobile det + DB 后处理

        完全对标 RapidOCR 的 DetPreProcess + DBPostProcess 实现。

        Returns:
            (boxes, scores) — boxes 是 list of (4, 2) numpy arrays，scores 是 list of float
        """
        h, w = img.shape[:2]
        if h < 10 or w < 10:
            return [], []

        # ---- 1. Resize（对标 RapidOCR DetPreProcess.resize）----
        ratio = 1.0
        if self.DET_LIMIT_TYPE == 'max':
            if max(h, w) > self.DET_LIMIT_SIDE_LEN:
                ratio = float(self.DET_LIMIT_SIDE_LEN) / h if h > w else float(self.DET_LIMIT_SIDE_LEN) / w
        else:  # 'min'
            if min(h, w) < self.DET_LIMIT_SIDE_LEN:
                ratio = float(self.DET_LIMIT_SIDE_LEN) / h if h < w else float(self.DET_LIMIT_SIDE_LEN) / w

        resize_h = int(h * ratio)
        resize_w = int(w * ratio)
        resize_h = int(round(resize_h / 32) * 32)
        resize_w = int(round(resize_w / 32) * 32)

        if resize_w <= 0 or resize_h <= 0:
            return [], []

        resized = cv2.resize(img, (resize_w, resize_h))

        # ---- 2. Normalize + Permute（对标 RapidOCR DetPreProcess）----
        inp = resized.astype(np.float32) * self.det_scale
        inp = (inp - self.det_mean) / self.det_std
        inp = inp.transpose(2, 0, 1)  # HWC -> CHW
        inp = np.expand_dims(inp, 0).astype(np.float32)

        # ---- 3. det 推理 ----
        outputs = self.det_session.run(None, {self.det_session.get_inputs()[0].name: inp})
        pred = outputs[0]  # (1, 1, H, W)

        # ---- 4. DB 后处理 ----
        boxes, scores = self._db_postprocess(pred, (h, w))
        return boxes, scores

    def _db_postprocess(self, pred, ori_shape):
        """DB 后处理 — 完全对标 RapidOCR DBPostProcess

        Args:
            pred: (1, 1, H, W) 模型输出
            ori_shape: (原图高, 原图宽)

        Returns:
            (boxes, scores)
        """
        src_h, src_w = ori_shape
        pred = pred[:, 0, :, :]   # (1, H, W)
        segmentation = pred > self.DET_DB_THRESH

        bitmap = segmentation[0].astype(np.uint8)  # (H, W)
        # 注意：RapidOCR 默认 use_dilation=False，不做膨胀

        boxes, scores = self._boxes_from_bitmap(pred[0], bitmap, src_w, src_h)
        return boxes, scores

    def _boxes_from_bitmap(self, pred, bitmap, dest_width, dest_height):
        """从 bitmap 提取文本框 — 对标 RapidOCR boxes_from_bitmap"""
        height, width = bitmap.shape

        contours, _ = cv2.findContours(
            (bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        num_contours = min(len(contours), self.MAX_CANDIDATES)

        boxes = []
        scores = []
        for index in range(num_contours):
            contour = contours[index]
            points, sside = self._get_mini_boxes(contour)
            if sside < self.MIN_BOX_SIDE:
                continue

            score = self._box_score_fast(pred, points.reshape(-1, 2))
            if self.DET_DB_BOX_THRESH > score:
                continue

            # 用 pyclipper 做 Vatti unclip（对标 RapidOCR）
            box = self._unclip(points)
            if box is None:
                continue

            box, sside = self._get_mini_boxes(box)
            if sside < self.MIN_BOX_SIDE + 2:
                continue

            # 缩放回原图坐标
            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
            boxes.append(box.astype(np.int32))
            scores.append(score)

        # 过滤太小的框
        filtered_boxes = []
        filtered_scores = []
        for box, score in zip(boxes, scores):
            box = self._order_points_clockwise(box)
            rect_width = int(np.linalg.norm(box[0] - box[1]))
            rect_height = int(np.linalg.norm(box[0] - box[3]))
            if rect_width <= 3 or rect_height <= 3:
                continue
            filtered_boxes.append(box)
            filtered_scores.append(score)

        return filtered_boxes, filtered_scores

    def _get_mini_boxes(self, contour):
        """获取最小外接矩形并排序顶点 — 对标 RapidOCR"""
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

        index_1, index_2, index_3, index_4 = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_1, index_4 = 0, 1
        else:
            index_1, index_4 = 1, 0

        if points[3][1] > points[2][1]:
            index_2, index_3 = 2, 3
        else:
            index_2, index_3 = 3, 2

        box = np.array([points[index_1], points[index_2], points[index_3], points[index_4]])
        return box, min(bounding_box[1])

    def _box_score_fast(self, bitmap, box):
        """快速 box score — 对标 RapidOCR box_score_fast"""
        h, w = bitmap.shape[:2]
        box = box.copy()
        xmin = int(np.clip(np.floor(box[:, 0].min()), 0, w - 1))
        xmax = int(np.clip(np.ceil(box[:, 0].max()), 0, w - 1))
        ymin = int(np.clip(np.floor(box[:, 1].min()), 0, h - 1))
        ymax = int(np.clip(np.ceil(box[:, 1].max()), 0, h - 1))

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype(np.int32), 1)
        return cv2.mean(bitmap[ymin:ymax + 1, xmin:xmax + 1], mask)[0]

    def _unclip(self, box):
        """Vatti clipping — 用 pyclipper 库（对标 RapidOCR）"""
        try:
            poly = Polygon(box)
            distance = poly.area * self.DET_DB_UNCLIP_RATIO / poly.length
            offset = pyclipper.PyclipperOffset()
            offset.AddPath(box.tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
            expanded = np.array(offset.Execute(distance)).reshape((-1, 1, 2))
            return expanded
        except Exception:
            return None

    @staticmethod
    def _order_points_clockwise(pts):
        """顺时针排序四个顶点 — 对标 RapidOCR"""
        xSorted = pts[np.argsort(pts[:, 0]), :]
        leftMost = xSorted[:2, :]
        rightMost = xSorted[2:, :]
        leftMost = leftMost[np.argsort(leftMost[:, 1]), :]
        (tl, bl) = leftMost
        rightMost = rightMost[np.argsort(rightMost[:, 1]), :]
        (tr, br) = rightMost
        return np.array([tl, tr, br, bl], dtype="float32")

    def _recognize_boxes(self, img, boxes, scores):
        """对检测到的文本框逐一识别"""
        results = []
        for box, score in zip(boxes, scores):
            text, rec_score = self._recognize_single(img, box)
            if text and len(text.strip()) > 0 and rec_score > 0.1:
                # 综合 det 分数和 rec 分数
                combined_score = rec_score * 0.9 + score * 0.1
                results.append((text.strip(), box.tolist(), combined_score))
        return results

    def _recognize_single(self, img, box):
        """识别单个文本框 — PP-OCRv5 rec"""
        box_np = np.array(box, dtype=np.float32)
        x_min = int(max(box_np[:, 0].min(), 0))
        y_min = int(max(box_np[:, 1].min(), 0))
        x_max = int(min(box_np[:, 0].max(), img.shape[1] - 1))
        y_max = int(min(box_np[:, 1].max(), img.shape[0] - 1))

        if x_max <= x_min or y_max <= y_min:
            return '', 0.0

        text_region = img[y_min:y_max, x_min:x_max]
        if text_region.size == 0:
            return '', 0.0

        # resize 到 rec 模型输入（高=48，宽动态）
        rh, rw = text_region.shape[:2]
        target_h = 48
        scale = target_h / max(rh, 1)
        target_w = max(int(rw * scale), 10)
        target_w = min(target_w, 512)
        # 对齐到 4 的倍数（RapidOCR 不强制对齐，但安全起见保留）
        target_w = int(round(target_w / 4) * 4)
        target_w = max(target_w, 4)

        try:
            resized = cv2.resize(text_region, (target_w, target_h))
        except cv2.error:
            return '', 0.0

        # 预处理：和 det 一样的归一化（mean=0.5, std=0.5）
        inp = resized.astype(np.float32) / 255.0
        inp = (inp - self.rec_mean) / self.rec_std
        inp = inp.transpose(2, 0, 1)  # HWC -> CHW
        inp = np.expand_dims(inp, 0).astype(np.float32)  # NCHW

        # 推理
        try:
            outputs = self.rec_session.run(
                None, {self.rec_session.get_inputs()[0].name: inp})
        except Exception:
            return '', 0.0

        # CTC 解码（对标 RapidOCR CTCLabelDecode）
        return self._ctc_decode(outputs[0])

    def _ctc_decode(self, logits):
        """CTC greedy decode — 对标 RapidOCR CTCLabelDecode.decode

        Args:
            logits: (1, T, num_classes)

        Returns:
            (text, score)
        """
        if logits.ndim != 3:
            return '', 0.0

        preds_idx = logits.argmax(axis=2)  # (1, T)
        preds_prob = logits.max(axis=2)    # (1, T)

        token_indices = preds_idx[0]

        # 去重 + 跳过 blank(索引 0)
        selection = np.ones(len(token_indices), dtype=bool)
        selection[1:] = token_indices[1:] != token_indices[:-1]
        selection &= token_indices != 0  # 忽略 blank token（索引 0）

        if selection.sum() == 0:
            return '', 0.0

        # 提取字符
        char_list = [self.char_list[text_id] for text_id in token_indices[selection]]
        text = ''.join(char_list)

        if not text.strip():
            return '', 0.0

        # 计算置信度
        conf_list = np.array(preds_prob[0][selection]).tolist()
        conf_list = [round(conf, 5) for conf in conf_list]
        score = round(np.mean(conf_list), 5)

        return text, score

    def _filter_by_expected(self, results, expected):
        """正则过滤"""
        if isinstance(expected, str):
            patterns = [expected]
        else:
            patterns = expected
        matched = []
        for text, box, score in results:
            for pat in patterns:
                try:
                    if re.search(pat, text):
                        matched.append((text, box, score))
                        break
                except re.error:
                    pass
        return matched


def create_ocr(model_dir=None):
    """创建 MaaOCR 实例"""
    return MaaOCR(model_dir=model_dir)
