<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowUpRight, History, Download, Eye } from '../icons'
import RefreshButton from '../components/RefreshButton.vue'
import { api, dateTime, statusText, type Row } from '../api'
import { attempt } from '../ui'

const runs = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(0)
const pageSizes = ref<number[]>([])
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const visiblePages = computed(() => {
  const count = Math.min(5, totalPages.value)
  const start = Math.max(1, Math.min(page.value - 2, totalPages.value - count + 1))
  return Array.from({ length: count }, (_, index) => start + index)
})
const rangeStart = computed(() => total.value ? (page.value - 1) * pageSize.value + 1 : 0)
const rangeEnd = computed(() => Math.min(page.value * pageSize.value, total.value))
const loading = ref(true)
const refreshing = ref(false)
const loadError = ref(false)
const tablePanel = ref<HTMLElement | null>(null)
let timer: number | undefined
let requestSeq = 0
async function load(silent = false): Promise<boolean> {
  if (!pageSizes.value.length) return initialize(silent)
  const seq = ++requestSeq
  if (!loading.value) refreshing.value = true
  const request = () => api<Row>(`/runs?page=${page.value}&page_size=${pageSize.value}`)
  const data = silent ? await request().catch(() => undefined) : await attempt(request)
  if (seq !== requestSeq) return false
  loadError.value = !data
  if (data) {
    const nextTotal = data.total || 0
    if (nextTotal > 0 && page.value > Math.ceil(nextTotal / pageSize.value)) {
      page.value = Math.ceil(nextTotal / pageSize.value)
      return load(silent)
    }
    runs.value = data.runs || []
    total.value = nextTotal
  } else if (!silent) {
    // The selected page or page size may have changed; stale rows would be mislabeled.
    runs.value = []
    total.value = 0
  }
  loading.value = false
  refreshing.value = false
  return Boolean(data)
}
async function initialize(silent = false): Promise<boolean> {
  loading.value = true
  const config = silent ? await api<Row>('/ui-config').catch(() => undefined) : await attempt(() => api<Row>('/ui-config'))
  const sizes = config?.run_page_sizes
  const defaultSize = Number(config?.run_default_page_size)
  if (!Array.isArray(sizes) || !sizes.includes(defaultSize)) {
    loadError.value = true
    loading.value = false
    return false
  }
  pageSizes.value = sizes.map(Number)
  pageSize.value = defaultSize
  return load()
}
async function showPageStart() {
  await nextTick()
  tablePanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
async function changePage(next: number) {
  if (next < 1 || next > totalPages.value || next === page.value) return
  page.value = next
  if (await load()) await showPageStart()
}
async function changePageSize() {
  page.value = 1
  if (await load()) await showPageStart()
}
onMounted(() => { initialize(); timer = window.setInterval(() => { if (!loading.value && !refreshing.value && (loadError.value || runs.value.some(run => run.status === 'queued' || run.status === 'running'))) load(true) }, 5000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section class="page-intro"><div><h1>巡检记录</h1></div><ButtonGroup class="page-actions"><RefreshButton :loading="refreshing" @click="() => load()" /></ButtonGroup></section>
  <div class="section-heading"><div><h2>全部记录</h2></div><span class="count-label">共 {{ total }} 条</span></div>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 5" :key="n"></div></div>
  <div v-else-if="loadError && !runs.length" class="empty-state"><h3>巡检记录加载失败</h3><p>网络或服务暂时不可用，页面会自动重试。</p><ActionButton type="button" variant="primary" @click="() => load()">重试</ActionButton></div>
  <div v-else-if="!runs.length" class="empty-state"><span class="empty-icon"><History :size="30" /></span><h3>暂无巡检记录</h3><p>运行任务后，结果会显示在这里。</p><ActionButton to="/jobs"  variant="primary">查看任务</ActionButton></div>
  <div v-else ref="tablePanel" class="panel table-panel runs-table-panel">
    <div class="responsive-table">
      <table class="data-table runs-data-table"><colgroup><col style="width:88px" /><col /><col style="width:178px" /><col style="width:150px" /><col style="width:130px" /><col style="width:100px" /><col style="width:116px" /></colgroup>
        <thead>
          <tr>
            <th>记录 ID</th>
            <th>任务</th>
            <th>开始时间</th>
            <th>截图结果</th>
            <th>数据监测</th>
            <th class="align-center">状态</th>
            <th class="align-center table-col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.id">
            <td>
              <RouterLink :to="`/runs/${run.id}`" class="text-link job-id-link" :title="`查看运行记录 #${run.id}`">
                #{{ run.id }}
              </RouterLink>
            </td>
            <td>
              <RouterLink class="table-primary table-name-link" :to="`/runs/${run.id}`"><TableText :interactive="false" :text="run.job_name || run.name || `任务 #${run.job_id || run.id}`" /></RouterLink>
            </td>
            <td>{{ dateTime(run.started_at) }}</td>
            <td>{{ run.success_count ?? 0 }} 成功 · {{ run.failed_count ?? 0 }} 失败</td>
            <td>{{ ({ alert: '发现告警', unknown: '无法判断', unconfigured: '未配置规则', normal: '暂无告警' } as Row)[run.monitor_status] || '未检查' }}</td>
            <td class="align-center">
              <span class="status-chip" :class="`status-${run.status}`">
                <span></span>{{ statusText(run.status) }}
              </span>
            </td>
            <td class="align-center table-col-actions"><div class="table-actions"><IconButton v-if="run.report_url" class="table-icon-action" :to="`/runs/${run.id}/report`" :aria-label="`在线预览 #${run.id}`" title="在线预览" :label="`在线预览 #${run.id}`"><Eye :size="16" /></IconButton><IconButton v-if="run.report_url" :href="run.report_url" class="table-icon-action" :aria-label="`下载报告 #${run.id}`" title="下载报告" :label="`下载报告 #${run.id}`"><Download :size="16" /></IconButton><IconButton class="table-icon-action" :to="`/runs/${run.id}`" :aria-label="`查看记录 #${run.id}`" title="查看记录" :label="`查看记录 #${run.id}`"><ArrowUpRight :size="16" /></IconButton></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div v-if="total > 0" class="runs-pagination" aria-label="巡检记录分页">
    <span class="runs-pagination-summary">显示 {{ rangeStart }}–{{ rangeEnd }} 条，共 {{ total }} 条</span>
    <div class="runs-pagination-controls">
      <label class="runs-page-size">每页
        <select v-model.number="pageSize" aria-label="每页显示条数" @change="changePageSize">
          <option v-for="size in pageSizes" :key="size" :value="size">{{ size }} 条</option>
        </select>
      </label>
      <nav v-if="totalPages > 1" class="runs-page-nav" aria-label="页码">
        <button type="button" :disabled="page === 1" aria-label="上一页" @click="changePage(page - 1)">上一页</button>
        <button v-for="number in visiblePages" :key="number" type="button" :class="{ active: number === page }" :aria-label="`第 ${number} 页`" :aria-current="number === page ? 'page' : undefined" @click="changePage(number)">{{ number }}</button>
        <button type="button" :disabled="page === totalPages" aria-label="下一页" @click="changePage(page + 1)">下一页</button>
      </nav>
    </div>
  </div>
</template>
