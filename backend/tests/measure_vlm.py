"""实测：MiMo-VLM 读学生答案 vs 当前 PaddleOCR 路径。
对比同一份真实试卷上，两种学生端识别的命中率。
"""
import os, sys, base64, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

for ln in open(Path(__file__).resolve().parents[1] / ".env", encoding="utf-8"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); os.environ[k] = v

from openai import OpenAI
from app.services import ocr, answer_key
from app.services.preprocess import enhance
from app.services.grader import _grade_objective

JOB = Path("_data/jobs/1a2c5dc4b9d7")
STU = sorted(JOB.glob("student_upload*"))[0]
ANS = sorted(JOB.glob("answer_upload*"))[0]

# 1) 正确答案（复用现有 DeepSeek 结构化）
enhance(ANS, JOB / "_ans_e.png")
ans_boxes = ocr.ocr_image(JOB / "_ans_e.png")
correct_items = answer_key.structure_answer_key(ans_boxes)
correct = {a.qid: a for a in correct_items}
print(f"正确答案题数: {len(correct)}  qids={sorted(correct)}")

# 2) MiMo-VLM 读学生答案
mimo = OpenAI(api_key=os.environ["MIMO_API_KEY"], base_url=os.environ["MIMO_BASE_URL"])
img_b64 = base64.b64encode(open(STU, "rb").read()).decode()
prompt = (
    "这是一份英语试卷的学生作答扫描图。请逐题识别【学生填写/选择的答案】。\n"
    "- 选择题：答案在题号前或后的括号里，如 (C) / （A）/ B32 表示该题选 B；空括号 () 表示未作答。\n"
    "- 填空题：学生手写的单词。\n"
    '- 只返回 JSON：{"answers":[{"qid":题号字符串,"answer":学生答案,没有作答用空字符串}]}。不要解释。'
)
t0 = time.time()
resp = mimo.chat.completions.create(
    model=os.environ["MIMO_MODEL"],
    messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
        {"type": "text", "text": prompt},
    ]}],
    max_completion_tokens=2000,
    temperature=0.0,
)
raw = resp.choices[0].message.content
# 去围栏
import re
raw2 = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw.strip(), flags=re.M).strip()
try:
    data = json.loads(raw2)
except Exception:
    # 取第一个 { 到最后一个 }
    s, e = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[s:e+1])
print(f"MiMo 耗时 {time.time()-t0:.1f}s, 识别题数 {len(data.get('answers', []))}")
vlm = {str(a["qid"]).strip(): str(a.get("answer", "")).strip() for a in data.get("answers", [])}

# 3) 比对
print("\nqid | 正确 | VLM学生 | 判定")
graded = 0
for qid in sorted(correct, key=lambda x: int(x) if x.isdigit() else 999):
    ans = correct[qid]
    stu_ans = vlm.get(qid, "❌未识别")
    if stu_ans == "❌未识别":
        verdict = "未识别"
    else:
        status, _ = _grade_objective(ans, stu_ans if stu_ans else " ")
        verdict = {"correct": "✓对", "incorrect": "✗错", "unmatched": "?"}.get(status, status)
        graded += 1
    print(f"  {qid:>3} | {ans.correct_answer:>5} | {stu_ans[:8]:>8} | {verdict}")

print(f"\nMiMo-VLM: 识别到 {len(vlm)} 题，其中与答案对上判分 {graded} 题")
print("对照：当前 PaddleOCR 路径 = 判分 6 题（11 unmatched）")
