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

let csrfRefresh: Promise<void> | undefined

function refreshCsrfCookie(): Promise<void> {
  if (!csrfRefresh) {
    csrfRefresh = (async () => {
      const response = await fetch(`${apiBase}/session`, { credentials: 'same-origin' })
      if (response.status === 401) {
        location.assign(frontendUrl('login'))
        throw new Error('登录已过期，请重新登录')
      }
      if (!response.ok || !cookie('csrf_token')) {
        throw new Error('无法获取安全验证 Cookie，请检查浏览器 Cookie 设置后刷新页面')
      }
    })().finally(() => { csrfRefresh = undefined })
  }
  return csrfRefresh
}

async function fetchWithCsrf(path: string, method: string, body?: BodyInit, contentType?: string): Promise<Response> {
  const write = method !== 'GET' && method !== 'HEAD'
  if (write && !cookie('csrf_token')) await refreshCsrfCookie()
  const send = () => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (contentType) headers['Content-Type'] = contentType
    if (write) headers['X-CSRF-Token'] = safeDecode(cookie('csrf_token'))
    return fetch(`${apiBase}${path}`, { method, headers, body, credentials: 'same-origin' })
  }
  let response = await send()
  if (write && response.status === 403 && (await response.clone().text()) === 'Forbidden: Missing CSRF token in cookie') {
    await refreshCsrfCookie()
    response = await send() // The first request was rejected before its handler ran.
  }
  return response
}

export async function api<T = Row>(path: string, method = 'GET', body?: Row): Promise<T> {
  const response = await fetchWithCsrf(path, method, body ? JSON.stringify(body) : undefined,
    body ? 'application/json' : undefined)
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
  const response = await fetchWithCsrf(path, 'POST', body)
  if (response.status === 401) {
    location.assign(frontendUrl('login'))
    throw new Error('登录已过期，请重新登录')
  }
  const data = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.text()
  if (!response.ok) throw new Error(String(typeof data === 'object' ? data.detail || '上传失败' : data))
  return data as T
}

export async function logout(): Promise<void> {
  try {
    await fetch(frontendUrl('logout'), {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': safeDecode(cookie('csrf_token')) },
    })
  } finally {
    try { localStorage.setItem('ops_inspection_logout_at', String(Date.now())) } catch { /* Storage may be disabled. */ }
    location.assign(frontendUrl('login'))
  }
}

export function dateTime(value?: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

// SQLite CURRENT_TIMESTAMP and Prometheus timestamps are UTC. Inspection runs
// already store the configured local time, so they continue to use dateTime().
export function utcDateTime(value?: string): string {
  if (!value) return '—'
  const input = value.trim().replace(' ', 'T')
  const timestamp = new Date(/([zZ]|[+-]\d{2}:?\d{2})$/.test(input) ? input : `${input}Z`)
  if (Number.isNaN(timestamp.getTime())) return dateTime(value)
  const timeZone = config.timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(timestamp).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export function statusText(value?: string): string {
  return ({ success: '成功', partial_success: '部分成功', failed: '失败', interrupted: '已中断', queued: '排队中', running: '运行中', pending: '等待中', waiting: '等待中', skipped: '已跳过' } as Row)[value || ''] || value || '未知'
}
