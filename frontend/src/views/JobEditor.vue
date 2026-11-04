<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, computed, toRaw } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Info, Plus, Trash2 } from '../icons'
import { api, type Row } from '../api'
import { attempt } from '../ui'
import ScheduleTimePicker from '../components/ScheduleTimePicker.vue'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id || 0))
const saving = ref(false)
const loading = ref(true)
const loadFailed = ref(false)
const reportTemplates = ref<Row[]>([])
const existingItems = ref<Row[]>([])
const dingtalkProfiles = ref<Row[]>([])
const calendarAvailable = ref(false)
const legacySchedule = ref(false)
const legacyConfirmed = ref(false)
const lastFixedTimes = ref<string[]>([])
let statusTimer: ReturnType<typeof setInterval> | undefined
let disposed = false
const form = reactive<Row>({})
const jobDefaults = ref<Row>({})
const imageRules = ref<Row>({})
const jobFrequencies = ref<Row[]>([])
const browserScaleFactors = ref<Row[]>([])

function bool(value: unknown): boolean { return value === true || value === 1 || value === '1' }
const mismatchedSections = computed(() => {
  const selected = reportTemplates.value.find(row => Number(row.id) === Number(form.template_id))
  if (!selected) return []
  const normalize = (value: unknown) => String(value || '').replace(/[\s\u3000:：，,。._-]+/g, '').toLowerCase()
  const allowed = new Set((selected.sections || []).map(normalize))
  return [...new Set(existingItems.value.map(item => String(item.section || '')).filter(section => !allowed.has(normalize(section))))]
})
async function load() {
  const [config, calendarStatus, dtRes, templateRes] = await Promise.all([attempt(() => api<Row>('/ui-config')), attempt(() => api<Row>('/calendar-status')), attempt(() => api<Row>('/dingtalk-profiles')), attempt(() => api<Row>('/report-templates'))])
  if (disposed) return
  if (!config?.job_defaults || !config?.watermark_rules || !Array.isArray(config.job_frequencies) || !Array.isArray(config.browser_scale_factors)) {
    loadFailed.value = true
    loading.value = false
    return
  }
  jobDefaults.value = config.job_defaults
  imageRules.value = config.watermark_rules
  jobFrequencies.value = config.job_frequencies
  browserScaleFactors.value = config.browser_scale_factors
  Object.assign(form, structuredClone(toRaw(jobDefaults.value)))
  dingtalkProfiles.value = dtRes?.profiles || []
  reportTemplates.value = templateRes?.templates || []
  calendarAvailable.value = Boolean(calendarStatus?.available)
  statusTimer = setInterval(async () => {
    try { calendarAvailable.value = Boolean((await api<Row>('/calendar-status')).available) } catch { calendarAvailable.value = false }
  }, 60_000)
  if (!id.value) {
    loading.value = false
    return
  }
  const result = await attempt(() => api<Row>(`/jobs/${id.value}`))
  if (disposed) return
  if (!result?.job) {
    loadFailed.value = true
    loading.value = false
    return
  }
  if (result.job) {
    Object.assign(form, result.job)
    existingItems.value = result.items || []
    form.dingtalk_profile_id = result.job.dingtalk_profile_id ?? null
    for (const key of ['enabled', 'dingtalk_enabled', 'send_dingtalk_on_complete', 'send_dingtalk_on_error']) form[key] = form[key] === undefined ? true : bool(form[key])
    let schedule: Row = {}
    try { schedule = JSON.parse(result.job.schedule_config || '{}') } catch { /* use defaults */ }
    legacySchedule.value = result.job.schedule_mode === 'cron'
    form.schedule_mode = 'calendar'
    if (result.job.schedule_mode === 'calendar') {
      form.frequency = schedule.frequency || jobDefaults.value.frequency
      form.days_of_week = schedule.days_of_week || [...jobDefaults.value.days_of_week]
      form.days_of_month = schedule.days_of_month || [...jobDefaults.value.days_of_month]
      form.times = schedule.times || [...jobDefaults.value.times]
    } else {
      if (schedule.frequency && !['daily', 'workday'].includes(schedule.frequency)) legacySchedule.value = true
      if (String(schedule.morning_time || '').includes(':R') || String(schedule.afternoon_time || '').includes(':R')) legacySchedule.value = true
      form.frequency = schedule.frequency === 'workday' ? 'weekdays' : jobDefaults.value.frequency
      const oldTimes = []
      if (schedule.morning_enabled) oldTimes.push(String(schedule.morning_time || jobDefaults.value.morning_time).toUpperCase())
      if (schedule.afternoon_enabled) oldTimes.push(String(schedule.afternoon_time || jobDefaults.value.afternoon_time).toUpperCase())
      if (oldTimes.length) form.times = oldTimes
    }
    form.morning_enabled = schedule.morning_enabled === undefined ? true : bool(schedule.morning_enabled)
    form.afternoon_enabled = schedule.afternoon_enabled === undefined ? true : bool(schedule.afternoon_enabled)
    form.morning_random = String(schedule.morning_time || '').toUpperCase().endsWith(':R')
    form.afternoon_random = String(schedule.afternoon_time || '').toUpperCase().endsWith(':R')
    form.morning_time = form.morning_random ? `${String(schedule.morning_time).slice(0, 2)}:05` : schedule.morning_time || jobDefaults.value.morning_time
    form.afternoon_time = form.afternoon_random ? `${String(schedule.afternoon_time).slice(0, 2)}:05` : schedule.afternoon_time || jobDefaults.value.afternoon_time
  }
  loading.value = false
}
onMounted(() => { void load().catch(error => {
  if (disposed) return
  console.error('任务编辑页加载失败', error)
  loadFailed.value = true
  loading.value = false
}) })
onUnmounted(() => { disposed = true; if (statusTimer) clearInterval(statusTimer) })
async function save() {
  if (!form.name.trim()) return
  if (legacySchedule.value && !legacyConfirmed.value) return
  saving.value = true
  const payload = {
    ...form,
    mail_profile_id: null,
    send_mail: false,
    send_mail_on_complete: false,
    send_mail_on_error: false,
    dingtalk_enabled: form.dingtalk_enabled,
    dingtalk_profile_id: form.dingtalk_enabled ? (form.dingtalk_profile_id || null) : null,
    send_dingtalk_on_complete: form.send_dingtalk_on_complete,
    send_dingtalk_on_error: form.send_dingtalk_on_error,
  }
  const result = await attempt(() => api<Row>(id.value ? `/jobs/${id.value}` : '/jobs', id.value ? 'PUT' : 'POST', payload), id.value ? '任务已更新' : '任务已创建')
  saving.value = false
  if (result?.job?.id) router.push(`/jobs/${result.job.id}`)
}
function addTime() { if (form.times.length < 8) { const time = String(jobDefaults.value.times?.[0] || ''); form.times.push(time); lastFixedTimes.value.push(time) } }
function isRandom(value: string) { return value.endsWith(':R') }
function toggleRandom(index: number, enable: boolean) {
  const current = String(form.times[index] || '09:05')
  if (isRandom(current) === enable) return
  const hour = current.slice(0, 2) || '09'
  if (enable) {
    if (!isRandom(current)) lastFixedTimes.value[index] = current
    form.times[index] = `${hour}:R`
  } else {
    const previous = lastFixedTimes.value[index]
    form.times[index] = previous?.startsWith(`${hour}:`) ? previous : `${hour}:00`
  }
}
function removeTime(index: number) { form.times.splice(index, 1); lastFixedTimes.value.splice(index, 1) }
</script>

<template>
  <div class="editor-top"><RouterLink :to="id ? `/jobs/${id}` : '/jobs'" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
  <section class="page-intro editor-intro"><div><h1>{{ id ? '编辑巡检任务' : '创建巡检任务' }}</h1></div></section>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 4" :key="n"></div></div>
  <div v-else-if="loadFailed" class="empty-state"><h3>任务加载失败</h3><p>请返回任务列表，确认任务仍然存在后重试。</p><ActionButton to="/jobs"  variant="secondary">返回</ActionButton></div>
  <form v-else class="editor-layout" @submit.prevent="save">
    <div class="editor-main">
      <section class="form-section"><div class="form-section-head"><div><h2>基本信息</h2></div></div><div class="form-grid">
        <label class="field"><span>任务名称 <b>*</b></span><input v-model="form.name" required maxlength="100" placeholder="例如：生产环境每日巡检" /></label>
        <label class="field"><span>运行环境</span><input v-model="form.environment" placeholder="例如：生产环境" /></label>
      </div></section>
      <section class="form-section"><div class="form-section-head"><div><h2>巡检时间</h2></div></div><div class="form-grid">
        <label v-if="legacySchedule" class="field field-full"><span>原定时规则</span><small>此任务使用旧规则。请核对下面的日期和时间，确认后再保存。</small><label class="switch-row"><input v-model="legacyConfirmed" type="checkbox" />我已核对新的巡检时间</label></label>
        <label class="field field-full"><span>巡检日期</span><select v-model="form.frequency"><option v-for="option in jobFrequencies.filter(option => !option.requires_calendar || calendarAvailable || form.frequency === option.value)" :key="option.value" :value="option.value">{{ option.label }}</option></select><small v-if="!calendarAvailable">节假日接口暂不可用，工作日和非工作日选项已隐藏。</small></label>
        <div v-if="form.frequency === 'weekly'" class="field field-full"><span>选择星期</span><div class="weekday-options"><label v-for="day in 7" :key="day" class="switch-row"><input v-model="form.days_of_week" type="checkbox" :value="day" />周{{ '一二三四五六日'[day - 1] }}</label></div></div>
        <div v-if="form.frequency === 'monthly'" class="field field-full"><span>选择日期（可多选）</span><div class="monthday-options"><label v-for="day in 31" :key="day" class="switch-row"><input v-model="form.days_of_month" type="checkbox" :value="day" />{{ day }}号</label></div><small>当月没有所选日期时，该日期自动跳过。例如 2 月没有 30 号。</small></div>
        <div class="field field-full inspection-schedule">
          <div class="inspection-time-heading">
            <div><span>执行时间</span><small>可设置多个时段，最多 8 个</small></div>
            <button v-if="form.times.length < 8" type="button" class="inspection-time-add" @click="addTime"><Plus :size="15" />添加时间</button>
          </div>
          <div class="inspection-time-list">
            <div v-for="(value, index) in form.times" :key="index" class="inspection-time-row" :class="{ 'is-random': isRandom(value) }">
              <div class="inspection-time-top">
                <span class="inspection-time-index">时间 {{ String(index + 1).padStart(2, '0') }}</span>
                <button v-if="form.times.length > 1" type="button" class="inspection-time-remove" :aria-label="`删除第 ${index + 1} 个时间`" @click="removeTime(index)"><Trash2 :size="16" /></button>
              </div>
              <div class="inspection-time-value">
                <span class="inspection-time-caption">{{ isRandom(value) ? '执行小时' : '执行时间' }}</span>
                <div class="inspection-time-control">
                  <ScheduleTimePicker v-model="form.times[index]" :label="`第 ${index + 1} 个执行时间`" />
                  <span v-if="isRandom(value)" class="inspection-minute-range">每天在这一小时内随机执行</span>
                </div>
              </div>
              <div class="inspection-time-mode-line">
                <span>分钟方式</span>
                <div class="inspection-time-mode" role="group" :aria-label="`第 ${index + 1} 个时间的分钟方式`">
                  <button type="button" :class="{ selected: !isRandom(value) }" :aria-pressed="!isRandom(value)" @click="toggleRandom(index, false)">固定分钟</button>
                  <button type="button" :class="{ selected: isRandom(value) }" :aria-pressed="isRandom(value)" @click="toggleRandom(index, true)">随机分钟</button>
                </div>
              </div>
            </div>
          </div>
          <small class="inspection-time-help">随机分钟由系统为每天确定，具体时间可在任务详情查看“未来三次”。</small>
        </div>
      </div></section>
      <section class="form-section"><div class="form-section-head"><div><h2>报告与通知</h2></div></div><div class="form-grid">
        <label class="field field-full"><span>报告标题 <b>*</b></span><input v-model="form.report_title" required placeholder="例如：自动化巡检报告" /></label>
        <label class="field field-full"><span>Word 报告模板</span><select v-model="form.template_id"><option :value="null">自动排版</option><option v-for="template in reportTemplates" :key="template.id" :value="template.id">{{ template.name }}</option></select><small><RouterLink to="/templates?tab=report">上传和管理模板</RouterLink>。未选择时，系统自动排版。</small></label>
        <p v-if="mismatchedSections.length" class="schedule-warning field-full">当前截图项的章节“{{ mismatchedSections.join('、') }}”不在所选模板中。保存后请修改这些截图项，否则生成报告会失败。</p>

        <div class="field-full">
          <label class="switch-row"><input v-model="form.dingtalk_enabled" type="checkbox" /><strong>发送钉钉通知</strong></label>
          <div v-if="form.dingtalk_enabled" class="notification-options">
            <label class="field">
              <span>指定钉钉机器人</span>
              <select v-model="form.dingtalk_profile_id">
                <option :value="null">默认机器人（{{ dingtalkProfiles[0]?.name }}）</option>
                <option v-for="bot in dingtalkProfiles" :key="bot.id" :value="bot.id">{{ bot.name }}</option>
              </select>
            </label>
            <p v-if="!dingtalkProfiles.length" class="schedule-warning" style="margin-top: 6px;">系统暂无可用钉钉机器人，请前往“通知配置”添加。</p>
            <div class="option-row" style="margin-top: 12px;">
              <label class="switch-row"><input v-model="form.send_dingtalk_on_complete" type="checkbox" />巡检成功后发送</label>
              <label class="switch-row"><input v-model="form.send_dingtalk_on_error" type="checkbox" />巡检异常时发送</label>
            </div>
          </div>
        </div>
      </div></section>
      <section class="form-section"><div class="form-section-head"><div><h2>浏览器设置</h2></div></div><div class="form-grid">
        <label class="field"><span>截图宽度（px）</span><input v-model.number="form.browser_width" type="number" :min="imageRules.image_width?.min" :max="imageRules.image_width?.max" required /></label>
        <label class="field"><span>截图高度（px）</span><input v-model.number="form.browser_height" type="number" :min="imageRules.image_height?.min" :max="imageRules.image_height?.max" required /><small>完整浏览器截图包含底部任务栏。</small></label>
        <label class="field"><span>显示缩放</span><select v-model.number="form.browser_scale_factor"><option v-for="option in browserScaleFactors" :key="option.value" :value="option.value">{{ option.label }}</option></select><small>只改变页面显示大小，不改变图片尺寸。</small></label>
      </div></section>
      <ButtonGroup class="editor-footer"><ActionButton :to="id ? `/jobs/${id}` : '/jobs'" variant="secondary">取消</ActionButton><ActionButton type="submit" :disabled="saving" variant="primary" :loading="saving">{{ id ? '保存任务' : '创建任务' }}</ActionButton></ButtonGroup>
    </div>
    <aside class="editor-aside"><div class="aside-card"><Info :size="20" /><h3>配置提示</h3><p>创建任务后，再添加要截图的页面。每个页面可以单独设置等待时间、重试次数和水印。</p></div><div class="aside-card soft"><label class="switch-row"><input v-model="form.enabled" type="checkbox" /><strong>启用定时巡检</strong></label><p>关闭后仍可手动运行任务。</p></div></aside>
  </form>
</template>
