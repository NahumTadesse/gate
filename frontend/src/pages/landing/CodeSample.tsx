import { useId, useRef, useState, type KeyboardEvent } from 'react'

type Change = 'same' | 'add' | 'del'
type Line = [Change, string]

type Sample = { id: string; label: string; file: string; lines: Line[] }

const same = (text: string): Line => ['same', text]
const add = (text: string): Line => ['add', text]
const del = (text: string): Line => ['del', text]

const SAMPLES: Sample[] = [
  {
    id: 'python',
    label: 'Python',
    file: 'app.py',
    lines: [
      same('import os'),
      same('from openai import OpenAI'),
      same(''),
      same('client = OpenAI('),
      add('    base_url="https://gate.example.com/v1",'),
      same('    api_key=os.environ["GATE_API_KEY"],'),
      same(')'),
      same(''),
      same('reply = client.chat.completions.create('),
      same('    model="gpt-4o-mini",'),
      same('    messages=[{"role": "user", "content": "Summarize this ticket"}],'),
      same(')'),
    ],
  },
  {
    id: 'node',
    label: 'Node.js',
    file: 'index.ts',
    lines: [
      same('import OpenAI from "openai";'),
      same(''),
      same('const client = new OpenAI({'),
      add('  baseURL: "https://gate.example.com/v1",'),
      same('  apiKey: process.env.GATE_API_KEY,'),
      same('});'),
      same(''),
      same('const stream = await client.chat.completions.create({'),
      same('  model: "gpt-4o-mini",'),
      same('  messages: [{ role: "user", content: "Summarize this ticket" }],'),
      same('  stream: true,'),
      same('});'),
    ],
  },
  {
    id: 'curl',
    label: 'curl',
    file: 'terminal',
    lines: [
      del('curl https://api.openai.com/v1/chat/completions \\'),
      add('curl https://gate.example.com/v1/chat/completions \\'),
      same('  -H "Authorization: Bearer $GATE_API_KEY" \\'),
      same('  -H "Content-Type: application/json" \\'),
      same(`  -d '{"model": "gpt-4o-mini", "messages": [...]}'`),
    ],
  },
]

// Just enough highlighting for these samples: strings, then keywords.
const TOKEN = /("[^"]*"|'[^']*')|\b(import|from|const|new|await|true)\b/g

function highlight(text: string) {
  const parts = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    parts.push(
      <span key={match.index} className={match[1] ? 'tok-string' : 'tok-keyword'}>
        {match[0]}
      </span>,
    )
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

const CHANGE_LABEL: Record<Change, string> = { same: '', add: 'Added: ', del: 'Removed: ' }
const GUTTER: Record<Change, string> = { same: ' ', add: '+', del: '-' }

/** The OpenAI SDK pointed at Gate, as a diff, in a few languages. */
export function CodeSample() {
  const [selected, setSelected] = useState(0)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const id = useId()

  function onKeyDown(event: KeyboardEvent) {
    const last = SAMPLES.length - 1
    const next = {
      ArrowRight: selected === last ? 0 : selected + 1,
      ArrowLeft: selected === 0 ? last : selected - 1,
      Home: 0,
      End: last,
    }[event.key]
    if (next === undefined) return
    event.preventDefault()
    setSelected(next)
    tabs.current[next]?.focus()
  }

  const sample = SAMPLES[selected]
  return (
    <figure className="code-window">
      <div className="code-bar">
        <div role="tablist" aria-label="Language" className="code-tabs" onKeyDown={onKeyDown}>
          {SAMPLES.map((candidate, index) => (
            <button
              key={candidate.id}
              ref={(element) => {
                tabs.current[index] = element
              }}
              type="button"
              role="tab"
              id={`${id}-tab-${candidate.id}`}
              aria-selected={index === selected}
              aria-controls={`${id}-panel`}
              tabIndex={index === selected ? 0 : -1}
              onClick={() => setSelected(index)}
            >
              {candidate.label}
            </button>
          ))}
        </div>
        <span className="code-file" aria-hidden="true">
          {sample.file}
        </span>
      </div>
      <div
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-tab-${sample.id}`}
        className="code-body"
        tabIndex={0}
      >
        <pre>
          <code>
            {sample.lines.map(([change, text], index) => (
              <span key={index} className={`code-line code-${change}`}>
                <span className="code-gutter" aria-hidden="true">
                  {GUTTER[change]}
                </span>
                {change !== 'same' && <span className="visually-hidden">{CHANGE_LABEL[change]}</span>}
                {highlight(text)}
                {'\n'}
              </span>
            ))}
          </code>
        </pre>
      </div>
      <figcaption className="code-caption">
        Add one line and use a Gate key. Your provider key stays in Gate, not in every service.
      </figcaption>
    </figure>
  )
}
