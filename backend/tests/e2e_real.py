"""完全真实的端到端验证：调用真实 DeepSeek（答案结构化 + 主观题判分）。

运行：python tests/e2e_real.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "_samples"


def main():
    assert settings.deepseek_api_key, "未配置 DEEPSEEK_API_KEY"
    print(f"使用模型: {settings.deepseek_model.strip()}  key: {settings.deepseek_api_key[:8]}...")

    from app.services import pipeline  # noqa: E402

    stu = (SAMPLES / "student.png").read_bytes()
    ans = (SAMPLES / "answer.png").read_bytes()

    resp = pipeline.run_pipeline(stu, "student.png", ans, "answer.png")
    page = resp.pages[0]
    print(f"job={resp.job}  {page.width}x{page.height}  题目数={len(page.questions)}")
    print("-" * 70)
    for q in page.questions:
        print(f"题{q.qid} [{q.type}] {q.status}  {q.score}/{q.max_score}")
        print(f"   学生: {q.student_answer[:40]}")
        print(f"   正确: {q.correct_answer[:40]}")
        if q.analysis:
            print(f"   解析: {q.analysis[:80]}")
    print("-" * 70)
    print("✓ 真实端到端完成")


if __name__ == "__main__":
    main()
