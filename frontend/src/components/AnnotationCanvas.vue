<template>
  <div class="annotation-wrap" ref="wrapRef" :class="{ placing: placementMode }">
    <canvas ref="canvasRef"></canvas>
    <div v-if="placementMode" class="placing-hint">点击试卷上该题的位置放置标记</div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Canvas, Path, Text, Group, FabricImage, Shadow } from 'fabric'

const props = defineProps({
  page: { type: Object, required: true },
  selectedQid: { type: String, default: '' },
  placementMode: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'place'])

const wrapRef = ref(null)
const canvasRef = ref(null)
let canvas = null
let scale = 1

function markColor(q) {
  if (q.status === 'correct') return '#ff4d4f'
  if (q.status === 'incorrect') return '#ff4d4f'
  if (q.status === 'partial') return '#ff4d4f'
  return '#ff4d4f'
}

function checkPath(x, y) {
  return [
    `M ${x},${y + 5}`,
    `L ${x + 5},${y + 11}`,
    `L ${x + 15},${y - 1}`,
  ].join(' ')
}

function crossPath(x, y) {
  return [
    `M ${x},${y}`,
    `L ${x + 13},${y + 13}`,
    `M ${x + 13},${y}`,
    `L ${x},${y + 13}`,
  ].join(' ')
}

function boxValid(b) {
  return Array.isArray(b) && b.length === 4 && (b[2] > 0 || b[3] > 0)
}

async function render() {
  if (!wrapRef.value || !canvasRef.value) return
  const natW = props.page.width
  const natH = props.page.height
  const avail = Math.max(300, wrapRef.value.clientWidth)
  const displayW = Math.min(avail, natW)
  scale = displayW / natW
  const displayH = natH * scale

  if (canvas) canvas.dispose()
  canvas = new Canvas(canvasRef.value, {
    width: displayW,
    height: displayH,
    selection: false,
  })

  // 背景图
  try {
    const img = await FabricImage.fromURL(props.page.image_url, { crossOrigin: 'anonymous' })
    img.scaleToWidth(displayW)
    canvas.backgroundImage = img
    canvas.renderAll()
  } catch (e) {
    console.warn('背景图加载失败', e)
  }

  // 逐题绘制标记
  for (const q of props.page.questions) {
    if (!boxValid(q.box)) continue
    const [bx, by] = q.box
    // 标记位置：括号和题号之间
    const mx = bx * scale + 2
    const my = by * scale + 2
    const color = markColor(q)

    if (q.type === 'essay') {
      const scoreText = q.status === 'correct' ? '满分' : `${q.score ?? '?'}`
      const txt = new Text(scoreText, {
        left: 0, top: 0,
        fontSize: 18, fontFamily: 'sans-serif',
        fill: color, fontWeight: 'bold',
      })
      const group = new Group([txt], {
        left: mx, top: my, originX: 'left', originY: 'top',
        selectable: true, hasControls: false, hasBorders: false,
        hoverCursor: 'move',
      })
      group.qid = q.qid
      group.on('mousedown', () => emit('select', q.qid))
      canvas.add(group)
      continue
    }

    // ✓/× 标记
    const pathD = q.status === 'correct' ? checkPath(0, 0) : crossPath(0, 0)
    const mark = new Path(pathD, {
      stroke: color, strokeWidth: 3.5, fill: 'transparent',
      strokeLineCap: 'round', strokeLineJoin: 'round',
      originX: 'left', originY: 'top',
    })

    const children = [mark]

    // 错题：合成 Group（× + 正确答案 + 解析，整体拖动）
    if (q.status === 'incorrect' && q.correct_answer) {
      const ansText = new Text(q.correct_answer, {
        left: 18, top: -3,
        fontSize: 16, fontFamily: 'sans-serif',
        fill: '#ff4d4f', fontWeight: 'bold',
        originX: 'left', originY: 'top',
      })
      children.push(ansText)

      if (q.analysis) {
        // 自动换行：每行最多 35 个字符（减少行数）
        const maxLen = 35
        const lines = []
        let remaining = q.analysis
        while (remaining.length > maxLen) {
          lines.push(remaining.slice(0, maxLen))
          remaining = remaining.slice(maxLen)
        }
        lines.push(remaining)
        const wrapped = lines.join('\n')
        const hintText = new Text(wrapped, {
          left: 18, top: 15,
          fontSize: 11, fontFamily: 'sans-serif',
          fill: '#262626',
          lineHeight: 1.2,
          originX: 'left', originY: 'top',
        })
        children.push(hintText)
      }
    }

    const group = new Group(children, {
      left: mx, top: my, originX: 'left', originY: 'top',
      selectable: true, hasControls: false, hasBorders: false,
      hoverCursor: 'move',
    })
    group.qid = q.qid
    group.on('mousedown', () => emit('select', q.qid))
    canvas.add(group)
  }

  // 放置模式：点击画布 → 发送原图坐标
  if (props.placementMode) {
    canvas.on('mouse:down', (opt) => {
      if (opt.target) return // 点击已有标记，不处理
      const pointer = canvas.getScenePoint(opt.e)
      const origX = pointer.x / scale
      const origY = pointer.y / scale
      emit('place', { x: origX, y: origY })
    })
  }

  canvas.renderAll()
  applySelection()
  if (import.meta.env && import.meta.env.DEV) {
    window.__lastFabric = canvas
  }
}

function applySelection() {
  if (!canvas) return
  canvas.getObjects().forEach((o) => {
    if (o.qid === props.selectedQid) {
      o.set({ shadow: new Shadow({ color: 'rgba(255,0,0,0.4)', blur: 8 }) })
    } else {
      o.set({ shadow: null })
    }
  })
  canvas.renderAll()
}

function onResize() { render() }

defineExpose({
  exportPng() {
    if (!canvas) return null
    return canvas.toDataURL({ format: 'png', multiplier: 2 })
  },
  rerender: render,
})

onMounted(async () => {
  await nextTick()
  render()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (canvas) canvas.dispose()
})
watch(() => props.page, render, { deep: false })
watch(() => props.selectedQid, applySelection)
watch(() => props.placementMode, () => { render() })
</script>

<style scoped>
.annotation-wrap {
  width: 100%;
  overflow: auto;
  background: #fafafa;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  position: relative;
}
.annotation-wrap.placing {
  border: 2px dashed #1677ff;
  cursor: crosshair;
}
.placing-hint {
  position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
  background: #1677ff; color: #fff; padding: 4px 12px; border-radius: 4px;
  font-size: 12px; pointer-events: none; z-index: 10;
}
</style>
