"""端到端流水线验证（LLM 桩）。

用真实的 PaddleOCR + matcher + grader + pipeline 跑两张样例图，
仅把 DeepSeek 网络调用替换为预设 JSON，从而验证除"真实 API 调用"外的全部集成逻辑。

运行：python tests/e2e_pipeline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.services import llm  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "_samples"

# ---- 预设 LLM 响应 ----
# 答案结构化（模拟 DeepSeek 对答案图的整理结果）
ANSWER_JSON = {
    "answers": [
        {"qid": "1", "type": "choice", "correct_answer": "B", "max_score": 1},
        {"qid": "2", "type": "choice", "correct_answer": "B", "max_score": 1},
        {"qid": "3", "type": "judge", "correct_answer": "对", "max_score": 1},
        {"qid": "4", "type": "fill", "correct_answer": "30", "max_score": 1},
        {"qid": "5", "type": "essay", "correct_answer": "植物利用光能...", "max_score": 10,
         "rubric": ["提到光能", "提到二氧化碳和水", "提到有机物/氧气"]},
    ]
}
# 主观题判分（题5 学生答"植物利用阳光合成有机物"——提到光能和有机物，缺二氧化碳和水）
ESSAY_JSON = {"score": 6, "analysis": "提到光能与有机物，但未提二氧化碳和水，扣4分。", "hit": ["提到光能", "提到有机物/氧气"]}


def fake_chat_json(system, user, retries=1):
    if "结构化答案" in system or "标准答案的 OCR" in system or "结构化答案" in system:
        return ANSWER_JSON
    if "阅卷老师" in system or "评分细则给学生答案打分" in system:
        return ESSAY_JSON
    return {}


def main():
    # 打桩
    llm.chat_json = fake_chat_json  # type: ignore

    from app.services import pipeline  # noqa: E402

    stu = (SAMPLES / "student.png").read_bytes()
    ans = (SAMPLES / "answer.png").read_bytes()

    resp = pipeline.run_pipeline(stu, "student.png", ans, "answer.png")

    print(f"job={resp.job}  页数={len(resp.pages)}")
    page = resp.pages[0]
    print(f"page1: {page.width}x{page.height}  image_url={page.image_url}")
    print(f"题目数={len(page.questions)}")
    print("-" * 60)

    ok = True
    expected = {
        "1": ("choice", "correct"),    # 学生 B, 答案 B
        "2": ("choice", "incorrect"),  # 学生 C, 答案 B
        "3": ("judge", "correct"),     # 学生 对, 答案 对
        "4": ("fill", "correct"),      # 学生 30, 答案 30
    }
    by_qid = {q.qid: q for q in page.questions}
    for qid, q in sorted(by_qid.items()):
        box_valid = q.box[2] > 0 or q.box[3] > 0
        print(f"题{qid} type={q.type} status={q.status} score={q.score}/{q.max_score} "
              f"box有效={box_valid} 学生='{q.student_answer[:20]}'")
        if qid in expected:
            t, s = expected[qid]
            if q.type != t or q.status != s:
                print(f"   ✗ 预期 type={t} status={s}")
                ok = False
        if not box_valid and q.status != "unmatched":
            print("   ✗ 有题但坐标为空")
            ok = False

    # 主观题题5：应得 6 分，status partial
    q5 = by_qid.get("5")
    if q5:
        if q5.score == 6 and q5.status == "partial" and q5.analysis:
            print(f"题5 主观题: score={q5.score} status={q5.status} 解析正常 ✓")
        else:
            print(f"   ✗ 题5 预期 6/partial，实际 {q5.score}/{q5.status}")
            ok = False

    # 图片文件是否真的生成
    from app.config import settings
    img_path = settings.data_path / "jobs" / resp.job / "student_p1.png"
    print("-" * 60)
    print(f"图片文件存在: {img_path.exists()}")
    if not img_path.exists():
        ok = False

    print("\n结论:", "✓ 全部集成逻辑通过" if ok else "✗ 存在问题")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
