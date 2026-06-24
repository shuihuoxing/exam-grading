"""Pydantic 数据模型。"""
from typing import Literal
from pydantic import BaseModel


class TextBox(BaseModel):
    text: str
    box: list[float]  # [x, y, w, h] 原图像素坐标


QuestionType = Literal["choice", "fill", "judge", "essay"]
GradeStatus = Literal["correct", "incorrect", "partial", "unmatched"]


class AnswerItem(BaseModel):
    """标准答案表中的一道题。"""
    qid: str
    type: QuestionType
    correct_answer: str
    max_score: float = 1.0
    rubric: list[str] = []  # 主观题得分点


class QuestionResult(BaseModel):
    qid: str
    type: QuestionType
    status: GradeStatus
    score: float | None = None
    max_score: float | None = None
    box: list[float]  # [x, y, w, h]
    student_answer: str
    correct_answer: str
    analysis: str = ""
    question_text: str = ""  # 题干原文（供解析用）


class PageResult(BaseModel):
    page: int
    image_url: str
    width: int
    height: int
    questions: list[QuestionResult]


class GradeResponse(BaseModel):
    job: str
    pages: list[PageResult]
    summary: str = ""


class LLMError(Exception):
    pass
