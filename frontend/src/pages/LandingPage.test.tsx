import { screen, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, test } from 'vitest'
import { orgPath, renderApp } from '../test/render'
import { errorBody, server, sessionHandlers, url } from '../test/server'
import { GITHUB_URL } from './LandingPage'

const HEADLINE = 'Every model API key gets a budget, a rate limit and an off switch.'

describe('landing page', () => {
  test('renders for a signed-out visitor, who stays on it', async () => {
    const { router } = renderApp('/')

    expect(await screen.findByRole('heading', { level: 1, name: HEADLINE })).toBeInTheDocument()
    for (const name of [
      'Model API keys come with no brakes.',
      'A key per service, and control over each one.',
      'How it works',
    ]) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument()
    }
    // Wide and narrow versions of the diagram; CSS (absent in jsdom) shows one.
    expect(screen.getAllByRole('img', { name: /^Request flow\./ })).not.toHaveLength(0)
    expect(screen.getByRole('link', { name: 'GitHub' })).toHaveAttribute('href', GITHUB_URL)
    // The 401 from /me decides what to show; it isn't a lost session.
    expect(router.state.location.pathname).toBe('/')
  })

  test('still renders when the API is down', async () => {
    server.use(http.get(url('/me'), () => errorBody(503, 'Unavailable')))
    renderApp('/')

    expect(await screen.findByRole('heading', { level: 1, name: HEADLINE })).toBeInTheDocument()
  })

  test('sends a signed-in user to their dashboard instead', async () => {
    server.use(...sessionHandlers('owner'))
    const { router } = renderApp('/')

    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(orgPath())
    expect(screen.queryByRole('heading', { name: HEADLINE })).not.toBeInTheDocument()
  })

  test.each([
    ['Sign in', 'Sign in to Gate', '/login'],
    ['Create account', 'Create your Gate account', '/register'],
  ])('the header’s %s link opens that page', async (link, heading, path) => {
    const { user, router } = renderApp('/')
    const header = within(await screen.findByRole('navigation', { name: 'Account' }))

    await user.click(header.getByRole('link', { name: link }))

    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(path)
  })

  test('the hero’s call to action opens registration', async () => {
    const { user, router } = renderApp('/')
    const hero = (await screen.findByRole('heading', { level: 1 })).closest('section')!

    await user.click(within(hero).getByRole('link', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: 'Create your Gate account' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/register')
  })

  test('the code sample switches language by click and arrow keys', async () => {
    const { user } = renderApp('/')
    const python = await screen.findByRole('tab', { name: 'Python' })

    expect(python).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('base_url="https://gate.example.com/v1"')

    await user.click(screen.getByRole('tab', { name: 'curl' }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Removed: curl https://api.openai.com')

    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Python' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Python' })).toHaveAttribute('aria-selected', 'true')

    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: 'curl' })).toHaveAttribute('aria-selected', 'true')
  })
})
