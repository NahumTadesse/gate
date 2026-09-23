import { useId, useRef, useState } from 'react'
import { Button } from './ui'

/** Read-only value with a Copy button. Falls back to selecting the text when
 * the clipboard API is unavailable, so the user can copy it themselves. */
export function CopyField({ label, value }: { label: string; value: string }) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<'idle' | 'copied' | 'manual'>('idle')

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setStatus('copied')
    } catch {
      input.current?.select()
      setStatus('manual')
    }
  }

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="copy-field">
        <input
          ref={input}
          id={id}
          className="input"
          value={value}
          readOnly
          onFocus={(event) => event.currentTarget.select()}
        />
        <Button onClick={copy}>{status === 'copied' ? 'Copied' : 'Copy'}</Button>
      </div>
      <span className="field-hint" aria-live="polite">
        {status === 'copied' && 'Copied to the clipboard.'}
        {status === 'manual' && 'Copying isn’t available here. The key is selected: press Ctrl+C or ⌘C.'}
      </span>
    </div>
  )
}
