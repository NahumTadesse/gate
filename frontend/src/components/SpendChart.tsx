import { useId, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import { formatCost, formatDay, formatInt } from '../lib/format'
import { Button } from './ui'

export type DailyUsage = { day: string; cost: number; requests: number; tokens: number }

function SpendTooltip({ active, payload }: TooltipContentProps<number, string>) {
  const point = active ? (payload?.[0]?.payload as DailyUsage | undefined) : undefined
  if (!point) return null
  return (
    <div className="chart-tooltip">
      <strong>{formatCost(point.cost, 2)}</strong>
      <span className="muted">
        <span className="key" aria-hidden="true" />
        {formatDay(point.day)} · {formatInt(point.requests)} requests
      </span>
    </div>
  )
}

/** Daily spend as one series, with a table view carrying every value. */
export function SpendChart({ data }: { data: DailyUsage[] }) {
  const [showTable, setShowTable] = useState(false)
  const tableId = useId()
  const total = data.reduce((sum, point) => sum + point.cost, 0)

  return (
    <div>
      <div className="toolbar chart-toolbar">
        <Button
          size="sm"
          variant="ghost"
          aria-pressed={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((shown) => !shown)}
        >
          {showTable ? 'Show chart' : 'Show table'}
        </Button>
      </div>
      {showTable ? (
        <div id={tableId} className="table-wrap">
          <table className="data">
            <caption className="visually-hidden">Spend per day (UTC)</caption>
            <thead>
              <tr>
                <th scope="col">Day (UTC)</th>
                <th scope="col" className="right">Spend</th>
                <th scope="col" className="right">Requests</th>
                <th scope="col" className="right">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {data.map((point) => (
                <tr key={point.day}>
                  <td>{formatDay(point.day)}</td>
                  <td className="right">{formatCost(point.cost, 2)}</td>
                  <td className="right">{formatInt(point.requests)}</td>
                  <td className="right">{formatInt(point.tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <figure
          id={tableId}
          className="chart"
          aria-label={`Daily spend over ${data.length} days, ${formatCost(total, 2)} in total`}
          role="img"
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis
                dataKey="day"
                tickFormatter={formatDay}
                tickLine={false}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                minTickGap={32}
              />
              <YAxis
                tickFormatter={(value: number) => formatCost(value, 2)}
                tickLine={false}
                axisLine={false}
                width={72}
                allowDecimals
              />
              <Tooltip
                content={SpendTooltip}
                cursor={{ stroke: 'var(--text-3)', strokeWidth: 1 }}
                isAnimationActive={false}
              />
              <Area
                type="linear"
                dataKey="cost"
                name="Spend"
                stroke="var(--accent)"
                strokeWidth={2}
                fill="var(--chart-area)"
                dot={false}
                activeDot={{ r: 4, fill: 'var(--accent)', stroke: 'var(--surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </figure>
      )}
    </div>
  )
}
