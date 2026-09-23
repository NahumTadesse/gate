/*
 * Illustrations for the landing page: inline SVG and small UI fragments. They
 * take every color from theme tokens through CSS classes (see landing.css),
 * so they follow the light and dark themes. Each one either carries an
 * accessible name or sits beside text that says the same thing.
 */

// --- usage and cost by key and model ---

// Daily spend for two weeks, per model, in dollars. Made up but plausible.
const DAYS = [
  [18.4, 6.1, 2.2],
  [21.9, 7.4, 1.8],
  [19.2, 5.2, 2.9],
  [24.6, 8.8, 3.1],
  [26.3, 9.9, 2.4],
  [9.8, 3.1, 0.9],
  [7.2, 2.6, 0.7],
  [22.7, 7.9, 3.6],
  [25.1, 8.3, 2.8],
  [41.6, 9.4, 3.3],
  [27.9, 10.2, 2.6],
  [23.4, 8.1, 3.9],
  [8.6, 2.9, 1.1],
  [6.9, 2.2, 0.6],
]
const MODELS = ['gpt-4o', 'gpt-4o-mini', 'text-embedding-3-small']

export function UsageFragment() {
  const width = 420
  const height = 132
  const max = 56
  const step = width / DAYS.length
  const barWidth = step - 8
  return (
    <div className="fragment">
      <svg
        className="ill"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Stacked bar chart of daily spend over two weeks, split by model, with a spike on day ten."
      >
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line
            key={fraction}
            className="ill-grid"
            x1="0"
            x2={width}
            y1={height * fraction}
            y2={height * fraction}
          />
        ))}
        {DAYS.map((models, day) => {
          let top = height
          return models.map((dollars, model) => {
            const barHeight = (dollars / max) * height
            top -= barHeight
            return (
              <rect
                key={`${day}-${model}`}
                className={`ill-series-${model}`}
                x={day * step + 4}
                y={top}
                width={barWidth}
                height={Math.max(barHeight - 1, 0)}
                rx="1"
              />
            )
          })
        })}
      </svg>
      <ul className="legend" aria-label="Models">
        {MODELS.map((model, index) => (
          <li key={model}>
            <span className={`legend-key ill-series-${index}`} aria-hidden="true" />
            {model}
          </li>
        ))}
      </ul>
      <table className="fragment-table">
        <caption className="visually-hidden">Spend by key over the same two weeks</caption>
        <thead>
          <tr>
            <th scope="col">Key</th>
            <th scope="col" className="right">
              Requests
            </th>
            <th scope="col" className="right">
              Cost
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>support-bot</td>
            <td className="right">48,213</td>
            <td className="right">$312.47</td>
          </tr>
          <tr>
            <td>nightly-eval</td>
            <td className="right">9,870</td>
            <td className="right">$121.06</td>
          </tr>
          <tr>
            <td>search-indexer</td>
            <td className="right">131,502</td>
            <td className="right">$38.91</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

// --- instant revocation ---

const LOG = [
  { time: '14:02:09', status: 200, detail: 'gpt-4o-mini' },
  { time: '14:02:10', status: 200, detail: 'gpt-4o-mini' },
  { time: '14:02:11', status: 401, detail: 'invalid_api_key' },
  { time: '14:02:11', status: 401, detail: 'invalid_api_key' },
]

export function RevocationFragment() {
  return (
    <ol className="fragment log" aria-label="Request log for key gk_live_7Qm">
      {LOG.slice(0, 2).map((row, index) => (
        <LogRow key={index} {...row} />
      ))}
      <li className="log-event">Key revoked by priya@</li>
      {LOG.slice(2).map((row, index) => (
        <LogRow key={index} {...row} />
      ))}
    </ol>
  )
}

function LogRow({ time, status, detail }: { time: string; status: number; detail: string }) {
  return (
    <li className="log-row">
      <span className="muted">{time}</span>
      <span className={`status ${status === 200 ? 'status-good' : 'status-critical'}`}>{status}</span>
      <span>{detail}</span>
    </li>
  )
}

// --- per-key rate limits and budgets ---

export function LimitsFragment() {
  const spent = 38.2
  const budget = 50
  return (
    <div className="fragment key-card">
      <div className="key-card-head">
        <span className="mono">gk_live_3xK</span>
        <span className="badge">staging</span>
      </div>
      <dl className="key-card-limits">
        <div>
          <dt>Rate limit</dt>
          <dd>120 requests / min</dd>
        </div>
        <div>
          <dt>Budget this month</dt>
          <dd>
            ${spent.toFixed(2)} of ${budget.toFixed(2)}
          </dd>
        </div>
      </dl>
      <svg className="ill meter" viewBox="0 0 200 6" preserveAspectRatio="none" aria-hidden="true">
        <rect className="ill-track" width="200" height="6" rx="2" />
        <rect className="ill-accent" width={(spent / budget) * 200} height="6" rx="2" />
      </svg>
      <p className="fragment-note">Over budget or over the limit: 429, and the provider never sees it.</p>
    </div>
  )
}

// --- streaming passthrough ---

export function StreamingDiagram() {
  const chunks = [0, 1, 2, 3, 4]
  return (
    <svg
      className="ill"
      viewBox="0 0 320 96"
      role="img"
      aria-label="Chunks from the provider pass through Gate to the client one by one, unbuffered."
    >
      <text className="ill-label" x="0" y="16">
        provider
      </text>
      <text className="ill-label" x="320" y="16" textAnchor="end">
        your app
      </text>
      <line className="ill-line" x1="0" x2="320" y1="52" y2="52" />
      <rect className="ill-node" x="146" y="28" width="28" height="48" rx="4" />
      <text className="ill-label ill-strong" x="160" y="92" textAnchor="middle">
        Gate
      </text>
      {chunks.map((chunk) => (
        <rect key={`in-${chunk}`} className="ill-accent" x={12 + chunk * 24} y="46" width="14" height="12" rx="2" opacity={0.35 + chunk * 0.13} />
      ))}
      {chunks.slice(0, 3).map((chunk) => (
        <rect key={`out-${chunk}`} className="ill-accent" x={196 + chunk * 24} y="46" width="14" height="12" rx="2" opacity={0.35 + (chunk + 2) * 0.13} />
      ))}
    </svg>
  )
}

// --- provider failover ---

export function FailoverDiagram() {
  return (
    <svg
      className="ill"
      viewBox="0 0 320 112"
      role="img"
      aria-label="Gate sends a request to provider A, which fails with a 502, then retries it on provider B, which answers 200."
    >
      <rect className="ill-node" x="0" y="38" width="72" height="36" rx="6" />
      <text className="ill-label ill-strong" x="36" y="61" textAnchor="middle">
        Gate
      </text>
      <path className="ill-line ill-dashed" d="M72 50 C 130 50, 150 22, 208 22" />
      <path className="ill-line ill-line-accent" d="M72 62 C 130 62, 150 90, 208 90" />
      <rect className="ill-node" x="208" y="6" width="112" height="32" rx="6" />
      <text className="ill-label" x="220" y="26">
        provider A
      </text>
      <text className="ill-label ill-critical" x="308" y="26" textAnchor="end">
        502
      </text>
      <rect className="ill-node ill-node-accent" x="208" y="74" width="112" height="32" rx="6" />
      <text className="ill-label" x="220" y="94">
        provider B
      </text>
      <text className="ill-label ill-good" x="308" y="94" textAnchor="end">
        200
      </text>
    </svg>
  )
}

// --- how it works ---

const CHECKS = ['Check the key', 'Enforce limit and budget', 'Record tokens and cost']

function Arrowheads({ id }: { id: string }) {
  return (
    <defs>
      <marker id={id} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
        <path className="ill-arrowhead" d="M0 0 L8 4 L0 8 z" />
      </marker>
    </defs>
  )
}

const FLOW_LABEL =
  'Request flow. Your app sends a chat completion request to Gate. Gate checks the key, enforces the rate limit and budget, and records tokens and cost, then forwards the request to the model provider. The provider’s response, or its stream, comes back through Gate to your app.'

/** Client, Gate and provider, left to right; stacked instead on narrow screens. */
export function RequestFlowDiagram() {
  return (
    <>
      <svg className="ill flow flow-wide" viewBox="0 0 960 250" role="img" aria-label={FLOW_LABEL}>
        <Arrowheads id="flow-arrow-wide" />
        <FlowBox x={0} y={70} width={210} title="Your app" subtitle="OpenAI SDK, any language" />
        <rect className="ill-node ill-node-accent" x="320" y="20" width="320" height="210" rx="6" />
        <text className="ill-title" x="344" y="56">
          Gate
        </text>
        {CHECKS.map((check, index) => (
          <g key={check}>
            <rect className="ill-chip" x="344" y={78 + index * 46} width="272" height="34" rx="4" />
            <text className="ill-label ill-strong" x="360" y={100 + index * 46}>
              {check}
            </text>
          </g>
        ))}
        <FlowBox x={750} y={70} width={210} title="Model provider" subtitle="OpenAI-compatible API" />

        <line className="ill-line" x1="214" x2="314" y1="105" y2="105" markerEnd="url(#flow-arrow-wide)" />
        <line className="ill-line" x1="646" x2="746" y1="105" y2="105" markerEnd="url(#flow-arrow-wide)" />
        <line className="ill-line ill-line-accent" x1="746" x2="646" y1="145" y2="145" markerEnd="url(#flow-arrow-wide)" />
        <line className="ill-line ill-line-accent" x1="314" x2="214" y1="145" y2="145" markerEnd="url(#flow-arrow-wide)" />
        <text className="ill-label" x="264" y="95" textAnchor="middle">
          request
        </text>
        <text className="ill-label" x="696" y="95" textAnchor="middle">
          request
        </text>
        <text className="ill-label" x="264" y="167" textAnchor="middle">
          response
        </text>
        <text className="ill-label" x="696" y="167" textAnchor="middle">
          or stream
        </text>
      </svg>

      <svg className="ill flow flow-tall" viewBox="0 0 320 560" role="img" aria-label={FLOW_LABEL}>
        <Arrowheads id="flow-arrow-tall" />
        <FlowBox x={40} y={0} width={240} title="Your app" subtitle="OpenAI SDK, any language" />
        <rect className="ill-node ill-node-accent" x="20" y="150" width="280" height="236" rx="6" />
        <text className="ill-title" x="40" y="186">
          Gate
        </text>
        {CHECKS.map((check, index) => (
          <g key={check}>
            <rect className="ill-chip" x="40" y={206 + index * 56} width="240" height="40" rx="4" />
            <text className="ill-label ill-strong" x="56" y={231 + index * 56}>
              {check}
            </text>
          </g>
        ))}
        <FlowBox x={40} y={486} width={240} title="Model provider" subtitle="OpenAI-compatible API" />

        <line className="ill-line" x1="130" x2="130" y1="74" y2="144" markerEnd="url(#flow-arrow-tall)" />
        <line className="ill-line ill-line-accent" x1="190" x2="190" y1="144" y2="74" markerEnd="url(#flow-arrow-tall)" />
        <line className="ill-line" x1="130" x2="130" y1="392" y2="480" markerEnd="url(#flow-arrow-tall)" />
        <line className="ill-line ill-line-accent" x1="190" x2="190" y1="480" y2="392" markerEnd="url(#flow-arrow-tall)" />
        <text className="ill-label" x="120" y="114" textAnchor="end">
          request
        </text>
        <text className="ill-label" x="200" y="114">
          response
        </text>
        <text className="ill-label" x="120" y="440" textAnchor="end">
          request
        </text>
        <text className="ill-label" x="200" y="440">
          or stream
        </text>
      </svg>
    </>
  )
}

function FlowBox({
  x,
  y,
  width,
  title,
  subtitle,
}: {
  x: number
  y: number
  width: number
  title: string
  subtitle: string
}) {
  return (
    <g>
      <rect className="ill-node" x={x} y={y} width={width} height="74" rx="6" />
      <text className="ill-title" x={x + 20} y={y + 32}>
        {title}
      </text>
      <text className="ill-label" x={x + 20} y={y + 54}>
        {subtitle}
      </text>
    </g>
  )
}
