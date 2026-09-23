import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { Role } from '../api/types'
import { apiKey, me, member, ORG_ID } from './fixtures'

/** Absolute URL for an API path, as the client requests it. */
export const url = (path: string) => `${location.origin}/api/v1${path}`

export const orgUrl = (path = '') => url(`/orgs/${ORG_ID}${path}`)

export const errorBody = (status: number, detail = 'Something went wrong') =>
  HttpResponse.json({ detail }, { status })

/** A signed-in session as `role`, with one key, one member and no usage. */
export function sessionHandlers(role: Role = 'owner') {
  return [
    http.get(url('/me'), () => HttpResponse.json(me(role))),
    http.get(orgUrl('/keys'), () => HttpResponse.json([apiKey()])),
    http.get(orgUrl('/members'), () => HttpResponse.json([member({ role })])),
    http.get(orgUrl('/requests'), () => HttpResponse.json({ data: [], next_cursor: null })),
    http.get(orgUrl('/usage'), () =>
      HttpResponse.json({ bucket: 'day', group_by: null, data: [] }),
    ),
  ]
}

// Signed out unless a test says otherwise.
export const server = setupServer(
  http.get(url('/me'), () => errorBody(401, 'Not authenticated')),
)

/** A resolver that never answers, to hold a view in its loading state. */
export const hang = () => new Promise<never>(() => {})
