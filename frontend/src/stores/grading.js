import { defineStore } from 'pinia'
import { ref } from 'vue'
import { grade } from '../api/client'

export const useGradingStore = defineStore('grading', () => {
  const result = ref(null)        // GradeResponse
  const loading = ref(false)
  const error = ref('')

  async function submit(studentFile, answerFile) {
    loading.value = true
    error.value = ''
    try {
      result.value = await grade(studentFile, answerFile)
      return result.value
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || '批改失败'
      error.value = msg
      throw new Error(msg)
    } finally {
      loading.value = false
    }
  }

  function clear() {
    result.value = null
    error.value = ''
  }

  /** 更新某道题的字段（answer/status/score 等），触发视图刷新 */
  function updateQuestion(pageIndex, qid, fields) {
    if (!result.value) return
    const page = result.value.pages[pageIndex]
    if (!page) return
    const q = page.questions.find((q) => q.qid === qid)
    if (q) Object.assign(q, fields)
  }

  return { result, loading, error, submit, clear, updateQuestion }
})
