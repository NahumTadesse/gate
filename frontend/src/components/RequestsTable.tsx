import type { RequestRow } from '../api/types'
import { formatCost, formatDateTime, formatInt } from '../lib/format'
import { StatusCode } from './ui'

export function RequestsTable({
  rows,
  caption,
  keyNames,
}: {
  rows: RequestRow[]
  caption: string
  keyNames?: Map<string, string>
}) {
  return (
    <div className="table-wrap">
      <table className="data">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Model</th>
            <th scope="col">Status</th>
            {keyNames && <th scope="col">API key</th>}
            <th scope="col" className="right">Prompt</th>
            <th scope="col" className="right">Completion</th>
            <th scope="col" className="right">Cost</th>
            <th scope="col" className="right">Latency</th>
            <th scope="col">Mode</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td title={row.created_at}>{formatDateTime(row.created_at)}</td>
              <td className="mono">{row.model}</td>
              <td>
                <StatusCode code={row.status_code} />
              </td>
              {keyNames && <td>{keyNames.get(row.api_key_id) ?? <span className="muted">Unknown key</span>}</td>}
              <td className="right">{formatInt(row.prompt_tokens)}</td>
              <td className="right">{formatInt(row.completion_tokens)}</td>
              <td className="right">{formatCost(row.cost_micros)}</td>
              <td className="right">{formatInt(row.latency_ms)} ms</td>
              <td className="muted">{row.streamed ? 'Stream' : 'Sync'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
