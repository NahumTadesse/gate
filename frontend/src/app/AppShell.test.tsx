import { screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import { orgPath, renderApp } from '../test/render'
import { server, sessionHandlers } from '../test/server'

test.each([
  ['Overview', ''],
  ['Requests', 'requests'],
  ['API keys', 'keys'],
  ['Members', 'members'],
])('the nav marks %s as the current page, and only it', async (label, page) => {
  server.use(...sessionHandlers('owner'))
  renderApp(orgPath(page))

  const nav = await screen.findByRole('navigation', { name: 'Organization' })
  expect(within(nav).getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page')
  expect(within(nav).getAllByRole('link').filter((link) => link.hasAttribute('aria-current'))).toHaveLength(1)
})
