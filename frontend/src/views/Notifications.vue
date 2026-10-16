<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, Mail, Pencil, Trash2, ArrowLeft, Send } from '../icons'
import { api, type Row } from '../api'
import { attempt, notify, confirmModal } from '../ui'

const route = useRoute()
const router = useRouter()

// Tab 状态：'dingtalk' 或 'mail'
const activeTab = ref<'dingtalk' | 'mail'>((route.query.tab as 'dingtalk' | 'mail') || 'dingtalk')

function switchTab(tab: 'dingtalk' | 'mail') {
  activeTab.value = tab
  router.replace({ query: { ...route.query, tab } })
}

function bool(value: unknown): boolean {
  return value === true || value === 1 || value === '1'
}

function formatWebhook(url?: string): string {
  if (!url) return '未设置 Webhook'
  if (url.length <= 48) return url
  return url.slice(0, 36) + '...' + url.slice(-8)
}

// ================= 钉钉通知状态 =================
const dingtalkProfiles = ref<Row[]>([])
const editingDingtalk = ref(false)
const currentDingtalkId = ref<number | null>(null)
const savingDingtalk = ref(false)
const testingDingtalkId = ref<number | null>(null)
const hasDingtalkSecret = ref(false)
const dingtalkForm = reactive<Row>({
  name: '',
  webhook: '',
  secret: '',
  keyword: '',
  emoji_enabled: true,
})

async function loadDingtalkProfiles() {
  const result = await attempt(() => api<Row>('/dingtalk-profiles'))
  dingtalkProfiles.value = result?.profiles || []
}

function startEditDingtalk(profile?: Row) {
  currentDingtalkId.value = profile?.id || null
  if (profile) {
    dingtalkForm.name = profile.name || ''
    dingtalkForm.webhook = profile.webhook || ''
    dingtalkForm.secret = ''
    dingtalkForm.keyword = profile.keyword || ''
    dingtalkForm.emoji_enabled = bool(profile.emoji_enabled)
    hasDingtalkSecret.value = Boolean(profile.has_secret)
  } else {
    dingtalkForm.name = ''
    dingtalkForm.webhook = ''
    dingtalkForm.secret = ''
    dingtalkForm.keyword = ''
    dingtalkForm.emoji_enabled = true
    hasDingtalkSecret.value = false
  }
  editingDingtalk.value = true
}

async function saveDingtalk() {
  const name = String(dingtalkForm.name || '').trim()
  const webhook = String(dingtalkForm.webhook || '').trim()
  if (!name) {
    notify('请输入机器人名称', 'error')
    return
  }
  if (!currentDingtalkId.value && !webhook) {
    notify('请输入 Webhook 地址', 'error')
    return
  }

  savingDingtalk.value = true
  const payload: Row = {
    name,
    webhook,
    keyword: String(dingtalkForm.keyword || '').trim(),
    emoji_enabled: Boolean(dingtalkForm.emoji_enabled),
  }
  const secret = String(dingtalkForm.secret || '').trim()
  if (secret) payload.secret = secret

  const path = currentDingtalkId.value ? `/dingtalk-profiles/${currentDingtalkId.value}` : '/dingtalk-profiles'
  const result = await attempt(() => api(path, currentDingtalkId.value ? 'PUT' : 'POST', payload), '钉钉机器人已保存')
  savingDingtalk.value = false
  if (result) {
    editingDingtalk.value = false
    loadDingtalkProfiles()
  }
}

async function removeDingtalk(profile: Row) {
  const ok = await confirmModal({
    title: '确认删除钉钉机器人',
    message: `确定要删除机器人“${profile.name}”吗？此操作无法撤销。`,
    confirmText: '确认删除',
    danger: true,
  })
  if (!ok) return
  if (await attempt(() => api(`/dingtalk-profiles/${profile.id}`, 'DELETE'), '钉钉机器人已删除')) {
    loadDingtalkProfiles()
  }
}

async function testDingtalk(profile: Row) {
  testingDingtalkId.value = profile.id
  const result = await attempt(() => api<Row>(`/dingtalk-profiles/${profile.id}/test`, 'POST'))
  testingDingtalkId.value = null
  if (result?.ok) {
    notify(result.message || '测试消息已成功发送至钉钉群！')
  } else if (result?.error) {
    notify(result.error, 'error')
  }
}

// ================= 邮件通知状态 =================
const mailProfiles = ref<Row[]>([])
const editingMail = ref(false)
const currentMailId = ref<number | null>(null)
const savingMail = ref(false)
const testingMailId = ref<number | null>(null)
const mailForm = reactive<Row>({})

async function loadMailProfiles() {
  const result = await attempt(() => api<Row>('/mail-profiles'))
  mailProfiles.value = result?.profiles || []
}

function startEditMail(profile?: Row) {
  currentMailId.value = profile?.id || null
  for (const key of Object.keys(mailForm)) delete mailForm[key]
  if (profile) {
    Object.assign(mailForm, JSON.parse(JSON.stringify(profile)))
    mailForm.use_ssl = bool(mailForm.use_ssl)
    mailForm.use_starttls = bool(mailForm.use_starttls)
  } else {
    Object.assign(mailForm, {
      name: '',
      smtp_host: '',
      smtp_port: 465,
      use_ssl: true,
      use_starttls: false,
      username: '',
      password: '',
      password_source: 'plain',
      sender: '',
      recipients: '',
      cc: '',
      subject_template: '自动化巡检报告 - {date}',
      body_template: '巡检报告已生成，请查看附件。',
    })
  }
  mailForm.password = ''
  editingMail.value = true
}

async function saveMail() {
  savingMail.value = true
  const path = currentMailId.value ? `/mail-profiles/${currentMailId.value}` : '/mail-profiles'
  const result = await attempt(() => api(path, currentMailId.value ? 'PUT' : 'POST', { ...mailForm }), '邮件配置已保存')
  savingMail.value = false
  if (result) {
    editingMail.value = false
    loadMailProfiles()
  }
}

async function removeMail(profile: Row) {
  const ok = await confirmModal({
    title: '确认删除邮件配置',
    message: `确定要删除邮件配置“${profile.name}”吗？此操作无法撤销。`,
    confirmText: '确认删除',
    danger: true,
  })
  if (!ok) return
  if (await attempt(() => api(`/mail-profiles/${profile.id}`, 'DELETE'), '邮件配置已删除')) {
    loadMailProfiles()
  }
}

async function testMail(profile: Row) {
  testingMailId.value = profile.id
  const result = await attempt(() => api<Row>(`/mail-profiles/${profile.id}/test`, 'POST'))
  testingMailId.value = null
  if (result?.ok) {
    notify(result.message || '测试邮件已发送！')
  } else if (result?.error) {
    notify(result.error, 'error')
  }
}

onMounted(() => {
  loadDingtalkProfiles()
  loadMailProfiles()
})
</script>

<template>
  <section class="page-intro">
    <div><h1>通知配置</h1></div>
  </section>

  <!-- 顶部 Tab 切换 -->
  <div class="settings-tabs">
    <a href="javascript:void(0)" :class="{ active: activeTab === 'dingtalk' }" @click="switchTab('dingtalk')">
      <Bell :size="16" style="vertical-align: middle; margin-right: 4px;" />钉钉通知
    </a>
    <a href="javascript:void(0)" :class="{ active: activeTab === 'mail' }" @click="switchTab('mail')">
      <Mail :size="16" style="vertical-align: middle; margin-right: 4px;" />邮件通知
    </a>
  </div>

  <!-- Tab 1: 钉钉通知 -->
  <div v-if="activeTab === 'dingtalk'" class="settings-section">
    <!-- 机器人列表视图 -->
    <template v-if="!editingDingtalk">
      <div class="section-heading" style="justify-content: flex-end;">
        <ButtonGroup class="page-actions">
          <ActionButton @click="startEditDingtalk()" variant="primary">新建通知</ActionButton>
        </ButtonGroup>
      </div>
      <div v-if="!dingtalkProfiles.length" class="empty-state">
        <span class="empty-icon"><Bell :size="30" /></span>
        <h3>暂无钉钉通知机器人</h3>
        <p>在钉钉群中添加自定义机器人后，在此配置 Webhook 即可接收巡检告警。</p>
        <ActionButton @click="startEditDingtalk()" variant="primary">新建通知</ActionButton>
      </div>
      <div v-else class="profile-list">
        <article v-for="bot in dingtalkProfiles" :key="bot.id" class="profile-row">
          <div class="profile-symbol"><Bell :size="20" /></div>
          <div class="profile-content">
            <h3>{{ bot.name }}</h3>
            <p>{{ formatWebhook(bot.webhook) }} · {{ bot.has_secret ? '已配置加签' : '无加签' }} · {{ bot.keyword ? '关键词: ' + bot.keyword : '无关键词' }}</p>
          </div>
          <div class="profile-actions">
            <IconButton class="table-icon-action" :disabled="testingDingtalkId === bot.id" :loading="testingDingtalkId === bot.id" :aria-label="`测试机器人 ${bot.name}`" :title="testingDingtalkId === bot.id ? '发送中' : '测试发送'" @click="testDingtalk(bot)" :label="`测试机器人 ${bot.name}`"><Send :size="16" /></IconButton>
            <IconButton class="table-icon-action" :aria-label="`编辑配置 ${bot.name}`" title="编辑配置" @click="startEditDingtalk(bot)" :label="`编辑配置 ${bot.name}`"><Pencil :size="16" /></IconButton>
            <IconButton class="table-icon-action danger-hover" :aria-label="`删除配置 ${bot.name}`" title="删除配置" @click="removeDingtalk(bot)" :label="`删除配置 ${bot.name}`"><Trash2 :size="16" /></IconButton>
          </div>
        </article>
      </div>
    </template>

    <!-- 机器人表单编辑视图 -->
    <template v-else>
      <div class="editor-top">
        <button class="back-link back-button" @click="editingDingtalk = false"><ArrowLeft :size="16" />返回</button>
      </div>
      <section class="page-intro editor-intro">
        <div><h1>{{ currentDingtalkId ? '编辑钉钉机器人' : '新增钉钉机器人' }}</h1></div>
      </section>
      <form class="editor-layout" @submit.prevent="saveDingtalk">
        <div class="editor-main">
          <section class="form-section">
            <div class="form-section-head">
              <div><h2>机器人基本信息</h2></div>
            </div>
            <div class="form-grid">
              <label class="field field-full">
                <span>机器人名称 <b>*</b></span>
                <input v-model="dingtalkForm.name" required placeholder="例如：运维巡检告警群" />
              </label>
              <label class="field field-full">
                <span>机器人 Webhook <b>*</b></span>
                <input v-model="dingtalkForm.webhook" required placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
              </label>
              <label class="field">
                <span>加签安全密钥</span>
                <input v-model="dingtalkForm.secret" type="password" :placeholder="hasDingtalkSecret ? '已配置加签密钥，留空则保持不变' : '请输入加签密钥（可选）'" />
              </label>
              <label class="field">
                <span>自定义关键词</span>
                <input v-model="dingtalkForm.keyword" placeholder="请输入自定义关键词（可选）" />
              </label>
              <label class="switch-row field-full">
                <input v-model="dingtalkForm.emoji_enabled" type="checkbox" />
                <span>使用状态表情符号</span>
              </label>
            </div>
          </section>
          <ButtonGroup class="editor-footer">
            <ActionButton type="button"  @click="editingDingtalk = false" variant="secondary">取消</ActionButton>
            <ActionButton type="submit" :disabled="savingDingtalk" variant="primary" :loading="savingDingtalk">
              保存配置
            </ActionButton>
          </ButtonGroup>
        </div>
      </form>
    </template>
  </div>

  <!-- Tab 2: 邮件通知 -->
  <div v-else class="settings-section">
    <!-- 邮件列表视图 -->
    <template v-if="!editingMail">
      <div class="section-heading" style="justify-content: flex-end;">
        <ButtonGroup class="page-actions">
          <ActionButton @click="startEditMail()" variant="primary">新建通知</ActionButton>
        </ButtonGroup>
      </div>
      <div v-if="!mailProfiles.length" class="empty-state">
        <span class="empty-icon"><Mail :size="30" /></span>
        <h3>暂无邮件通知配置</h3>
        <p>需要邮件通知或发送巡检附件时，请在此添加 SMTP 邮箱配置。</p>
        <ActionButton @click="startEditMail()" variant="primary">新建通知</ActionButton>
      </div>
      <div v-else class="profile-list">
        <article v-for="profile in mailProfiles" :key="profile.id" class="profile-row">
          <div class="profile-symbol"><Mail :size="20" /></div>
          <div class="profile-content">
            <h3>{{ profile.name }}</h3>
            <p>{{ profile.smtp_host || '未设置服务器' }} · {{ profile.sender || profile.username || '未设置发件人' }}</p>
          </div>
          <div class="profile-actions">
            <IconButton class="table-icon-action" :disabled="testingMailId === profile.id" :loading="testingMailId === profile.id" :aria-label="`测试邮箱 ${profile.name}`" :title="testingMailId === profile.id ? '发送中' : '测试发送'" @click="testMail(profile)" :label="`测试邮箱 ${profile.name}`"><Send :size="16" /></IconButton>
            <IconButton class="table-icon-action" :aria-label="`编辑配置 ${profile.name}`" title="编辑配置" @click="startEditMail(profile)" :label="`编辑配置 ${profile.name}`"><Pencil :size="16" /></IconButton>
            <IconButton class="table-icon-action danger-hover" :aria-label="`删除配置 ${profile.name}`" title="删除配置" @click="removeMail(profile)" :label="`删除配置 ${profile.name}`"><Trash2 :size="16" /></IconButton>
          </div>
        </article>
      </div>
    </template>

    <!-- 邮件表单编辑视图 -->
    <template v-else>
      <div class="editor-top">
        <button class="back-link back-button" @click="editingMail = false"><ArrowLeft :size="16" />返回</button>
      </div>
      <section class="page-intro editor-intro">
        <div><h1>{{ currentMailId ? '编辑邮件配置' : '新增邮件配置' }}</h1></div>
      </section>
      <form class="editor-layout" @submit.prevent="saveMail">
        <div class="editor-main">
          <section class="form-section">
            <div class="form-section-head">
              <div><h2>SMTP 服务器信息</h2></div>
            </div>
            <div class="form-grid">
              <label class="field"><span>配置名称 <b>*</b></span><input v-model="mailForm.name" required placeholder="例如：日常报告发送邮箱" /></label>
              <label class="field"><span>SMTP 服务器 <b>*</b></span><input v-model="mailForm.smtp_host" required placeholder="smtp.example.com" /></label>
              <label class="field"><span>端口</span><input v-model.number="mailForm.smtp_port" type="number" min="1" max="65535" required /></label>
              <label class="field"><span>登录账号</span><input v-model="mailForm.username" placeholder="发信账号用户名" /></label>
              <label class="field"><span>密码或授权码</span><input v-model="mailForm.password" type="password" :placeholder="currentMailId ? '留空则保持原密码' : '请输入密码或授权码'" /></label>
              <label class="field"><span>发件人邮箱</span><input v-model="mailForm.sender" type="email" placeholder="report@example.com" /></label>
              <label class="field"><span>默认收件人</span><input v-model="mailForm.recipients" placeholder="多个地址用英文逗号分隔" /></label>
              <div class="field-full option-row">
                <label class="switch-row"><input v-model="mailForm.use_ssl" type="checkbox" />SSL 加密</label>
                <label class="switch-row"><input v-model="mailForm.use_starttls" type="checkbox" />STARTTLS 加密</label>
              </div>
            </div>
          </section>
          <section class="form-section">
            <div class="form-section-head">
              <div><h2>默认邮件模版</h2></div>
            </div>
            <div class="form-grid">
              <label class="field field-full"><span>邮件标题模版</span><input v-model="mailForm.subject_template" /><small>{date} 会自动替换为巡检日期。</small></label>
              <label class="field field-full"><span>邮件正文模版</span><textarea v-model="mailForm.body_template" rows="4" /></label>
              <label class="field field-full"><span>默认抄送人</span><input v-model="mailForm.cc" placeholder="多个地址用英文逗号分隔" /></label>
            </div>
          </section>
          <ButtonGroup class="editor-footer">
            <ActionButton type="button"  @click="editingMail = false" variant="secondary">取消</ActionButton>
            <ActionButton type="submit" :disabled="savingMail" variant="primary" :loading="savingMail">
              保存配置
            </ActionButton>
          </ButtonGroup>
        </div>
      </form>
    </template>
  </div>
</template>
