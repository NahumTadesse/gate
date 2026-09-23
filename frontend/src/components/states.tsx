import type { ReactNode } from 'react'
import { isApiError } from '../api/client'
import { Button } from './ui'

/** Placeholder rows shaped like the table that will replace them. */
export function LoadingRows({ label, columns, rows = 5 }: { label: string; columns: number; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="table-wrap">
      <table className="data">
        <tbody>
          {Array.from({ length: rows }, (_, row) => (
            <tr key={row}>
              {Array.from({ length: columns }, (_, column) => (
                <td key={column}>
                  <span className="skeleton" style={{ width: `${40 + ((row + column) % 3) * 20}%` }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function LoadingBlock({ label, className = '' }: { label: string; className?: string }) {
  return (
    <div role="status" aria-label={label} className="panel-body">
      <span className={`skeleton skeleton-block ${className}`} />
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children}
    </div>
  )
}

export function ErrorState({
  title,
  error,
  onRetry,
  action,
}: {
  title: string
  error: unknown
  onRetry?: () => void
  action?: ReactNode
}) {
  const detail = isApiError(error)
    ? error.status >= 500
      ? 'The server had a problem. Try again in a moment.'
      : error.message
    : 'Gate could not be reached. Check your connection and try again.'
  return (
    <div className="state state-error" role="alert">
      <h3>{title}</h3>
      <p>{detail}</p>
      {action ?? (onRetry && <Button onClick={onRetry}>Try again</Button>)}
    </div>
  )
}
