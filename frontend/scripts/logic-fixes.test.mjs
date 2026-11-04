import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

function componentFunctions(file, names, context, extras = '') {
  const source = readFileSync(new URL(`../src/views/${file}`, import.meta.url), 'utf8')
  const functions = names.map(name => {
    const match = source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n\\}`))
    assert.ok(match, `${name} must exist in ${file}`)
    return match[0]
  }).join('\n')
  const javascript = ts.transpileModule(`${extras}\n${functions}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText
  return new Function(...Object.keys(context), `${javascript}\nreturn { ${names.join(', ')} }`)(...Object.values(context))
}

test('failed login probe shows its failure instead of a success toast', async () => {
  const notifications = []
  const handlers = componentFunctions('Profiles.vue', ['test'], {
    isAuth: { value: true }, endpoint: { value: '/auth-profiles' },
    api: async () => ({ success: false, message: '测试登录失败: 凭据错误' }),
    attempt: task => task(),
    notify: (...args) => notifications.push(args),
  })
  await handlers.test({ id: 7 })
  assert.deepEqual(notifications, [['测试登录失败: 凭据错误', 'error']])
})

test('old Grafana search cannot bind its dashboards to a newly selected connection', async () => {
  const pending = []
  const added = []
  const context = {
    connectionId: { value: 1 }, searchText: { value: '' }, searching: { value: false },
    error: { value: '' }, dashboards: { value: [] }, selected: { value: [] },
    dashboardConnectionId: { value: '' }, testing: { value: false }, saving: { value: false },
    api: () => new Promise(resolve => pending.push(resolve)),
    addRow: (...args) => added.push(args),
  }
  const handlers = componentFunctions('ItemBulk.vue', ['loadDashboards', 'addSelected'], context,
    'let dashboardRequestId = 0;')

  const old = handlers.loadDashboards()
  context.connectionId.value = 2
  const current = handlers.loadDashboards()
  pending[0]({ dashboards: [{ uid: 'old', title: 'A', url: 'https://example.com/a' }] })
  await old
  assert.deepEqual(context.dashboards.value, [])
  assert.equal(context.searching.value, true)

  pending[1]({ dashboards: [{ uid: 'new', title: 'B', url: 'https://example.com/b' }] })
  await current
  context.selected.value = ['new']
  handlers.addSelected()
  assert.deepEqual(added, [['https://example.com/b', 'B', 2]])
})

test('editing a row during its check cannot save the unchecked target', async () => {
  const pending = []
  const posted = []
  const row = {
    key: 1, name: 'CPU', url: 'https://example.com/old', section: '系统资源',
    auth_profile_id: 1, checked: null, checkMessage: '', checkVersion: 0,
  }
  const context = {
    rows: { value: [row] }, testing: { value: false }, saving: { value: false },
    error: { value: '' }, jobId: { value: 4 }, router: { push: () => {} },
    api: (path, _method, body) => path === '/connections/check-target'
      ? new Promise(resolve => pending.push(resolve))
      : (posted.push(body), Promise.resolve({ ok: true })),
    attempt: task => task(),
  }
  const handlers = componentFunctions('ItemBulk.vue',
    ['rowFingerprint', 'invalidateCheck', 'check', 'checkAll', 'save'], context)
  const saving = handlers.save()
  assert.equal(pending.length, 1)
  row.url = 'https://example.com/changed'
  handlers.invalidateCheck(row)
  pending[0]({ ok: true })
  await saving
  assert.deepEqual(posted, [])
  assert.match(context.error.value, /检查期间发生变化/)
})

test('failed page change cannot label old run rows as the new page', async () => {
  const context = {
    pageSizes: { value: [10] }, pageSize: { value: 10 }, page: { value: 1 },
    totalPages: { value: 3 }, total: { value: 0 }, runs: { value: [] },
    loading: { value: true }, refreshing: { value: false }, loadError: { value: false },
    api: async path => {
      if (path.includes('page=1&')) return { runs: [{ id: 101 }], total: 30 }
      throw new Error('temporary failure')
    },
    attempt: async task => { try { return await task() } catch { return undefined } },
  }
  const { load, changePage } = componentFunctions('Runs.vue', ['load', 'changePage'], context,
    'let requestSeq = 0;')
  await load()
  assert.equal(context.runs.value[0].id, 101)
  await changePage(2)
  assert.equal(context.loadError.value, true)
  assert.deepEqual(context.runs.value, [])
  assert.equal(context.total.value, 0)
})

test('UTC database and alert times use the configured timezone; run time stays local', async () => {
  globalThis.window = { APP_CONFIG: { timeZone: 'Asia/Shanghai' } }
  const { utcDateTime, dateTime, statusText } = await import('../src/api.ts')
  assert.equal(utcDateTime('2026-10-30 00:00:00'), '2026-10-30 08:00:00')
  assert.equal(utcDateTime('2026-10-30T00:00:00+00:00'), '2026-10-30 08:00:00')
  assert.equal(dateTime('2026-10-30T08:00:00+08:00'), '2026-10-30 08:00:00')
  assert.equal(statusText('waiting'), '等待中')
})
