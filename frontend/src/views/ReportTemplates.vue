<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, apiUrl, uploadForm, type Row } from '../api'
import { attempt, confirmModal, notify } from '../ui'

const templates = ref<Row[]>([])
const name = ref('')
const file = ref<File | null>(null)
const picker = ref<HTMLInputElement | null>(null)
const busy = ref(false)

async function load() {
  const response = await attempt(() => api<Row>('/report-templates'))
  if (response) templates.value = response.templates || []
}

function choose(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  if (file.value && !name.value) name.value = file.value.name.replace(/\.docx$/i, '')
}

async function upload() {
  if (!file.value || !name.value.trim() || busy.value) return
  busy.value = true
  try {
    const body = new FormData()
    body.append('name', name.value.trim())
    body.append('file', file.value)
    const result = await attempt(() => uploadForm<Row>('/report-templates', body))
    if (result?.ok) {
      notify('模板已上传')
      name.value = ''
      file.value = null
      if (picker.value) picker.value.value = ''
      await load()
    }
  } finally { busy.value = false }
}

async function remove(template: Row) {
  const confirmed = await confirmModal({
    title: '删除报告模板', message: `确定删除“${template.name}”吗？`, confirmText: '删除', danger: true,
  })
  if (!confirmed) return
  const result = await attempt(() => api<Row>(`/report-templates/${template.id}`, 'DELETE'))
  if (result?.ok) { notify('模板已删除'); await load() }
}

onMounted(load)
</script>

<template>
  <p class="template-intro">上传 DOCX 后，可在巡检任务中选择使用。未选择模板的任务自动排版。</p>
  <section class="form-section"><div class="form-section-head"><div><h2>上传模板</h2></div></div>
    <form class="form-grid" @submit.prevent="upload">
      <label class="field"><span>模板名称</span><input v-model="name" required maxlength="100" placeholder="例如：生产环境日报" /></label>
      <label class="field"><span>DOCX 文件</span><input ref="picker" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required @change="choose" /></label>
      <div class="field-full"><ActionButton type="submit" variant="primary" :disabled="busy || !file || !name.trim()" :loading="busy">上传模板</ActionButton></div>
    </form>
    <p class="template-help">文件最大 10 MB。正文需有日期占位符 yyyy年mm月dd日 或 yyyy-mm-dd，并包含“巡检时间：上午”“巡检时间：下午”及两边相同的数字开头章节。截图会插入对应章节。</p>
  </section>
  <section class="form-section"><div class="form-section-head"><div><h2>已上传模板</h2><p>{{ templates.length }} 份</p></div></div>
    <div v-if="!templates.length" class="simple-empty">暂无模板。任务仍可使用自动排版。</div>
    <div v-else class="template-list">
      <div v-for="template in templates" :key="template.id" class="template-row">
        <div><strong>{{ template.name }}</strong><small>{{ template.original_name }} · 章节：{{ (template.sections || []).join('、') }}</small></div>
        <div class="template-actions"><a :href="apiUrl(`/report-templates/${template.id}/download`)">下载</a><button type="button" @click="remove(template)">删除</button></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.template-help{margin:16px 0 0;color:var(--text-muted,#607080);font-size:13px;line-height:1.7}
.template-intro{margin:0 0 24px;color:var(--text-muted,#607080);line-height:1.6}
.template-list{display:grid;gap:10px}
.template-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px;border:1px solid var(--line,#dce4e8);border-radius:12px}
.template-row strong,.template-row small{display:block}.template-row small{margin-top:6px;color:var(--text-muted,#607080);line-height:1.5}
.template-actions{display:flex;gap:16px;white-space:nowrap}.template-actions button{border:0;background:none;color:#b42318;cursor:pointer}.template-actions a{text-decoration:none}
@media(max-width:650px){.template-row{align-items:flex-start;flex-direction:column}}
</style>
