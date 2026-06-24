"""端到端批改流水线：把上传的两个文件转成 GradeResponse。"""
from __future__ import annotations
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from ..config import settings
from ..models import GradeResponse, PageResult, QuestionResult
from . import matcher, grader, vlm, llm
from .matcher import StudentQuestion
from .pdf import to_images, PdfUnavailableError

# 可选导入：轻量版不需要 OCR 和 OpenCV
try:
    from . import ocr
    from .preprocess import enhance
except ImportError:
    ocr = None
    enhance = None

# 检测 PaddleOCR 是否可用
_OCR_AVAILABLE = False
if os.environ.get("FORCE_LITE") != "1" and ocr is not None and enhance is not None:
    try:
        import paddleocr as _pocr
        _OCR_AVAILABLE = True
    except ImportError:
        pass

_TYPE_CN = {"choice": "选择题", "fill": "填空题", "judge": "判断题", "essay": "简答题"}


def _log(msg: str) -> None:
    print(f"[pipeline] {msg}", file=sys.stderr, flush=True)


def _read_all_answer_keys(imgs):
    items = []
    for ap in imgs:
        items.extend(vlm.read_answer_key(ap))
    return items


def _read_all_student_pages(imgs):
    return [vlm.read_student_answers(sp) for sp in imgs]


def _read_all_student_pages_with_boxes(imgs):
    results = []
    for sp in imgs:
        w, h = Image.open(sp).size
        results.append(vlm.read_student_answers_with_boxes(sp, w, h))
    return results


def _estimate_missing_boxes(merged, page_w, page_h):
    def _box_ok(sq):
        return sq.box[2] > 0 or sq.box[3] > 0
    qids = sorted(merged.keys(), key=lambda x: int(x) if x.isdigit() else 9999)
    for qid in qids:
        sq = merged[qid]
        if _box_ok(sq) or not qid.isdigit():
            continue
        n = int(qid)
        prev_sq, next_sq = None, None
        for p in range(n - 1, 0, -1):
            ps = merged.get(str(p))
            if ps and _box_ok(ps):
                prev_sq = ps
                break
        for p in range(n + 1, 999):
            ps = merged.get(str(p))
            if ps and _box_ok(ps):
                next_sq = ps
                break
        if prev_sq and next_sq:
            pn = int(prev_sq.qid) if prev_sq.qid.isdigit() else n - 1
            nn = int(next_sq.qid) if next_sq.qid.isdigit() else n + 1
            ratio = (n - pn) / (nn - pn)
            bx = prev_sq.box[0] + ratio * (next_sq.box[0] - prev_sq.box[0])
            by = prev_sq.box[1] + ratio * (next_sq.box[1] - prev_sq.box[1])
            bw = prev_sq.box[2]
            bh = max(25, (next_sq.box[1] - prev_sq.box[1]) / max(nn - pn, 1))
        elif next_sq:
            nn = int(next_sq.qid) if next_sq.qid.isdigit() else n + 1
            LINE_GAP = 38
            bx = next_sq.box[0]
            by = max(0, next_sq.box[1] - LINE_GAP * (nn - n))
            bw = next_sq.box[2]
            bh = LINE_GAP
        elif prev_sq:
            pn = int(prev_sq.qid) if prev_sq.qid.isdigit() else n - 1
            LINE_GAP = 38
            bx = prev_sq.box[0]
            by = prev_sq.box[1] + LINE_GAP * (n - pn)
            bw = prev_sq.box[2]
            bh = LINE_GAP
        else:
            continue
        by = max(0, min(by, page_h - 30))
        sq.box = [bx, by, bw, bh]


def _llm_analysis(pages):
    context_lines = []
    for page in pages:
        for q in page.questions:
            if q.status == "incorrect":
                context_lines.append(f"第{q.qid}题：学生答「{q.student_answer or '未作答'}」，正确答案「{q.correct_answer}」。")
            elif q.status == "partial":
                context_lines.append(f"第{q.qid}题：学生答「{q.student_answer or '未作答'}」，满分{q.max_score}得{q.score}分。")
    if not context_lines:
        return ""
    return vlm._call_text("你是学科教师，根据批改结果深度分析。", "\n".join(context_lines) + "\n分析知识点、错误类型、薄弱环节、建议。")


def _detailed_per_question_analysis(pages, student_image=None):
    wrong = [q for page in pages for q in page.questions if q.status in ("incorrect", "partial")]
    if not wrong:
        return
    for q in wrong:
        # 用题干原文生成准确解析
        qtext = q.question_text or ""
        prompt = (
            f"题目类型：{_TYPE_CN.get(q.type, q.type)}\n"
            f"题干：{qtext or '（无题干）'}\n"
            f"正确答案：{q.correct_answer}\n"
            f"学生答案：{q.student_answer or '未作答'}\n\n"
            f"请根据以上信息，写一段80字以内的解析：为什么错 + 正确知识点 + 记忆技巧。直接输出解析内容。"
        )
        result = vlm._call_text("你是经验丰富的学科教师。根据题目内容写准确的解析。", prompt)
        if result:
            q.analysis = result.strip()


def _generate_summary(pages):
    total = correct = incorrect = partial = unmatched = 0
    by_type = {}
    wrong_details = []
    partial_details = []
    for page in pages:
        for q in page.questions:
            total += 1
            if q.status == "correct": correct += 1
            elif q.status == "incorrect":
                incorrect += 1
                wrong_details.append({"qid": q.qid, "type": _TYPE_CN.get(q.type, q.type), "student": q.student_answer or "未作答", "correct": q.correct_answer or "—", "analysis": q.analysis or ""})
            elif q.status == "partial":
                partial += 1
                partial_details.append({"qid": q.qid, "student": q.student_answer or "未作答", "correct": q.correct_answer or "—", "score": q.score, "max": q.max_score, "analysis": q.analysis or ""})
            else: unmatched += 1
            bt = by_type.setdefault(q.type, {"total": 0, "correct": 0, "incorrect": 0, "partial": 0})
            bt["total"] += 1
            if q.status == "correct": bt["correct"] += 1
            elif q.status == "incorrect": bt["incorrect"] += 1
            elif q.status == "partial": bt["partial"] += 1
    rate = correct / total * 100 if total else 0
    grade_label = "优秀" if rate >= 90 else "良好" if rate >= 75 else "及格" if rate >= 60 else "需加强"
    lines = ["=" * 40, "试卷批改点评报告", "=" * 40, "", f"一、总体成绩", f"  正确率：{correct}/{total}（{rate:.0f}%）", f"  等级：{grade_label}", ""]
    lines.append("二、各题型得分")
    for t, s in by_type.items():
        name = _TYPE_CN.get(t, t)
        tr = s["correct"] / s["total"] * 100 if s["total"] else 0
        lines.append(f"  {name}：{s['correct']}/{s['total']}（{tr:.0f}%）")
    weak = [(t, s) for t, s in by_type.items() if s["total"] > 0 and s["correct"] / s["total"] < 0.6]
    if weak:
        lines.append("")
        lines.append("三、薄弱环节")
        for t, s in weak:
            lines.append(f"  {_TYPE_CN.get(t, t)}正确率仅 {s['correct']}/{s['total']}，建议重点复习")
    if wrong_details:
        lines.append("")
        lines.append(f"四、错题详情（{len(wrong_details)}题）")
        for i, d in enumerate(wrong_details, 1):
            lines += [f"  {i}. 第{d['qid']}题（{d['type']}）学生答{d['student']}，正确答案{d['correct']}"]
            if d["analysis"]: lines.append(f"     {d['analysis']}")
    lines.append("")
    lines.append("六、学习建议")
    if rate < 60: lines.append("  建议从基础开始系统复习")
    elif rate < 75: lines.append("  基础有待加强，多做练习")
    elif rate < 90: lines.append("  成绩良好，针对错题巩固")
    else: lines.append("  优秀，继续保持")
    lines.append("=" * 40)
    return "\n".join(lines)


def _save_upload(data, role, suffix, job_dir):
    p = job_dir / f"{role}_upload{suffix or '.bin'}"
    p.write_bytes(data)
    return p


def run_pipeline(student_bytes, student_name, answer_bytes, answer_name):
    t_start = time.time()
    job = uuid.uuid4().hex[:12]
    job_dir = settings.data_path / "jobs" / job
    job_dir.mkdir(parents=True, exist_ok=True)
    _log(f"job={job}")

    stu_src = _save_upload(student_bytes, "student", Path(student_name).suffix or ".bin", job_dir)
    ans_src = _save_upload(answer_bytes, "answer", Path(answer_name).suffix or ".bin", job_dir)
    stu_imgs = to_images(stu_src, job_dir, prefix="student")
    ans_imgs = to_images(ans_src, job_dir, prefix="answer")
    _log(f"images: {len(stu_imgs)} stu, {len(ans_imgs)} ans")

    t = time.time()
    if _OCR_AVAILABLE:
        with ThreadPoolExecutor(max_workers=2) as pool:
            answer_items = pool.submit(_read_all_answer_keys, ans_imgs).result()
            all_stu_vlm = pool.submit(_read_all_student_pages, stu_imgs).result()
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            answer_items = pool.submit(_read_all_answer_keys, ans_imgs).result()
            all_stu_vlm = pool.submit(_read_all_student_pages_with_boxes, stu_imgs).result()
    _log(f"VLM in {time.time()-t:.1f}s")

    pages = []
    matched_qids = set()
    for idx, sp in enumerate(stu_imgs, start=1):
        w, h = Image.open(sp).size
        vlm_data = all_stu_vlm[idx - 1] if idx - 1 < len(all_stu_vlm) else {}
        if _OCR_AVAILABLE and enhance and ocr:
            enhanced = job_dir / f"student_e{idx}.png"
            enhance(sp, enhanced)
            boxes = ocr.ocr_image(enhanced)
            squestions = matcher.split_by_qid(boxes)
            merged = {sq.qid: sq for sq in squestions}
            for qid, ans in vlm_data.items():
                if qid in merged: merged[qid].answer = ans
                else: merged[qid] = StudentQuestion(qid=qid, text="", answer=ans, box=[0,0,0,0])
        else:
            merged = {}
            for qid, info in vlm_data.items():
                answer = info.get("answer", "") if isinstance(info, dict) else str(info)
                box = info.get("box", [0,0,0,0]) if isinstance(info, dict) else [0,0,0,0]
                qtext = info.get("question", "") if isinstance(info, dict) else ""
                merged[qid] = StudentQuestion(qid=qid, text="", answer=answer, box=box, question_text=qtext)
        _estimate_missing_boxes(merged, w, h)
        qresults = []
        for ans in answer_items:
            sq = merged.get(ans.qid)
            if sq is not None:
                matched_qids.add(ans.qid)
                qresults.append(grader.grade_question(ans, sq))
        pages.append(PageResult(page=idx, image_url=f"/api/images/{job}/student_p{idx}.png", width=w, height=h, questions=qresults))
    if pages:
        for ans in answer_items:
            if ans.qid not in matched_qids:
                pages[0].questions.append(grader.grade_question(ans, None))
    summary = _generate_summary(pages)
    import json as _json
    (job_dir / "result.json").write_text(_json.dumps(GradeResponse(job=job, pages=pages, summary=summary).model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"DONE {time.time()-t_start:.1f}s")
    return GradeResponse(job=job, pages=pages, summary=summary)
