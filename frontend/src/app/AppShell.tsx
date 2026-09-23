import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router'
import { useLogout } from '../api/hooks'
import { Button } from '../components/ui'
import { applyTheme, storedTheme, type Theme } from '../lib/theme'
import { useOrg } from './org'

const NAV = [
  { to: '', label: 'Overview', end: true },
  { to: 'requests', label: 'Requests', end: false },
  { to: 'keys', label: 'API keys', end: false },
  { to: 'members', label: 'Members', end: false },
]

export function AppShell({ children }: { children: ReactNode }) {
  const { org, me } = useOrg()
  const navigate = useNavigate()
  const logout = useLogout()
  const [theme, setTheme] = useState<Theme>(storedTheme)

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Gate
        </div>
        {me.orgs.length > 1 ? (
          <div className="field">
            <label htmlFor="org-switcher">Organization</label>
            <select
              id="org-switcher"
              className="input"
              value={org.id}
              onChange={(event) => navigate(`/orgs/${event.target.value}`)}
            >
              {me.orgs.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="muted" title={org.name}>
            {org.name}
          </div>
        )}
        <nav className="nav" aria-label="Organization">
          {NAV.map((item) => (
            <NavLink key={item.label} to={`/orgs/${org.id}/${item.to}`} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="email" title={me.email}>
            {me.email}
          </span>
          <span className="muted">Role: {org.role}</span>
          <div className="toolbar">
            <Button size="sm" onClick={toggleTheme}>
              {theme === 'dark' ? 'Light theme' : 'Dark theme'}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={logout.isPending}
              onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/login') })}
            >
              Sign out
            </Button>
          </div>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        {children}
      </main>
    </div>
  )
}
