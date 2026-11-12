<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { KeyRound, Pencil, Trash2, ShieldCheck, ArrowLeft } from '../icons'
import { api, type Row } from '../api'
import { attempt, confirmModal, notify } from '../ui'
import PasswordInput from '../components/PasswordInput.vue'

const profiles = ref<Row[]>([])
const editing = ref(false)
const currentId = ref<number | null>(null)
const saving = ref(false)
const form = reactive<Row>({})

async function load() {
  const result = await attempt(() => api<Row>('/auth-profiles'))
  profiles.value = result?.profiles || []
}

function start(profile?: Row) {
  currentId.value = profile?.id || null
  for (const key of Object.keys(form)) delete form[key]
  if (profile) {
    Object.assign(form, JSON.parse(JSON.stringify(profile)))
  } else {
    Object.assign(form, {
      name: '', auth_type: 'form', login_url: '', username_value: '', password: '',
      username_source: 'plain', password_source: 'plain',
      username_selector: 'input[name="user"], input[name="username"], input[type="email"], input[placeholder*="username"], input[placeholder*="email"], input[placeholder*="user"]',
      password_selector: 'input[name="password"], input[type="password"], input[placeholder*="password"]',
      submit_selector: 'button[type="submit"], button:has-text("Log in"), button:has-text("Login"), button:has-text("登录"), input[type="submit"]',
      success_selector: '.react-grid-layout',
    })
  }
  form.password = ''
  editing.value = true
}

async function save() {
  saving.value = true
  const path = currentId.value ? `/auth-profiles/${currentId.value}` : '/auth-profiles'
  const result = await attempt(() => api(path, currentId.value ? 'PUT' : 'POST', { ...form }), '配置已保存')
  saving.value = false
  if (result) { editing.value = false; load() }
}

async function remove(profile: Row) {
  const ok = await confirmModal({
    title: '确认删除连接',
    message: `确定要删除网站连接“${profile.name}”吗？此操作无法撤销。`,
    confirmText: '确认删除', danger: true,
  })
  if (!ok) return
  if (await attempt(() => api(`/auth-profiles/${profile.id}`, 'DELETE'), '连接已删除')) load()
}

async function test(profile: Row) {
  const result = await attempt(() => api<Row>(`/auth-profiles/${profile.id}/test`, 'POST'))
  if (!result) return
  notify(String(result.message || (result.success ? '登录测试成功' : '登录测试失败')), result.success ? 'success' : 'error')
}

onMounted(load)
</script>

<template>
  <template v-if="!editing">
    <section class="page-intro"><div><h1>网站连接</h1></div><ButtonGroup class="page-actions"><ActionButton @click="start()" variant="primary">新建连接</ActionButton></ButtonGroup></section>
    <div class="section-heading"><div><h2>已保存的配置</h2></div><span class="count-label">{{ profiles.length }} 项</span></div>
    <div v-if="!profiles.length" class="empty-state"><span class="empty-icon"><KeyRound :size="30" /></span><h3>暂无认证配置</h3><p>需要登录的网站，可以在这里保存认证信息。</p><ActionButton @click="start()" variant="primary">新建连接</ActionButton></div>
    <div v-else class="profile-list"><article v-for="profile in profiles" :key="profile.id" class="profile-row"><div class="profile-symbol"><KeyRound :size="20" /></div><div class="profile-content"><h3>{{ profile.name }}</h3><p>{{ profile.login_url || '未设置登录地址' }}</p></div><div class="profile-actions"><IconButton class="table-icon-action" :aria-label="`测试连接 ${profile.name}`" title="测试连接" @click="test(profile)" :label="`测试连接 ${profile.name}`"><ShieldCheck :size="16" /></IconButton><IconButton class="table-icon-action" :aria-label="`编辑配置 ${profile.name}`" title="编辑配置" @click="start(profile)" :label="`编辑配置 ${profile.name}`"><Pencil :size="16" /></IconButton><IconButton class="table-icon-action danger-hover" :aria-label="`删除配置 ${profile.name}`" title="删除配置" @click="remove(profile)" :label="`删除配置 ${profile.name}`"><Trash2 :size="16" /></IconButton></div></article></div>
  </template>
  <template v-else>
    <div class="editor-top"><button class="back-link back-button" @click="editing = false"><ArrowLeft :size="16" />返回</button></div>
    <section class="page-intro editor-intro"><div><h1>{{ currentId ? '编辑' : '新增' }}认证</h1></div></section>
    <form class="editor-layout" @submit.prevent="save"><div class="editor-main">
      <section class="form-section"><div class="form-section-head"><div><h2>基本信息</h2></div></div><div class="form-grid"><label class="field"><span>配置名称 <b>*</b></span><input v-model="form.name" required placeholder="例如：生产 Grafana" /></label><label class="field"><span>认证方式</span><select v-model="form.auth_type"><option value="form">网页登录</option><option value="none">无需登录</option></select></label><label class="field field-full"><span>登录地址</span><input v-model="form.login_url" type="url" placeholder="https://grafana.example.com/login" /><small>Grafana 填写登录页地址；同一网站的不同账号分别建立连接。</small></label><label class="field"><span>用户名</span><input v-model="form.username_value" autocomplete="username" /></label><label class="field"><span>密码</span><PasswordInput v-model="form.password" autocomplete="new-password" :placeholder="currentId ? '留空则保持原密码' : '请输入密码'" /></label></div></section>
      <section class="form-section"><div class="form-section-head"><div><h2>登录页面元素</h2><p>目标网站使用特殊登录表单时再填写。</p></div></div><div class="form-grid"><label class="field"><span>用户名输入框</span><input v-model="form.username_selector" placeholder="自动识别" /></label><label class="field"><span>密码输入框</span><input v-model="form.password_selector" placeholder="自动识别" /></label><label class="field"><span>登录按钮</span><input v-model="form.submit_selector" placeholder="自动识别" /></label><label class="field"><span>登录成功标记</span><input v-model="form.success_selector" placeholder="可留空" /></label></div></section>
      <ButtonGroup class="editor-footer"><ActionButton type="button" @click="editing = false" variant="secondary">取消</ActionButton><ActionButton type="submit" :disabled="saving" variant="primary" :loading="saving">保存配置</ActionButton></ButtonGroup>
    </div></form>
  </template>
</template>
