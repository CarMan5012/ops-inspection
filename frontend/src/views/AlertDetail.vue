<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ArrowLeft, Info } from '../icons'
import { api, apiUrl, utcDateTime, type Row } from '../api'
import { collectRunAlerts, detailEntries } from '../monitorAlerts'

const route = useRoute()
const runId = Number(route.params.id)
const resultId = Number(route.params.resultId)
const alertIndex = Number(route.params.alertIndex)
const loading = ref(true)
const error = ref('')
const run = ref<Row | null>(null)
const alert = ref<Row | null>(null)
const instances = ref<Row[]>([])

const firingCount = computed(() => instances.value.filter(item => item.state === 'firing').length)
const pendingCount = computed(() => instances.value.filter(item => item.state === 'pending').length)
const prometheusLink = computed(() => alert.value?.source_uid
  ? apiUrl(`runs/${runId}/prometheus/${resultId}/${encodeURIComponent(alert.value.source_uid)}`)
  : '')
const stateLabel = computed(() => firingCount.value ? `触发 ${firingCount.value}`
  : pendingCount.value ? `待触发 ${pendingCount.value}` : String(alert.value?.state || '未知'))

function instanceName(item: Row, index: number): string {
  return String(item.labels?.pod || item.instance || item.labels?.service || item.labels?.job || `实例 ${index + 1}`)
}

function instanceContext(item: Row): string {
  return [item.labels?.namespace, item.labels?.phase || item.labels?.status]
    .filter(Boolean).join(' · ')
}

function otherAnnotations(item: Row): [string, string][] {
  return detailEntries(item.annotations).filter(([key]) => key !== 'summary' && key !== 'description')
}

onMounted(async () => {
  try {
    const data = await api<Row>(`/runs/${runId}`)
    run.value = data.run
    const result = (data.results || []).find((row: Row) => Number(row.id) === resultId)
    const snapshot = JSON.parse(String(result?.monitor_data || '{}'))
    const selected = Array.isArray(snapshot.alerts) ? snapshot.alerts[alertIndex] : null
    if (!selected || typeof selected !== 'object') throw new Error('该告警记录不存在')
    const sources = Array.isArray(snapshot.sources) ? snapshot.sources : []
    const source = sources.find((row: Row) => row?.uid === selected.source_uid || row?.name === selected.source)
    const selectedAlert = {
      ...selected,
      source_uid: selected.source_uid || source?.uid || '',
      observed_at: snapshot.observed_at || '',
    }
    alert.value = selectedAlert
    instances.value = collectRunAlerts(data.results || []).filter(item =>
      item.name === selectedAlert.name &&
      (item.source_uid || item.source) === (selectedAlert.source_uid || selectedAlert.source))
    if (!instances.value.length) instances.value = [selectedAlert]
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法读取告警详情'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="alert-page">
    <RouterLink :to="`/runs/${runId}`" class="alert-back"><ArrowLeft :size="16" />返回</RouterLink>

    <div v-if="loading" class="alert-loading" aria-label="正在加载告警详情">
      <div class="skeleton"></div><div class="skeleton"></div>
    </div>
    <div v-else-if="error || !alert" class="alert-missing">
      <Info :size="22" />
      <h1>暂时无法查看告警</h1>
      <p>{{ error || '该告警记录不存在' }}</p>
      <ActionButton :to="`/runs/${runId}`"  variant="secondary">返回</ActionButton>
    </div>
    <template v-else>
      <header class="alert-page-head">
        <div class="alert-kicker"><span class="alert-kicker-dot"></span>Prometheus 告警 · 运行记录 #{{ runId }}</div>
        <div class="alert-heading-line">
          <h1>{{ alert.name }}</h1>
          <span class="alert-state" :class="firingCount ? 'is-firing' : pendingCount ? 'is-pending' : ''">{{ stateLabel }}</span>
          <span v-if="alert.ignored" class="alert-state is-ignored">巡检时已忽略</span>
        </div>
        <p>本次运行采集的告警信息。Prometheus 页面显示当前实时状态。</p>
        <p v-if="alert.ignored && alert.ignore_reason">忽略原因：{{ alert.ignore_reason }}</p>
        <div class="alert-head-actions">
          <ActionButton v-if="prometheusLink"  :href="prometheusLink" target="_blank" rel="noopener noreferrer" variant="primary">查看源站</ActionButton>
          <span v-else class="alert-link-unavailable">该记录未保存 Prometheus 数据源信息</span>
        </div>
      </header>

      <div class="alert-content-grid">
        <div class="alert-content-main">
          <section class="alert-content-section alert-message" v-if="alert.summary && alert.summary !== alert.name">
            <div class="alert-section-label">告警内容</div>
            <h2>{{ alert.summary }}</h2>
          </section>

          <section class="alert-content-section">
            <div class="alert-section-header"><h2>告警实例</h2><span>{{ instances.length }} 个</span></div>
            <div class="alert-instances">
              <details v-for="(item, index) in instances" :key="`${item.result_id}-${item.alert_index}-${index}`" :open="instances.length === 1" class="alert-instance">
                <summary>
                  <span class="instance-identity"><strong>{{ instanceName(item, index) }}</strong><small v-if="instanceContext(item)">{{ instanceContext(item) }}</small></span>
                  <span class="instance-state" :class="`is-${item.state}`">{{ item.state === 'firing' ? '触发中' : item.state === 'pending' ? '待触发' : item.state }}</span>
                  <span class="instance-expand"><span class="instance-expand-open">详情</span><span class="instance-expand-close">收起</span></span>
                </summary>
                <div class="instance-body">
                  <div class="instance-metrics">
                    <div><span>当前值</span><strong>{{ item.value || '—' }}</strong></div>
                    <div><span>触发时间</span><strong>{{ item.active_at ? utcDateTime(item.active_at) : '—' }}</strong></div>
                  </div>
                  <p v-if="item.annotations?.description && item.annotations.description !== item.summary" class="instance-description">{{ item.annotations.description }}</p>
                  <div v-if="detailEntries(item.labels).length" class="alert-labels">
                    <div v-for="[key, value] in detailEntries(item.labels)" :key="key" class="alert-label"><span>{{ key }}</span><strong>{{ value }}</strong></div>
                  </div>
                  <p v-else class="alert-section-empty">这条历史记录没有保存完整标签。</p>
                  <dl v-if="otherAnnotations(item).length" class="alert-annotations">
                    <div v-for="[key, value] in otherAnnotations(item)" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div>
                  </dl>
                </div>
              </details>
            </div>
          </section>
        </div>

        <aside class="alert-facts" aria-label="告警信息">
          <h2>告警信息</h2>
          <dl>
            <div><dt>严重程度</dt><dd>{{ alert.severity || '未设置' }}</dd></div>
            <div><dt>数据源</dt><dd>{{ alert.source || '—' }}</dd></div>
            <div><dt>实例数</dt><dd>{{ instances.length }} 个</dd></div>
            <div><dt>触发 / 待触发</dt><dd>{{ firingCount }} / {{ pendingCount }}</dd></div>
            <div><dt>采集时间</dt><dd>{{ alert.observed_at ? utcDateTime(alert.observed_at) : '—' }}</dd></div>
          </dl>
          <p>若原生页面无法打开，请确认你的浏览器可以访问 Grafana 数据源中配置的 Prometheus 地址。</p>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.alert-page { width: min(100%, 1160px); margin: 0 auto; }
.alert-back { display: inline-flex; align-items: center; gap: 7px; color: #547181; font-size: 12px; font-weight: 700; text-decoration: none; margin-bottom: 24px; transition: color .18s ease, transform .18s ease; }
.alert-back:hover { color: #14657d; transform: translateX(-2px); }
.alert-back:focus-visible, .alert-head-actions a:focus-visible { outline: 3px solid #9ccbd8; outline-offset: 3px; }
.alert-page-head { border-bottom: 1px solid #dce7eb; padding: 0 0 30px; margin-bottom: 28px; }
.alert-kicker { display: inline-flex; align-items: center; gap: 8px; color: #638290; font-size: 11px; font-weight: 700; letter-spacing: .04em; }
.alert-kicker-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #e2564c; box-shadow: 0 0 0 4px #fdecea; }
.alert-heading-line { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-top: 10px; }
.alert-heading-line h1 { margin: 0; color: #1d3948; font-size: clamp(26px, 3vw, 38px); line-height: 1.2; letter-spacing: -.035em; overflow-wrap: anywhere; }
.alert-page-head p { margin: 12px 0 0; color: #708794; font-size: 13px; line-height: 1.6; }
.alert-state { display: inline-flex; align-items: center; min-height: 28px; padding: 0 10px; border-radius: 6px; background: #eaf1f4; color: #486673; font-size: 12px; font-weight: 700; white-space: nowrap; }
.alert-state.is-firing { color: #b83a32; background: #fceae8; }
.alert-state.is-ignored { color: #5b7180; background: #e8f0f3; }
.alert-state.is-pending { color: #956513; background: #fff3d9; }
.alert-head-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 22px; }
.alert-link-unavailable { color: #8a9ca5; font-size: 12px; }
.alert-content-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(255px, 290px); gap: clamp(24px, 4vw, 58px); align-items: start; }
.alert-content-main { min-width: 0; }
.alert-content-section { padding: 0 0 28px; margin-bottom: 28px; border-bottom: 1px solid #e5ecef; }
.alert-content-section:last-child { border-bottom: 0; }
.alert-section-label { color: #8096a0; font-size: 11px; font-weight: 700; margin-bottom: 10px; }
.alert-message h2 { margin: 0; color: #274553; font-size: 18px; line-height: 1.45; font-weight: 650; text-wrap: pretty; }
.alert-message p { color: #536e7a; font-size: 13px; line-height: 1.7; margin: 12px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.alert-section-header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.alert-section-header h2 { margin: 0; color: #26414e; font-size: 15px; }
.alert-section-header span { color: #91a2aa; font-size: 11px; }
.alert-instances { display: grid; gap: 10px; }
.alert-instance { border: 1px solid #dfe9ed; border-radius: 8px; background: #fff; overflow: hidden; }
.alert-instance summary { list-style: none; display: flex; align-items: center; gap: 14px; padding: 15px 16px; cursor: pointer; transition: background .18s ease; }
.alert-instance summary::-webkit-details-marker { display: none; }
.alert-instance summary:hover { background: #f8fbfc; }
.alert-instance summary:focus-visible { outline: 3px solid #9ccbd8; outline-offset: -3px; }
.instance-identity { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.instance-identity strong { color: #294754; font-size: 13px; overflow-wrap: anywhere; }
.instance-identity small { color: #8297a2; font-size: 11px; overflow-wrap: anywhere; }
.instance-state { color: #647e8a; font-size: 11px; font-weight: 700; white-space: nowrap; }
.instance-state.is-firing { color: #bd423a; }
.instance-state.is-pending { color: #9b701f; }
.instance-expand { color: #2b7188; font-size: 11px; font-weight: 700; white-space: nowrap; }
.instance-expand-close { display: none; }
.alert-instance[open] .instance-expand { color: #7e939d; }
.alert-instance[open] .instance-expand-open { display: none; }
.alert-instance[open] .instance-expand-close { display: inline; }
.instance-body { border-top: 1px solid #e9eff1; padding: 16px; }
.instance-metrics { display: flex; flex-wrap: wrap; gap: 14px 32px; margin-bottom: 16px; }
.instance-metrics > div { display: flex; flex-direction: column; gap: 3px; }
.instance-metrics span { color: #8a9ca5; font-size: 11px; }
.instance-metrics strong { color: #385766; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.instance-description { color: #506c79; font-size: 12px; line-height: 1.65; white-space: pre-wrap; margin: 0 0 14px; overflow-wrap: anywhere; }
.alert-labels { display: flex; flex-wrap: wrap; gap: 9px; }
.alert-label { display: inline-flex; max-width: 100%; min-height: 30px; border: 1px solid #dce8ec; border-radius: 6px; overflow: hidden; background: #f8fbfc; color: #385868; font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }
.alert-label span, .alert-label strong { padding: 5px 9px; overflow-wrap: anywhere; }
.alert-label span { border-right: 1px solid #dce8ec; color: #66818d; font-weight: 500; }
.alert-label strong { font-weight: 600; }
.alert-section-empty { color: #8b9ea8; font-size: 13px; }
.alert-annotations { margin: 16px 0 0; display: grid; gap: 12px; }
.alert-annotations > div { display: grid; grid-template-columns: minmax(120px, 25%) minmax(0, 1fr); gap: 12px; font-size: 13px; line-height: 1.6; }
.alert-annotations dt { color: #8296a1; overflow-wrap: anywhere; }
.alert-annotations dd { margin: 0; color: #3d5a68; white-space: pre-wrap; overflow-wrap: anywhere; }
.alert-facts { background: #f6f9fa; border: 1px solid #e3ebee; border-radius: 10px; padding: 21px 22px; }
.alert-facts h2 { margin: 0 0 20px; color: #294653; font-size: 14px; }
.alert-facts dl { margin: 0; }
.alert-facts dl > div { padding: 12px 0; border-top: 1px solid #e2eaed; }
.alert-facts dt { color: #8499a4; font-size: 11px; margin-bottom: 5px; }
.alert-facts dd { margin: 0; color: #365361; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.alert-facts .alert-value { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.alert-facts p { margin: 19px 0 0; color: #8a9ea8; font-size: 11px; line-height: 1.6; }
.alert-loading .skeleton { height: 110px; margin-bottom: 18px; }
.alert-missing { max-width: 520px; padding: 60px 0; color: #68818e; }
.alert-missing h1 { font-size: 22px; color: #2b4856; }
.alert-missing p { margin-bottom: 20px; font-size: 13px; }
@media (max-width: 800px) { .alert-content-grid { grid-template-columns: 1fr; } .alert-facts { order: -1; } }
@media (max-width: 520px) { .alert-page-head { padding-bottom: 24px; } .alert-annotations > div { grid-template-columns: 1fr; gap: 2px; } }
</style>
