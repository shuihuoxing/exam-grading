"""判分：客观题代码比对，主观题 DeepSeek 按 Rubric 打分。"""
from __future__ import annotations
import re
import unicodedata

from ..models import AnswerItem, QuestionResult
from . import llm
from .matcher import StudentQuestion


# ---------- 归一化工具 ----------

_FULLWIDTH_OFFSET = 0xFEE0  # ord('！') - ord('!')


def _to_halfwidth(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _normalize(s: str) -> str:
    s = _to_halfwidth(s or "")
    s = s.lower()
    s = re.sub(r"[\s,，、;；。.（）()【】\[\]]+", "", s)
    return s.strip()


def _extract_choices(s: str) -> set[str]:
    """从学生答案文本抽选项字母，如 'A和C' -> {a,c}。"""
    found = set(re.findall(r"[a-dA-D]", _to_halfwidth(s)))
    return {f.lower() for f in found}


_TRUE = {"对", "正确", "t", "true", "√", "v", "是", "y", "yes"}
_FALSE = {"错", "错误", "f", "false", "x", "×", "否", "n", "no"}


def _normalize_judge(s: str) -> bool | None:
    t = _normalize(s)
    if not t:
        return None
    if t in { _normalize(x) for x in _TRUE}:
        return True
    if t in {_normalize(x) for x in _FALSE}:
        return False
    return None


# ---------- 判分 ----------

def _grade_objective(ans: AnswerItem, student_text: str) -> tuple[str, str]:
    """返回 (status, analysis)。status: correct / incorrect / unmatched。

    student_text 为 matcher 提取出的"作答部分"（答案：之后）；若无标记则为整行。
    """
    stu = student_text or ""
    correct = ans.correct_answer or ""

    if ans.type == "choice":
        s_set = _extract_choices(stu)
        c_set = _extract_choices(correct)
        if not s_set:
            return "unmatched", "未识别到学生作答的选项字母"
        if s_set == c_set:
            return "correct", ""
        stu_str = ",".join(sorted(s_set)).upper()
        return "incorrect", f"学生选{stu_str}，正确答案为{correct.upper()}"

    if ans.type == "judge":
        sv = _normalize_judge(stu)
        cv = _normalize_judge(correct)
        if sv is None:
            return "unmatched", "未识别到明确的对/错作答"
        if cv is None:
            cv = True if _normalize(correct) in {"right", "dui"} else None
        if sv == cv:
            return "correct", ""
        stu_judge = "对" if sv else "错"
        ans_judge = "对" if cv else "错"
        return "incorrect", f"学生答{stu_judge}，正确答案为{ans_judge}"

    # fill（填空）：先精确归一化比对；失败则判断正确答案是否作为独立 token / 出现在末尾
    if _normalize(stu) == _normalize(correct):
        return "correct", ""
    half_stu = _to_halfwidth(stu)
    half_tok = _to_halfwidth(correct.strip())
    if half_tok:
        # 独立 token（数字/字母边界）
        if re.search(r"(?<![0-9A-Za-z])" + re.escape(half_tok) + r"(?![0-9A-Za-z])", half_stu, re.IGNORECASE):
            return "correct", ""
        # 出现在作答末尾（去尾部标点），如 "5×6=30"
        tail = re.sub(r"[。.；;，,、\s]+$", "", half_stu)
        if tail.endswith(half_tok):
            return "correct", ""
    return "incorrect", ""


def _grade_subjective(ans: AnswerItem, student_text: str) -> tuple[str, float, str]:
    """主观题：DeepSeek 按 Rubric 打分。返回 (status, score, analysis)。"""
    rubric_txt = "\n".join(f"- {r}" for r in ans.rubric) or "（无明确得分点，按要点完整度给分）"
    system = (
        "你是严格的阅卷老师。根据评分细则给学生答案打分。"
        "返回 JSON：{\"score\":数字, \"analysis\":\"简短点评说明扣分原因\", "
        "\"hit\":[命中的得分点...]}。score 不能超过满分。只输出 JSON。"
    )
    user = (
        f"满分：{ans.max_score}\n"
        f"正确答案：{ans.correct_answer}\n"
        f"评分细则：\n{rubric_txt}\n"
        f"学生答案：\n{student_text or '（空）'}\n"
    )
    try:
        data = llm.chat_json(system, user)
    except llm.LLMError:
        # 兜底：判分失败，不给分，标 partial
        return "partial", 0.0, "判分服务暂不可用，未能评分，请人工复核。"

    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(score, float(ans.max_score)))
    analysis = str(data.get("analysis", "")).strip()
    status = "correct" if score >= ans.max_score else ("incorrect" if score <= 0 else "partial")
    return status, score, analysis


def grade_question(ans: AnswerItem, sq: StudentQuestion | None) -> QuestionResult:
    """对单题判分。sq 为 None 表示学生卷上没找到该题号。"""
    answer_text = (sq.answer if sq else "").strip()
    base = {
        "qid": ans.qid,
        "type": ans.type,
        "box": sq.box if sq else [0, 0, 0, 0],
        "student_answer": answer_text,
        "correct_answer": ans.correct_answer,
        "question_text": (sq.question_text if sq else ""),
    }

    if sq is None:
        return QuestionResult(
            **base, status="unmatched", max_score=ans.max_score,
            score=0.0, analysis="学生卷面未识别到该题",
        )

    if ans.type == "essay":
        # 主观题：用学生作答部分，为空则用整段文本
        status, score, analysis = _grade_subjective(ans, answer_text or sq.text)
        return QuestionResult(
            **base, status=status, score=score, max_score=ans.max_score, analysis=analysis,
        )

    if ans.type == "fill":
        # 填空题：作答为空时回退到整段（用于 token 匹配，如 "5×6=30"）
        status, analysis = _grade_objective(ans, answer_text or sq.text)
    else:
        # 选择/判断：只用提取到的作答字母；为空直接判 unmatched，避免选项字母污染
        if not answer_text:
            status, analysis = "unmatched", "未识别到学生作答的选项/判断"
        else:
            status, analysis = _grade_objective(ans, answer_text)

    score = ans.max_score if status == "correct" else 0.0
    return QuestionResult(
        **base, status=status, score=score, max_score=ans.max_score, analysis=analysis,
    )
