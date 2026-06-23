"""PDF / 图片归一化：把上传文件统一转成 PNG 图片列表。

依赖 pdf2image（需系统安装 poppler）。若 poppler 缺失，PDF 转换会抛出
PdfUnavailableError，调用方应回退为"仅接受图片"。
"""
from __future__ import annotations
from pathlib import Path
import tempfile

from PIL import Image


class PdfUnavailableError(RuntimeError):
    """pdf2image / poppler 不可用。"""


def to_images(src: Path, work_dir: Path, dpi: int = 300, prefix: str = "img") -> list[Path]:
    """把图片或 PDF 转成 PNG 列表，写入 work_dir。

    - 图片（png/jpg/jpeg/webp/bmp）：直接（必要时转存）成 PNG。
    - PDF：pdf2image 每页转 300DPI PNG。
    """
    ext = src.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        img = Image.open(src)
        if img.mode != "RGB":
            img = img.convert("RGB")
        out = work_dir / f"{prefix}_p1.png"
        img.save(out, "PNG")
        return [out]

    if ext == ".pdf":
        try:
            from pdf2image import convert_from_path
        except Exception as e:  # noqa: BLE001
            raise PdfUnavailableError(
                "pdf2image 未安装或 poppler 缺失，无法处理 PDF。"
                " 请安装 poppler 或改为上传图片。"
            ) from e
        try:
            pages = convert_from_path(str(src), dpi=dpi)
        except Exception as e:  # noqa: BLE001
            raise PdfUnavailableError(f"PDF 转图片失败：{e}") from e
        out_paths: list[Path] = []
        for i, page in enumerate(pages, start=1):
            if page.mode != "RGB":
                page = page.convert("RGB")
            out = work_dir / f"{prefix}_p{i}.png"
            page.save(out, "PNG")
            out_paths.append(out)
        return out_paths

    raise ValueError(f"不支持的文件类型：{ext}")
