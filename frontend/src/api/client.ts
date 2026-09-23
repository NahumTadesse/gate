import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './schema'

/** A non-2xx response from the API, with FastAPI's `detail` when present. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, body: unknown) {
    super(messageFrom(status, body))
    this.name = 'ApiError'
    this.status = status
    this.detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body
  }
}

function messageFrom(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const { detail } = body
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg
  }
  return `Request failed (${status})`
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status)
}

// --- 401 handling ---
//
// Any 401 outside of login itself means the session is gone. The router
// subscribes (see UnauthorizedRedirect) and sends the user to /login.

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()

export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

const LOGIN_PATH = '/api/v1/auth/login'

const unauthorized: Middleware = {
  onResponse({ request, response }) {
    if (response.status === 401 && new URL(request.url).pathname !== LOGIN_PATH) {
      unauthorizedListeners.forEach((listener) => listener())
    }
    return undefined
  },
}

export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? '',
  // The session is an httpOnly cookie; every request must carry it.
  credentials: 'include',
  // Looked up per call so test interceptors installed later still apply.
  fetch: (request) => globalThis.fetch(request),
})
api.use(unauthorized)

/** Resolves to the response data, or throws ApiError for a non-2xx status. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call
  if (!response.ok) throw new ApiError(response.status, error)
  return data as T
}
