<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ExternalLink, X, ImageIcon } from '../icons'
import { api, apiUrl, dateTime, statusText, type Row } from '../api'
import { groupRunAlerts } from '../monitorAlerts'
import { attempt } from '../ui'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))
const run = ref<Row | null>(null)
const results = ref<Row[]>([])
const activePreview = ref<{ url: string; title: string } | null>(null)
const previewZoomed = ref(false)
const rerunBusy = ref(false)

let timer: number | undefined
async function load() {
  const data = await attempt(() => api<Row>(`/runs/${id.value}`))
  if (data) {
    run.value = data.run
    results.value = data.results || []
  }
}

async function rerunJob(jobId: number | undefined) {
  if (!jobId || rerunBusy.value) return
  rerunBusy.value = true
  const res = await attempt(() => api<Row>(`/jobs/${jobId}/run`, 'POST'), '任务已重新开始运行')
  rerunBusy.value = false
  if (res?.run_id) {
    router.push(`/runs/${res.run_id}`)
  }
}

function openModal(url: string, title: string) {
  previewZoomed.value = false
  activePreview.value = { url, title }
}
function closeModal() {
  activePreview.value = null
  previewZoomed.value = false
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && activePreview.value) closeModal()
}

onMounted(() => {
  load()
  timer = window.setInterval(() => {
    if (run.value?.status === 'running') load()
  }, 4000)
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('keydown', onKeydown)
})

const successCount = computed(() => results.value.filter(r => r.status === 'success').length)
const failCount = computed(() => results.value.filter(r => r.status !== 'success').length)
const monitorAlerts = computed<Row[]>(() => groupRunAlerts(results.value))
const monitorSummary = computed(() => {
  if (run.value?.monitor_status !== 'alert' || !monitorAlerts.value.length) return run.value?.monitor_summary || ''
  const count = monitorAlerts.value.reduce((total, group) => total + group.instances.length, 0)
  const firing = monitorAlerts.value.reduce((total, group) => total + group.firing_count, 0)
  const pending = monitorAlerts.value.reduce((total, group) => total + group.pending_count, 0)
  return `${monitorAlerts.value.length} 类 · ${count} 个实例（触发 ${firing}${pending ? `，待触发 ${pending}` : ''}）`
})
const monitorLabel = computed(() => ({
  alert: '发现告警', unknown: '无法判断', unconfigured: '未配置告警规则', normal: '暂无活跃告警', not_checked: '未检查',
} as Row)[run.value?.monitor_status || 'not_checked'] || '未检查')
</script>

<template>
  <div class="editor-top">
    <RouterLink to="/runs" class="back-link">
      <ArrowLeft :size="16" />返回
    </RouterLink>
  </div>

  <template v-if="run">
    <!-- 1. 顶部标题与操作栏（匹配参考图） -->
    <div class="run-head-bar">
      <div class="run-head-info">
        <h1>运行记录 #{{ id }}</h1>
        <p class="run-head-sub">
          {{ run.job_name || '巡检任务' }} · 截图{{ statusText(run.status) }}<template v-if="run.monitor_status && run.monitor_status !== 'not_checked' && run.monitor_status !== 'not_applicable'"> · {{ monitorLabel }}</template> · {{ dateTime(run.started_at) }}
        </p>
      </div>
      <ButtonGroup class="run-head-actions">
        <ActionButton v-if="run.report_url"  class="run-action-btn" :to="`/runs/${id}/report`" variant="secondary">
          在线预览
        </ActionButton>
        <ActionButton v-if="run.report_url"  class="run-action-btn" :href="run.report_url" variant="primary">
          下载报告
        </ActionButton>
        <ActionButton
          v-if="run.job_id"
          type="button"
           class="run-action-btn"
          :disabled="rerunBusy"
          :aria-busy="rerunBusy"
          @click="rerunJob(run.job_id)"
         variant="secondary" :loading="rerunBusy">
          重新运行
        </ActionButton>
        <ActionButton v-if="run.job_id" :to="`/jobs/${run.job_id}`"  class="run-action-btn" variant="secondary">
          查看任务
        </ActionButton>
      </ButtonGroup>
    </div>

    <!-- 2. 4 列核心指标卡片横条（匹配参考图） -->
    <div class="run-summary-grid">
      <div class="run-stat-card">
        <span class="stat-card-title">成功</span>
        <strong class="stat-card-num text-success">{{ run.success_count ?? successCount }} 张</strong>
      </div>
      <div class="run-stat-card">
        <span class="stat-card-title">失败</span>
        <strong
          class="stat-card-num"
          :class="(run.failed_count ?? failCount) > 0 ? 'text-danger' : 'text-normal'"
        >
          {{ run.failed_count ?? failCount }} 张
        </strong>
      </div>
      <div class="run-stat-card">
        <span class="stat-card-title">通知状态</span>
        <strong class="stat-card-text">
          {{ run.dingtalk_status_label || run.mail_status_label || '已完成' }}
        </strong>
      </div>
      <div class="run-stat-card">
        <span class="stat-card-title">结束时间</span>
        <strong class="stat-card-text">
          {{ run.finished_at ? dateTime(run.finished_at) : (run.status === 'running' ? '正在执行...' : '-') }}
        </strong>
      </div>
    </div>

    <section v-if="run.monitor_status && run.monitor_status !== 'not_checked'" class="run-section-block">
      <div class="run-section-heading">
        <span class="heading-accent-bar"></span>
        <h2>Prometheus 告警</h2>
      </div>
      <div class="monitor-overview" :class="`monitor-${run.monitor_status}`">
        <strong>{{ monitorLabel }}</strong>
        <span>{{ monitorSummary }}</span>
      </div>
      <p class="monitor-scope-note">这里保存本次运行的告警快照；Prometheus 页面显示实时状态。告警不一定只属于这张看板。</p>
      <div v-if="monitorAlerts.length" class="run-table-card">
        <table class="run-table data-table alerts-table"><colgroup><col style="width:30%" /><col style="width:12%" /><col style="width:12%" /><col style="width:24%" /><col style="width:22%" /></colgroup>
          <thead><tr><th>告警</th><th class="align-center">状态</th><th class="align-right">实例</th><th>数据源</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="(alert, index) in monitorAlerts" :key="index">
              <td><TableText class="item-name-bold" :text="alert.name" /></td>
              <td class="align-center">{{ alert.firing_count ? '触发' : alert.pending_count ? '待触发' : alert.state }}</td>
              <td class="align-right">{{ alert.instances.length }} 个实例</td>
              <td><TableText :text="alert.source" /></td>
              <td>
                <div class="alert-row-actions">
                  <RouterLink class="alert-row-link" :to="`/runs/${id}/alerts/${alert.result_id}/${alert.alert_index}`">详情</RouterLink>
                  <a v-if="alert.source_uid" class="alert-row-link alert-row-link-external" :href="apiUrl(`runs/${id}/prometheus/${alert.result_id}/${encodeURIComponent(alert.source_uid)}`)" target="_blank" rel="noopener noreferrer"><ExternalLink :size="15" />Prometheus</a>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 3. 第一板块：截图结果（表格明细） -->
    <section class="run-section-block">
      <div class="run-section-heading">
        <span class="heading-accent-bar"></span>
        <h2>截图结果</h2>
      </div>

      <div class="run-table-card">
        <div v-if="!results.length" class="run-empty-box">
          <ImageIcon :size="28" />
          <p>{{ run.status === 'running' ? '正在执行截图，请稍候...' : '暂无截图结果' }}</p>
        </div>
        <table v-else class="run-table data-table screenshot-table">
          <colgroup><col style="width:23%" /><col style="width:14%" /><col style="width:80px" /><col style="width:90px" /><col style="width:156px" /><col style="width:84px" /><col /></colgroup>
          <thead>
            <tr>
              <th>截图项</th>
              <th>报告章节</th>
              <th class="align-center">状态</th>
              <th class="align-right">耗时</th>
              <th class="align-right">图片信息</th>
              <th class="align-center">图片</th>
              <th>内容状态 / 错误信息</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in results" :key="item.id">
              <td>
                <TableText class="item-name-bold" :text="item.item_name" />
              </td>
              <td class="text-secondary"><TableText :text="item.section" /></td>
              <td class="align-center">
                <span class="pill-chip" :class="`pill-${item.status}`">
                  {{ statusText(item.status) }}
                </span>
              </td>
              <td class="text-secondary font-num align-right">
                {{ item.duration_seconds != null ? `${Number(item.duration_seconds).toFixed(1)} 秒` : '-' }}
              </td>
              <td class="text-secondary font-num align-right">
                <div v-if="item.file_size_human" class="table-file-info">
                  <span>{{ item.file_size_human }}</span><small>{{ item.dimensions || '—' }}</small>
                </div>
                <span v-else class="muted">-</span>
              </td>
              <td class="align-center">
                <div v-if="item.file_url" class="table-img-actions">
                  <IconButton
                    type="button"
                    class="table-icon-action"
                    @click="openModal(item.file_url, item.item_name)"
                    title="在当前窗口弹窗预览"
                   label="预览截图">
                    <ImageIcon :size="15" />
                  </IconButton>
                  <IconButton
                    :href="item.file_url"
                    target="_blank"
                    rel="noopener"
                    class="table-open-new-btn"
                    title="在新标签页打开原图"
                   label="在新标签页打开原图">
                    <ExternalLink :size="13" />
                  </IconButton>
                </div>
                <span v-else class="muted">-</span>
              </td>
              <td>
                <TableText v-if="item.error_message" class="text-error-cell" :text="item.error_message" />
                <TableText v-else-if="item.content_summary" :class="item.content_status === 'no_data' || item.content_status === 'partial_no_data' ? 'text-warning-cell' : 'text-secondary'" :text="item.content_summary" />
                <span v-else class="muted">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 4. 第二板块：截图预览（3列等比例卡片网格） -->
    <section class="run-section-block" v-if="results.some(r => r.file_url)">
      <div class="run-section-heading">
        <span class="heading-accent-bar"></span>
        <h2>截图预览</h2>
      </div>

      <div class="preview-cards-grid">
        <div
          v-for="item in results.filter(r => r.file_url)"
          :key="'thumb-' + item.id"
          class="preview-thumb-card"
        >
          <button
            type="button"
            class="thumb-img-wrapper"
            @click="openModal(item.file_url, item.item_name)"
            :aria-label="`预览截图 ${item.item_name}`"
            title="预览"
          >
            <img :src="item.file_url" :alt="item.item_name" loading="lazy" />
          </button>
          <div class="thumb-caption">
            <h4>{{ item.item_name }}</h4>
            <p v-if="item.section">所属章节: {{ item.section }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 全屏灯箱 Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="activePreview" class="image-modal-backdrop" @click="closeModal">
          <div class="image-modal-container" role="dialog" aria-modal="true" :aria-label="`截图预览：${activePreview.title}`" @click.stop>
            <div class="image-modal-toolbar">
              <div class="image-modal-actions">
                <ActionButton
                  :href="activePreview.url"
                  target="_blank"
                  rel="noopener"
                   class="image-modal-open"
                  aria-label="在新标签页打开原图"
                  title="在新标签页打开"
                 variant="secondary">
                  <span>查看原图</span>
                </ActionButton>
                <IconButton
                  type="button"
                  class="image-modal-close"
                  @click="closeModal"
                  aria-label="关闭截图预览"
                  title="关闭 (Esc)"
                 label="关闭截图预览">
                  <X :size="20" />
                </IconButton>
              </div>
            </div>
            <div class="image-modal-body" :class="{ zoomed: previewZoomed }">
              <img :src="activePreview.url" :alt="activePreview.title" role="button" tabindex="0" :aria-label="previewZoomed ? '缩小截图' : '放大截图'" :title="previewZoomed ? '点击缩小' : '点击放大'" @click="previewZoomed = !previewZoomed" @keydown.enter="previewZoomed = !previewZoomed" @keydown.space.prevent="previewZoomed = !previewZoomed" />
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

  </template>

  <div v-else class="loading-panel">
    <div class="skeleton" v-for="n in 3" :key="n"></div>
  </div>
</template>

<style scoped>
.text-warning-cell { color: #a16207; }
.alert-row-actions { display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.alert-row-link { display: inline-flex; align-items: center; gap: 5px; color: #176a83; text-decoration: none; font-size: 12px; font-weight: 700; transition: color .18s ease, transform .18s ease; }
.alert-row-link:hover { color: #0b4c61; transform: translateY(-1px); }
.alert-row-link:active { transform: translateY(1px); }
.alert-row-link:focus-visible { outline: 3px solid #9ccbd8; outline-offset: 3px; border-radius: 2px; }
.alert-row-link-external { color: #506e7d; }

.size-dim-text {
  white-space: nowrap;
  font-size: 13px;
  color: #334155;
  font-weight: 500;
}
</style>
