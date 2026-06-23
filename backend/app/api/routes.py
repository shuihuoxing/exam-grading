"""HTTP 路由：上传批改 + 静态图片服务 + 深度分析 + 简单鉴权。"""
from __future__ import annotations
import json as _json
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..models import GradeResponse, PageResult, QuestionResult
from ..services import pipeline, llm
from ..services.pdf import PdfUnavailableError
from ..services.ocr import OCRError
from ..services.vlm import VLMError
from ..models import LLMError

router = APIRouter(prefix="/api")


def _check_token(x_access_token: str | None):
    expected = settings.access_token
    # 令牌为默认占位时不强制鉴权（方便本地首次跑通）
    if expected and expected != "change-me-to-a-long-random-string":
        if x_access_token != expected:
            raise HTTPException(status_code=401, detail="访问令牌无效")


@router.get("/health")
def health():
    return {"ok": True}


@router.post("/grade", response_model=GradeResponse)
async def grade(
    student: UploadFile = File(...),
    answer: UploadFile = File(...),
    x_access_token: str | None = Header(default=None, alias="X-Access-Token"),
):
    _check_token(x_access_token)
    stu_bytes = await student.read()
    ans_bytes = await answer.read()
    if not stu_bytes or not ans_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        return pipeline.run_pipeline(
            stu_bytes, student.filename or "student",
            ans_bytes, answer.filename or "answer",
        )
    except PdfUnavailableError as e:
        raise HTTPException(status_code=415, detail=str(e))
    except OCRError as e:
        raise HTTPException(status_code=500, detail=f"OCR 错误：{e}")
    except VLMError as e:
        raise HTTPException(status_code=502, detail=f"视觉识别错误：{e}")
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analyze/{job}")
def analyze(job: str,
            x_access_token: str | None = Header(default=None, alias="X-Access-Token")):
    """读取已保存的批改结果，调用 DeepSeek 生成深度错题分析。"""
    _check_token(x_access_token)
    if "/" in job or "\\" in job or ".." in job:
        raise HTTPException(status_code=400, detail="非法路径")
    result_path = settings.data_path / "jobs" / job / "result.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="批改结果不存在，请先批改")
    try:
        data = _json.loads(result_path.read_text(encoding="utf-8"))
        pages = [PageResult(**p) for p in data.get("pages", [])]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取结果失败：{e}")
    analysis = pipeline._llm_analysis(pages)
    if not analysis:
        analysis = "深度分析生成失败，请稍后重试。"
    return {"job": job, "analysis": analysis}


@router.post("/analyze-questions/{job}")
def analyze_questions(job: str,
                      x_access_token: str | None = Header(default=None, alias="X-Access-Token")):
    """为错题生成学科级详细解析。"""
    _check_token(x_access_token)
    if "/" in job or "\\" in job or ".." in job:
        raise HTTPException(status_code=400, detail="非法路径")
    result_path = settings.data_path / "jobs" / job / "result.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="批改结果不存在")
    try:
        data = _json.loads(result_path.read_text(encoding="utf-8"))
        pages = [PageResult(**p) for p in data.get("pages", [])]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取结果失败：{e}")

    pipeline._detailed_per_question_analysis(pages)

    # 更新保存的结果
    data["pages"] = [p.model_dump() for p in pages]
    result_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 返回每题的解析
    result = {}
    for p in pages:
        for q in p.questions:
            if q.analysis:
                result[q.qid] = q.analysis
    return {"job": job, "analyses": result}


@router.get("/images/{job}/{name}")
def get_image(job: str, name: str,
              x_access_token: str | None = Header(default=None, alias="X-Access-Token")):
    _check_token(x_access_token)
    # 防路径穿越
    if "/" in job or "\\" in job or "/" in name or "\\" in name or ".." in job or ".." in name:
        raise HTTPException(status_code=400, detail="非法路径")
    p = settings.data_path / "jobs" / job / name
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(str(p), media_type="image/png")
