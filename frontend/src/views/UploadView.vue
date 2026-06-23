<template>
  <div class="upload-page">
    <a-card title="上传试卷批改">
      <a-alert
        type="info"
        show-icon
        message="上传两张图/PDF：① 学生答卷  ② 标准答案（含主观题评分细则）。系统将 OCR 后自动批改并在原图上画 √/× 与解析。"
        style="margin-bottom: 16px"
      />

      <a-form layout="vertical">
        <a-form-item label="访问令牌（若后端已设置）">
          <a-input-password
            v-model:value="token"
            placeholder="留空则不发送（后端默认占位时不鉴权）"
            allow-clear
          />
        </a-form-item>

        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="① 学生答卷（图片或 PDF）" required>
              <a-upload-dragger
                :before-upload="(f) => pick(f, 'student')"
                :max-count="1"
                :file-list="studentList"
                accept="image/*,application/pdf"
                @remove="() => clear('student')"
              >
                <p class="ant-upload-drag-icon"><InboxOutlined /></p>
                <p class="ant-upload-text">点击或拖拽学生答卷到此处</p>
              </a-upload-dragger>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="② 标准答案（图片或 PDF）" required>
              <a-upload-dragger
                :before-upload="(f) => pick(f, 'answer')"
                :max-count="1"
                :file-list="answerList"
                accept="image/*,application/pdf"
                @remove="() => clear('answer')"
              >
                <p class="ant-upload-drag-icon"><InboxOutlined /></p>
                <p class="ant-upload-text">点击或拖拽标准答案到此处</p>
              </a-upload-dragger>
            </a-form-item>
          </a-col>
        </a-row>

        <a-space>
          <a-button
            type="primary"
            size="large"
            :loading="store.loading"
            :disabled="!studentFile || !answerFile"
            @click="onSubmit"
          >
            开始批改
          </a-button>
          <span v-if="store.error" style="color: #ff4d4f">{{ store.error }}</span>
        </a-space>
      </a-form>
    </a-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { InboxOutlined } from '@ant-design/icons-vue'
import { useGradingStore } from '../stores/grading'
import { setAccessToken } from '../api/client'

const router = useRouter()
const store = useGradingStore()

const token = ref(localStorage.getItem('access_token') || '')
const studentFile = ref(null)
const answerFile = ref(null)
const studentList = ref([])
const answerList = ref([])

function pick(file, which) {
  if (which === 'student') {
    studentFile.value = file
    studentList.value = [{ uid: '1', name: file.name, status: 'done' }]
  } else {
    answerFile.value = file
    answerList.value = [{ uid: '1', name: file.name, status: 'done' }]
  }
  return false // 阻止自动上传
}
function clear(which) {
  if (which === 'student') {
    studentFile.value = null
    studentList.value = []
  } else {
    answerFile.value = null
    answerList.value = []
  }
}

async function onSubmit() {
  setAccessToken(token.value.trim())
  try {
    await store.submit(studentFile.value, answerFile.value)
    router.push('/review')
  } catch {
    // store.error 已展示
  }
}
</script>

<style scoped>
.upload-page { max-width: 900px; margin: 0 auto; }
</style>
