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
const activePreview = ref<{ url: string; title: string; section: string; createdAt: string; dimensions: string; fileSize: string } | null>(null)
const previewDownloadUrl = computed(() => {
  if (!activePreview.value) return ''
  const url = new URL(activePreview.value.url, window.location.href)
  url.searchParams.set('download', '1')
  return url.toString()
})
const previewZoomed = ref(false)
const glassStyles = ref<Record<number, Record<string, string>>>({})
const thumbnailImages = new Map<number, HTMLImageElement>()
const rerunBusy = ref(false)
const loadError = ref(false)

let timer: number | undefined
let requestSeq = 0
let requestPending = false
let resizeFrame = 0
let thumbnailObserver: ResizeObserver | undefined

function updateGlass(itemId: number, image: HTMLImageElement) {
  const width = image.clientWidth
  const height = image.clientHeight
  if (!image.naturalWidth || !image.naturalHeight || !width || !height) return
  try {
    // Match the top-centered cover crop used by the thumbnail CSS.
    const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight)
    const cropLeft = Math.max(0, (image.naturalWidth - width / scale) / 2)
    const sourceX = cropLeft + 12 / scale
    const sourceY = 12 / scale
    const sourceWidth = Math.min(310, width - 24) / scale
    const sourceHeight = Math.min(150, height - 24) / scale
    const canvas = document.createElement('canvas')
    canvas.width = 40
    canvas.height = 24
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (!context) return
    context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, canvas.width, canvas.height)
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let red = 0, green = 0, blue = 0, luminance = 0, luminanceSquared = 0
    const count = pixels.length / 4
    for (let index = 0; index < pixels.length; index += 4) {
      const r = pixels[index], g = pixels[index + 1], b = pixels[index + 2]
      const light = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
      red += r; green += g; blue += b
      luminance += light
      luminanceSquared += light * light
    }
    const average = luminance / count
    const variation = Math.sqrt(Math.max(0, luminanceSquared / count - average * average))
    const dark = average < 0.52
    const target = dark ? [18, 35, 46] : [248, 251, 253]
    const sampled = [red / count, green / count, blue / count]
    const tint = sampled.map((channel, index) => Math.round(channel * 0.3 + target[index] * 0.7))
    const opacity = Math.min(0.74, (dark ? 0.62 : 0.54) + variation * 0.24)
    glassStyles.value = {
      ...glassStyles.value,
      [itemId]: {
        '--glass-rgb': tint.join(' '),
        '--glass-opacity': opacity.toFixed(2),
        '--glass-ink': dark ? '#f7fbfd' : '#1d3745',
        '--glass-muted': dark ? '#bed1da' : '#536f7d',
        '--glass-divider': dark ? 'rgba(255,255,255,.22)' : 'rgba(34,73,89,.17)',
      },
    }
  } catch {
    // A failed or cross-origin image keeps the CSS fallback glass treatment.
  }
}

function onThumbnailLoad(itemId: number, event: Event) {
  const image = event.target
  if (!(image instanceof HTMLImageElement)) return
  thumbnailImages.set(itemId, image)
  thumbnailObserver?.observe(image)
  updateGlass(itemId, image)
}

function onResize() {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    for (const [itemId, image] of thumbnailImages) {
      if (image.isConnected) updateGlass(itemId, image)
      else thumbnailImages.delete(itemId)
    }
  })
}
async function load(silent = false) {
  if (requestPending) return
  requestPending = true
  const seq = ++requestSeq
  const request = () => api<Row>(`/runs/${id.value}`)
  const data = silent ? await request().catch(() => undefined) : await attempt(request)
  requestPending = false
  if (seq === requestSeq) loadError.value = !data
  if (seq === requestSeq && data) {
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

function openModal(item: Row) {
  previewZoomed.value = false
  activePreview.value = {
    url: String(item.file_url), title: String(item.item_name || '截图'),
    section: String(item.section || ''), createdAt: String(item.created_at_local || ''),
    dimensions: String(item.dimensions || ''),
    fileSize: String(item.file_size_human || ''),
  }
}
function closeModal() {
  activePreview.value = null
  previewZoomed.value = false
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && activePreview.value) closeModal()
}

onMounted(() => {
  if ('ResizeObserver' in window) thumbnailObserver = new ResizeObserver(onResize)
  load()
  timer = window.setInterval(() => {
    if (loadError.value || run.value?.status === 'queued' || run.value?.status === 'running') load(true)
  }, 4000)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  requestSeq++
  clearInterval(timer)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onResize)
  cancelAnimationFrame(resizeFrame)
  thumbnailObserver?.disconnect()
  thumbnailImages.clear()
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

  <div v-if="loadError && !run" class="empty-state"><h3>巡检记录加载失败</h3><p>记录不存在，或服务暂时不可用，页面会自动重试。</p><ActionButton type="button" variant="primary" @click="() => load()">重试</ActionButton></div>
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
          {{ run.status === 'queued' ? '等待执行' : run.status === 'running' ? '执行中' : run.dingtalk_status_label || '已完成' }}
        </strong>
      </div>
      <div class="run-stat-card">
        <span class="stat-card-title">结束时间</span>
        <strong class="stat-card-text">
          {{ run.finished_at ? dateTime(run.finished_at) : (run.status === 'queued' ? '等待执行...' : run.status === 'running' ? '正在执行...' : '-') }}
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
          <p>{{ run.status === 'queued' ? '任务排队中，请稍候...' : run.status === 'running' ? '正在执行截图，请稍候...' : '暂无截图结果' }}</p>
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
                    @click="openModal(item)"
                    title="在当前窗口弹窗预览"
                   label="预览截图">
                    <ImageIcon :size="15" />
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
            @click="openModal(item)"
            :aria-label="`预览截图 ${item.item_name}，${item.section || '未分组'}，${item.created_at_local || '时间未知'}，${item.dimensions || '尺寸未知'}，${item.file_size_human || '大小未知'}`"
          >
            <img :src="item.file_url" :alt="item.item_name" loading="lazy" @load="onThumbnailLoad(Number(item.id), $event)" />
            <span class="thumb-info-popover" :style="glassStyles[Number(item.id)]" aria-hidden="true">
              <strong class="thumb-info-title">{{ item.item_name }}</strong>
              <span class="thumb-info-row"><span class="thumb-info-label">章节</span><span class="thumb-info-value">{{ item.section || '未分组' }}</span></span>
              <span class="thumb-info-row"><span class="thumb-info-label">时间</span><span class="thumb-info-value">{{ item.created_at_local || '时间未知' }}</span></span>
              <span class="thumb-info-metrics">
                <span><small>尺寸</small>{{ item.dimensions || '未知' }}</span>
                <span><small>大小</small>{{ item.file_size_human || '未知' }}</span>
              </span>
            </span>
          </button>
        </div>
      </div>
    </section>

    <!-- 全屏灯箱 Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="activePreview" class="image-modal-backdrop" @click="closeModal">
          <div class="image-modal-container" role="dialog" aria-modal="true" :aria-label="`截图预览：${activePreview.title}`" @click.stop>
            <div class="image-modal-toolbar">
              <div class="image-modal-caption"><strong>{{ activePreview.title }}</strong><span>{{ [activePreview.section, activePreview.createdAt, activePreview.dimensions, activePreview.fileSize].filter(Boolean).join(' · ') }}</span></div>
              <div class="image-modal-actions">
                <ActionButton
                  :href="previewDownloadUrl"
                   class="image-modal-open"
                  aria-label="下载当前截图原图"
                  title="下载原图"
                 variant="secondary">
                  <span>下载原图</span>
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

  <div v-else-if="!loadError" class="loading-panel">
    <div class="skeleton" v-for="n in 3" :key="n"></div>
  </div>
</template>

<style scoped>
.preview-thumb-card { position: relative; overflow: hidden; border: 0; background: transparent; box-shadow: none; }
.preview-thumb-card:hover, .preview-thumb-card:focus-within { border: 0; box-shadow: none; }
.thumb-img-wrapper { position: relative; display: block; width: 100%; aspect-ratio: 16 / 9; padding: 0; border: 0; border-radius: 8px; overflow: hidden; }
.thumb-img-wrapper img { display: block; width: 100%; height: 100%; max-height: none; aspect-ratio: 16 / 9; object-fit: cover; object-position: top center; border: 0; border-radius: 8px; box-shadow: none; }
.thumb-info-popover { position: absolute; top: 12px; left: 12px; z-index: 1; display: flex; flex-direction: column; align-items: stretch; width: min(310px, calc(100% - 24px)); box-sizing: border-box; padding: 12px 14px; background: rgb(var(--glass-rgb, 248 251 253) / var(--glass-opacity, .58)); backdrop-filter: blur(14px) saturate(1.3); border-radius: 9px; box-shadow: 0 8px 26px rgba(8, 23, 32, .18), inset 0 1px rgba(255, 255, 255, .14); color: var(--glass-ink, #1d3745); text-align: left; line-height: 1.45; white-space: normal; opacity: 0; transform: translateY(-5px); transition: opacity .16s ease, transform .16s ease, background-color .2s ease; pointer-events: none; }
.thumb-info-title { display: block; margin-bottom: 7px; color: var(--glass-ink, #1d3745); font-size: 12px; font-weight: 700; line-height: 1.45; overflow-wrap: anywhere; }
.thumb-info-row { display: flex; align-items: flex-start; gap: 7px; margin-top: 4px; font-size: 11px; line-height: 1.45; }
.thumb-info-label { flex: 0 0 24px; color: var(--glass-muted, #536f7d); }
.thumb-info-value { min-width: 0; color: var(--glass-ink, #1d3745); overflow-wrap: anywhere; }
.thumb-info-metrics { display: flex; flex-wrap: wrap; gap: 5px 14px; margin-top: 9px; padding-top: 8px; border-top: 1px solid var(--glass-divider, rgba(34, 73, 89, .17)); color: var(--glass-ink, #1d3745); font-size: 11px; line-height: 1.45; font-variant-numeric: tabular-nums; }
.thumb-info-metrics > span { display: inline-flex; gap: 6px; }
.thumb-info-metrics small { color: var(--glass-muted, #536f7d); font-size: 11px; }
@supports not (backdrop-filter: blur(1px)) { .thumb-info-popover { background: rgb(var(--glass-rgb, 248 251 253) / .97); } }
.thumb-img-wrapper:hover .thumb-info-popover, .thumb-img-wrapper:focus-visible .thumb-info-popover { opacity: 1; transform: translateY(0); }
.thumb-img-wrapper:focus-visible { outline: 3px solid #2b8cac; outline-offset: -3px; }
.image-modal-toolbar { justify-content: space-between; gap: 16px; }
.image-modal-caption { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.image-modal-caption strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; color: #243b49; }
.image-modal-caption span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: #78909e; }
@media (hover: none) { .thumb-info-popover { display: none; } }
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
