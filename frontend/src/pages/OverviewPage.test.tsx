import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { lastDays } from '../lib/range'
import { requestRow } from '../test/fixtures'
import { orgPath, renderApp } from '../test/render'
import { errorBody, hang, orgUrl, server, sessionHandlers } from '../test/server'

function usageFor(rows: { day: string; cost_micros: number; request_count: number; tokens: number }[]) {
  return http.get(orgUrl('/usage'), () =>
    HttpResponse.json({
      bucket: 'day',
      group_by: null,
      data: rows.map(({ day, ...row }) => ({ bucket_start: day, ...row })),
    }),
  )
}

describe('overview: usage', () => {
  test('loading', async () => {
    server.use(http.get(orgUrl('/usage'), hang), ...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByRole('status', { name: 'Loading usage' })).toBeInTheDocument()
  })

  test('empty', async () => {
    server.use(...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByRole('heading', { name: 'No usage in the last 30 days' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create an API key' })).toHaveAttribute('href', orgPath('keys'))
  })

  test('error', async () => {
    server.use(http.get(orgUrl('/usage'), () => errorBody(500)), ...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByText('Couldn’t load usage')).toBeInTheDocument()
  })

  test('success: totals, the chart, and its table view', async () => {
    const { days } = lastDays(30)
    server.use(
      usageFor([
        { day: days[28], cost_micros: 1_500_000, request_count: 1200, tokens: 400_000 },
        { day: days[29], cost_micros: 2_250_000, request_count: 300, tokens: 90_000 },
      ]),
      ...sessionHandlers('member'),
    )
    const { user } = renderApp(orgPath())

    const stats = await screen.findByLabelText('Totals')
    expect(stats).toHaveTextContent('Spend$3.75')
    expect(stats).toHaveTextContent('Requests1,500')
    expect(stats).toHaveTextContent('Tokens490,000')
    expect(screen.getByRole('img', { name: 'Daily spend over 30 days, $3.75 in total' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show table' }))
    const table = screen.getByRole('table', { name: 'Spend per day (UTC)' })
    // A row for every day, zero-filled where there was no usage.
    expect(within(table).getAllByRole('row')).toHaveLength(31)
    expect(within(table).getAllByRole('row')[30]).toHaveTextContent('$2.25300')
  })

  test('changing the range asks for that range', async () => {
    const asked: string[] = []
    server.use(
      http.get(orgUrl('/usage'), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get('from')!)
        return HttpResponse.json({ bucket: 'day', group_by: null, data: [] })
      }),
      ...sessionHandlers('member'),
    )
    const { user } = renderApp(orgPath())

    await screen.findByRole('heading', { name: 'No usage in the last 30 days' })
    await user.click(screen.getByRole('button', { name: '7d' }))

    expect(await screen.findByRole('heading', { name: 'No usage in the last 7 days' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '7d' })).toHaveAttribute('aria-pressed', 'true')
    expect(asked.at(-1)).toBe(lastDays(7).from)
  })
})

describe('overview: recent activity', () => {
  test('loading', async () => {
    server.use(http.get(orgUrl('/requests'), hang), ...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByRole('status', { name: 'Loading recent requests' })).toBeInTheDocument()
  })

  test('empty', async () => {
    server.use(...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByRole('heading', { name: 'No requests yet' })).toBeInTheDocument()
  })

  test('error', async () => {
    server.use(http.get(orgUrl('/requests'), () => errorBody(500)), ...sessionHandlers('member'))
    renderApp(orgPath())
    expect(await screen.findByText('Couldn’t load recent requests')).toBeInTheDocument()
  })

  test('success shows the ten most recent', async () => {
    let limit: string | null = null
    server.use(
      http.get(orgUrl('/requests'), ({ request }) => {
        limit = new URL(request.url).searchParams.get('limit')
        return HttpResponse.json({ data: [requestRow({ status_code: 429 })], next_cursor: 'more' })
      }),
      ...sessionHandlers('member'),
    )
    renderApp(orgPath())

    const table = await screen.findByRole('table', { name: 'Most recent requests' })
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('gpt-4o-mini429')
    expect(limit).toBe('10')
    expect(screen.getByRole('link', { name: 'View all requests' })).toHaveAttribute('href', orgPath('requests'))
  })
})
