<template>
  <div class="review-page" v-if="result">
    <a-page-header
      title="批改结果"
      :sub-title="`作业号 ${result.job}`"
      @back="() => router.push('/')"
    >
      <template #extra>
        <a-space>
          <a-tag color="green">正确 {{ stats.correct }}</a-tag>
          <a-tag color="red">错误 {{ stats.incorrect }}</a-tag>
          <a-tag color="orange">部分 {{ stats.partial }}</a-tag>
          <a-tag>未匹配 {{ stats.unmatched }}</a-tag>
          <a-button type="primary" @click="exportPng">导出批改图</a-button>
          <a-button :loading="analyzingQuestions" @click="generateQuestionAnalysis">
            {{ analyzingQuestions ? '解析生成中...' : '生成详细解析' }}
          </a-button>
        </a-space>
      </template>
    </a-page-header>

    <a-tabs v-if="result.pages.length > 1" v-model:activeKey="pageKey">
      <a-tab-pane v-for="(p, i) in result.pages" :key="String(i)" :tab="`第 ${i + 1} 页`" />
    </a-tabs>

    <!-- 点评摘要 -->
    <a-card v-if="result.summary" size="small" class="summary-card">
      <template #title>
        <span>试卷点评</span>
        <a-button size="small" type="link" @click="copySummary" style="float:right">
          {{ copied ? '已复制' : '复制全文' }}
        </a-button>
      </template>
      <pre class="summary-text">{{ result.summary }}</pre>
      <!-- 深度分析 -->
      <a-divider v-if="analyzing || llmAnalysis" />
      <div v-if="analyzing" class="analyzing">
        <a-spin size="small" /> AI 深度分析生成中...
      </div>
      <pre v-if="llmAnalysis" class="summary-text llm">{{ llmAnalysis }}</pre>
    </a-card>

    <a-row :gutter="16">
      <a-col :span="16">
        <AnnotationCanvas
          :key="renderKey"
          ref="canvasRef"
          :page="currentPage"
          :selected-qid="selectedQid"
          :placement-mode="placementMode"
          @select="(qid) => (selectedQid = qid)"
          @place="onPlace"
          @edit-analysis="onEditAnalysis"
        />
      </a-col>
      <a-col :span="8">
        <!-- 编辑面板：选中题目时显示 -->
        <a-card v-if="editingQ" :title="`编辑 · 第 ${editingQ.qid} 题`" class="edit-panel">
          <a-form layout="vertical" size="small">
            <a-form-item label="学生答案">
              <a-input v-model:value="editAnswer" placeholder="输入学生作答" />
            </a-form-item>
            <a-form-item label="正确答案">
              <a-tag>{{ editingQ.correct_answer || '—' }}</a-tag>
              <a-tag>{{ editingQ.type }}</a-tag>
              <a-tag v-if="editingQ.max_score">满分 {{ editingQ.max_score }}</a-tag>
            </a-form-item>
            <a-form-item label="批改状态">
              <a-radio-group v-model:value="editStatus" button-style="solid" size="small">
                <a-radio-button value="correct" :style="{ color: editStatus === 'correct' ? '#fff' : '#52c41a' }">✓ 正确</a-radio-button>
                <a-radio-button value="incorrect" :style="{ color: editStatus === 'incorrect' ? '#fff' : '#ff4d4f' }">× 错误</a-radio-button>
                <a-radio-button value="partial">部分</a-radio-button>
                <a-radio-button value="unmatched">未匹配</a-radio-button>
              </a-radio-group>
            </a-form-item>
            <a-form-item label="错题解析">
              <a-textarea
                v-model:value="editAnalysis"
                :rows="5"
                placeholder="输入或修改错题解析"
                @click.stop
                @mousedown.stop
                @input="editAnalysis = $event.target.value"
                style="font-size: 14px; border: 2px solid #1677ff; border-radius: 4px; cursor: text;"
              />
            </a-form-item>
            <a-space>
              <a-button type="primary" size="small" @click="saveEdit">保存修改</a-button>
              <a-button v-if="editStatus === 'unmatched'" size="small" @click="enterPlacement" type="dashed">
                点击试卷设置位置
              </a-button>
              <a-button size="small" @click="selectedQid = ''; placementMode = false">取消</a-button>
            </a-space>
          </a-form>
        </a-card>

        <a-card title="题目解析" class="panel">
          <a-empty v-if="!currentPage.questions.length" description="未识别到题目" />
          <div v-else>
            <a-list size="small" :data-source="currentPage.questions" :split="true">
              <template #renderItem="{ item }">
                <a-list-item
                  :class="{ active: item.qid === selectedQid }"
                  @click="selectedQid = item.qid"
                  style="cursor: pointer"
                >
                  <a-list-item-meta>
                    <template #title>
                      <a-space>
                        <span>第 {{ item.qid }} 题</span>
                        <a-tag :color="tagColor(item)">{{ statusText(item) }}</a-tag>
                        <a-tag v-if="item.type === 'essay'" color="purple">
                          {{ item.score ?? 0 }}/{{ item.max_score }}
                        </a-tag>
                      </a-space>
                    </template>
                    <template #description>
                      <div><b>学生答案：</b>{{ item.student_answer || '（空）' }}</div>
                      <div><b>正确答案：</b>{{ item.correct_answer || '—' }}</div>
                      <div v-if="item.analysis" class="analysis">{{ item.analysis }}</div>
                    </template>
                  </a-list-item-meta>
                </a-list-item>
              </template>
            </a-list>
          </div>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useGradingStore } from '../stores/grading'
import { analyze, analyzeQuestions } from '../api/client'
import AnnotationCanvas from '../components/AnnotationCanvas.vue'
import { message } from 'ant-design-vue'

const router = useRouter()
const store = useGradingStore()
const result = computed(() => store.result)

const pageKey = ref('0')
const selectedQid = ref('')
const canvasRef = ref(null)
const copied = ref(false)
const llmAnalysis = ref('')
const analyzing = ref(false)
const placementMode = ref(false)
const analyzingQuestions = ref(false)
const renderKey = ref(0)

// 编辑状态
const editAnswer = ref('')
const editStatus = ref('correct')
const editAnalysis = ref('')

const currentPage = computed(() => result.value.pages[Number(pageKey.value)] || result.value.pages[0])

const editingQ = computed(() => {
  if (!selectedQid.value) return null
  return currentPage.value.questions.find((q) => q.qid === selectedQid.value) || null
})

// 选中题目时同步编辑面板
watch(selectedQid, (qid) => {
  const q = currentPage.value.questions.find((q) => q.qid === qid)
  if (q) {
    editAnswer.value = q.student_answer || ''
    editStatus.value = q.status || 'correct'
    editAnalysis.value = q.analysis || ''
  }
})

const stats = computed(() => {
  const s = { correct: 0, incorrect: 0, partial: 0, unmatched: 0 }
  for (const p of result.value.pages) {
    for (const q of p.questions) {
      s[q.status] = (s[q.status] || 0) + 1
    }
  }
  return s
})

function tagColor(q) {
  return { correct: 'green', incorrect: 'red', partial: 'orange', unmatched: 'default' }[q.status]
}
function statusText(q) {
  return { correct: '正确', incorrect: '错误', partial: '部分得分', unmatched: '未匹配' }[q.status]
}

function saveEdit() {
  const pi = Number(pageKey.value)
  const q = editingQ.value
  const hasBox = q && q.box && q.box[2] > 0 && q.box[3] > 0
  const wasUnmatched = q && q.status === 'unmatched'
  const nowGraded = editStatus.value !== 'unmatched'

  // 未匹配题改状态但没坐标 → 进入放置模式
  if (wasUnmatched && nowGraded && !hasBox) {
    store.updateQuestion(pi, selectedQid.value, {
      student_answer: editAnswer.value,
      status: editStatus.value,
      analysis: editAnalysis.value,
      score: editStatus.value === 'correct' ? editingQ.value?.max_score : editStatus.value === 'incorrect' ? 0 : editingQ.value?.score,
    })
    placementMode.value = true
    message.info('请点击试卷上该题的位置放置标记')
    return
  }

  store.updateQuestion(pi, selectedQid.value, {
    student_answer: editAnswer.value,
    status: editStatus.value,
    analysis: editAnalysis.value,
    score: editStatus.value === 'correct' ? editingQ.value?.max_score : editStatus.value === 'incorrect' ? 0 : editingQ.value?.score,
  })
  canvasRef.value?.rerender()
  message.success('已保存')
}

function exportPng() {
  const comp = canvasRef.value
  if (!comp) return
  const url = comp.exportPng()
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `graded_${result.value.job}_p${Number(pageKey.value) + 1}.png`
  a.click()
}

async function copySummary() {
  try {
    await navigator.clipboard.writeText(result.value.summary + (llmAnalysis.value ? '\n\n' + llmAnalysis.value : ''))
    copied.value = true
    message.success('点评已复制到剪贴板')
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    message.error('复制失败，请手动选中复制')
  }
}

function enterPlacement() {
  placementMode.value = true
  message.info('请点击试卷上该题的位置')
}

function onPlace(pos) {
  if (!editingQ.value) return
  const pi = Number(pageKey.value)
  store.updateQuestion(pi, selectedQid.value, {
    box: [pos.x - 10, pos.y - 10, 30, 30],
    status: editStatus.value === 'unmatched' ? 'correct' : editStatus.value,
  })
  placementMode.value = false
  canvasRef.value?.rerender()
  message.success('标记已放置')
}

// 画布上直接编辑解析
function onEditAnalysis(qid, newText) {
  const pi = Number(pageKey.value)
  store.updateQuestion(pi, qid, { analysis: newText })
}

async function generateQuestionAnalysis() {
  if (!result.value?.job) return
  analyzingQuestions.value = true
  try {
    const data = await analyzeQuestions(result.value.job)
    console.log('解析返回:', data)
    const analyses = data.analyses || {}
    // 更新每题的 analysis 字段
    for (const page of result.value.pages) {
      for (const q of page.questions) {
        if (analyses[q.qid]) {
          q.analysis = analyses[q.qid]
        }
      }
    }
    await nextTick()
    renderKey.value++
    await nextTick()
    message.success('详细解析已生成')
  } catch {
    message.error('解析生成失败，请稍后重试')
  } finally {
    analyzingQuestions.value = false
  }
}

// 批改完成后自动请求深度分析
watch(() => result.value?.job, async (job) => {
  if (!job) return
  analyzing.value = true
  llmAnalysis.value = ''
  try {
    const data = await analyze(job)
    llmAnalysis.value = data.analysis || ''
  } catch {
    llmAnalysis.value = '深度分析生成失败，请稍后重试。'
  } finally {
    analyzing.value = false
  }
}, { immediate: true })
</script>

<style scoped>
.review-page { background: #fff; border-radius: 8px; }
.panel { max-height: 65vh; overflow: auto; }
.edit-panel { margin-bottom: 12px; border: 1px solid #91caff; background: #e6f4ff; }
:deep(.edit-panel .ant-card-body) { max-height: 50vh; overflow: auto; }
:deep(.edit-panel textarea) { border: 2px solid #1677ff !important; font-size: 14px !important; z-index: 10; position: relative; pointer-events: auto !important; }
.summary-card { margin-bottom: 16px; background: #f6ffed; border: 1px solid #b7eb8f; }
.summary-text {
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 13px; line-height: 1.8; white-space: pre-wrap;
  margin: 0; background: transparent;
}
.summary-text.llm { color: #1677ff; }
.analyzing { color: #8c8c8c; font-size: 13px; padding: 8px 0; }
.analysis { color: #595959; margin-top: 4px; white-space: pre-wrap; }
:deep(.ant-list-item.active) { background: #e6f4ff; }
</style>
