<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ArrowLeft } from '../icons'
import { api, dateTime, type Row } from '../api'

const route = useRoute()
const runId = computed(() => Number(route.params.id))
const run = ref<Row | null>(null)
const loading = ref(true)
const error = ref('')
const documentSurface = ref<HTMLElement | null>(null)
const reportName = computed(() => String(run.value?.report_path || '巡检报告.docx').replace(/\\/g, '/').split('/').pop() || '巡检报告.docx')

let requestId = 0
let controller: AbortController | null = null

async function loadReport() {
  const currentRequest = ++requestId
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  run.value = null
  if (documentSurface.value) documentSurface.value.replaceChildren()

  try {
    const result = await api<Row>(`/runs/${runId.value}`)
    if (currentRequest !== requestId) return
    run.value = result.run
    const reportUrl = String(result.run?.report_url || '')
    if (!reportUrl) throw new Error('这次运行没有可预览的报告')

    await nextTick()
    const response = await fetch(reportUrl, { credentials: 'same-origin', signal: controller.signal })
    if (!response.ok || response.redirected) throw new Error('读取报告失败，请检查登录状态后重试')
    const blob = await response.blob()
    const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer())
    if (signature[0] !== 0x50 || signature[1] !== 0x4b) throw new Error('报告文件格式不正确，无法预览')
    if (currentRequest !== requestId || !documentSurface.value) return

    const { renderAsync } = await import('docx-preview')
    if (currentRequest !== requestId || !documentSurface.value) return
    await renderAsync(blob, documentSurface.value, documentSurface.value, {
      breakPages: true,
      renderHeaders: true,
      renderFooters: true,
      renderAltChunks: false,
      useBase64URL: true,
    })
    if (currentRequest !== requestId) return
  } catch (reason) {
    if (currentRequest !== requestId || (reason instanceof DOMException && reason.name === 'AbortError')) return
    error.value = reason instanceof Error ? reason.message : '报告预览失败，请重试或下载查看'
    documentSurface.value?.replaceChildren()
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

watch(runId, loadReport, { immediate: true })
onUnmounted(() => { requestId++; controller?.abort() })
</script>

<template>
  <div class="report-preview-page">
    <div class="editor-top"><RouterLink :to="`/runs/${runId}`" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
    <section class="report-preview-head">
      <div class="report-preview-heading">
        <span class="report-preview-eyebrow">WORD 报告</span>
        <h1>报告预览</h1>
        <p>{{ run?.job_name || '巡检任务' }}<template v-if="run?.started_at"> · {{ dateTime(run.started_at) }}</template></p>
      </div>
      <ButtonGroup class="report-preview-actions">
        <ActionButton type="button"  :disabled="loading" @click="loadReport" variant="secondary" :loading="loading">刷新内容</ActionButton>
        <ActionButton v-if="run?.report_url"  :href="run.report_url" variant="primary">下载报告</ActionButton>
      </ButtonGroup>
    </section>
    <div class="report-preview-file"><span class="report-preview-file-icon">W</span><span>{{ reportName }}</span></div>
    <section class="report-preview-stage" aria-label="Word 报告内容">
      <div v-if="loading" class="report-preview-state" role="status"><span class="report-preview-spinner" aria-hidden="true"></span><strong>正在打开报告…</strong></div>
      <div v-else-if="error" class="report-preview-state report-preview-error" role="alert"><strong>无法预览报告</strong><p>{{ error }}</p><ActionButton type="button"  @click="loadReport" variant="secondary">重新加载</ActionButton></div>
      <div ref="documentSurface" class="report-document-surface" :class="{ hidden: loading || error }"></div>
    </section>
  </div>
</template>

<style scoped>
.report-preview-page{min-width:0}
.report-preview-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin:10px 0 18px}
.report-preview-heading{min-width:0}
.report-preview-eyebrow{display:block;margin-bottom:7px;color:#1b7896;font-size:11px;font-weight:800;letter-spacing:.09em}
.report-preview-heading h1{margin:0;color:#173748;font-size:27px;line-height:1.25;letter-spacing:-.03em}
.report-preview-heading p{margin:7px 0 0;color:#8196a1;font-size:13px}
.report-preview-actions{display:flex;flex-wrap:wrap;gap:9px}
.report-preview-file{display:flex;align-items:center;gap:10px;min-width:0;margin-bottom:13px;color:#4d6775;font-size:12px}
.report-preview-file span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.report-preview-file-icon{display:grid;place-items:center;flex:none;width:23px;height:23px;border-radius:5px;background:#e6f0f5;color:#176783;font-size:11px;font-weight:900}
.report-preview-stage{position:relative;min-height:460px;overflow:auto;border:1px solid #dce7eb;border-radius:12px;background:#eaf0f3}
.report-document-surface{min-height:460px}
.report-document-surface.hidden{visibility:hidden;height:0;min-height:0;overflow:hidden}
.report-document-surface :deep(.docx-wrapper){min-height:460px;padding:30px 20px;background:transparent}
.report-document-surface :deep(.docx){box-shadow:0 10px 35px rgba(32,65,81,.12)}
.report-preview-state{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;min-height:320px;padding:30px;text-align:center;color:#456674}
.report-preview-state strong{font-size:14px}
.report-preview-state p{max-width:440px;margin:0;color:#8296a1;font-size:12px;line-height:1.6}
.report-preview-spinner{width:27px;height:27px;border:3px solid #c6dce3;border-top-color:#1b7896;border-radius:50%;animation:report-spin .75s linear infinite}
.report-preview-error{background:#f5f8f9}
@keyframes report-spin{to{transform:rotate(360deg)}}
@media(max-width:700px){.report-preview-head{align-items:flex-start;flex-direction:column}.report-preview-heading h1{font-size:23px}.report-preview-stage{min-height:360px}.report-document-surface :deep(.docx-wrapper){padding:16px}}
</style>
