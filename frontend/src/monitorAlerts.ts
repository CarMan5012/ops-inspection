import type { Row } from './api'

export function detailEntries(value: unknown): [string, string][] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value)
    .map(([key, item]): [string, string] => [key, String(item)])
    .sort(([left], [right]) => left.localeCompare(right))
}

export function collectRunAlerts(results: Row[]): Row[] {
  const found = new Map<string, Row>()
  for (const result of results) {
    try {
      const snapshot = JSON.parse(String(result.monitor_data || '{}'))
      if (!Array.isArray(snapshot.alerts)) continue
      const sources = Array.isArray(snapshot.sources) ? snapshot.sources : []
      snapshot.alerts.forEach((alert: Row, alertIndex: number) => {
        if (!alert || typeof alert !== 'object') return
        const source = sources.find((entry: Row) =>
          entry?.uid === alert.source_uid || entry?.name === alert.source)
        const sourceUid = String(alert.source_uid || source?.uid || '')
        const key = JSON.stringify([
          sourceUid || alert.source, alert.name, alert.instance, alert.state,
          detailEntries(alert.labels),
        ])
        if (!found.has(key)) {
          found.set(key, {
            ...alert,
            result_id: result.id,
            alert_index: alertIndex,
            source_uid: sourceUid,
            observed_at: snapshot.observed_at,
          })
        }
      })
    } catch { /* Older records may have no monitoring snapshot. */ }
  }
  return [...found.values()]
}

export function groupRunAlerts(results: Row[]): Row[] {
  const groups = new Map<string, Row>()
  for (const alert of collectRunAlerts(results)) {
    const key = JSON.stringify([alert.source_uid || alert.source, alert.name])
    const group = groups.get(key)
    if (group) {
      group.instances.push(alert)
      group.firing_count += alert.state === 'firing' ? 1 : 0
      group.pending_count += alert.state === 'pending' ? 1 : 0
    } else {
      groups.set(key, {
        ...alert,
        instances: [alert],
        firing_count: alert.state === 'firing' ? 1 : 0,
        pending_count: alert.state === 'pending' ? 1 : 0,
      })
    }
  }
  return [...groups.values()]
}
