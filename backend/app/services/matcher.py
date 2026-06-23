"""题号配对：把学生答卷的 OCR 文本块按题号切分，得到每题的作答文本 + bbox。

支持多种常见题号格式：
  - 行首数字+分隔符：`1.` `2、` `3）`
  - 答案在题号前（英语卷常见）：`(C)35.`  `（A）41.`  `B32.`  `(）31.`  `D）42.`
题号边界的统一特征：数字后跟 `.` / `、` / `．`。
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from ..models import TextBox

# 题号边界：可选的"前置答案"（括号字母 或 裸字母）+ 数字 + [.、．]
#   组1 = 括号内字母（可能为空，如 `(）31.`）
#   组2 = 裸字母（如 `B32.`）
#   组3 = 题号
_BOUNDARY = re.compile(
    r"(?:[\(（]\s*([A-Da-d])?\s*[\)）]\s*|([A-Da-d])\s*)?"
    r"(?<!\d)(\d{1,3})\s*[.、．]"
)
# 作答标记（旧格式）：答案/我的答案 后的内容
_ANS_RE = re.compile(r"答案\s*[:：]?\s*(.+)$")
# 括号内字母（兜底，用于答案不在题号紧邻处时）
_PAREN_ANS_RE = re.compile(r"[\(（]\s*([A-Da-d])\s*[\)）]")
# 孤立"字母+右括号"（OCR 把左括号读丢/读错时，如 C）39.）；选项是 A. 不会出现 A)
_BARE_LETTER_PAREN_RE = re.compile(r"(?<![\(（A-Za-z])([A-Da-d])\s*[\)）]")
# 独立成行的题号（如 OCR 把 `38` 单独成行）
_BARE_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*$")


@dataclass
class StudentQuestion:
    qid: str
    text: str              # 该题题干+作答文本（拼接）
    answer: str            # 提取出的"学生作答"（选择/判断为字母；为空表示未提取到）
    box: list[float]       # 该题所有文本块 bbox 的并集 [x,y,w,h]


def _union_boxes(boxes: list[list[float]]) -> list[float]:
    xs0 = min(b[0] for b in boxes)
    ys0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [xs0, ys0, x1 - xs0, y1 - ys0]


def _find_boundary(text: str) -> tuple[str | None, str | None]:
    """返回 (qid, 紧邻题号的答案字母或 None)。找不到题号返回 (None, None)。"""
    m = _BOUNDARY.search(text)
    if m:
        ans = m.group(1) or m.group(2)
        return m.group(3), (ans.upper() if ans else None)
    bm = _BARE_NUM_RE.match(text)
    if bm:
        return bm.group(1), None
    return None, None


def _extract_answer(full: str, boundary_ans: str | None) -> str:
    """优先级：边界答案 > '答案：'标记 > 括号字母 > 空（不改用整行，避免选项字母污染）。"""
    if boundary_ans:
        return boundary_ans
    last = None
    for line in full.splitlines():
        m = _ANS_RE.search(line)
        if m:
            last = m.group(1).strip()
    if last:
        return last
    m = _PAREN_ANS_RE.search(full)
    if m:
        return m.group(1).upper()
    m = _BARE_LETTER_PAREN_RE.search(full)
    if m:
        return m.group(1).upper()
    return ""


def split_by_qid(boxes: list[TextBox]) -> list[StudentQuestion]:
    """按题号切分。OCR 文本按 y 主排序。

    遍历每个文本块：若能识别到题号边界，则开启新题并把"紧邻答案字母"记下；
    否则归入当前题。
    """
    questions: list[StudentQuestion] = []
    current: StudentQuestion | None = None
    current_boxes: list[list[float]] = []
    current_boundary_ans: str | None = None

    def flush():
        nonlocal current, current_boxes, current_boundary_ans
        if current is not None and current_boxes:
            current.box = _union_boxes(current_boxes)
            current.answer = _extract_answer(current.text, current_boundary_ans)
            questions.append(current)
        current = None
        current_boxes = []
        current_boundary_ans = None

    for tb in boxes:
        qid, bans = _find_boundary(tb.text)
        if qid is not None:
            flush()
            current = StudentQuestion(qid=qid, text=tb.text, answer="", box=[0, 0, 0, 0])
            current_boxes = [list(tb.box)]
            current_boundary_ans = bans
        else:
            if current is not None:
                current.text += "\n" + tb.text
                current_boxes.append(list(tb.box))
            # 题号出现前的内容（页眉、姓名等）忽略
    flush()
    return questions
