"""小米 MiMo 多模态客户端（OpenAI 兼容接口）—— 看图读答案。

用于：
  - read_student_answers：看学生答卷图，逐题返回学生作答 {qid: answer}
  - read_answer_key：看标准答案图，返回结构化答案列表 [AnswerItem]

VLM 负责"读懂内容"；题目的坐标（bbox）仍由 PaddleOCR 提供。
"""
from __future__ import annotations
import base64
import json
import re
import sys
import time
from pathlib import Path

from openai import OpenAI

from ..config import settings
from ..models import AnswerItem


class VLMError(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(f"[vlm] {msg}", file=sys.stderr, flush=True)


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.mimo_api_key:
            raise VLMError("未配置 MIMO_API_KEY，请在 backend/.env 设置。")
        _client = OpenAI(
            api_key=settings.mimo_api_key,
            base_url=settings.mimo_base_url,
            timeout=90.0,
            max_retries=0,
        )
    return _client


def _image_data_url(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".") or "png"
    media = "jpeg" if ext in {"jpg", "jpeg"} else ("png" if ext == "png" else "png")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/{media};base64,{b64}"


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _repair_json(s: str) -> str:
    """尝试修复模型偶发的 JSON 格式问题：尾随逗号、控制字符。"""
    # 去掉控制字符（换行保留）
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    # 尾随逗号：}, 或 ] 前的逗号
    s = re.sub(r",\s*([\]}])", r"\1", s)
    return s


def _parse_json(text: str) -> dict:
    s = _strip_fence(text)
    # 1) 直接解析
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    # 2) 截取最外层 { ... }
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        frag = s[i:j + 1]
        try:
            return json.loads(frag)
        except Exception:  # noqa: BLE001
            pass
        # 3) 修复后重试
        try:
            return json.loads(_repair_json(frag))
        except Exception:  # noqa: BLE001
            pass
        # 4) 逐对象抢救：用正则抠出每个 {...}，能解析几个算几个
        answers = []
        for m in re.finditer(r"\{[^{}]*\}", frag):
            try:
                obj = json.loads(_repair_json(m.group(0)))
                if isinstance(obj, dict):
                    answers.append(obj)
            except Exception:  # noqa: BLE001
                continue
        if answers:
            return {"answers": answers}
    raise VLMError(f"无法解析模型返回的 JSON：{text[:160]}")


def _call(image: Path, prompt: str, max_tokens: int = 2500) -> dict:
    client = _get_client()
    t0 = time.time()
    _log(f"call model={settings.mimo_model} image={image.name}")
    try:
        resp = client.chat.completions.create(
            model=settings.mimo_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                {"type": "text", "text": prompt},
            ]}],
            max_completion_tokens=max_tokens,
            temperature=0.0,
        )
        data = _parse_json(resp.choices[0].message.content or "")
        _log(f"ok in {time.time()-t0:.1f}s")
        return data
    except VLMError:
        raise
    except Exception as e:  # noqa: BLE001
        _log(f"fail after {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")
        raise VLMError(f"MiMo 调用失败：{e}")


_STUDENT_PROMPT = (
    "这是一份试卷的学生作答扫描图。请逐题识别【学生填写/选择的答案】。\n"
    "- 选择题：学生在题号旁括号里填的字母，如 (C)/(A)/（B）表示选该字母；空括号 () 表示未作答。\n"
    "- 判断题：对/错/√/×/T/F。\n"
    "- 填空题：学生手写的内容（保持原文，不要补全或纠正拼写）。\n"
    "- 简答题：学生写的内容。\n"
    '只返回 JSON：{"answers":[{"qid":"题号","answer":"学生答案"}]}，未作答 answer 为空字符串。不要解释。'
)

_ANSWER_PROMPT = (
    "这是一份试卷的标准答案图。请整理成结构化答案。\n"
    "- type 只能是 choice(选择)/fill(填空)/judge(判断)/essay(简答论述)。\n"
    "- choice: 正确选项字母；fill: 填空内容；judge: 对/错；essay: 参考答案。\n"
    "- max_score: 该题满分（客观题默认 1，主观题按题意）。\n"
    "- rubric: 仅 essay 需要，列出得分点。\n"
    '只返回 JSON：{"answers":[{"qid":"题号","type":...,"correct_answer":...,"max_score":数字,"rubric":["得分点"]}]}.不要解释。'
)


def read_student_answers(image: Path) -> dict[str, str]:
    """看学生图，返回 {qid: 学生答案}。"""
    data = _call(image, _STUDENT_PROMPT)
    out: dict[str, str] = {}
    for a in data.get("answers", []):
        qid = str(a.get("qid", "")).strip()
        if qid:
            out[qid] = str(a.get("answer", "")).strip()
    return out


_STUDENT_BOX_PROMPT = (
    "请逐题完成两件事：\n"
    "1. 识别学生填写的答案（选择题括号里的字母，填空题手写内容）\n"
    "2. 给出该题题号在图中的精确像素坐标\n\n"
    "重要规则：\n"
    "- qid 必须是纯数字（如 \"26\" \"31\" \"35\"），不要带括号、句点或其他字符\n"
    "- box = [x, y, w, h]，x,y 是题号数字左上角的像素坐标，w,h 是题号区域宽高\n"
    "- 坐标要精确到像素\n"
    "- 未作答的题 answer 为空字符串，box 为 [0,0,0,0]\n\n"
    "返回JSON：{\"answers\":[{\"qid\":\"纯数字题号\",\"answer\":\"学生答案\",\"box\":[x,y,w,h]}]}"
)


def read_student_answers_with_boxes(image: Path, img_w: int = 0, img_h: int = 0) -> dict[str, dict]:
    """看学生图，返回 {qid: {answer, box}}。box 为 [x,y,w,h] 像素坐标。"""
    prompt = _STUDENT_BOX_PROMPT
    if img_w and img_h:
        prompt += f"\n（图片尺寸：{img_w}x{img_h} 像素）"
    data = _call(image, prompt, max_tokens=3000)
    out: dict[str, dict] = {}
    for a in data.get("answers", []):
        qid_raw = str(a.get("qid", "")).strip()
        # 兜底：提取纯数字题号（VLM 可能返回 "31." "(C)31." 等格式）
        qid = re.sub(r"[^\d]", "", qid_raw)
        if not qid:
            continue
        box = a.get("box", [0, 0, 0, 0])
        if not isinstance(box, list) or len(box) != 4:
            box = [0, 0, 0, 0]
        out[qid] = {
            "answer": str(a.get("answer", "")).strip(),
            "box": [float(v) for v in box],
        }
    return out

def _call_with_image(image, system, user):
    """MiMo 图片+文本调用"""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=settings.mimo_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                {"type": "text", "text": f"[系统指令]{system}\n\n{user}"},
            ]}],
            temperature=0.3, max_completion_tokens=3000,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""

def _call_with_image(image: Path, system: str, user: str) -> str:
    """MiMo 图片+文本调用。失败返回空字符串。"""
    client = _get_client()
    t0 = time.time()
    _log(f"call_with_image model={settings.mimo_model} image={image.name}")
    try:
        resp = client.chat.completions.create(
            model=settings.mimo_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                {"type": "text", "text": f"[系统指令]{system}\n\n{user}"},
            ]}],
            temperature=0.3, max_completion_tokens=3000,
        )
        text = (resp.choices[0].message.content or "").strip()
        _log(f"call_with_image ok in {time.time()-t0:.1f}s")
        return text
    except Exception as e:
        _log(f"call_with_image fail: {type(e).__name__}: {str(e)[:100]}")
        return ""


def _call_text(system: str, user: str) -> str:
    """MiMo 纯文本调用（不传图片）。失败返回空字符串。"""
    client = _get_client()
    t0 = time.time()
    _log(f"call_text model={settings.mimo_model}")
    try:
        resp = client.chat.completions.create(
            model=settings.mimo_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        _log(f"call_text ok in {time.time()-t0:.1f}s")
        return text
    except Exception as e:  # noqa: BLE001
        _log(f"call_text fail after {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")
        return ""


def read_answer_key(image: Path) -> list[AnswerItem]:
    """看答案图，返回结构化答案列表。"""
    data = _call(image, _ANSWER_PROMPT)
    items: list[AnswerItem] = []
    seen: set[str] = set()
    for a in data.get("answers", []):
        qid = str(a.get("qid", "")).strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)
        try:
            items.append(AnswerItem(
                qid=qid,
                type=a.get("type", "choice"),
                correct_answer=str(a.get("correct_answer", "")).strip(),
                max_score=float(a.get("max_score", 1) or 1),
                rubric=[str(r) for r in a.get("rubric", [])],
            ))
        except Exception:  # noqa: BLE001
            continue
    return items
