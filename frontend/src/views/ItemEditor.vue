<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Info, CheckCircle2, Plus, Minus } from '../icons'
import { api, type Row } from '../api'
import { attempt, notify } from '../ui'
import GrafanaOptions from '../components/GrafanaOptions.vue'

const route = useRoute()
const router = useRouter()
const jobId = computed(() => Number(route.params.jobId))
const id = computed(() => Number(route.params.id || 0))
const saving = ref(false)
const loading = ref(true)
const loadFailed = ref(false)
const authProfiles = ref<Row[]>([])
const taskbarTemplates = ref<Row[]>([])
const selectedTaskbar = computed(() => taskbarTemplates.value.find(template => template.key === form.taskbar_template_key))
const sections = ref<string[]>([])
const templateSelected = ref(false)
const selectedTemplateSection = ref('')
const sectionError = ref('')
const nameError = ref('')
const advanced = ref(false)
const checkingPage = ref(false)
const dashboardQuery = ref('')
const dashboardOptions = ref<Row[]>([])
const dashboardsLoaded = ref(false)
const dashboardsLoading = ref(false)
const showDashboardPicker = ref(false)
const selectedDashboardTitle = ref('')
const selectedDashboardUid = computed(() => {
  try {
    const url = new URL(String(form.url || ''))
    return form.item_type === 'kibana'
      ? url.hash.match(/\/view\/([^/?]+)/)?.[1] || ''
      : url.pathname.match(/\/d(?:-solo)?\/([^/]+)/)?.[1] || ''
  }
  catch { return '' }
})
const selectionError = ref('')
const previewImage = ref('')
const previewError = ref('')
const previewBusy = ref(false)
const jobImageWidth = ref(0)
const jobImageHeight = ref(0)
const watermarkDefaults = ref<Row>({})
const itemDefaults = ref<Row>({})
const watermarkRules = ref<Row>({})
const itemTypes = ref<Row[]>([])
const captureModes = ref<Row[]>([])
const imageWidth = computed(() => Math.min(Number(watermarkRules.value.image_width?.max), Math.max(Number(watermarkRules.value.image_width?.min), Number(form.browser_width) || jobImageWidth.value)))
const imageHeight = computed(() => Math.min(Number(watermarkRules.value.image_height?.max), Math.max(Number(watermarkRules.value.image_height?.min), Number(form.browser_height) || jobImageHeight.value)))
const previewCanvasWidth = computed(() =>
  `min(100%, ${Math.min(1400, Math.round(760 * imageWidth.value / imageHeight.value))}px)`,
)
const previewAspectRatio = computed(() => `${imageWidth.value} / ${imageHeight.value}`)
let previewTimer: ReturnType<typeof setTimeout> | undefined
let previewRequestId = 0
let dashboardRequestId = 0
const form = reactive<Row>({})
function bool(value: unknown): boolean { return value === true || value === 1 || value === '1' }

const baseWatermarkAngle = computed(() => Number(watermarkRules.value.angle?.base))
const angleOffsetMin = computed(() => Number(watermarkRules.value.angle_offset?.min))
const angleOffsetMax = computed(() => Number(watermarkRules.value.angle_offset?.max))
const watermarkAngleOffset = computed({
  get: () => Number(form.watermark_angle ?? baseWatermarkAngle.value) - baseWatermarkAngle.value,
  set: value => { form.watermark_angle = baseWatermarkAngle.value + Number(value || 0) },
})

function useReferenceWatermarkLayout() {
  for (const key of ['watermark_time_enabled', 'watermark_time_format', 'watermark_font_size', 'watermark_gap_x', 'watermark_gap_y', 'watermark_alignment']) {
    form[key] = watermarkDefaults.value[key]
  }
  const angle = Number(form.watermark_angle)
  form.watermark_angle = Number.isFinite(angle) && angle >= baseWatermarkAngle.value + angleOffsetMin.value && angle <= baseWatermarkAngle.value + angleOffsetMax.value ? angle : baseWatermarkAngle.value
}

function restoreWatermarkDefaults() {
  Object.assign(form, watermarkDefaults.value)
  form.watermark_enabled = bool(form.watermark_enabled)
  useReferenceWatermarkLayout()
}

watch(() => [
  loading.value, form.watermark_enabled, form.watermark_time_enabled, form.watermark_time_format,
  form.watermark_text, form.watermark_opacity, form.watermark_font_size, form.watermark_gap_x,
  form.watermark_gap_y, form.watermark_angle, form.watermark_alignment, imageWidth.value, imageHeight.value,
], () => {
  clearTimeout(previewTimer)
  const requestId = ++previewRequestId
  if (loading.value || !form.watermark_enabled) {
    previewImage.value = ''
    previewBusy.value = false
    previewError.value = ''
    return
  }
  previewBusy.value = true
  previewError.value = ''
  previewTimer = setTimeout(async () => {
    try {
      const result = await api<{ image: string }>('/watermark/preview', 'POST', {
        watermark_enabled: form.watermark_enabled,
        watermark_time_enabled: form.watermark_time_enabled,
        watermark_time_format: form.watermark_time_format,
        watermark_text: form.watermark_text,
        watermark_opacity: form.watermark_opacity,
        watermark_font_size: form.watermark_font_size,
        watermark_gap_x: form.watermark_gap_x,
        watermark_gap_y: form.watermark_gap_y,
        watermark_angle: form.watermark_angle,
        watermark_alignment: form.watermark_alignment,
        image_width: imageWidth.value,
        image_height: imageHeight.value,
      })
      if (requestId === previewRequestId) previewImage.value = result.image
    } catch (cause) {
      if (requestId === previewRequestId) {
        previewImage.value = ''
        previewError.value = cause instanceof Error ? cause.message : '预览生成失败'
      }
    } finally {
      if (requestId === previewRequestId) previewBusy.value = false
    }
  }, 250)
}, { immediate: true })

onUnmounted(() => { clearTimeout(previewTimer); previewRequestId++ })

onMounted(async () => {
  const [config, data, taskbarData] = await Promise.all([
    attempt(() => api<Row>('/ui-config')),
    attempt(() => api<Row>(`/jobs/${jobId.value}`)),
    attempt(() => api<Row>('/taskbar-templates')),
  ])
  if (!config?.item_defaults || !config?.watermark_defaults || !config?.watermark_rules || !Array.isArray(config.item_types) || !Array.isArray(config.capture_modes) || !data || (id.value && !(data.items || []).some((row: Row) => Number(row.id) === id.value))) {
    loadFailed.value = true
    loading.value = false
    return
  }
  authProfiles.value = data?.auth_profiles || []
  jobImageWidth.value = Number(data?.job?.browser_width) || Number(config.job_defaults.browser_width)
  jobImageHeight.value = Number(data?.job?.browser_height) || Number(config.job_defaults.browser_height)
  taskbarTemplates.value = taskbarData?.templates || []
  sections.value = data?.template_sections || []
  watermarkDefaults.value = config.watermark_defaults
  itemDefaults.value = config.item_defaults
  watermarkRules.value = config.watermark_rules
  itemTypes.value = config.item_types
  captureModes.value = config.capture_modes
  Object.assign(form, config.item_defaults)
  form.watermark_enabled = bool(form.watermark_enabled)
  templateSelected.value = Boolean(data?.job?.template_id)
  if (id.value) {
    const item = (data?.items || []).find((row: Row) => Number(row.id) === id.value)
    if (item) {
      Object.assign(form, item)
      form.auth_profile_id = item.auth_profile_id ?? ''
      if (['grafana', 'kibana'].includes(String(item.item_type)) && item.url) selectedDashboardTitle.value = String(item.name || '当前看板')
      for (const key of ['enabled', 'real_browser_capture', 'taskbar_enabled', 'watermark_enabled']) form[key] = bool(form[key])

      const curSection = String(item.section || '').trim()
      selectedTemplateSection.value = sections.value.includes(curSection) ? curSection : ''
    }
  }
  useReferenceWatermarkLayout()
  loading.value = false
})
async function save() {
  selectionError.value = ''
  sectionError.value = ''
  nameError.value = ''
  if (form.item_type === 'grafana' && (!form.auth_profile_id || !form.url)) {
    selectionError.value = '请先选择网站连接和 Grafana 看板'
    return
  }
  if (!String(form.name || '').trim()) { nameError.value = '请填写截图名称'; return }
  if (templateSelected.value && !sections.value.includes(selectedTemplateSection.value)) {
    sectionError.value = '请选择 Word 模板中的报告章节'
    return
  }
  saving.value = true
  form.section = templateSelected.value ? selectedTemplateSection.value : String(form.name).trim()
  useReferenceWatermarkLayout()
  const payload = { ...form, auth_profile_id: form.auth_profile_id || null, browser_width: form.browser_width || null, browser_height: form.browser_height || null }
  const result = await attempt(() => api(id.value ? `/items/${id.value}` : `/jobs/${jobId.value}/items`, id.value ? 'PUT' : 'POST', payload), id.value ? '截图项已更新' : '截图项已创建')
  saving.value = false
  if (result) router.push(`/jobs/${jobId.value}`)
}

async function loadDashboards() {
  if (!form.auth_profile_id) {
    dashboardsLoading.value = false
    selectionError.value = '请先选择网站连接'
    return
  }
  dashboardsLoading.value = true
  dashboardsLoaded.value = false
  selectionError.value = ''
  const requestId = ++dashboardRequestId
  const profileId = form.auth_profile_id
  const query = dashboardQuery.value
  const kind = form.item_type === 'kibana' ? 'kibana-dashboards' : 'dashboards'
  const result = await attempt(() => api<Row>(`/auth-profiles/${profileId}/${kind}?query=${encodeURIComponent(query)}`))
  if (requestId !== dashboardRequestId || profileId !== form.auth_profile_id) return
  dashboardOptions.value = result?.dashboards || []
  dashboardsLoaded.value = Boolean(result)
  dashboardsLoading.value = false
}

async function onConnectionChange() {
  dashboardRequestId++
  dashboardsLoading.value = false
  form.url = ''
  selectedDashboardTitle.value = ''
  dashboardOptions.value = []
  dashboardsLoaded.value = false
  showDashboardPicker.value = true
  if (form.auth_profile_id) await loadDashboards()
}

function chooseDashboard(dashboard: Row) {
  showDashboardPicker.value = false
  if (selectedDashboardUid.value === String(dashboard.uid)) return
  form.url = String(dashboard.url)
  form.name = String(dashboard.title || (form.item_type === 'kibana' ? 'Kibana 仪表盘' : 'Grafana 看板'))
  selectedDashboardTitle.value = String(dashboard.title || form.name)
  selectionError.value = ''
}

async function toggleDashboardPicker() {
  showDashboardPicker.value = !showDashboardPicker.value
  if (showDashboardPicker.value && !dashboardsLoaded.value && form.auth_profile_id) await loadDashboards()
}

function onPageTypeChange() {
  dashboardRequestId++
  dashboardsLoading.value = false
  dashboardsLoaded.value = false
  dashboardOptions.value = []
  dashboardQuery.value = ''
  showDashboardPicker.value = true
  selectionError.value = ''
  if (form.item_type === 'grafana' && form.url && !/\/d(?:-solo)?\//.test(String(form.url))) form.url = ''
  if (form.item_type === 'kibana' && /\/d(?:-solo)?\//.test(String(form.url))) form.url = ''
  selectedDashboardTitle.value = form.url ? String(form.name || '当前看板') : ''
  if (['grafana', 'kibana'].includes(String(form.item_type)) && form.auth_profile_id) void loadDashboards()
}

async function checkPage() {
  if (checkingPage.value || !String(form.url || '').trim()) return
  checkingPage.value = true
  try {
    const result = await api<Row>('/connections/check-target', 'POST', {
      url: String(form.url || '').trim(), auth_profile_id: form.auth_profile_id || null,
    })
    if (!result.ok) notify(String(result.message || '页面验证失败'), 'error')
    else if (result.monitor_status === 'unknown') notify(`页面可访问；告警无法判断${result.monitor_message ? `：${result.monitor_message}` : ''}`, 'info')
    else notify('页面可访问，登录有效')
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : '页面验证失败', 'error')
  } finally {
    checkingPage.value = false
  }
}
</script>

<template>
  <div class="editor-top"><RouterLink :to="`/jobs/${jobId}`" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
  <section class="page-intro editor-intro"><div><h1>{{ id ? '编辑截图项' : '新增截图项' }}</h1></div></section>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 4" :key="n"></div></div>
  <div v-else-if="loadFailed" class="empty-state"><h3>截图项加载失败</h3><p>请返回任务详情，确认截图项仍然存在后重试。</p><ActionButton :to="`/jobs/${jobId}`"  variant="secondary">返回</ActionButton></div>
  <form v-else class="editor-layout item-editor-layout" @submit.prevent="save"><div class="editor-main">
    <section class="form-section"><div class="form-section-head"><div><h2>页面信息</h2></div></div><div class="form-grid">
      <label class="field"><span>截图名称 <b>*</b></span><input v-model="form.name" required placeholder="例如：生产看板" @input="nameError = ''" /><small v-if="nameError" class="schedule-warning" role="alert">{{ nameError }}</small><small v-else-if="!templateSelected">自动排版使用此名称，无需设置报告章节。</small></label>
      <label class="field"><span>页面类型</span><select v-model="form.item_type" @change="onPageTypeChange"><option v-for="option in itemTypes" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
      <div v-if="form.item_type === 'grafana' || form.item_type === 'kibana'" class="field field-full dashboard-select-field">
        <span>选择 {{ form.item_type === 'kibana' ? 'Kibana 仪表盘' : 'Grafana 看板' }} <b v-if="form.item_type === 'grafana'">*</b></span>
        <div class="dashboard-select-header"><select v-model="form.auth_profile_id" aria-label="选择网站连接" @change="onConnectionChange"><option value="">先选择网站连接</option><option v-for="profile in authProfiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select><RouterLink to="/auth" class="connection-link">管理连接</RouterLink></div>
        <div v-if="form.url" class="dashboard-selected"><CheckCircle2 :size="19" class="dashboard-selected-icon" /><div class="dashboard-selected-copy"><strong>{{ selectedDashboardTitle || form.name || '当前看板' }}</strong><small>截图将使用这个看板</small></div><ActionButton type="button"  class="dashboard-change-button" :aria-expanded="showDashboardPicker" @click="toggleDashboardPicker" variant="secondary">{{ showDashboardPicker ? '收起看板' : '更换看板' }}</ActionButton></div>
        <div v-if="form.auth_profile_id && (!form.url || showDashboardPicker)" class="dashboard-search-tools"><input v-model="dashboardQuery" placeholder="搜索看板名称" @keydown.enter.prevent="loadDashboards" /><ActionButton type="button" :disabled="dashboardsLoading" @click="loadDashboards" variant="secondary" :loading="dashboardsLoading">搜索看板</ActionButton></div>
        <div v-if="(!form.url || showDashboardPicker) && dashboardOptions.length" class="dashboard-picker dashboard-single-picker">
          <button v-for="dashboard in dashboardOptions" :key="dashboard.uid" type="button" class="dashboard-select-option" :class="{ selected: selectedDashboardUid === dashboard.uid }" @click="chooseDashboard(dashboard)"><span><strong>{{ dashboard.title }}</strong><small>{{ dashboard.folder || (form.item_type === 'kibana' ? 'Kibana 仪表盘' : 'Grafana 看板') }}</small></span><span class="dashboard-option-mark">{{ selectedDashboardUid === dashboard.uid ? '已选' : '选择' }}</span></button>
        </div>
        <p v-else-if="(!form.url || showDashboardPicker) && dashboardsLoading" class="connection-hint">正在读取看板…</p>
        <p v-else-if="(!form.url || showDashboardPicker) && dashboardsLoaded" class="connection-hint">没有找到看板，请换个名称搜索。</p>
        <p v-else-if="!form.auth_profile_id" class="connection-hint">先选择连接，再选择看板。</p>
        <p v-if="selectionError" class="dashboard-selection-error" role="alert">{{ selectionError }}</p>
        <label v-if="form.item_type === 'kibana'" class="field field-full"><span>页面地址（也可手动填写） <b>*</b></span><input v-model="form.url" type="url" required placeholder="https://example.com/app/dashboards#/view/..." /></label>
        <GrafanaOptions v-if="form.item_type === 'grafana' && form.url && form.auth_profile_id" :url="String(form.url)" :auth-profile-id="form.auth_profile_id" @update:url="form.url = $event" />
      </div>
      <label v-else class="field field-full"><span>页面地址 <b>*</b></span><input v-model="form.url" type="url" required placeholder="https://example.com/dashboard" /></label>
      <div v-if="templateSelected" class="field report-section-field">
        <div class="field-title-line">
          <span>模板章节 <b>*</b></span>
        </div>
        <template v-if="sections.length">
          <select v-model="selectedTemplateSection" class="section-select" @change="sectionError = ''">
            <option value="" disabled>请选择模板章节</option>
            <option v-for="section in sections" :key="section" :value="section">{{ section }}</option>
          </select>
          <small>章节标题来自当前 Word 模板。</small>
        </template>
        <small v-else class="schedule-warning">当前模板没有可选章节，请在任务设置中更换模板。</small>
        <small v-if="sectionError" class="schedule-warning" role="alert">{{ sectionError }}</small>
      </div>
      <label v-if="form.item_type === 'web'" class="field"><span>网站连接</span><select v-model="form.auth_profile_id"><option value="">无需登录</option><option v-for="profile in authProfiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select><small>每个页面可选择不同账号。</small></label>
    </div><div class="item-page-check-actions"><ActionButton type="button" :disabled="checkingPage || !form.url" :aria-busy="checkingPage" @click="checkPage" variant="primary" :loading="checkingPage">验证页面</ActionButton></div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>截图方式</h2></div></div><div class="form-grid">
      <label class="switch-row field-full"><input v-model="form.real_browser_capture" type="checkbox" /><span><strong>完整浏览器截图</strong><small>包含标签页、地址栏和窗口按钮。</small></span></label>
      <label v-if="!form.real_browser_capture" class="field"><span>网页截图范围</span><select v-model="form.capture_mode"><option v-for="option in captureModes" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
      <label v-if="!form.real_browser_capture && form.capture_mode === 'selector'" class="field"><span>元素选择器 <b>*</b></span><input v-model="form.css_selector" required placeholder="#chart-panel" /></label>
      <label class="switch-row field-full"><input v-model="form.taskbar_enabled" type="checkbox" /><span><strong>显示 Windows 风格任务栏</strong><small>完整浏览器截图时，任务栏包含在图片尺寸内。</small></span></label>
      <label v-if="form.taskbar_enabled" class="field field-full"><span>底栏图片</span><select v-model="form.taskbar_template_key"><option value="">自动轮换（同一次巡检统一底栏）</option><option v-for="template in taskbarTemplates" :key="template.key" :value="template.key">{{ template.name }}{{ template.kind === 'builtin' ? '（预置）' : '' }}</option></select><small>自动轮换是默认设置：同一次巡检的截图项共用一张预置底栏，不同巡检可能切换。手动选中图片后，该截图项固定使用所选底栏。<RouterLink to="/templates?tab=taskbar">上传和管理底栏</RouterLink></small><img v-if="selectedTaskbar" :src="selectedTaskbar.image_url" :alt="selectedTaskbar.name" class="taskbar-choice-preview" /></label>
    </div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>等待与重试</h2></div></div><div class="form-grid">
      <label class="field"><span>截图前等待（秒）</span><input v-model.number="form.wait_seconds" type="number" step="0.5" min="0" required /></label>
      <label class="field"><span>失败重试次数</span><input v-model.number="form.retry_count" type="number" min="0" max="10" required /></label>
      <label class="field"><span>页面截图超时（秒）</span><input v-model.number="form.timeout_seconds" type="number" min="1" required /><small>单次截图超过此时间则重新尝试。</small></label>
      <label class="field"><span>截图总时限（秒）</span><input v-model.number="form.total_timeout_seconds" type="number" min="1" required /><small>包含登录、截图和所有重试。</small></label>
    </div></section>
    <section class="form-section"><button class="advanced-toggle" type="button" :aria-expanded="advanced" @click="advanced = !advanced">高级设置 <Minus v-if="advanced" :size="15" /><Plus v-else :size="15" /></button><div v-if="advanced" class="form-grid advanced-fields">
      <label class="field field-full"><span>等待元素</span><input v-model="form.wait_selector" placeholder="例如：.dashboard-container" /><small>页面中出现指定元素后再截图。无需设置时留空。</small></label>
      <label class="field"><span>单独设置宽度（px）</span><input v-model.number="form.browser_width" type="number" :min="watermarkRules.image_width?.min" :max="watermarkRules.image_width?.max" placeholder="沿用任务设置" /></label><label class="field"><span>单独设置高度（px）</span><input v-model.number="form.browser_height" type="number" :min="watermarkRules.image_height?.min" :max="watermarkRules.image_height?.max" placeholder="沿用任务设置" /></label>
      <label class="field"><span>排序位置</span><input v-model.number="form.sort_order" type="number" /></label>
    </div></section>
    <section class="form-section watermark-settings"><div class="form-section-head watermark-section-head"><div><h2>截图水印</h2><p>位置和排版按浏览器截图尺寸自动计算。</p></div><button class="watermark-reset" type="button" @click="restoreWatermarkDefaults">恢复默认</button></div><div class="form-grid">
      <label class="switch-row field-full"><input v-model="form.watermark_enabled" type="checkbox" /><strong>启用截屏水印</strong></label>
      <template v-if="form.watermark_enabled">
        <label class="field field-full"><span>姓名／标识</span><input v-model="form.watermark_text" type="text" :maxlength="watermarkRules.text?.max_length" placeholder="例如：王" /><small>显示在时间上方；时间会在截图时自动更新。</small></label>
        <label class="field"><span>透明度</span><input v-model.number="form.watermark_opacity" type="number" :min="watermarkRules.opacity?.min" :max="watermarkRules.opacity?.max" /><small>默认 {{ watermarkDefaults.watermark_opacity }}；数值越大，水印越明显。</small></label>
        <label class="field"><span>倾斜角度微调</span><input v-model.number="watermarkAngleOffset" type="number" :min="angleOffsetMin" :max="angleOffsetMax" step="1" /><small>默认 0°；在自动适配的角度上增减。</small></label>
      </template>
    </div>
    <div class="watermark-preview-layout" :style="{ width: previewCanvasWidth }">
      <div class="watermark-preview-heading"><strong>效果预览</strong><span>{{ imageWidth }} × {{ imageHeight }}{{ previewBusy && previewImage ? ' · 更新中…' : '' }}</span></div>
      <div class="watermark-preview-frame" :style="{ aspectRatio: previewAspectRatio }">
        <img v-if="previewImage && form.watermark_enabled" :src="previewImage" alt="当前水印效果预览" class="watermark-preview-image" />
        <div v-if="previewBusy && previewImage" class="watermark-preview-updating">更新预览…</div>
        <div v-if="!form.watermark_enabled" class="watermark-preview-message">水印已关闭</div>
        <div v-else-if="previewBusy && !previewImage" class="watermark-preview-message">正在生成预览…</div>
      </div>
      <p v-if="previewError" class="watermark-preview-error" role="alert">{{ previewError }}</p>
    </div>
    </section>
    <ButtonGroup class="editor-footer"><ActionButton :to="`/jobs/${jobId}`" variant="secondary">取消</ActionButton><ActionButton type="submit" :disabled="saving" variant="primary" :loading="saving">保存截图</ActionButton></ButtonGroup>
  </div><aside class="editor-aside"><div class="aside-card"><Info :size="20" /><h3>截图建议</h3><p>先保存截图项，再点击“测试”确认页面、登录和截图结果都符合预期。</p></div><div class="aside-card soft"><label class="switch-row"><input v-model="form.enabled" type="checkbox" /><strong>启用截图项</strong></label><p>关闭后，任务运行时会跳过这一项。</p></div></aside></form>
</template>

<style scoped>
.taskbar-choice-preview{display:block;width:100%;max-width:850px;height:auto;margin-top:10px;border:1px solid #dce4e8;background:#eef2f4}
.watermark-section-head{display:flex;justify-content:space-between;align-items:start;gap:16px}
.watermark-reset{border:1px solid #ccd8e3;background:#fff;color:#245780;border-radius:7px;padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.watermark-reset:hover{background:#eef6fc;border-color:#9cc6e7}
.watermark-preview-layout{max-width:100%;}
.watermark-preview-heading{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:24px 0 12px;color:#24313d}
.watermark-preview-heading strong{font-size:14px}
.watermark-preview-heading>span{font-size:12px;color:#6c7c89}
.watermark-preview-frame{position:relative;border:1px solid #d7e0e8;border-radius:8px;overflow:hidden;background:#fff;box-shadow:0 4px 16px rgba(28,52,73,.06)}
.watermark-preview-image{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;pointer-events:none}
.watermark-preview-updating{position:absolute;right:12px;bottom:12px;padding:6px 9px;border-radius:6px;background:rgba(35,49,61,.78);color:#fff;font-size:11px}
.watermark-preview-message{position:absolute;inset:0;display:grid;place-items:center;color:#667784;font-size:14px;background:rgba(255,255,255,.45)}
.watermark-preview-error{margin:10px 0 0;color:#bd3d3d;font-size:13px}
</style>
