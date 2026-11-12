<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Trash2 } from '../icons'
import { api, type Row } from '../api'
import { attempt } from '../ui'

const route = useRoute()
const router = useRouter()
const type = computed(() => String(route.params.type))
const title = computed(() => type.value === 'weekly' ? '周报' : '月报')
const loading = ref(true)
const loadFailed = ref(false)
const saving = ref(false)
const legacySchedule = ref(false)
const legacyConfirmed = ref(false)
const timeError = ref('')
const dingtalkProfiles = ref<Row[]>([])
const form = reactive<Row>({
  name: '', enabled: true, schedule_mode: 'calendar', cron_expression: '', day_of_week: '1',
  times: [{ ampm: 'am', hour: 9, minute: 0 }],
  dingtalk_enabled: false, dingtalk_profile_id: '', include_docx: true,
  include_screenshots: false, send_empty_report: false, wait_for_daily_jobs: false,
  retry_until_time: '23:50', retry_interval_minutes: 10, send_on_timeout: true,
})
const bool = (value: unknown) => value === true || value === 1 || value === '1'

onMounted(async () => {
  const [reportData, botData] = await Promise.all([
    attempt(() => api<Row>('/periodic-reports')),
    attempt(() => api<Row>('/dingtalk-profiles')),
  ])
  dingtalkProfiles.value = reportData?.dingtalk_profiles?.length ? reportData.dingtalk_profiles : (botData?.profiles || [])
  const setting = (reportData?.settings || []).find((row: Row) => row.report_type === type.value)
  if (!setting) {
    loadFailed.value = true
    loading.value = false
    return
  }
  if (setting) {
    Object.assign(form, setting)
    legacySchedule.value = setting.schedule_mode === 'cron'
    form.schedule_mode = 'calendar'
    form.dingtalk_profile_id = setting.dingtalk_profile_id || ''
    for (const key of ['enabled', 'dingtalk_enabled', 'include_docx', 'include_screenshots', 'send_empty_report', 'wait_for_daily_jobs', 'send_on_timeout']) form[key] = bool(form[key])
    try {
      const config = JSON.parse(setting.schedule_config || '{}')
      form.day_of_week = String(config.day_of_week || '1')
      if (Array.isArray(config.times) && config.times.length) form.times = config.times
    } catch { /* use current defaults */ }
  }
  loading.value = false
})
function addTime() {
  if (form.times.length < 4) {
    const options = [{ ampm: 'am', hour: 9 }, { ampm: 'pm', hour: 3 }, { ampm: 'pm', hour: 6 }, { ampm: 'am', hour: 6 }]
    const candidate = options.find(value => !form.times.some((entry: Row) => entry.ampm === value.ampm && Number(entry.hour) === value.hour && Number(entry.minute) === 0)) || options[0]
    form.times.push({ ...candidate, minute: 0 })
  }
}
async function save() {
  if (legacySchedule.value && !legacyConfirmed.value) return
  const keys = form.times.map((entry: Row) => `${entry.ampm}:${entry.hour}:${entry.minute}`)
  if (new Set(keys).size !== keys.length) { timeError.value = '生成时间不能重复'; return }
  timeError.value = ''
  saving.value = true
  const result = await attempt(() => api(`/periodic-reports/${type.value}`, 'PUT', {
    ...form,
    enable_email: false,
    mail_profile_id: null,
    recipients_override: '',
    dingtalk_profile_id: form.dingtalk_profile_id || null,
  }), `${title.value}计划已保存`)
  saving.value = false
  if (result) router.push('/periodic')
}
</script>

<template>
  <div class="editor-top"><RouterLink to="/periodic" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
  <section class="page-intro editor-intro"><div><h1>编辑{{ title }}计划</h1><p>设置生成时间、报告内容与通知方式。</p></div></section>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 3" :key="n"></div></div>
  <div v-else-if="loadFailed" class="empty-state"><h3>计划加载失败</h3><p>请返回周期报告，确认计划仍然存在后重试。</p><ActionButton to="/periodic"  variant="secondary">返回</ActionButton></div>
  <form v-else class="editor-layout" @submit.prevent="save"><div class="editor-main">
    <section class="form-section"><div class="form-section-head"><div><h2>基本信息</h2></div></div><div class="form-grid"><label class="field field-full"><span>计划名称 <b>*</b></span><input v-model="form.name" required placeholder="例如：每周巡检报告" /></label><label class="switch-row field-full"><input v-model="form.enabled" type="checkbox" /><strong>启用定时生成</strong></label></div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>生成时间</h2><p>{{ type === 'monthly' ? '月报在每月最后一天生成。' : '周报按所选星期生成。' }}</p></div></div><div class="form-grid">
      <label v-if="legacySchedule" class="field field-full"><span>原定时规则</span><small>此计划使用旧规则。请核对下面的时间，确认后再保存。</small><label class="switch-row"><input v-model="legacyConfirmed" type="checkbox" />我已核对新的生成时间</label></label>
      <label v-if="type === 'weekly'" class="field field-full"><span>执行星期</span><select v-model="form.day_of_week"><option v-for="day in 7" :key="day" :value="String(day)">星期{{ ['一','二','三','四','五','六','日'][day - 1] }}</option></select></label><div class="field-full time-list"><div v-for="(time, index) in form.times" :key="index" class="time-line"><select v-model="time.ampm" aria-label="上午或下午"><option value="am">上午</option><option value="pm">下午</option></select><select v-model.number="time.hour" aria-label="小时"><option v-for="hour in 12" :key="hour" :value="hour">{{ hour }} 点</option></select><select v-model.number="time.minute" aria-label="分钟"><option v-for="minute in 60" :key="minute" :value="minute - 1">{{ String(minute - 1).padStart(2, '0') }} 分</option></select><button v-if="form.times.length > 1" type="button" class="icon-button danger-hover" aria-label="删除时间" @click="form.times.splice(index, 1)"><Trash2 :size="16" /></button></div><ActionButton v-if="form.times.length < 4" type="button"  class="btn-small" @click="addTime" variant="secondary">添加时间</ActionButton><small v-if="timeError" class="schedule-warning" role="alert">{{ timeError }}</small></div>
    </div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>报告内容</h2></div></div><div class="form-grid"><label class="switch-row field-full"><input v-model="form.include_docx" type="checkbox" />ZIP 包含 Word 报告</label><label class="switch-row field-full"><input v-model="form.include_screenshots" type="checkbox" />ZIP 包含截图</label><label class="switch-row field-full"><input v-model="form.send_empty_report" type="checkbox" />没有报告时也生成归档包</label></div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>发送通知</h2></div></div><div class="form-grid"><label class="switch-row field-full"><input v-model="form.dingtalk_enabled" type="checkbox" />发送钉钉通知（使用通知配置）</label><template v-if="form.dingtalk_enabled && dingtalkProfiles.length"><label class="field field-full"><span>指定钉钉机器人</span><select v-model="form.dingtalk_profile_id"><option value="">默认首个机器人</option><option v-for="bot in dingtalkProfiles" :key="bot.id" :value="bot.id">{{ bot.name }}</option></select></label></template></div></section>
    <section v-if="type === 'monthly'" class="form-section"><div class="form-section-head"><div><h2>月报等待设置</h2></div></div><div class="form-grid"><label class="switch-row field-full"><input v-model="form.wait_for_daily_jobs" type="checkbox" />等待当天巡检完成后再生成</label><template v-if="form.wait_for_daily_jobs"><label class="field"><span>最晚等待时间</span><input v-model="form.retry_until_time" type="time" /></label><label class="field"><span>重试间隔（分钟）</span><input v-model.number="form.retry_interval_minutes" type="number" min="1" /></label><label class="field field-full"><span>等待超时后</span><select v-model="form.send_on_timeout"><option :value="true">生成已完成部分</option><option :value="false">标记失败</option></select></label></template></div></section>
    <ButtonGroup class="editor-footer"><ActionButton to="/periodic" variant="secondary">取消</ActionButton><ActionButton type="submit" :disabled="saving" variant="primary" :loading="saving">保存计划</ActionButton></ButtonGroup>
  </div></form>
</template>
