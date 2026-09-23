import '../styles/landing.css'

import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { applyTheme, storedTheme, type Theme } from '../lib/theme'
import { CodeSample } from './landing/CodeSample'
import {
  FailoverDiagram,
  LimitsFragment,
  RequestFlowDiagram,
  RevocationFragment,
  StreamingDiagram,
  UsageFragment,
} from './landing/Illustrations'

export const GITHUB_URL = 'https://github.com/NahumTadesse/gate'

const PROBLEMS = [
  {
    title: 'Nobody sees the spend until the invoice',
    body: 'Every service shares one provider key, so the bill arrives as a single number with no owner.',
    evidence: 'invoice: $4,812.36, forecast was $600',
  },
  {
    title: 'A runaway script has no brakes',
    body: 'A retry loop that starts at 2 a.m. keeps sending requests, and paying for them, until someone wakes up.',
    evidence: '38,406 requests between 02:11 and 06:47',
  },
  {
    title: 'A leaked key can’t be turned off alone',
    body: 'Revoking it means rotating the provider key and redeploying every service that shares it.',
    evidence: 'key found in a public repo, rotated 3 days later',
  },
]

export function LandingPage() {
  return (
    <div className="landing">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="landing-header">
        <div className="landing-container landing-header-inner">
          <Link to="/" className="brand" aria-label="Gate home">
            <span className="brand-mark" aria-hidden="true" />
            Gate
          </Link>
          <nav aria-label="Account" className="landing-nav">
            <Link to="/login" className="btn btn-ghost">
              Sign in
            </Link>
            <Link to="/register" className="btn btn-primary">
              Create account
            </Link>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        <section className="landing-container hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <h1 id="hero-title">Every model API key gets a budget, a rate limit and an off switch.</h1>
            <p className="lede">
              Gate is an OpenAI-compatible proxy. Point your SDK at it and every request is metered,
              capped and revocable.
            </p>
            <div className="hero-actions">
              <Link to="/register" className="btn btn-primary btn-lg">
                Create account
              </Link>
              <a href="#how-it-works" className="btn btn-lg">
                See how it works
              </a>
            </div>
          </div>
          <div className="hero-code">
            <CodeSample />
          </div>
        </section>

        <section className="landing-section" aria-labelledby="problem-title">
          <div className="landing-container problem">
            <div className="problem-intro">
              <h2 id="problem-title">Model API keys come with no brakes.</h2>
              <p className="section-lede">
                Teams get access to a provider and hand the key to every service that asks. It works
                until the first surprise.
              </p>
            </div>
            <ul className="problem-list">
              {PROBLEMS.map((problem) => (
                <li key={problem.title}>
                  <h3>{problem.title}</h3>
                  <p>{problem.body}</p>
                  <p className="evidence mono">{problem.evidence}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="landing-section" aria-labelledby="features-title">
          <div className="landing-container">
            <h2 id="features-title" className="section-title">
              A key per service, and control over each one.
            </h2>
            <div className="bento">
              <Feature
                className="bento-usage"
                title="Usage and cost by key and model"
                body="Tokens come from the provider’s own usage numbers, priced per model, so the dashboard matches the invoice."
              >
                <UsageFragment />
              </Feature>
              <Feature
                className="bento-revoke"
                title="Instant revocation"
                body="Revoke one key and its next request is refused. Every other service keeps running."
              >
                <RevocationFragment />
              </Feature>
              <Feature
                className="bento-limits"
                title="Per-key rate limits and budgets"
                body="Give each key its own requests per minute and a monthly spend ceiling."
              >
                <LimitsFragment />
              </Feature>
              <Feature
                className="bento-stream"
                title="Streaming passthrough"
                body="Server-sent events are relayed chunk by chunk as they arrive, never buffered."
              >
                <StreamingDiagram />
              </Feature>
              <Feature
                className="bento-failover"
                title="Provider failover"
                body="When a provider is down, the request goes to the next one you configured."
              >
                <FailoverDiagram />
              </Feature>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="landing-section" aria-labelledby="how-title" tabIndex={-1}>
          <div className="landing-container">
            <h2 id="how-title" className="section-title">
              How it works
            </h2>
            <p className="section-lede">
              Gate sits between your code and the provider. Your code keeps using the SDK it already
              has.
            </p>
            <div className="flow-figure">
              <RequestFlowDiagram />
            </div>
            <ol className="flow-steps">
              <li>
                <h3>Authenticate</h3>
                <p>Each request carries a Gate key. Unknown or revoked keys get a 401 and go no further.</p>
              </li>
              <li>
                <h3>Forward</h3>
                <p>The body is passed to the provider unchanged, and the response or stream comes straight back.</p>
              </li>
              <li>
                <h3>Record</h3>
                <p>When the response finishes, Gate stores its tokens, cost and latency against the key.</p>
              </li>
            </ol>
          </div>
        </section>
      </main>

      <LandingFooter />
    </div>
  )
}

function Feature({
  className,
  title,
  body,
  children,
}: {
  className: string
  title: string
  body: string
  children: ReactNode
}) {
  return (
    <article className={`bento-cell ${className}`}>
      <div className="bento-text">
        <h3>{title}</h3>
        <p>{body}</p>
      </div>
      <div className="bento-visual">{children}</div>
    </article>
  )
}

function LandingFooter() {
  const [theme, setTheme] = useState<Theme>(storedTheme)

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }

  return (
    <footer className="landing-footer">
      <div className="landing-container landing-footer-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Gate
        </div>
        <nav aria-label="Footer" className="landing-footer-links">
          <a href={GITHUB_URL} rel="noreferrer">
            GitHub
          </a>
          <Link to="/login">Sign in</Link>
          <Link to="/register">Create account</Link>
        </nav>
        <button type="button" className="btn btn-sm" onClick={toggleTheme}>
          {theme === 'dark' ? 'Light theme' : 'Dark theme'}
        </button>
      </div>
    </footer>
  )
}
