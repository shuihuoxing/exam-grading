# 自动阅卷系统

上传学生答卷（图片/PDF）+ 标准答案（图片/PDF），系统自动批改：
- 客观题（选择/填空/判断）：代码规范化比对，打 √ / ×。
- 主观题（简答）：DeepSeek 按评分细则（Rubric）打分并生成点评。
- 在学生原图对应题目坐标画红 √ / ×，右侧分栏显示正确答案与解析，可点击联动、导出批改图。

技术栈：FastAPI + **MiMo-VLM 多模态(看图读答案)** + PaddleOCR(定位坐标) + DeepSeek(主观题判分)（后端）；Vue 3 + Vite + Ant Design Vue + Fabric.js（前端）。

详细设计见 [docs/superpowers/specs/2026-06-18-exam-grading-system-design.md](docs/superpowers/specs/2026-06-18-exam-grading-system-design.md)。

---

## 架构与数据流

```
学生答卷(图/PDF) ─┐                          ┌→ 客观题: 代码规范化比对
                 ├→ MiMo-VLM 看图读作答 ──→ 按题号配对 ─┤
答案(图/PDF) ────┘  → MiMo-VLM 看图读标准答案+Rubric  └→ 主观题: DeepSeek 按 Rubric 打分+点评
        + PaddleOCR 给每题坐标(画 √/× 用)                 ↓
                            每题 {qid,type,status/score,box,student_answer,correct_answer,analysis}
                                                          ↓
                                  前端 Canvas 在原图坐标画 √/× + 右侧批注联动
```

> **混合架构(Path A+B)**：MiMo-VLM 负责"读懂答案内容"(学生作答 + 标准答案都直接看图，
> 比纯 OCR 准、覆盖全)，PaddleOCR 负责"定位"(给每题 bbox 用于画 √/×)。
> 实测同一份卷子：纯 PaddleOCR 路径判分 6/17，接入 VLM 后 17/17 全判。

---

## 环境要求

- Python 3.10+（已在 3.12 验证）
- Node.js 18+（已在 24 验证）
- **PaddleOCR 依赖**：`paddlepaddle` + `paddleocr`（首次运行会下载模型，体积较大）
- **PDF 支持（可选）**：[poppler](https://github.com/oschwartz10612/poppler-windows/releases) 的 `bin` 需加入 PATH。不装也能用——只是不能传 PDF，可传图片。
- **DeepSeek API Key**：在 [platform.deepseek.com](https://platform.deepseek.com/) 申请。

---

## 后端启动

```bash
cd backend

# 1) 安装依赖（轻量）
pip install -r requirements.txt

# 2) 配置环境变量
cp .env.example .env
#   编辑 .env，填入 DEEPSEEK_API_KEY（主观题判分）+ MIMO_API_KEY（看图读答案，必需）

# 3) 启动
uvicorn app.main:app --reload --port 8000
```

启动后访问 `http://localhost:8000/docs` 查看接口。

> 说明：`paddlepaddle` / `paddleocr` 体积大，若 `pip install -r requirements.txt` 安装它们失败，
> 可单独执行 `pip install paddlepaddle paddleocr`。OCR/LLM 均为懒加载，未装也能启动服务，
> 只在真正批改时才需要。

### 单元测试（纯逻辑，无需 OCR/LLM）

```bash
cd backend
python tests/test_grading_logic.py
```

---

## 前端启动

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

前端开发服务器已配置代理：`/api` → `http://localhost:8000`，所以前后端同时跑即可联调。

生产构建：`npm run build`，产物在 `frontend/dist/`。

---

## 使用流程

1. 启动后端与前端。
2. 打开 `http://localhost:5173`。
3. 上传 **① 学生答卷** 和 **② 标准答案**（图片或 PDF）。
4. 若后端 `.env` 设置了非默认的 `ACCESS_TOKEN`，在上传页填入对应令牌。
5. 点击「开始批改」，等待 OCR + DeepSeek 处理。
6. 在结果页查看：左侧原图上的 √/× 标记（可点击），右侧题目解析；可导出带批注的 PNG。

---

## 目录结构

```
试卷批阅/
├─ backend/
│  ├─ app/
│  │  ├─ main.py              # FastAPI 入口
│  │  ├─ config.py            # 配置（.env）
│  │  ├─ models.py            # Pydantic 数据模型
│  │  ├─ api/routes.py        # /api/grade, /api/images, /api/health
│  │  └─ services/
│  │     ├─ pdf.py            # PDF/图片 → PNG
│  │     ├─ preprocess.py     # OpenCV 增强
│  │     ├─ ocr.py            # PaddleOCR 封装（定位坐标）
│  │     ├─ vlm.py            # MiMo 多模态客户端（看图读答案）
│  │     ├─ llm.py            # DeepSeek 客户端（主观题判分）
│  │     ├─ matcher.py        # 按题号切分 + bbox 并集
│  │     ├─ grader.py         # 客观题比对 + 主观题判分
│  │     └─ pipeline.py       # 端到端流水线
│  ├─ tests/test_grading_logic.py
│  ├─ requirements.txt
│  └─ .env.example
├─ frontend/
│  └─ src/
│     ├─ views/UploadView.vue, ReviewView.vue
│     ├─ components/AnnotationCanvas.vue   # Fabric.js 画 √/×
│     ├─ stores/grading.js
│     └─ api/client.js
└─ docs/superpowers/specs/                  # 设计文档
```

---

## MVP 边界（YAGNI）

当前版本**不做**：多用户账号体系、班级/统计、批量导入、答案手动编辑、透视纠偏、Docker。
这些在后续迭代再加。

## 已知限制

- 题号配对依赖卷面有清晰的「数字+分隔符」题号格式；手写无题号时可能匹配不到（标记为 unmatched）。
- 主观题判分质量取决于 DeepSeek 与 Rubric 的完整度；判分服务不可用时会标记为「部分得分」需人工复核。
- 批注标记位置取题目 bbox 的右上角；题目靠得太近时可能重叠（防碰撞为后续优化项）。

---

## 在线部署

### 方案一：免费部署（Vercel + Railway）

**原理**：前端部署到 Vercel（静态站点，永久免费），后端部署到 Railway（每月 $5 免费额度）。使用轻量版后端（无 PaddleOCR，MiMo-VLM 同时负责读答案+定位）。

#### 前端（Vercel）

1. 将项目推送到 GitHub
2. 登录 [vercel.com](https://vercel.com)，Import 该 GitHub 仓库
3. 设置：
   - Root Directory: `frontend`
   - Build Command: `npm run build`
   - Output Directory: `dist`
4. 部署后获得域名（如 `exam-grading.vercel.app`）

#### 后端（Railway）

1. 登录 [railway.app](https://railway.app)，新建项目
2. 选择 Deploy from GitHub Repo，选中仓库
3. 设置 Root Directory: `backend`
4. 添加环境变量：
   ```
   DEEPSEEK_API_KEY=sk-xxx
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   DEEPSEEK_MODEL=deepseek-v4-flash
   MIMO_API_KEY=sk-xxx
   MIMO_BASE_URL=https://api.xiaomimimo.com/v1
   MIMO_MODEL=mimo-v2.5
   ```
5. Railway 会自动检测 `Procfile` 并启动

#### 联通前端和后端

修改 `frontend/vercel.json`，将 `your-railway-app` 替换为你的 Railway 实际域名。

### 方案二：Docker 一键部署（推荐正式环境）

```bash
# 1. 配置密钥
cp backend/.env.example backend/.env
nano backend/.env  # 填入 API Key

# 2. 一键启动
docker compose up -d --build

# 访问 http://你的服务器IP
```

首次构建约 5-10 分钟（下载 PaddleOCR 依赖），后续启动 ~30 秒。

### 方案三：手动部署（无 Docker）

```bash
# 后端
cd backend
pip install -r requirements.txt  # 完整版（含 PaddleOCR）
# 或 pip install -r requirements-lite.txt  # 轻量版
cp .env.example .env && nano .env
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（另开终端）
cd frontend
npm install && npm run build
# 将 dist/ 目录部署到 Nginx
```

### 轻量版 vs 完整版

| | 轻量版（免费部署） | 完整版（Docker） |
|---|---|---|
| 依赖 | FastAPI + OpenAI SDK | + PaddleOCR + paddlepaddle + OpenCV |
| 后端大小 | ~100MB | ~1.5GB |
| 定位方式 | MiMo-VLM 返回近似坐标 | PaddleOCR 精确 bbox |
| 定位精度 | 可拖动调整 | 精确（可拖动微调） |
| 部署平台 | Vercel + Railway（免费） | 阿里云 ECS（~¥50/月） |
