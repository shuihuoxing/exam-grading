# 自动阅卷系统 设计文档

> 状态：VLM 混合架构已实施并验证（17/17 判分）
> 日期：2026-06-18 → 更新于 2026-06-23

## 1. 目标

上传学生答卷（图片/PDF）+ 标准答案（图片/PDF），系统自动批改：
- 客观题（选择/填空/判断）：代码比对，打 √ / ×。
- 主观题（简答）：按 Rubric 打分并生成点评。
- 在学生原图对应题目坐标画红 √ / ×，右侧分栏显示正确答案与解析（点击红叉联动）。
- 支持查看、手动修正批注、导出 PNG。

## 2. 关键决策

| 维度 | 决策 |
|---|---|
| 读答案 | **MiMo-VLM 多模态**（直接看图读学生作答 + 标准答案，比纯 OCR 准、覆盖全） |
| 定位坐标 | PaddleOCR（本地，给每题 bbox 用于画 √/×） |
| 主观题判分 | DeepSeek（文本，按 Rubric 打分+点评） |
| 答案来源 | 答案图片/PDF，由 MiMo-VLM 直接看图结构化为答案表（替代 OCR+DeepSeek） |
| 部署形态 | 面向公网的产品级 Web 应用，当前仅少数人使用（架构正规，不为高并发过度设计） |
| 题型 | 客观题 + 主观题 |
| 渲染层 | 客户端 Canvas（Fabric.js） |
| 前端 | Vue 3 + Vite + Ant Design Vue + Fabric.js + Pinia |
| 后端 | Python FastAPI |
| 鉴权（MVP） | 单一访问 Token 保护入口，后续再加多用户 |

## 3. 数据流

```
学生答卷(图/PDF) ─┐                          ┌→ 客观题: 代码规范化比对
                 ├→ PaddleOCR(文字+box) → 按题号配对 ─┤
答案(图/PDF) ────┘  → DeepSeek结构化答案+Rubric      └→ 主观题: DeepSeek 按Rubric打分+点评
                                                          ↓
                            每题 {qid,type,status/score,box,student_answer,correct_answer,analysis}
                                                          ↓
                                  前端 Canvas 在原图坐标画 √/× + 右侧批注联动
```

## 4. 仓库结构

```
试卷批阅/
├─ backend/
│  ├─ app/
│  │  ├─ main.py             # FastAPI 入口, CORS, 路由, 静态图片服务
│  │  ├─ config.py           # 环境变量(DeepSeek key/token)
│  │  ├─ api/
│  │  │  └─ routes.py        # POST /api/grade, GET /api/images/{...}
│  │  ├─ services/
│  │  │  ├─ pdf.py           # PDF → 300DPI PNG (pdf2image)
│  │  │  ├─ preprocess.py    # OpenCV 去噪/灰度/对比度增强(可选纠偏)
│  │  │  ├─ ocr.py           # PaddleOCR 封装: 图片 → [{text, box}]
│  │  │  ├─ llm.py           # DeepSeek 客户端(JSON 输出, 重试)
│  │  │  ├─ answer_key.py    # 答案 OCR → DeepSeek → 结构化答案表
│  │  │  ├─ matcher.py       # 学生作答按题号与答案配对
│  │  │  └─ grader.py        # 客观题比对 + 主观题 DeepSeek
│  │  └─ models.py           # Pydantic 模型
│  ├─ requirements.txt
│  └─ .env.example
├─ frontend/
│  ├─ src/
│  │  ├─ main.js, App.vue
│  │  ├─ router.js
│  │  ├─ api/client.js       # axios
│  │  ├─ stores/grading.js   # pinia
│  │  ├─ views/UploadView.vue, ReviewView.vue
│  │  └─ components/AnnotationCanvas.vue
│  └─ package.json
└─ docs/
```

## 5. API

### POST `/api/grade`
- multipart：`student`（图/PDF）, `answer`（图/PDF）
- 返回 JSON：
```json
{
  "pages": [
    {
      "page": 1,
      "image_url": "/api/images/<job>/student_p1.png",
      "width": 2480, "height": 3508,
      "questions": [
        {"qid":"1","type":"choice","status":"correct","box":[x,y,w,h],
         "student_answer":"B","correct_answer":"B","analysis":""},
        {"qid":"5","type":"essay","status":"incorrect","score":6,"max_score":10,
         "box":[x,y,w,h],"student_answer":"...","correct_answer":"...","analysis":"要点缺失..."}
      ]
    }
  ]
}
```
- 坐标 `box` 为原图像素坐标系 `[x, y, w, h]`，前端按显示缩放换算。

### GET `/api/images/{job}/{file}`
- 返回生成的图片（学生原图分页）。

## 6. 数据模型（Pydantic 摘要）

```python
class TextBox(BaseModel):
    text: str
    box: list[float]  # [x,y,w,h]

class AnswerItem(BaseModel):
    qid: str
    type: Literal["choice","fill","judge","essay"]
    correct_answer: str
    max_score: int = 1
    rubric: list[str] = []          # 主观题得分点

class QuestionResult(BaseModel):
    qid: str
    type: str
    status: Literal["correct","incorrect","partial","unmatched"]
    score: float | None = None
    max_score: float | None = None
    box: list[float]                 # 该题在原图的 bbox
    student_answer: str
    correct_answer: str
    analysis: str = ""
```

## 7. 关键算法

### 7.1 题号配对（matcher）
- OCR 出来的文本块按从上到下、从左到右排序。
- 正则识别题号 `^\s*(\d+)\s*[.、．)]` 作为分隔点；该题题干+作答文本 = 该题号块到下一题号块之间的所有文本。
- 题的 `box` = 合并这些文本块的 bbox（取并集）。
- 按题号与学生答案表 join。

### 7.2 客观题比对
- 选择题：抽取学生答案里的字母（A/B/C/D），与正确答案字母集合比对。
- 判断题：识别 对/错、T/F、√/×、正确/错误 → 归一化。
- 填空题：去空格/标点、全角→半角、大小写归一化后字符串相等比对；可配置模糊容差。

### 7.3 主观题判分（DeepSeek）
- Prompt 注入：题干、学生答案、正确答案、Rubric 各得分点、满分。
- 要求模型返回 JSON `{score, analysis, hit_points:[...]}`。
- 后端兜底：解析失败则重试 1 次，再失败标记 `status="partial"` 不给分。

### 7.4 防碰撞（前端）
- 批注引线/√× 默认落在题 box 右上；右侧批注栏按 Y 轴堆叠，相邻过近时自动下移。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| PDF 转图失败（缺 poppler） | 启动时探测；缺失则只接受图片上传，前端提示 |
| OCR 无文本 | 该页标记为"识别为空"，仍返回原图 |
| 答案结构化失败 | 400，提示用户检查答案图清晰度 |
| DeepSeek 调用失败/超时 | 重试 1 次后该主观题标 partial，不中断整体 |
| 坐标越界 | 前端 clamp 到画布内 |

## 9. 测试策略

- **单元**：matcher 题号切分、客观题归一化比对（多个用例：大小写/全半角/多选）。
- **服务**：DeepSeek 客户端用 mock（录制的 JSON 响应），避免真实调用。
- **集成**：一张样例学生图 + 样例答案图走全流程，断言返回结构。
- **前端**：AnnotationCanvas 给定 JSON，断言绘制了正确数量的 √/×。

## 10. MVP 边界（YAGNI）

不做：多用户账号体系、班级/统计、批量导入、答案手动编辑界面、纠偏透视矫正（仅做基本灰度+对比度）、Docker（提供本地运行脚本即可）。

## 11. 运行方式

- 后端：`pip install -r backend/requirements.txt` → 配 `.env`(DEEPSEEK_API_KEY, ACCESS_TOKEN) → `uvicorn app.main:app --reload`
- 前端：`cd frontend && npm install && npm run dev`（Vite 代理 `/api` 到后端）
