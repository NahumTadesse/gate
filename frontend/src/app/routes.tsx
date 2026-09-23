import type { RouteObject } from 'react-router'
import { ApiKeysPage } from '../pages/ApiKeysPage'
import { LoginPage, RegisterPage } from '../pages/AuthPages'
import { MembersPage } from '../pages/MembersPage'
import { OverviewPage } from '../pages/OverviewPage'
import { RequestsPage } from '../pages/RequestsPage'
import { HomeRedirect, OrgLayout, RequireAuth, UnauthorizedRedirect } from './guards'

export const routes: RouteObject[] = [
  {
    element: <UnauthorizedRedirect />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      {
        element: <RequireAuth />,
        children: [
          { path: '/', element: <HomeRedirect /> },
          {
            path: '/orgs/:orgId',
            element: <OrgLayout />,
            children: [
              { index: true, element: <OverviewPage /> },
              { path: 'requests', element: <RequestsPage /> },
              { path: 'keys', element: <ApiKeysPage /> },
              { path: 'members', element: <MembersPage /> },
            ],
          },
          { path: '*', element: <HomeRedirect /> },
        ],
      },
    ],
  },
]
