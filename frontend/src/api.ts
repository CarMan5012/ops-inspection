export type Row = Record<string, any>

const config = window.APP_CONFIG || {}
const frontendBase = (config.frontendBase || '/').replace(/\/$/, '')
const apiBase = (config.apiBase || '/api').replace(/\/$/, '')

export function frontendUrl(path = ''): string {
  return `${frontendBase}/${path.replace(/^\//, '')}`
}

export function apiUrl(path: string): string {
  return `${apiBase}/${path.replace(/^\//, '')}`
}

function cookie(name: string): string {
  return document.cookie.split('; ').find(part => part.startsWith(`${name}=`))?.split('=').slice(1).join('=') || ''
}

function safeDecode(val: string): string {
  try { return decodeURIComponent(val) } catch { return val }
}

export async function api<T = Row>(path: string, method = 'GET', body?: Row): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = safeDecode(cookie('csrf_token'))
  const response = await fetch(`${apiBase}${path}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin',
  })
  if (response.status === 401) {
    location.assign(frontendUrl('login'))
    throw new Error('登录已过期，请重新登录')
  }
  const data = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = typeof data === 'object' ? data.detail : data
    throw new Error(Array.isArray(detail) ? detail.map((item: Row) => item.msg).join('；') : String(detail || '操作失败，请稍后重试'))
  }
  return data as T
}

export async function uploadForm<T = Row>(path: string, body: FormData): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': safeDecode(cookie('csrf_token')), Accept: 'application/json' },
    body,
    credentials: 'same-origin',
  })
  if (response.status === 401) {
    location.assign(frontendUrl('login'))
    throw new Error('登录已过期，请重新登录')
  }
  const data = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text()
  if (!response.ok) throw new Error(String(typeof data === 'object' ? data.detail || '上传失败' : data))
  return data as T
}

export async function logout(): Promise<void> {
  await fetch(frontendUrl('logout'), {
    method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': decodeURIComponent(cookie('csrf_token')) },
  })
  location.assign(frontendUrl('login'))
}

export function dateTime(value?: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

export function statusText(value?: string): string {
  return ({ success: '成功', partial_success: '部分成功', failed: '失败', running: '运行中', pending: '等待中', skipped: '已跳过' } as Row)[value || ''] || value || '未知'
}
