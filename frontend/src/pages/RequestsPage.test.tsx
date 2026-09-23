import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { apiKey, requestRow } from '../test/fixtures'
import { orgPath, renderApp } from '../test/render'
import { errorBody, hang, orgUrl, server, sessionHandlers } from '../test/server'

/** Three pages of one request each, chained by cursor; records every query. */
function pagedRequests() {
  const queries: URLSearchParams[] = []
  const pages: Record<string, { model: string; next: string | null }> = {
    first: { model: 'page-1-model', next: 'c2' },
    c2: { model: 'page-2-model', next: 'c3' },
    c3: { model: 'page-3-model', next: null },
  }
  const handler = http.get(orgUrl('/requests'), ({ request }) => {
    const query = new URL(request.url).searchParams
    queries.push(query)
    const page = pages[query.get('cursor') ?? 'first']
    return HttpResponse.json({
      data: [requestRow({ id: page.model, model: page.model })],
      next_cursor: page.next,
    })
  })
  return { handler, queries }
}

describe('requests: states', () => {
  test('loading', async () => {
    server.use(http.get(orgUrl('/requests'), hang), ...sessionHandlers('member'))
    renderApp(orgPath('requests'))
    expect(await screen.findByRole('status', { name: 'Loading requests' })).toBeInTheDocument()
  })

  test('empty', async () => {
    server.use(...sessionHandlers('member'))
    renderApp(orgPath('requests'))
    expect(await screen.findByRole('heading', { name: 'No requests yet' })).toBeInTheDocument()
  })

  test('empty with filters', async () => {
    server.use(...sessionHandlers('member'))
    renderApp(orgPath('requests?model=nope'))
    expect(await screen.findByRole('heading', { name: 'No requests match these filters' })).toBeInTheDocument()
  })

  test('error', async () => {
    server.use(http.get(orgUrl('/requests'), () => errorBody(500)), ...sessionHandlers('member'))
    renderApp(orgPath('requests'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Couldn’t load requests')
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  test('success, naming each request’s key', async () => {
    server.use(
      http.get(orgUrl('/requests'), () =>
        HttpResponse.json({
          data: [requestRow(), requestRow({ id: 'other', api_key_id: 'deleted-key', streamed: true })],
          next_cursor: null,
        }),
      ),
      ...sessionHandlers('member'),
    )
    renderApp(orgPath('requests'))

    const table = await screen.findByRole('table', { name: 'Requests, newest first' })
    const [, known, unknown] = within(table).getAllByRole('row')
    expect(await within(known).findByText(apiKey().name)).toBeInTheDocument()
    expect(known).toHaveTextContent('1,200')
    expect(known).toHaveTextContent('$0.0013')
    expect(known).toHaveTextContent('812 ms')
    expect(unknown).toHaveTextContent('Unknown key')
    expect(unknown).toHaveTextContent('Stream')
  })
})

describe('requests: filters', () => {
  test('apply to the URL and the query, and clear', async () => {
    const { handler, queries } = pagedRequests()
    server.use(handler, ...sessionHandlers('member'))
    const { user, router } = renderApp(orgPath('requests'))

    await screen.findByText('page-1-model')
    await user.type(screen.getByLabelText('Model'), 'gpt-4o')
    await user.type(screen.getByLabelText('Status'), '429')
    await user.selectOptions(screen.getByLabelText('API key'), apiKey().id)
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))

    await expect.poll(() => queries.at(-1)?.get('model')).toBe('gpt-4o')
    expect(queries.at(-1)?.get('status_code')).toBe('429')
    expect(queries.at(-1)?.get('api_key_id')).toBe(apiKey().id)
    expect(router.state.location.search).toBe(`?model=gpt-4o&status_code=429&api_key_id=${apiKey().id}`)

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(router.state.location.search).toBe('')
    expect(screen.getByLabelText('Model')).toHaveValue('')
  })

  test('read from the URL on load', async () => {
    const { handler, queries } = pagedRequests()
    server.use(handler, ...sessionHandlers('member'))
    renderApp(orgPath('requests?model=claude&status_code=500'))

    await screen.findByText('page-1-model')
    expect(screen.getByLabelText('Model')).toHaveValue('claude')
    expect(screen.getByLabelText('Status')).toHaveValue(500)
    expect(queries[0].get('model')).toBe('claude')
    expect(queries[0].get('status_code')).toBe('500')
  })
})

describe('requests: pagination', () => {
  test('pages older by cursor and back to newer', async () => {
    const { handler, queries } = pagedRequests()
    server.use(handler, ...sessionHandlers('member'))
    const { user } = renderApp(orgPath('requests'))

    await screen.findByText('page-1-model')
    const newer = screen.getByRole('button', { name: 'Newer' })
    const older = screen.getByRole('button', { name: 'Older' })
    expect(newer).toBeDisabled()

    await user.click(older)
    expect(await screen.findByText('page-2-model')).toBeInTheDocument()
    expect(queries.at(-1)?.get('cursor')).toBe('c2')
    expect(screen.getByText('Page 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByText('page-3-model')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Older' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Newer' }))
    expect(await screen.findByText('page-2-model')).toBeInTheDocument()
    expect(screen.getByText('Page 2')).toBeInTheDocument()
  })

  test('applying filters starts again from the first page', async () => {
    const { handler, queries } = pagedRequests()
    server.use(handler, ...sessionHandlers('member'))
    const { user } = renderApp(orgPath('requests'))

    await screen.findByText('page-1-model')
    await user.click(screen.getByRole('button', { name: 'Older' }))
    await screen.findByText('page-2-model')

    await user.type(screen.getByLabelText('Model'), 'x')
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))

    await expect.poll(() => queries.at(-1)?.get('model')).toBe('x')
    expect(queries.at(-1)?.has('cursor')).toBe(false)
    expect(await screen.findByText('Page 1')).toBeInTheDocument()
  })

  test('a rejected cursor offers a way back to the first page', async () => {
    const { handler } = pagedRequests()
    server.use(
      http.get(orgUrl('/requests'), ({ request }) =>
        new URL(request.url).searchParams.get('cursor') === 'c2' ? errorBody(400, 'Invalid cursor') : undefined,
      ),
      handler,
      ...sessionHandlers('member'),
    )
    const { user } = renderApp(orgPath('requests'))

    await screen.findByText('page-1-model')
    await user.click(screen.getByRole('button', { name: 'Older' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Invalid cursor')
    await user.click(within(alert).getByRole('button', { name: 'Back to the first page' }))
    expect(await screen.findByText('page-1-model')).toBeInTheDocument()
  })
})
