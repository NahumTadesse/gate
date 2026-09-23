import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import type { MemberInput, Role } from '../api/types'
import { member } from '../test/fixtures'
import { orgPath, renderApp } from '../test/render'
import { errorBody, hang, orgUrl, server, sessionHandlers } from '../test/server'

const GRACE = member({ user_id: 'grace', email: 'grace@example.com', role: 'member' })

function withTeam(role: Role = 'owner') {
  return [
    http.get(orgUrl('/members'), () => HttpResponse.json([member({ role }), GRACE])),
    ...sessionHandlers(role),
  ]
}

describe('members: states', () => {
  test('loading', async () => {
    server.use(http.get(orgUrl('/members'), hang), ...sessionHandlers('owner'))
    renderApp(orgPath('members'))
    expect(await screen.findByRole('status', { name: 'Loading members' })).toBeInTheDocument()
  })

  test('empty', async () => {
    server.use(http.get(orgUrl('/members'), () => HttpResponse.json([])), ...sessionHandlers('owner'))
    renderApp(orgPath('members'))
    expect(await screen.findByRole('heading', { name: 'No members' })).toBeInTheDocument()
  })

  test('error', async () => {
    server.use(http.get(orgUrl('/members'), () => errorBody(503)), ...sessionHandlers('owner'))
    renderApp(orgPath('members'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Couldn’t load members')
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  test('success, marking the current user', async () => {
    server.use(...withTeam('member'))
    renderApp(orgPath('members'))

    const table = await screen.findByRole('table', { name: 'Members' })
    const [, mine, theirs] = within(table).getAllByRole('row')
    expect(mine).toHaveTextContent('ada@example.com')
    expect(mine).toHaveTextContent('You')
    expect(theirs).toHaveTextContent('grace@example.com')
    expect(theirs).toHaveTextContent('member')
  })
})

describe('members: roles', () => {
  test.each(['admin', 'member'] as const)('an %s gets a read-only list', async (role) => {
    server.use(...withTeam(role))
    renderApp(orgPath('members'))

    expect(await screen.findByText('grace@example.com')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add member' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /Role for/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
  })

  test('an owner can add, change roles and remove', async () => {
    server.use(...withTeam('owner'))
    renderApp(orgPath('members'))

    expect(await screen.findByRole('combobox', { name: 'Role for grace@example.com' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add member' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove grace@example.com' })).toBeInTheDocument()
  })
})

describe('members: add', () => {
  test('validates the email', async () => {
    server.use(...withTeam('owner'))
    const { user } = renderApp(orgPath('members'))

    await user.click(await screen.findByRole('button', { name: 'Add member' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add member' })
    await user.type(within(dialog).getByLabelText('Email'), 'grace@')
    await user.click(within(dialog).getByRole('button', { name: 'Add member' }))

    expect(await within(dialog).findByText('Enter a valid email address.')).toBeInTheDocument()
  })

  test('explains when there is no account for the email', async () => {
    server.use(http.post(orgUrl('/members'), () => errorBody(404, 'User not found')), ...withTeam('owner'))
    const { user } = renderApp(orgPath('members'))

    await user.click(await screen.findByRole('button', { name: 'Add member' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add member' })
    await user.type(within(dialog).getByLabelText('Email'), 'linus@example.com')
    await user.click(within(dialog).getByRole('button', { name: 'Add member' }))

    expect(await within(dialog).findByText(/No Gate account uses this email/)).toBeInTheDocument()
  })

  test('adds a member with the chosen role', async () => {
    let sent: MemberInput | undefined
    server.use(
      http.post(orgUrl('/members'), async ({ request }) => {
        sent = (await request.json()) as MemberInput
        return HttpResponse.json(member({ user_id: 'linus', ...sent }), { status: 201 })
      }),
      ...withTeam('owner'),
    )
    const { user } = renderApp(orgPath('members'))

    await user.click(await screen.findByRole('button', { name: 'Add member' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add member' })
    await user.type(within(dialog).getByLabelText('Email'), 'linus@example.com')
    await user.selectOptions(within(dialog).getByLabelText('Role'), 'admin')
    expect(within(dialog).getByLabelText('Role')).toHaveAccessibleDescription('Manages API keys')
    await user.click(within(dialog).getByRole('button', { name: 'Add member' }))

    await expect.poll(() => sent).toEqual({ email: 'linus@example.com', role: 'admin' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('members: change role and remove', () => {
  test('changes a role', async () => {
    let sent: { userId: unknown; role: Role } | undefined
    server.use(
      http.patch(orgUrl('/members/:userId'), async ({ params, request }) => {
        sent = { userId: params.userId, ...((await request.json()) as { role: Role }) }
        return HttpResponse.json({ ...GRACE, role: sent.role })
      }),
      ...withTeam('owner'),
    )
    const { user } = renderApp(orgPath('members'))

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Role for grace@example.com' }),
      'admin',
    )
    await expect.poll(() => sent).toEqual({ userId: 'grace', role: 'admin' })
  })

  test('reports a refused role change', async () => {
    server.use(
      http.patch(orgUrl('/members/:userId'), () => errorBody(409, 'An organization needs at least one owner')),
      ...withTeam('owner'),
    )
    const { user } = renderApp(orgPath('members'))

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Role for ada@example.com' }), 'member')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The role wasn’t changed: An organization needs at least one owner',
    )
  })

  test('removes only after confirmation', async () => {
    let removed: unknown
    server.use(
      http.delete(orgUrl('/members/:userId'), ({ params }) => {
        removed = params.userId
        return new HttpResponse(null, { status: 204 })
      }),
      ...withTeam('owner'),
    )
    const { user } = renderApp(orgPath('members'))

    await user.click(await screen.findByRole('button', { name: 'Remove grace@example.com' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove grace@example.com?' })
    expect(removed).toBeUndefined()
    await user.click(within(dialog).getByRole('button', { name: 'Remove member' }))

    await expect.poll(() => removed).toBe('grace')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
