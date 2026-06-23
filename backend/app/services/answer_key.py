"""答案结构化：把答案图的 OCR 文本交给 DeepSeek 整理成 AnswerItem 列表。"""
from __future__ import annotations
import json

from ..models import AnswerItem, TextBox
from . import llm

_SYSTEM = (
    "你是一个试卷答案解析助手。用户会给你一份标准答案的 OCR 文本（可能含识别噪声），"
    "请整理成结构化答案。题型 type 只能是 choice/fill/judge/essay 之一："
    "选择题=choice，填空题=fill，判断题=judge，简答/论述=essay。"
    "返回 JSON：{\"answers\":[{\"qid\":题号字符串, \"type\":..., "
    "\"correct_answer\":正确答案字符串, \"max_score\":数字(客观题默认1,主观题按题意), "
    "\"rubric\":[得分点...](仅 essay 需要)}]}。"
    "题号 qid 必须与学生卷面一致(如 \"1\",\"2\")。只输出 JSON。"
)


def structure_answer_key(boxes: list[TextBox]) -> list[AnswerItem]:
    if not boxes:
        return []
    raw = "\n".join(b.text for b in boxes)
    user = f"答案 OCR 文本：\n{raw}"
    try:
        data = llm.chat_json(_SYSTEM, user)
    except llm.LLMError as e:
        raise llm.LLMError(
            "答案解析失败（模型超时或无响应）。常见原因：答案图不是按题号给出的"
            "（例如按知识点分组、只有连续字母 ACBCA 而无题号）。"
            "请改用『每题一行、带题号』的答案（如 1. B / 2. 对 / 3. 30），或拆成更清晰的答案图。"
            f" 原始错误：{e}"
        )
    answers_raw = data.get("answers", [])
    items: list[AnswerItem] = []
    for a in answers_raw:
        try:
            items.append(AnswerItem(
                qid=str(a.get("qid", "")).strip(),
                type=a.get("type", "choice"),
                correct_answer=str(a.get("correct_answer", "")).strip(),
                max_score=float(a.get("max_score", 1) or 1),
                rubric=[str(r) for r in a.get("rubric", [])],
            ))
        except Exception:  # noqa: BLE001
            continue
    # 去重：同题号保留第一条
    seen: set[str] = set()
    deduped: list[AnswerItem] = []
    for it in items:
        if it.qid and it.qid not in seen:
            seen.add(it.qid)
            deduped.append(it)
    return deduped
