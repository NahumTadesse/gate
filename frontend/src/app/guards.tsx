import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router'
import { isApiError, onUnauthorized } from '../api/client'
import { useMe } from '../api/hooks'
import { ErrorState } from '../components/states'
import { LandingPage } from '../pages/LandingPage'
import { OrgContext } from './org'
import { AppShell } from './AppShell'

// Pages a signed-out visitor may stay on. A 401 there is expected (the home
// page asks /me to decide what to show), not a sign the session ended, and
// clearing the cache would drop that very query.
const PUBLIC_PATHS = new Set(['/', '/login', '/register'])

/** Sends the user to /login whenever the API says the session is gone. */
export function UnauthorizedRedirect() {
  const navigate = useNavigate()
  const location = useLocation()
  const client = useQueryClient()

  useEffect(
    () =>
      onUnauthorized(() => {
        if (PUBLIC_PATHS.has(location.pathname)) return
        client.clear()
        navigate('/login', { replace: true, state: { from: location.pathname + location.search } })
      }),
    [client, location, navigate],
  )
  return <Outlet />
}

/** Renders its routes only for a logged-in user. */
export function RequireAuth() {
  const me = useMe()
  const location = useLocation()

  if (me.isPending) {
    return <div role="status" aria-label="Loading your account" className="auth" />
  }
  if (isApiError(me.error, 401)) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  if (me.isError) {
    return (
      <main className="auth">
        <ErrorState title="Couldn’t load your account" error={me.error} onRetry={() => me.refetch()} />
      </main>
    )
  }
  return <Outlet context={me.data} />
}

/** The landing page for visitors; signed-in users go on to their dashboard. */
export function Home() {
  const me = useMe()

  if (me.data) {
    return <HomeRedirect />
  }
  // Any failure, not just a 401, gets the landing page: it needs no API. It
  // shows after the first failed attempt rather than waiting out the retries
  // of a server error; if a retry does find a session, the redirect follows.
  if (me.isError || me.failureCount > 0) {
    return <LandingPage />
  }
  return <div role="status" aria-label="Loading" className="auth" />
}

export function HomeRedirect() {
  const me = useMe()
  const first = me.data?.orgs[0]
  if (!first) {
    return (
      <main className="auth">
        <div className="state">
          <h3>You’re not in any organization</h3>
          <p>Ask an organization owner to add you, then reload this page.</p>
        </div>
      </main>
    )
  }
  return <Navigate to={`/orgs/${first.id}`} replace />
}

/** Resolves :orgId against the user's memberships and provides it below. */
export function OrgLayout() {
  const { orgId } = useParams()
  const me = useMe()
  if (!me.data) return null
  const org = me.data.orgs.find((candidate) => candidate.id === orgId)
  if (!org) {
    return (
      <main className="auth">
        <div className="state">
          <h3>Organization not found</h3>
          <p>It doesn’t exist, or you’re not a member of it.</p>
          <Link to="/">Go to your organization</Link>
        </div>
      </main>
    )
  }
  return (
    <OrgContext.Provider value={{ org, me: me.data }}>
      <AppShell>
        <Outlet />
      </AppShell>
    </OrgContext.Provider>
  )
}
