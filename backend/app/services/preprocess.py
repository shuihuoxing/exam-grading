"""OpenCV 图像预处理：灰度 + 对比度增强。轻量、保守，避免破坏坐标。"""
from __future__ import annotations
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def enhance(src_png: Path, dst_png: Path) -> tuple[Path, int, int]:
    """对原图做轻度增强后另存；返回 (路径, 宽, 高)。

    注意：增强图仅用于 OCR 提升识别率，坐标仍基于【原图】像素，
    因此本函数不缩放尺寸，只做灰度+CLAHE 对比度增强。
    """
    img = cv2.imdecode(np.fromfile(str(src_png), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        # 解码失败则原样拷贝
        Image.open(src_png).save(dst_png)
        w, h = Image.open(src_png).size
        return dst_png, w, h

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    # 用 imencode + tofile 支持中文路径
    ok, buf = cv2.imencode(".png", enhanced_bgr)
    if ok:
        buf.tofile(str(dst_png))
    else:
        Image.open(src_png).save(dst_png)

    h, w = img.shape[:2]
    return dst_png, w, h
