import axios from 'axios'

const instance = axios.create({
  baseURL: '/',
  timeout: 120000, // OCR + LLM 可能较慢
})

// 统一带上访问令牌（若设置）
instance.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers['X-Access-Token'] = token
  }
  return config
})

export function setAccessToken(token) {
  if (token) localStorage.setItem('access_token', token)
  else localStorage.removeItem('access_token')
}

export function getAccessToken() {
  return localStorage.getItem('access_token') || ''
}

/** 提交批改：student 与 answer 均为 File */
export async function grade(studentFile, answerFile) {
  const form = new FormData()
  form.append('student', studentFile)
  form.append('answer', answerFile)
  const { data } = await instance.post('/api/grade', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export function imageUrl(path) {
  // path 形如 /api/images/...，开发期走 vite 代理
  const url = path
  return url
}

/** 请求深度错题分析（异步，非阻塞） */
export async function analyze(job) {
  const { data } = await instance.post(`/api/analyze/${job}`)
  return data
}

/** 为错题生成学科级详细解析 */
export async function analyzeQuestions(job) {
  const { data } = await instance.post(`/api/analyze-questions/${job}`)
  return data
}
