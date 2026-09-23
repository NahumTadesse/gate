import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useRequests, useUsage } from '../api/hooks'
import { useOrg } from '../app/org'
import { RequestsTable } from '../components/RequestsTable'
import { SpendChart, type DailyUsage } from '../components/SpendChart'
import { EmptyState, ErrorState, LoadingBlock, LoadingRows } from '../components/states'
import { PageHeader, Panel } from '../components/ui'
import { formatCost, formatInt } from '../lib/format'
import { lastDays } from '../lib/range'

const RANGES = [7, 30, 90] as const

export function OverviewPage() {
  const { org } = useOrg()
  const [days, setDays] = useState<(typeof RANGES)[number]>(30)
  const range = useMemo(() => lastDays(days), [days])

  return (
    <>
      <PageHeader
        title="Overview"
        actions={
          <div className="segmented" role="group" aria-label="Date range">
            {RANGES.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={option === days}
                onClick={() => setDays(option)}
              >
                {option}d
              </button>
            ))}
          </div>
        }
      />
      <Usage orgId={org.id} range={range} days={days} />
      <RecentActivity orgId={org.id} />
    </>
  )
}

function Usage({
  orgId,
  range,
  days,
}: {
  orgId: string
  range: ReturnType<typeof lastDays>
  days: number
}) {
  const usage = useUsage(orgId, range.from, range.to)

  const series: DailyUsage[] = useMemo(() => {
    const byDay = new Map(
      (usage.data?.data ?? []).map((row) => [new Date(row.bucket_start).toISOString(), row]),
    )
    return range.days.map((day) => {
      const row = byDay.get(day)
      return {
        day,
        cost: row?.cost_micros ?? 0,
        requests: row?.request_count ?? 0,
        tokens: row?.tokens ?? 0,
      }
    })
  }, [usage.data, range.days])

  const totals = series.reduce(
    (sum, point) => ({
      cost: sum.cost + point.cost,
      requests: sum.requests + point.requests,
      tokens: sum.tokens + point.tokens,
    }),
    { cost: 0, requests: 0, tokens: 0 },
  )

  const title = `Spend, last ${days} days`
  if (usage.isPending) {
    return (
      <Panel title={title}>
        <LoadingBlock label="Loading usage" />
      </Panel>
    )
  }
  if (usage.isError) {
    return (
      <Panel title={title}>
        <ErrorState title="Couldn’t load usage" error={usage.error} onRetry={() => usage.refetch()} />
      </Panel>
    )
  }
  return (
    <>
      <dl className="stats" aria-label="Totals">
        <div className="stat">
          <dt className="stat-label">Spend</dt>
          <dd className="stat-value">{formatCost(totals.cost, 2)}</dd>
        </div>
        <div className="stat">
          <dt className="stat-label">Requests</dt>
          <dd className="stat-value">{formatInt(totals.requests)}</dd>
        </div>
        <div className="stat">
          <dt className="stat-label">Tokens</dt>
          <dd className="stat-value">{formatInt(totals.tokens)}</dd>
        </div>
      </dl>
      <Panel title={title}>
        {totals.requests === 0 ? (
          <EmptyState title={`No usage in the last ${days} days`}>
            <p>
              Spend appears here once requests go through the proxy.{' '}
              <Link to={`/orgs/${orgId}/keys`}>Create an API key</Link> and send a request to{' '}
              <code className="mono">/v1/chat/completions</code>.
            </p>
          </EmptyState>
        ) : (
          <SpendChart data={series} />
        )}
      </Panel>
    </>
  )
}

function RecentActivity({ orgId }: { orgId: string }) {
  const recent = useRequests(orgId, {}, undefined, 10)
  return (
    <Panel
      title="Recent activity"
      actions={<Link to={`/orgs/${orgId}/requests`}>View all requests</Link>}
    >
      {recent.isPending ? (
        <LoadingRows label="Loading recent requests" columns={6} />
      ) : recent.isError ? (
        <ErrorState
          title="Couldn’t load recent requests"
          error={recent.error}
          onRetry={() => recent.refetch()}
        />
      ) : recent.data.data.length === 0 ? (
        <EmptyState title="No requests yet">
          <p>Requests made with this organization’s API keys show up here.</p>
        </EmptyState>
      ) : (
        <RequestsTable rows={recent.data.data} caption="Most recent requests" />
      )}
    </Panel>
  )
}
