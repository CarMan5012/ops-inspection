<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ArrowUpRight, Eye, Play, ClipboardList, Download, Pencil, Trash2, CheckCircle2, CircleAlert } from '../icons'
import RefreshButton from '../components/RefreshButton.vue'
import { api, dateTime, statusText, type Row } from '../api'
import { attempt, confirmModal } from '../ui'

const router = useRouter()
const jobs = ref<Row[]>([])
const runs = ref<Row[]>([])
const loading = ref(true)
const refreshing = ref(false)
const running = ref<number | null>(null)

async function load() {
  if (refreshing.value) return
  if (!loading.value) refreshing.value = true
  const data = await attempt(() => api<Row>('/dashboard'))
  if (data) { jobs.value = data.jobs || []; runs.value = data.runs || [] }
  loading.value = false
  refreshing.value = false
}
async function run(id: number) {
  running.value = id
  const result = await attempt(() => api<Row>(`/jobs/${id}/run`, 'POST'), '巡检已开始')
  running.value = null
  if (result?.run_id) router.push(`/runs/${result.run_id}`)
}
async function remove(job: Row) {
  const ok = await confirmModal({
    title: '确认删除巡检任务',
    message: `确定要删除任务“${job.name}”吗？此操作无法撤销。`,
    confirmText: '确认删除',
    danger: true,
  })
  if (!ok) return
  const result = await attempt(() => api(`/jobs/${job.id}`, 'DELETE'), '任务已删除')
  if (result) load()
}
onMounted(load)
</script>

<template>
  <section class="page-intro">
    <div><h1>巡检任务</h1></div>
    <ButtonGroup class="page-actions">
      <RefreshButton :loading="refreshing" @click="load" />
      <ActionButton to="/jobs/new" variant="primary">新建任务</ActionButton>
    </ButtonGroup>
  </section>
  <div class="section-heading"><div><h2>任务列表</h2></div><span class="count-label">{{ jobs.length }} 个任务</span></div>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 3" :key="n"></div></div>
  <div v-else-if="!jobs.length" class="empty-state"><span class="empty-icon"><ClipboardList :size="28" /></span><h3>还没有巡检任务</h3><p>创建任务后，添加需要截图的页面即可开始巡检。</p><ActionButton to="/jobs/new" variant="primary">新建任务</ActionButton></div>
  <div v-else class="job-list">
    <article v-for="job in jobs" :key="job.id" class="job-row">
      <div class="job-main">
        <RouterLink :to="`/jobs/${job.id}`" class="job-name">
          {{ job.name }} <ArrowUpRight :size="14" />
        </RouterLink>
        <div class="job-meta">
          <span>{{ job.environment || '未设置环境' }}</span>
          <span class="meta-dot"></span>
          <span>{{ job.schedule_label || '未设置执行时间' }}</span>
        </div>
      </div>
      <div class="job-controls">
        <span class="job-status-icon" :class="job.enabled ? 'is-enabled' : 'is-disabled'" :aria-label="job.enabled ? '已启用' : '已停用'" :data-tooltip="job.enabled ? '已启用' : '已停用'" role="status" tabindex="0">
          <CheckCircle2 v-if="job.enabled" :size="18" />
          <CircleAlert v-else :size="18" />
        </span>
        <div class="row-actions">
          <IconButton class="job-icon-action" :disabled="running === job.id" :loading="running === job.id" :aria-label="running === job.id ? `正在启动：${job.name}` : `立即运行：${job.name}`" :data-tooltip="running === job.id ? '正在启动' : '立即运行'" @click="run(job.id)" :label="running === job.id ? `正在启动：${job.name}` : `立即运行：${job.name}`"><Play :size="18" /></IconButton>
          <IconButton class="job-icon-action" :to="`/jobs/${job.id}`" :aria-label="`查看详情：${job.name}`" data-tooltip="查看详情" :label="`查看详情：${job.name}`"><Eye :size="18" /></IconButton>
          <IconButton class="job-icon-action" :to="`/jobs/${job.id}/edit`" :aria-label="`编辑任务：${job.name}`" data-tooltip="编辑任务" :label="`编辑任务：${job.name}`"><Pencil :size="18" /></IconButton>
          <IconButton class="job-icon-action danger-action" :aria-label="`删除任务：${job.name}`" data-tooltip="删除任务" @click="remove(job)" :label="`删除任务：${job.name}`"><Trash2 :size="18" /></IconButton>
        </div>
      </div>
    </article>
  </div>
  <div class="section-heading section-later"><div><h2>最近运行</h2></div><RouterLink class="text-link" to="/runs">查看全部 <ArrowUpRight :size="14" /></RouterLink></div>
  <div class="panel table-panel">
    <div v-if="!runs.length" class="simple-empty">暂无运行记录</div>
    <div v-else class="responsive-table">
      <table class="data-table recent-runs-table"><colgroup><col style="width:88px" /><col /><col style="width:180px" /><col style="width:100px" /><col style="width:88px" /></colgroup>
        <thead>
          <tr>
            <th>记录 ID</th>
            <th>任务</th>
            <th>开始时间</th>
            <th class="align-center">结果</th>
            <th class="align-center table-col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs.slice(0, 5)" :key="run.id">
            <td>
              <RouterLink :to="`/runs/${run.id}`" class="text-link job-id-link" :title="`查看运行记录 #${run.id}`">
                #{{ run.id }}
              </RouterLink>
            </td>
            <td><RouterLink class="table-primary table-name-link" :to="`/runs/${run.id}`"><TableText :interactive="false" :text="run.job_name || run.name || `任务 #${run.job_id || run.id}`" /></RouterLink></td>
            <td>{{ dateTime(run.started_at) }}</td>
            <td class="align-center"><span class="status-chip" :class="`status-${run.status}`"><span></span>{{ statusText(run.status) }}</span></td>
            <td class="align-center table-col-actions"><div class="table-actions"><IconButton v-if="run.report_url" :href="run.report_url" class="table-icon-action" :aria-label="`下载报告 #${run.id}`" title="下载报告" :label="`下载报告 #${run.id}`"><Download :size="16" /></IconButton><IconButton class="table-icon-action" :to="`/runs/${run.id}`" :aria-label="`查看记录 #${run.id}`" title="查看记录" :label="`查看记录 #${run.id}`"><ArrowUpRight :size="16" /></IconButton></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
