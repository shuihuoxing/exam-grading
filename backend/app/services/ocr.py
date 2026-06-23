"""OCR 服务：用 PaddleOCR 抽取文字 + 边界框。

PaddleOCR 在 Windows 上首次加载较慢并会下载模型。本模块懒加载单例，
若 paddleocr 未安装则抛出清晰错误。
"""
from __future__ import annotations
from pathlib import Path
from threading import Lock

from ..models import TextBox

_ocr = None
_lock = Lock()


class OCRError(RuntimeError):
    pass


def _get_engine():
    global _ocr
    if _ocr is None:
        try:
            from paddleocr import PaddleOCR
        except Exception as e:  # noqa: BLE001
            raise OCRError(
                "paddleocr 未安装。请先安装："
                "pip install paddlepaddle paddleocr"
            ) from e
        # show_log=False 关掉冗余日志；use_angle_cls 处理旋转字
        _ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    return _ocr


def _four_points_to_xywh(pts: list[list[float]]) -> list[float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x, y = min(xs), min(ys)
    return [x, y, max(xs) - x, max(ys) - y]


def ocr_image(img_path: Path) -> list[TextBox]:
    """对一张图片做 OCR，返回按"从上到下、从左到右"排序的 TextBox 列表。"""
    engine = _get_engine()
    try:
        result = engine.ocr(str(img_path), cls=True)
    except Exception as e:  # noqa: BLE001
        raise OCRError(f"OCR 失败：{e}") from e

    boxes: list[TextBox] = []
    # paddleocr>=2.6 result 结构: [ page0 ] -> page0 = [ [box, (text, conf)], ... ]
    page = result[0] if result else None
    if not page:
        return boxes

    for item in page:
        try:
            box_pts, txt_conf = item[0], item[1]
            text = txt_conf[0] if isinstance(txt_conf, (list, tuple)) else str(txt_conf)
        except Exception:  # noqa: BLE001
            continue
        text = (text or "").strip()
        if not text:
            continue
        boxes.append(TextBox(text=text, box=_four_points_to_xywh(box_pts)))

    # 排序：先 y（行）后 x（列），容差取行高的 1/2
    boxes.sort(key=lambda b: (round(b.box[1] / max(b.box[3], 1) * 0.5), b.box[0]))
    boxes.sort(key=lambda b: b.box[1])  # 主排序按 y
    return boxes
