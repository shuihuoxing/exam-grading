// 浏览器端到端验证：上传样例图 → 批改 → 截图 + 读取 Fabric 标记
import { chromium } from 'playwright'
import { fileURLToPath } from 'url'
import path from 'path'

const ROOT = path.resolve(fileURLToPath(import.meta.url), '..', '..')
const STUDENT = path.join(ROOT, '_samples', 'student.png')
const ANSWER = path.join(ROOT, '_samples', 'answer.png')
const SHOT = path.join(ROOT, '_samples', 'review_screenshot.png')

const consoleErrors = []
const pageErrors = []

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
page.on('pageerror', (e) => pageErrors.push(String(e)))

await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' })

// 上传两个文件（Antd Dragger 的隐藏 input）
const inputs = await page.locator('input[type=file]').all()
console.log('file inputs found:', inputs.length)
await inputs[0].setInputFiles(STUDENT)
await inputs[1].setInputFiles(ANSWER)

// 点击“开始批改”
await page.getByRole('button', { name: '开始批改' }).click()

// 等待进入结果页并出现 canvas
await page.waitForURL('**/review', { timeout: 150000 })
await page.waitForSelector('canvas', { timeout: 30000 })

// 轮询等待 Fabric 标记渲染完成
let markers = null
for (let i = 0; i < 60; i++) {
  markers = await page.evaluate(() => {
    const c = window.__lastFabric
    if (!c) return null
    const objs = c.getObjects()
    if (!objs.length) return null
    return objs.map((o) => ({
      qid: o.qid,
      fill: o.item(0) && o.item(0).fill,
      sym: o.item(1) && o.item(1).text,
    }))
  })
  if (markers) break
  await page.waitForTimeout(500)
}

// 读取右侧题目解析面板文本
const panelText = await page.locator('.panel').innerText().catch(() => '')

await page.screenshot({ path: SHOT, fullPage: true })

console.log('--- markers ---')
console.log(JSON.stringify(markers, null, 2))
console.log('--- panel (first 600 chars) ---')
console.log(panelText.slice(0, 600))
console.log('--- console errors ---', consoleErrors.length)
consoleErrors.slice(0, 5).forEach((e) => console.log('  ERR:', e.slice(0, 200)))
console.log('--- page errors ---', pageErrors.length)
pageErrors.slice(0, 5).forEach((e) => console.log('  PAGE:', e.slice(0, 200)))

await browser.close()
console.log('screenshot:', SHOT)
