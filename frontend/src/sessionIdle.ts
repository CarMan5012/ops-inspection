import { api, frontendUrl, logout } from './api'

const ACTIVITY_KEY = 'ops_inspection_last_activity'
const RENEW_INTERVAL_MS = 30_000

export function startIdleSession(): () => void {
  let timeoutMs = 30 * 60_000
  let lastActivityAt = Date.now()
  let lastSharedWriteAt = 0
  let lastRenewAt = Date.now()
  let renewInFlight = false
  let exiting = false
  let stopped = false

  function sharedActivityAt(): number {
    try {
      const value = Number(localStorage.getItem(ACTIVITY_KEY))
      return Number.isFinite(value) && value <= Date.now() ? value : 0
    } catch {
      return 0
    }
  }

  function publishActivity(now: number) {
    lastActivityAt = now
    if (now - lastSharedWriteAt < 1000) return
    try { localStorage.setItem(ACTIVITY_KEY, String(now)) } catch { /* Storage may be disabled. */ }
    lastSharedWriteAt = now
  }

  function isIdle(): boolean {
    return Date.now() - Math.max(lastActivityAt, sharedActivityAt()) >= timeoutMs
  }

  function exitIfIdle(): boolean {
    if (!isIdle()) return false
    if (!exiting) {
      exiting = true
      void logout().catch(() => {})
    }
    return true
  }

  async function renew(force = false) {
    if (stopped || exiting || renewInFlight || exitIfIdle()) return
    if (!force && Date.now() - lastRenewAt < RENEW_INTERVAL_MS) return
    renewInFlight = true
    try {
      const session = await api<{ idle_timeout_seconds: number }>('/session/renew', 'POST', {})
      lastRenewAt = Date.now()
      if (session.idle_timeout_seconds) timeoutMs = session.idle_timeout_seconds * 1000
    } catch {
      // A later interaction retries; the API handler redirects on an expired session.
    } finally {
      renewInFlight = false
    }
  }

  function onActivity(event: Event) {
    if (!event.isTrusted || stopped || exiting || exitIfIdle()) return
    publishActivity(Date.now())
    if (Date.now() - lastRenewAt >= RENEW_INTERVAL_MS) {
      void renew()
    }
  }

  function onStorage(event: StorageEvent) {
    if (event.key === 'ops_inspection_logout_at') {
      exiting = true
      location.assign(frontendUrl('login'))
      return
    }
    if (event.key === ACTIVITY_KEY) lastActivityAt = Math.max(lastActivityAt, sharedActivityAt())
  }

  for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    window.addEventListener(type, onActivity, { passive: true })
  }
  publishActivity(Date.now())
  window.addEventListener('storage', onStorage)
  const checkTimer = window.setInterval(() => { if (!stopped) exitIfIdle() }, 5_000)
  void api<{ idle_timeout_seconds: number }>('/session').then((session) => {
    if (!stopped) {
      if (session.idle_timeout_seconds) timeoutMs = session.idle_timeout_seconds * 1000
    }
  }).catch(() => { /* The API handler redirects on an expired session. */ })

  return () => {
    stopped = true
    clearInterval(checkTimer)
    for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart']) window.removeEventListener(type, onActivity)
    window.removeEventListener('storage', onStorage)
  }
}
