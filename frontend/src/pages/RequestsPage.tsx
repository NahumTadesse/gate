import { useMemo, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { isApiError } from '../api/client'
import { useApiKeys, useRequests, type RequestFilters } from '../api/hooks'
import { useOrg } from '../app/org'
import { RequestsTable } from '../components/RequestsTable'
import { EmptyState, ErrorState, LoadingRows } from '../components/states'
import { Button, Field, PageHeader, Panel } from '../components/ui'

const PAGE_SIZE = 50
const FILTER_KEYS = ['model', 'status_code', 'api_key_id', 'from', 'to'] as const

// datetime-local has no zone; treat it as the browser's local time.
function localToIso(value: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

function isoToLocal(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function filtersFrom(params: URLSearchParams): RequestFilters {
  const status = params.get('status_code')
  return {
    model: params.get('model') || undefined,
    status_code: status ? Number(status) : undefined,
    api_key_id: params.get('api_key_id') || undefined,
    from: params.get('from') || undefined,
    to: params.get('to') || undefined,
  }
}

export function RequestsPage() {
  const { org } = useOrg()
  const [params, setParams] = useSearchParams()
  const filters = useMemo(() => filtersFrom(params), [params])
  const filterKey = JSON.stringify(filters)

  // Cursors of the pages before the current one; reset whenever filters change.
  const [pages, setPages] = useState<{ filterKey: string; cursors: (string | undefined)[] }>({
    filterKey,
    cursors: [undefined],
  })
  const cursors = pages.filterKey === filterKey ? pages.cursors : [undefined]
  const cursor = cursors[cursors.length - 1]

  const requests = useRequests(org.id, filters, cursor, PAGE_SIZE)
  const apiKeys = useApiKeys(org.id)
  const keyNames = useMemo(
    () => new Map((apiKeys.data ?? []).map((key) => [key.id, key.name])),
    [apiKeys.data],
  )

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const next = new URLSearchParams()
    for (const key of FILTER_KEYS) {
      const raw = String(form.get(key) ?? '').trim()
      const value = key === 'from' || key === 'to' ? localToIso(raw) : raw
      if (value) next.set(key, value)
    }
    setParams(next)
  }

  const next = requests.data?.next_cursor
  const pageNumber = cursors.length
  const hasFilters = FILTER_KEYS.some((key) => params.has(key))

  return (
    <>
      <PageHeader title="Requests" />
      {/* Keyed on the URL so the inputs reset when filters change elsewhere. */}
      <form key={params.toString()} className="toolbar" onSubmit={applyFilters} aria-label="Filter requests">
        <Field label="Model">
          {(props) => (
            <input className="input mono" name="model" defaultValue={params.get('model') ?? ''} {...props} />
          )}
        </Field>
        <Field label="Status">
          {(props) => (
            <input
              className="input input-status"
              name="status_code"
              type="number"
              min={100}
              max={599}
              defaultValue={params.get('status_code') ?? ''}
              {...props}
            />
          )}
        </Field>
        <Field label="API key">
          {(props) => (
            <select className="input" name="api_key_id" defaultValue={params.get('api_key_id') ?? ''} {...props}>
              <option value="">Any key</option>
              {(apiKeys.data ?? []).map((key) => (
                <option key={key.id} value={key.id}>
                  {key.name} ({key.prefix}…)
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="From">
          {(props) => (
            <input
              className="input"
              name="from"
              type="datetime-local"
              defaultValue={isoToLocal(params.get('from'))}
              {...props}
            />
          )}
        </Field>
        <Field label="To">
          {(props) => (
            <input
              className="input"
              name="to"
              type="datetime-local"
              defaultValue={isoToLocal(params.get('to'))}
              {...props}
            />
          )}
        </Field>
        <Button type="submit" variant="primary">
          Apply filters
        </Button>
        {hasFilters && (
          <Button variant="ghost" onClick={() => setParams(new URLSearchParams())}>
            Clear
          </Button>
        )}
      </form>

      <Panel title="Request log">
        {requests.isPending ? (
          <LoadingRows label="Loading requests" columns={9} rows={8} />
        ) : requests.isError ? (
          <ErrorState
            title="Couldn’t load requests"
            error={requests.error}
            action={
              isApiError(requests.error, 400) ? (
                <Button onClick={() => setPages({ filterKey, cursors: [undefined] })}>
                  Back to the first page
                </Button>
              ) : (
                <Button onClick={() => requests.refetch()}>Try again</Button>
              )
            }
          />
        ) : requests.data.data.length === 0 ? (
          <EmptyState title={hasFilters ? 'No requests match these filters' : 'No requests yet'}>
            <p>
              {hasFilters
                ? 'Widen the date range or clear a filter.'
                : 'Requests made with this organization’s API keys show up here, newest first.'}
            </p>
          </EmptyState>
        ) : (
          <>
            <div className={requests.isPlaceholderData ? 'is-stale' : undefined}>
              <RequestsTable rows={requests.data.data} caption="Requests, newest first" keyNames={keyNames} />
            </div>
            <nav className="pagination" aria-label="Pages">
              <span className="num">Page {pageNumber}</span>
              <div className="buttons">
                <Button
                  size="sm"
                  disabled={pageNumber === 1}
                  onClick={() => setPages({ filterKey, cursors: cursors.slice(0, -1) })}
                >
                  Newer
                </Button>
                <Button
                  size="sm"
                  disabled={!next || requests.isPlaceholderData}
                  onClick={() => next && setPages({ filterKey, cursors: [...cursors, next] })}
                >
                  Older
                </Button>
              </div>
            </nav>
          </>
        )}
      </Panel>
    </>
  )
}
