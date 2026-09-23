import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { me } from '../test/fixtures'
import { orgPath, renderApp } from '../test/render'
import { errorBody, orgUrl, server, sessionHandlers, url } from '../test/server'

describe('login', () => {
  test('shows validation errors without calling the API', async () => {
    const { user } = renderApp('/login')

    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Enter a valid email address.')
  })

  test('says so when the credentials are wrong, and stays on the page', async () => {
    server.use(http.post(url('/auth/login'), () => errorBody(401, 'Invalid email or password')))
    const { user, router } = renderApp('/login')

    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Email or password is incorrect.')
    expect(router.state.location.pathname).toBe('/login')
  })

  test('signs in and returns to the page that asked for it', async () => {
    let signedIn = false
    // Handlers listed first win, so the overrides come before the defaults.
    server.use(
      http.get(url('/me'), () =>
        signedIn ? HttpResponse.json(me('owner')) : errorBody(401, 'Not authenticated'),
      ),
      http.post(url('/auth/login'), () => {
        signedIn = true
        return new HttpResponse(null, { status: 204 })
      }),
      ...sessionHandlers('owner'),
    )
    const { user, router } = renderApp(orgPath('keys'))

    await user.type(await screen.findByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: 'API keys' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(orgPath('keys'))
  })
})

describe('register', () => {
  test('shows validation errors', async () => {
    const { user } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'not-an-email')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Use at least 8 characters.')).toBeInTheDocument()
  })

  test('points at the email field when the account already exists', async () => {
    server.use(http.post(url('/auth/register'), () => errorBody(409, 'Email already registered')))
    const { user } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'long enough')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('An account with this email already exists.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  test('registers, signs in and lands on the organization', async () => {
    let signedIn = false
    server.use(
      http.get(url('/me'), () =>
        signedIn ? HttpResponse.json(me('owner')) : errorBody(401, 'Not authenticated'),
      ),
      http.post(url('/auth/register'), () => HttpResponse.json({}, { status: 201 })),
      http.post(url('/auth/login'), () => {
        signedIn = true
        return new HttpResponse(null, { status: 204 })
      }),
      ...sessionHandlers('owner'),
    )
    const { user, router } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password'), 'long enough')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(orgPath())
  })
})

describe('auth redirect', () => {
  test('sends a signed-out visitor to the login page', async () => {
    const { router } = renderApp(orgPath('members'))

    expect(await screen.findByRole('heading', { name: 'Sign in to Gate' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.state).toEqual({ from: orgPath('members') })
  })

  test('sends the user to login when the session expires mid-visit', async () => {
    server.use(
      http.get(orgUrl('/keys'), () => errorBody(401, 'Not authenticated')),
      ...sessionHandlers('owner'),
    )
    const { router } = renderApp(orgPath('keys'))

    expect(await screen.findByRole('heading', { name: 'Sign in to Gate' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
  })
})
