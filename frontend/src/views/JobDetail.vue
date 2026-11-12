<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Pencil, Trash2, Camera } from '../icons'
import { api, type Row } from '../api'
import { attempt, confirmModal } from '../ui'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))
const job = ref<Row | null>(null)
const items = ref<Row[]>([])
const nextRuns = ref<string[]>([])
const busy = ref(false)
const testingItemId = ref<number | null>(null)
const loadError = ref(false)

async function load() {
  const data = await attempt(() => api<Row>(`/jobs/${id.value}`))
  if (data) {
    loadError.value = false
    job.value = data.job
    items.value = data.items || []
    nextRuns.value = data.next_runs || []
  } else {
    job.value = null
    loadError.value = true
  }
}

async function run() {
  busy.value = true
  const result = await attempt(() => api<Row>(`/jobs/${id.value}/run`, 'POST'), '巡检已开始')
  busy.value = false
  if (result?.run_id) router.push(`/runs/${result.run_id}`)
}

async function test(item: Row) {
  if (testingItemId.value !== null) return
  testingItemId.value = Number(item.id)
  const result = await attempt(() => api<Row>(`/items/${item.id}/test`, 'POST'), `“${item.name}”测试已加入队列`)
  testingItemId.value = null
  if (result?.run_id) router.push(`/runs/${result.run_id}`)
}

async function remove(item: Row) {
  const ok = await confirmModal({
    title: '确认删除截图项',
    message: `确定要删除截图项“${item.name}”吗？此操作无法撤销。`,
    confirmText: '确认删除',
    danger: true,
  })
  if (!ok) return
  if (await attempt(() => api(`/items/${item.id}`, 'DELETE'), '截图项已删除')) load()
}

onMounted(load)
</script>

<template>
  <div class="editor-top">
    <RouterLink to="/jobs" class="back-link"><ArrowLeft :size="16" />返回</RouterLink>
  </div>
  <template v-if="job">
    <section class="page-intro detail-intro">
      <div>
        <h1>{{ job.name }}</h1>
        <p>{{ job.environment }} · {{ job.schedule_label || '未设置执行时间' }}</p>
      </div>
      <ButtonGroup class="page-actions">
        <ActionButton :to="`/jobs/${id}/edit`" variant="secondary">编辑任务</ActionButton>
        <ActionButton :disabled="busy" @click="run" variant="primary" :loading="busy">
          立即运行
        </ActionButton>
      </ButtonGroup>
    </section>

    <div v-if="job.load_error" class="schedule-warning">{{ job.load_error }}</div>

    <!-- 任务核心属性汇总 (5列：任务状态、截图项、截图尺寸、报告标题、未来执行时间) -->
    <div class="detail-meta">
      <div>
        <span>任务状态</span>
        <strong>
          <span class="status-chip" :class="job.load_error ? 'status-muted' : job.enabled ? 'status-success' : 'status-muted'">
            <span></span>{{ job.load_error ? '定时计划已暂停' : job.enabled ? '定时巡检已启用' : '定时巡检已关闭' }}
          </span>
        </strong>
      </div>
      <div><span>截图项</span><strong>{{ items.length }} 项</strong></div>
      <div><span>截图尺寸</span><strong>{{ job.browser_width }} × {{ job.browser_height }}</strong></div>
      <div><span>报告标题</span><strong>{{ job.report_title }}</strong></div>
      <div class="detail-runs-col">
        <span>未来执行时间</span>
        <div v-if="job.enabled && nextRuns.length" class="detail-runs-list">
          <div
            v-for="(timeStr, idx) in nextRuns"
            :key="timeStr"
            class="detail-run-row"
            :class="idx === 0 ? 'run-green' : 'run-blue'"
          >
            <span class="run-bullet">•</span>
            <span class="run-row-time">{{ timeStr.slice(0, 16) }}</span>
          </div>
        </div>
        <strong v-else class="muted">未开启定时</strong>
      </div>
    </div>

    <!-- 截图项标题与操作 -->
    <div class="section-heading section-later">
      <div><h2>截图项</h2></div>
      <ButtonGroup class="page-actions"><ActionButton :to="`/jobs/${id}/items/add`" variant="secondary">批量添加</ActionButton><ActionButton :to="`/jobs/${id}/items/new`" variant="primary">新建截图</ActionButton></ButtonGroup>
    </div>

    <!-- 截图项列表 -->
    <div v-if="!items.length" class="empty-state">
      <span class="empty-icon"><Camera :size="28" /></span>
      <h3>还没有截图项</h3>
      <p>添加要访问的页面地址，任务运行时会自动截图。</p>
      <ActionButton :to="`/jobs/${id}/items/new`" variant="primary">新建截图</ActionButton>
      <ActionButton :to="`/jobs/${id}/items/add`" variant="secondary">批量添加</ActionButton>
    </div>
    <div v-else class="item-list">
      <article v-for="item in items" :key="item.id" class="item-row">
        <div class="item-details">
          <div class="item-title-row">
            <h3>{{ item.name }}</h3>
            <span class="status-chip" :class="item.enabled ? 'status-success' : 'status-muted'">
              <span></span>{{ item.enabled ? '已启用' : '已停用' }}
            </span>
          </div>
          <p class="item-url">{{ item.url }}</p>
          <div class="job-meta">
            <template v-if="job.template_id"><span>{{ item.section || '未选择模板章节' }}</span><span class="meta-dot"></span></template>
            <span>{{ item.real_browser_capture ? '完整浏览器窗口' : '网页截图' }}</span>
          </div>
        </div>
        <div class="item-actions">
          <IconButton class="table-icon-action" type="button" :disabled="testingItemId !== null" :aria-label="`测试截图：${item.name}`" title="测试截图" @click="test(item)" :label="`测试截图：${item.name}`"><Camera :size="16" /></IconButton>
          <IconButton class="table-icon-action" :to="`/jobs/${id}/items/${item.id}/edit`" :aria-label="`编辑截图：${item.name}`" title="编辑截图" :label="`编辑截图：${item.name}`"><Pencil :size="15" /></IconButton>
          <IconButton class="table-icon-action danger-hover" :aria-label="`删除截图：${item.name}`" title="删除截图" @click="remove(item)" :label="`删除截图：${item.name}`"><Trash2 :size="15" /></IconButton>
        </div>
      </article>
    </div>
  </template>
  <div v-else-if="loadError" class="empty-state"><h3>任务加载失败或已删除</h3><p>请返回任务列表确认，或重试加载。</p><ActionButton type="button" @click="load" variant="primary">重试</ActionButton><ActionButton to="/jobs" variant="secondary">返回任务列表</ActionButton></div>
  <div v-else class="loading-panel"><div class="skeleton" v-for="n in 3" :key="n"></div></div>
</template>
