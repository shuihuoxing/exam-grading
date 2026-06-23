"""纯逻辑单元测试：题号切分 + 客观题归一化比对。
不依赖 PaddleOCR / DeepSeek，可直接 `pytest` 运行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import TextBox, AnswerItem
from app.services.matcher import split_by_qid
from app.services.grader import grade_question, _normalize, _extract_choices
from app.services.matcher import StudentQuestion


def test_normalize_fullwidth_and_case():
    assert _normalize("Ａ，Ｂ") == _normalize("ab")
    assert _normalize("  Hello ") == "hello"


def test_extract_choices():
    assert _extract_choices("我选 A 和 C") == {"a", "c"}
    assert _extract_choices("BD") == {"b", "d"}


def test_split_by_qid():
    boxes = [
        TextBox(text="1. What is 2+2?", box=[10, 10, 200, 30]),
        TextBox(text="A) 3  B) 4", box=[10, 50, 200, 30]),
        TextBox(text="2、判断题", box=[10, 100, 200, 30]),
        TextBox(text="对", box=[10, 140, 40, 30]),
    ]
    qs = split_by_qid(boxes)
    assert len(qs) == 2
    assert qs[0].qid == "1"
    assert qs[1].qid == "2"
    assert "A) 3" in qs[0].text
    # box 并集应覆盖两个块
    assert qs[0].box[3] >= 30 + 30  # 高度包含两块


def test_grade_choice_correct():
    ans = AnswerItem(qid="1", type="choice", correct_answer="B")
    sq = StudentQuestion(qid="1", text="我选B", answer="我选B", box=[0, 0, 10, 10])
    r = grade_question(ans, sq)
    assert r.status == "correct"


def test_grade_choice_incorrect():
    ans = AnswerItem(qid="1", type="choice", correct_answer="B")
    sq = StudentQuestion(qid="1", text="选 C", answer="选 C", box=[0, 0, 10, 10])
    r = grade_question(ans, sq)
    assert r.status == "incorrect"


def test_grade_fill_normalization():
    ans = AnswerItem(qid="2", type="fill", correct_answer="Beijing")
    sq = StudentQuestion(qid="2", text="ｂｅｉｊｉｎｇ", answer="ｂｅｉｊｉｎｇ", box=[0, 0, 10, 10])
    r = grade_question(ans, sq)
    assert r.status == "correct"


def test_grade_judge():
    ans = AnswerItem(qid="3", type="judge", correct_answer="对")
    sq_correct = StudentQuestion(qid="3", text="√", answer="√", box=[0, 0, 10, 10])
    sq_wrong = StudentQuestion(qid="3", text="错误", answer="错误", box=[0, 0, 10, 10])
    assert grade_question(ans, sq_correct).status == "correct"
    assert grade_question(ans, sq_wrong).status == "incorrect"


def test_grade_unmatched_when_no_student():
    ans = AnswerItem(qid="9", type="choice", correct_answer="A")
    r = grade_question(ans, None)
    assert r.status == "unmatched"


if __name__ == "__main__":
    # 无 pytest 也能跑
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL TESTS PASSED")
