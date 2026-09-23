import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import type { ApiKeyInput } from '../api/types'
import { apiKey } from '../test/fixtures'
import { orgPath, renderApp } from '../test/render'
import { errorBody, hang, orgUrl, server, sessionHandlers } from '../test/server'

const KEY = 'gk_live_ab12cd34ef56gh78ij90kl12mn34op56'

describe('API keys: states', () => {
  test('loading', async () => {
    server.use(http.get(orgUrl('/keys'), hang), ...sessionHandlers('owner'))
    renderApp(orgPath('keys'))
    expect(await screen.findByRole('status', { name: 'Loading API keys' })).toBeInTheDocument()
  })

  test('empty', async () => {
    server.use(http.get(orgUrl('/keys'), () => HttpResponse.json([])), ...sessionHandlers('owner'))
    renderApp(orgPath('keys'))
    expect(await screen.findByRole('heading', { name: 'No API keys yet' })).toBeInTheDocument()
  })

  test('error, with a retry that recovers', async () => {
    let fail = true
    server.use(
      http.get(orgUrl('/keys'), () => (fail ? errorBody(500) : HttpResponse.json([apiKey()]))),
      ...sessionHandlers('owner'),
    )
    const { user } = renderApp(orgPath('keys'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Couldn’t load API keys')
    expect(alert).toHaveTextContent('The server had a problem')

    fail = false
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('billing-service')).toBeInTheDocument()
  })

  test('success', async () => {
    server.use(
      http.get(orgUrl('/keys'), () =>
        HttpResponse.json([
          apiKey({ monthly_budget_micros: 25_000_000, rpm_limit: 1200 }),
          apiKey({ id: 'revoked', name: 'old-key', revoked_at: '2026-03-01T00:00:00Z' }),
        ]),
      ),
      ...sessionHandlers('owner'),
    )
    renderApp(orgPath('keys'))

    const table = await screen.findByRole('table', { name: 'API keys' })
    const [, active, revoked] = within(table).getAllByRole('row')
    expect(active).toHaveTextContent('billing-service')
    expect(active).toHaveTextContent('gk_live_ab12…')
    expect(active).toHaveTextContent('1,200/min')
    expect(active).toHaveTextContent('$25.00')
    expect(revoked).toHaveTextContent('Revoked')
    expect(within(revoked).queryByRole('button', { name: /Revoke/ })).not.toBeInTheDocument()
  })
})

describe('API keys: roles', () => {
  test('a member sees the keys but no controls', async () => {
    server.use(...sessionHandlers('member'))
    renderApp(orgPath('keys'))

    expect(await screen.findByText('billing-service')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create key' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revoke billing-service' })).not.toBeInTheDocument()
  })

  test.each(['admin', 'owner'] as const)('an %s can create and revoke', async (role) => {
    server.use(...sessionHandlers(role))
    renderApp(orgPath('keys'))

    expect(await screen.findByRole('button', { name: 'Revoke billing-service' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create key' })).toBeInTheDocument()
  })
})

describe('API keys: create', () => {
  test('validates the form', async () => {
    server.use(...sessionHandlers('owner'))
    const { user } = renderApp(orgPath('keys'))

    await user.click(await screen.findByRole('button', { name: 'Create key' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create API key' })
    await user.type(within(dialog).getByLabelText('Rate limit (requests per minute)'), '1.5')
    await user.type(within(dialog).getByLabelText('Monthly budget (USD)'), '-3')
    await user.click(within(dialog).getByRole('button', { name: 'Create key' }))

    expect(await within(dialog).findByText('Name the key, e.g. after the service that uses it.')).toBeInTheDocument()
    expect(within(dialog).getByText('Enter a whole number.')).toBeInTheDocument()
    expect(within(dialog).getByText('Can’t be negative.')).toBeInTheDocument()
  })

  test('shows the plaintext key once, with a warning and a copy control', async () => {
    let sent: ApiKeyInput | undefined
    server.use(
      http.post(orgUrl('/keys'), async ({ request }) => {
        sent = (await request.json()) as ApiKeyInput
        return HttpResponse.json({ ...apiKey({ name: 'search' }), key: KEY }, { status: 201 })
      }),
      ...sessionHandlers('owner'),
    )
    const { user } = renderApp(orgPath('keys'))

    await user.click(await screen.findByRole('button', { name: 'Create key' }))
    let dialog = await screen.findByRole('dialog', { name: 'Create API key' })
    await user.type(within(dialog).getByLabelText('Name'), 'search')
    await user.type(within(dialog).getByLabelText('Monthly budget (USD)'), '12.5')
    await user.click(within(dialog).getByRole('button', { name: 'Create key' }))

    dialog = await screen.findByRole('dialog', { name: 'Copy your new key' })
    expect(sent).toEqual({ name: 'search', monthly_budget_micros: 12_500_000 })
    expect(within(dialog).getByRole('note')).toHaveTextContent('you won’t be able to see it again')
    expect(within(dialog).getByLabelText('Key for search')).toHaveValue(KEY)

    await user.click(within(dialog).getByRole('button', { name: 'Copy' }))
    expect(await navigator.clipboard.readText()).toBe(KEY)
    expect(within(dialog).getByText('Copied to the clipboard.')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'I’ve saved the key' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Reopening starts a fresh form; the key is gone.
    await user.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByRole('dialog', { name: 'Create API key' })).toBeInTheDocument()
    expect(screen.queryByDisplayValue(KEY)).not.toBeInTheDocument()
  })

  test('reports an API failure in the dialog', async () => {
    server.use(http.post(orgUrl('/keys'), () => errorBody(422, 'Name is too long')), ...sessionHandlers('owner'))
    const { user } = renderApp(orgPath('keys'))

    await user.click(await screen.findByRole('button', { name: 'Create key' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create API key' })
    await user.type(within(dialog).getByLabelText('Name'), 'search')
    await user.click(within(dialog).getByRole('button', { name: 'Create key' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('The key wasn’t created: Name is too long')
  })
})

describe('API keys: revoke', () => {
  test('asks for confirmation, and cancelling revokes nothing', async () => {
    let revoked = false
    server.use(
      http.delete(orgUrl('/keys/:keyId'), () => {
        revoked = true
        return new HttpResponse(null, { status: 204 })
      }),
      ...sessionHandlers('owner'),
    )
    const { user } = renderApp(orgPath('keys'))

    await user.click(await screen.findByRole('button', { name: 'Revoke billing-service' }))
    const dialog = await screen.findByRole('dialog', { name: 'Revoke “billing-service”?' })
    expect(dialog).toHaveTextContent('This can’t be undone.')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(revoked).toBe(false)
  })

  test('revokes on confirmation and refreshes the list', async () => {
    let revoked = false
    server.use(
      http.get(orgUrl('/keys'), () =>
        HttpResponse.json([apiKey(revoked ? { revoked_at: '2026-09-22T00:00:00Z' } : {})]),
      ),
      http.delete(orgUrl('/keys/:keyId'), ({ params }) => {
        revoked = params.keyId === apiKey().id
        return new HttpResponse(null, { status: 204 })
      }),
      ...sessionHandlers('owner'),
    )
    const { user } = renderApp(orgPath('keys'))

    await user.click(await screen.findByRole('button', { name: 'Revoke billing-service' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Revoke key' }))

    expect(await screen.findByText('Revoked')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(revoked).toBe(true)
  })
})
