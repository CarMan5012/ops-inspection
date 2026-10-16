import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'

// Exercise the component's actual request handlers without requiring a browser or remote Grafana.
function fixture() {
  const source = readFileSync(new URL('../src/views/ItemEditor.vue', import.meta.url), 'utf8')
  const code = ['loadDashboards', 'onConnectionChange', 'onPageTypeChange']
    .map(name => source.match(new RegExp(`(?:async )?function ${name}\\(\\) \\{[\\s\\S]*?\\n\\}`))[0])
    .join('\n').replaceAll('api<Row>', 'api')
  const state = {
    form: { auth_profile_id: 1, item_type: 'grafana', url: '' },
    dashboardsLoading: { value: false }, dashboardsLoaded: { value: false },
    selectionError: { value: '' }, dashboardQuery: { value: '' },
    dashboardOptions: { value: [] }, showDashboardPicker: { value: false },
    selectedDashboardTitle: { value: '' },
  }
  const pending = []
  const api = () => new Promise(resolve => pending.push(resolve))
  const handlers = new Function(...Object.keys(state), 'api', 'attempt',
    `let dashboardRequestId = 0; ${code}; return {loadDashboards,onConnectionChange,onPageTypeChange}`
  )(...Object.values(state), api, task => task())
  return { ...state, ...handlers, pending }
}

test('clearing connection ends loading and discards the old response', async () => {
  const f = fixture()
  const old = f.loadDashboards()
  f.form.auth_profile_id = null
  await f.onConnectionChange()
  assert.equal(f.dashboardsLoading.value, false)
  f.pending[0]({ dashboards: [{ uid: 'old' }] })
  await old
  assert.deepEqual(f.dashboardOptions.value, [])
})

test('old response cannot end loading of the newly selected connection', async () => {
  const f = fixture()
  const old = f.loadDashboards()
  f.form.auth_profile_id = 2
  const current = f.onConnectionChange()
  f.pending[0]({ dashboards: [{ uid: 'old' }] })
  await old
  assert.equal(f.dashboardsLoading.value, true)
  f.pending[1]({ dashboards: [{ uid: 'new' }] })
  await current
  assert.equal(f.dashboardsLoading.value, false)
  assert.deepEqual(f.dashboardOptions.value, [{ uid: 'new' }])
})

test('switching to a normal page clears loading and discards Grafana response', async () => {
  const f = fixture()
  const old = f.loadDashboards()
  f.form.item_type = 'web'
  f.onPageTypeChange()
  assert.equal(f.dashboardsLoading.value, false)
  f.pending[0]({ dashboards: [{ uid: 'old' }] })
  await old
  assert.deepEqual(f.dashboardOptions.value, [])
})
