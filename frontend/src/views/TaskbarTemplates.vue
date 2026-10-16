<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, uploadForm, type Row } from '../api'
import { attempt, confirmModal, notify } from '../ui'

const templates = ref<Row[]>([])
const name = ref('')
const file = ref<File | null>(null)
const picker = ref<HTMLInputElement | null>(null)
const busy = ref(false)

async function load() {
  const response = await attempt(() => api<Row>('/taskbar-templates'))
  if (response) templates.value = response.templates || []
}

function choose(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  if (file.value && !name.value) name.value = file.value.name.replace(/\.[^.]+$/, '')
}

async function upload() {
  if (!file.value || !name.value.trim() || busy.value) return
  busy.value = true
  try {
    const body = new FormData()
    body.append('name', name.value.trim())
    body.append('file', file.value)
    const result = await attempt(() => uploadForm<Row>('/taskbar-templates', body))
    if (result?.ok) {
      notify('底栏已上传')
      name.value = ''
      file.value = null
      if (picker.value) picker.value.value = ''
      await load()
    }
  } finally { busy.value = false }
}

async function remove(template: Row) {
  const confirmed = await confirmModal({
    title: '删除底栏图片', message: `确定删除“${template.name}”吗？`, confirmText: '删除', danger: true,
  })
  if (!confirmed) return
  const id = Number(String(template.key).replace('custom:', ''))
  const result = await attempt(() => api<Row>(`/taskbar-templates/${id}`, 'DELETE'))
  if (result?.ok) { notify('底栏已删除'); await load() }
}

onMounted(load)
</script>

<template>
  <p class="taskbar-intro">内置底栏和上传底栏都由后端提供，可在截图项中选择。</p>
  <section class="form-section"><div class="form-section-head"><div><h2>上传底栏</h2></div></div>
    <form class="form-grid" @submit.prevent="upload">
      <label class="field"><span>底栏名称</span><input v-model="name" required maxlength="100" placeholder="例如：生产环境底栏" /></label>
      <label class="field"><span>图片文件</span><input ref="picker" type="file" accept="image/png,image/jpeg,image/webp" required @change="choose" /></label>
      <div class="field-full"><ActionButton type="submit" variant="primary" :disabled="busy || !file || !name.trim()" :loading="busy">上传底栏</ActionButton></div>
    </form>
    <p class="taskbar-help">支持 PNG、JPEG、WebP，最大 5 MB。请上传宽幅横条图片；右侧时间和日期会由系统在截图时绘制。</p>
  </section>
  <section class="form-section"><div class="form-section-head"><div><h2>可用底栏</h2><p>{{ templates.length }} 张</p></div></div>
    <div class="taskbar-list">
      <div v-for="template in templates" :key="template.key" class="taskbar-row">
        <div class="taskbar-details"><strong>{{ template.name }}</strong><small>{{ template.kind === 'builtin' ? '系统预置' : '用户上传' }}</small><img :src="template.image_url" :alt="template.name" loading="lazy" /></div>
        <button v-if="template.kind === 'custom'" type="button" @click="remove(template)">删除</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.taskbar-help{margin:16px 0 0;color:var(--text-muted,#607080);font-size:13px;line-height:1.7}
.taskbar-intro{margin:0 0 24px;color:var(--text-muted,#607080);line-height:1.6}
.taskbar-list{display:grid;gap:12px}.taskbar-row{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:16px;border:1px solid var(--line,#dce4e8);border-radius:12px}
.taskbar-details{min-width:0;flex:1}.taskbar-details strong,.taskbar-details small{display:block}.taskbar-details small{margin:4px 0 12px;color:var(--text-muted,#607080)}
.taskbar-details img{display:block;width:100%;max-width:850px;height:auto;border:1px solid #dce4e8;background:#eef2f4}
.taskbar-row button{border:0;background:none;color:#b42318;cursor:pointer}
</style>
