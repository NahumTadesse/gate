import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { createQueryClient } from '../app/queryClient'
import { routes } from '../app/routes'
import { ORG_ID } from './fixtures'

/** Renders the real app, routes and all, at `path`. */
export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const user = userEvent.setup()
  const view = render(
    <QueryClientProvider client={createQueryClient({ retry: false })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...view, router, user }
}

export const orgPath = (page = '') => `/orgs/${ORG_ID}${page ? `/${page}` : ''}`
