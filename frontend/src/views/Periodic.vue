<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { FileArchive, Download, Trash2 } from '../icons'
import RefreshButton from '../components/RefreshButton.vue'
import { api, dateTime, statusText, type Row } from '../api'
import { attempt, confirmModal } from '../ui'

const settings = ref<Row[]>([])
const runs = ref<Row[]>([])
const busy = ref<string | null>(null)
const refreshing = ref(false)
let pollTimer: number | undefined
let requestSeq = 0
let refreshUntil = 0
async function load() {
  const seq = ++requestSeq
  refreshing.value = true
  const data = await attempt(() => api<Row>('/periodic-reports'))
  if (seq !== requestSeq) return
  if (data) { settings.value = data.settings || []; runs.value = data.runs || [] }
  refreshing.value = false
}
async function generate(type: string) {
  if (busy.value) return
  busy.value = type
  const result = await attempt(() => api(`/periodic-reports/${type}/run`, 'POST'), '报告生成已开始')
  busy.value = null
  if (result) {
    refreshUntil = Date.now() + 120_000
    void load()
  }
}
async function removeRun(run: Row) {
  const ok = await confirmModal({
    title: '删除周期报告记录',
    message: `确定要删除此条 #${run.id} 周期报告记录及文件吗？`,
    confirmText: '确认删除',
    danger: true,
  })
  if (!ok) return
  if (await attempt(() => api(`/periodic-reports/runs/${run.id}`, 'DELETE'), '记录已删除')) load()
}
onMounted(() => {
  load()
  pollTimer = window.setInterval(() => {
    if (Date.now() < refreshUntil || runs.value.some(run => run.status === 'running' || run.status === 'waiting')) load()
  }, 10000)
})
onUnmounted(() => {
  clearInterval(pollTimer)
})
</script>

<template>
  <section class="page-intro"><div><h1>周期报告</h1></div><ButtonGroup class="page-actions"><RefreshButton :loading="refreshing" @click="load" /></ButtonGroup></section>
  <div class="section-heading"><div><h2>报告计划</h2></div></div>
  <div class="periodic-grid">
    <article v-for="setting in settings" :key="setting.report_type" class="periodic-card">
      <div class="periodic-card-top">
        <div class="periodic-icon"><FileArchive :size="20" /></div>
        <span class="status-chip" :class="setting.enabled ? 'status-success' : 'status-muted'">
          <span></span>{{ setting.enabled ? '已启用' : '已停用' }}
        </span>
      </div>
      <h3>{{ setting.report_type === 'weekly' ? '巡检周报' : '巡检月报' }}</h3>
      <p class="periodic-card-name">{{ setting.name || (setting.report_type === 'weekly' ? '每周巡检周报' : '每月巡检月报') }}</p>
      <p class="periodic-card-schedule">{{ setting.schedule_label || '按配置的时间自动生成' }}</p>
      <div v-if="setting.enabled && setting.next_run" class="periodic-next-run">
        <span>下次生成：</span><strong>{{ dateTime(setting.next_run) }}</strong>
      </div>
      <p v-if="setting.load_error" class="schedule-warning">{{ setting.load_error }}</p>
      <ButtonGroup class="periodic-actions">
        <ActionButton :disabled="busy === setting.report_type" @click="generate(setting.report_type)" variant="primary" :loading="busy === setting.report_type">
          立即生成
        </ActionButton>
        <ActionButton :to="`/periodic/${setting.report_type}/edit`" variant="secondary">
          编辑配置
        </ActionButton>
      </ButtonGroup>
    </article>
  </div>
  <div class="section-heading section-later"><div><h2>生成记录</h2></div><span class="count-label">{{ runs.length }} 条记录</span></div>
  <div class="panel table-panel">
    <div v-if="!runs.length" class="simple-empty">暂无周期报告</div>
    <div v-else class="responsive-table">
      <table class="data-table periodic-data-table"><colgroup><col style="width:88px" /><col /><col style="width:178px" /><col style="width:220px" /><col style="width:100px" /><col style="width:100px" /><col style="width:88px" /></colgroup>
        <thead>
          <tr>
            <th>记录 ID</th>
            <th>报告</th>
            <th>生成时间</th>
            <th>周期范围</th>
            <th class="align-center">状态</th>
            <th class="align-right">文件大小</th>
            <th class="align-center table-col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.id">
            <td><span class="id-badge">#{{ run.id }}</span></td>
            <td class="table-primary">{{ run.report_type === 'weekly' ? '巡检周报' : '巡检月报' }}</td>
            <td>{{ dateTime(run.started_at || run.created_at) }}</td>
            <td class="periodic-range">{{ run.period_start?.slice(0, 10) }} ~ {{ run.period_end?.slice(0, 10) }}</td>
            <td class="align-center"><span class="status-chip" :class="`status-${run.status}`"><span></span>{{ statusText(run.status) }}</span></td>
            <td class="align-right font-num">{{ run.size_str || '—' }}</td>
            <td class="align-center table-col-actions">
              <div class="table-actions">
                <IconButton v-if="run.download_url" :href="run.download_url" class="table-icon-action" :aria-label="`下载报告 #${run.id}`" title="下载报告" :label="`下载报告 #${run.id}`"><Download :size="16" /></IconButton>
                <IconButton type="button" class="table-icon-action danger-hover" :disabled="run.status === 'running' || run.status === 'waiting'" :aria-label="`删除周期报告 #${run.id}`" :title="run.status === 'running' || run.status === 'waiting' ? '报告生成完成后才能删除' : '删除记录'" @click="removeRun(run)" :label="`删除周期报告 #${run.id}`"><Trash2 :size="16" /></IconButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
