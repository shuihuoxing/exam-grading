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
from . import ocr, matcher, grader, vlm, llm

# 检测 PaddleOCR 是否可用（轻量部署时可省略，或设 FORCE_LITE=1 强制轻量模式）
_OCR_AVAILABLE = False
if os.environ.get("FORCE_LITE") != "1":
    try:
        import paddleocr as _pocr  # noqa: F401 — 仅检测包是否存在
        _OCR_AVAILABLE = True
    except ImportError:
        pass
from .matcher import StudentQuestion
from .pdf import to_images, PdfUnavailableError
from .preprocess import enhance

_TYPE_CN = {"choice": "选择题", "fill": "填空题", "judge": "判断题", "essay": "简答题"}


def _log(msg: str) -> None:
    print(f"[pipeline] {msg}", file=sys.stderr, flush=True)


# ---------- 并行读取 ----------

def _read_all_answer_keys(imgs: list[Path]) -> list:
    items = []
    for ap in imgs:
        items.extend(vlm.read_answer_key(ap))
    return items


def _read_all_student_pages(imgs: list[Path]) -> list[dict]:
    return [vlm.read_student_answers(sp) for sp in imgs]


def _read_all_student_pages_with_boxes(imgs: list[Path]) -> list[dict]:
    """无 PaddleOCR 时：VLM 同时返回答案 + 坐标。"""
    results = []
    for sp in imgs:
        w, h = Image.open(sp).size
        raw = vlm.read_student_answers_with_boxes(sp, w, h)
        # 统一格式：{qid: answer_str} 或 {qid: {answer, box}}
        results.append(raw)
    return results


# ---------- 位置估算 ----------

def _estimate_missing_boxes(merged: dict, page_w: int, page_h: int) -> None:
    """为无 bbox 的题号估算位置（用相邻题号线性插值）。"""
    def _box_ok(sq: StudentQuestion) -> bool:
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


# ---------- 点评生成 ----------

def _llm_analysis(pages: list[PageResult]) -> str:
    """调用 DeepSeek 对错题做深度分析。失败返回空，不阻塞主流程。"""
    context_lines = []
    for page in pages:
        for q in page.questions:
            if q.status == "incorrect":
                context_lines.append(
                    f"第{q.qid}题（{_TYPE_CN.get(q.type, q.type)}）："
                    f"学生答「{q.student_answer or '未作答'}」，"
                    f"正确答案「{q.correct_answer}」。"
                )
            elif q.status == "partial":
                context_lines.append(
                    f"第{q.qid}题（{_TYPE_CN.get(q.type, q.type)}）："
                    f"学生答「{q.student_answer or '未作答'}」，"
                    f"满分{q.max_score}得{q.score}分。"
                )

    if not context_lines:
        return ""

    system = (
        "你是一位经验丰富的学科教师和教育分析师。根据以下学生试卷批改结果，"
        "对错题和部分得分题进行深度分析。"
    )
    user = (
        "以下是学生试卷的批改结果（错题和部分得分题）：\n\n"
        + "\n".join(context_lines)
        + "\n\n请从以下维度进行深度分析，用简洁中文回答：\n"
        "1. 知识点诊断：每道错题涉及的具体知识点是什么？\n"
        "2. 错误类型归类：是概念混淆、审题不清、知识盲区还是计算粗心？\n"
        "3. 薄弱环节总结：整体来看哪些知识模块最需要加强？\n"
        "4. 针对性提升建议：给出 2-3 条具体可执行的学习建议。"
    )

    result = llm.chat_text(system, user, retries=1)
    return result


def _detailed_per_question_analysis(pages: list[PageResult]) -> None:
    """调用 MiMo 为每道错题生成学科级详细解析，直接写回 q.analysis。"""
    wrong = []
    for page in pages:
        for q in page.questions:
            if q.status in ("incorrect", "partial"):
                wrong.append(q)
    if not wrong:
        return

    system = (
        "你是一位经验丰富的学科教师。请为每道错题写出详细解析。\n"
        "每道题的解析必须包含：\n"
        "1. 为什么错：指出学生的错误原因\n"
        "2. 正确用法：给出正确的语法规则、搭配或知识点\n"
        "3. 记忆技巧：一句话帮助记忆\n"
        "每道题解析控制在 1-2 句话，简洁但有实质内容。"
    )

    lines = []
    for q in wrong:
        lines.append(
            f"第{q.qid}题（{_TYPE_CN.get(q.type, q.type)}）："
            f"正确答案「{q.correct_answer}」，学生答「{q.student_answer or '未作答'}」"
        )

    user = "\n".join(lines) + "\n\n请逐题输出，每题格式：\n第X题：为什么错。正确用法。记忆技巧。"

    # 用 MiMo（文本能力）代替 DeepSeek
    from . import vlm as _vlm
    result = _vlm._call_text(system, user)
    if not result:
        return

    import re as _re
    parts = _re.split(r"第(\d+)题[：:]", result)
    analysis_map = {}
    for i in range(1, len(parts) - 1, 2):
        qid = parts[i].strip()
        text = parts[i + 1].strip()
        if qid and text:
            analysis_map[qid] = text

    for q in wrong:
        if q.qid in analysis_map:
            q.analysis = analysis_map[q.qid]


def _generate_summary(pages: list[PageResult]) -> str:
    total = correct = incorrect = partial = unmatched = 0
    by_type: dict[str, dict] = {}
    wrong_details: list[dict] = []
    partial_details: list[dict] = []

    for page in pages:
        for q in page.questions:
            total += 1
            if q.status == "correct":
                correct += 1
            elif q.status == "incorrect":
                incorrect += 1
                wrong_details.append({
                    "qid": q.qid,
                    "type": _TYPE_CN.get(q.type, q.type),
                    "student": q.student_answer or "未作答",
                    "correct": q.correct_answer or "—",
                    "analysis": q.analysis or "",
                })
            elif q.status == "partial":
                partial += 1
                partial_details.append({
                    "qid": q.qid,
                    "student": q.student_answer or "未作答",
                    "correct": q.correct_answer or "—",
                    "score": q.score,
                    "max": q.max_score,
                    "analysis": q.analysis or "",
                })
            else:
                unmatched += 1
            bt = by_type.setdefault(q.type, {"total": 0, "correct": 0, "incorrect": 0, "partial": 0})
            bt["total"] += 1
            if q.status == "correct":
                bt["correct"] += 1
            elif q.status == "incorrect":
                bt["incorrect"] += 1
            elif q.status == "partial":
                bt["partial"] += 1

    rate = correct / total * 100 if total else 0
    if rate >= 90:
        grade_label = "优秀"
    elif rate >= 75:
        grade_label = "良好"
    elif rate >= 60:
        grade_label = "及格"
    else:
        grade_label = "需加强"

    lines = []
    lines.append("=" * 40)
    lines.append("          试卷批改点评报告")
    lines.append("=" * 40)
    lines.append("")
    lines.append(f"一、总体成绩")
    lines.append(f"  正确率：{correct}/{total}（{rate:.0f}%）")
    lines.append(f"  等级评定：{grade_label}")
    lines.append(f"  正确 {correct} 题 / 错误 {incorrect} 题 / 部分得分 {partial} 题 / 未识别 {unmatched} 题")
    lines.append("")

    # 各题型
    lines.append(f"二、各题型得分情况")
    for t, s in by_type.items():
        name = _TYPE_CN.get(t, t)
        tr = s["correct"] / s["total"] * 100 if s["total"] else 0
        bar = "#" * int(tr / 10) + "-" * (10 - int(tr / 10))
        lines.append(f"  {name}：{s['correct']}/{s['total']}（{tr:.0f}%）[{bar}]")
    lines.append("")

    # 薄弱环节
    weak = [(t, s) for t, s in by_type.items()
            if s["total"] > 0 and s["correct"] / s["total"] < 0.6]
    if weak:
        lines.append(f"三、薄弱环节分析")
        for t, s in weak:
            name = _TYPE_CN.get(t, t)
            tr = s["correct"] / s["total"] * 100
            lines.append(f"  {name}正确率仅 {tr:.0f}%（{s['correct']}/{s['total']}），建议重点复习")
        lines.append("")

    # 错题详情
    if wrong_details:
        lines.append(f"四、错题详情（共 {len(wrong_details)} 题）")
        for i, d in enumerate(wrong_details, 1):
            lines.append(f"  {i}. 第 {d['qid']} 题（{d['type']}）")
            lines.append(f"     学生答案：{d['student']}")
            lines.append(f"     正确答案：{d['correct']}")
            if d["analysis"]:
                lines.append(f"     点评：{d['analysis']}")
        lines.append("")

    # 部分得分
    if partial_details:
        lines.append(f"五、部分得分（共 {len(partial_details)} 题）")
        for i, d in enumerate(partial_details, 1):
            lines.append(f"  {i}. 第 {d['qid']} 题：得分 {d['score']}/{d['max']}")
            lines.append(f"     学生答案：{d['student']}")
            lines.append(f"     参考答案：{d['correct']}")
            if d["analysis"]:
                lines.append(f"     点评：{d['analysis']}")
        lines.append("")

    # 学习建议
    lines.append(f"六、学习建议")
    if rate >= 90:
        lines.append("  成绩优秀，继续保持。可适当挑战更高难度的综合题。")
    elif rate >= 75:
        lines.append("  成绩良好，基础扎实。建议针对错题涉及的知识点进行巩固。")
    elif rate >= 60:
        lines.append("  刚及格，基础有待加强。建议系统复习薄弱题型，多做同类型练习。")
    else:
        lines.append("  成绩不理想，建议从基础知识点开始系统复习，每天坚持做题。")

    if weak:
        weak_names = [_TYPE_CN.get(t, t) for t, _ in weak]
        lines.append(f"  重点复习：{'、'.join(weak_names)}")
    if wrong_details:
        lines.append(f"  建议重新做一遍错题，确保掌握正确解法。")
    lines.append("")
    lines.append("=" * 40)

    return "\n".join(lines)


# ---------- 入口 ----------

def _save_upload(data: bytes, role: str, suffix: str, job_dir: Path) -> Path:
    p = job_dir / f"{role}_upload{suffix or '.bin'}"
    p.write_bytes(data)
    return p


def run_pipeline(student_bytes: bytes, student_name: str,
                 answer_bytes: bytes, answer_name: str) -> GradeResponse:
    t_start = time.time()
    job = uuid.uuid4().hex[:12]
    job_dir = settings.data_path / "jobs" / job
    job_dir.mkdir(parents=True, exist_ok=True)
    _log(f"job={job} student={student_name} answer={answer_name} "
         f"stu_bytes={len(student_bytes)} ans_bytes={len(answer_bytes)}")

    # 1. 归一化为图片
    t = time.time()
    stu_src = _save_upload(student_bytes, "student", Path(student_name).suffix or ".bin", job_dir)
    ans_src = _save_upload(answer_bytes, "answer", Path(answer_name).suffix or ".bin", job_dir)
    try:
        stu_imgs = to_images(stu_src, job_dir, prefix="student")
        ans_imgs = to_images(ans_src, job_dir, prefix="answer")
    except PdfUnavailableError:
        raise
    _log(f"to_images: {len(stu_imgs)} stu pages, {len(ans_imgs)} ans pages in {time.time()-t:.1f}s")

    # 2. VLM 并行：答案结构化 + 学生作答读取（两路同时跑，省 ~15s）
    t = time.time()
    if _OCR_AVAILABLE:
        # 有 PaddleOCR：VLM 只读答案内容，OCR 负责坐标
        with ThreadPoolExecutor(max_workers=2) as pool:
            ans_future = pool.submit(_read_all_answer_keys, ans_imgs)
            stu_future = pool.submit(_read_all_student_pages, stu_imgs)
            answer_items = ans_future.result()
            all_stu_vlm = stu_future.result()
    else:
        # 无 PaddleOCR：VLM 同时读答案内容 + 学生答案+坐标
        with ThreadPoolExecutor(max_workers=2) as pool:
            ans_future = pool.submit(_read_all_answer_keys, ans_imgs)
            stu_future = pool.submit(_read_all_student_pages_with_boxes, stu_imgs)
            answer_items = ans_future.result()
            all_stu_vlm = stu_future.result()
    _log(f"VLM parallel: answer {len(answer_items)} items, student {[len(v) for v in all_stu_vlm]}, ocr={'Y' if _OCR_AVAILABLE else 'N'} in {time.time()-t:.1f}s")

    # 3. 逐页处理：PaddleOCR 给坐标，合并 VLM 作答
    pages: list[PageResult] = []
    matched_qids: set[str] = set()

    for idx, sp in enumerate(stu_imgs, start=1):
        t = time.time()
        w, h = Image.open(sp).size
        vlm_data = all_stu_vlm[idx - 1] if idx - 1 < len(all_stu_vlm) else {}

        if _OCR_AVAILABLE:
            # PaddleOCR 模式：OCR 给坐标，VLM 给答案
            enhanced = job_dir / f"student_e{idx}.png"
            enhance(sp, enhanced)
            boxes = ocr.ocr_image(enhanced)
            squestions = matcher.split_by_qid(boxes)
            sq_by_qid = {sq.qid: sq for sq in squestions}
            vlm_ans = vlm_data  # {qid: answer_str}
            _log(f"page{idx}: OCR {len(squestions)}题, VLM {len(vlm_ans)}题, {w}x{h} in {time.time()-t:.1f}s")
            merged: dict[str, StudentQuestion] = dict(sq_by_qid)
            for qid, ans in vlm_ans.items():
                if qid in merged:
                    merged[qid].answer = ans
                else:
                    merged[qid] = StudentQuestion(qid=qid, text="", answer=ans, box=[0, 0, 0, 0])
        else:
            # 纯 VLM 模式：VLM 同时给答案 + 坐标
            merged = {}
            for qid, info in vlm_data.items():
                answer = info.get("answer", "") if isinstance(info, dict) else str(info)
                box = info.get("box", [0, 0, 0, 0]) if isinstance(info, dict) else [0, 0, 0, 0]
                merged[qid] = StudentQuestion(qid=qid, text="", answer=answer, box=box)
            _log(f"page{idx}: VLM {len(merged)}题, {w}x{h} in {time.time()-t:.1f}s")
        _estimate_missing_boxes(merged, w, h)

        t = time.time()
        qresults: list[QuestionResult] = []
        for ans in answer_items:
            sq = merged.get(ans.qid)
            if sq is not None:
                matched_qids.add(ans.qid)
                qresults.append(grader.grade_question(ans, sq))
        _log(f"page{idx} grade: {len(qresults)} matched in {time.time()-t:.1f}s")
        pages.append(PageResult(
            page=idx,
            image_url=f"/api/images/{job}/student_p{idx}.png",
            width=w, height=h,
            questions=qresults,
        ))

    # 4. 未在任何页匹配的题 → 挂到第一页
    if pages:
        for ans in answer_items:
            if ans.qid not in matched_qids:
                pages[0].questions.append(grader.grade_question(ans, None))

    # 5. 生成即时摘要
    summary = _generate_summary(pages)

    # 6. 保存结果供后续深度分析使用
    import json as _json
    result_data = GradeResponse(job=job, pages=pages, summary=summary).model_dump()
    (job_dir / "result.json").write_text(_json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")

    _log(f"DONE total {time.time()-t_start:.1f}s")
    return GradeResponse(job=job, pages=pages, summary=summary)
