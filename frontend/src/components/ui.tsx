import { useId, type ComponentProps, type ReactNode } from 'react'

type ButtonProps = ComponentProps<'button'> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'md' | 'sm'
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  type = 'button',
  ...props
}: ButtonProps) {
  const classes = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', className]
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />
}

type FieldProps = {
  label: string
  error?: string
  hint?: string
  children: (props: {
    id: string
    'aria-invalid': boolean
    'aria-describedby': string | undefined
  }) => ReactNode
}

/** Label above, control, then hint and error below: the one field layout. */
export function Field({ label, error, hint, children }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({ id, 'aria-invalid': Boolean(error), 'aria-describedby': describedBy })}
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} className="field-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      {actions}
    </header>
  )
}

export function Panel({
  title,
  actions,
  children,
}: {
  title: string
  actions?: ReactNode
  children: ReactNode
}) {
  const id = useId()
  return (
    <section className="panel" aria-labelledby={id}>
      <div className="panel-header">
        <h2 id={id}>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}

/** HTTP status with a colored mark; the number itself is the label. */
export function StatusCode({ code }: { code: number }) {
  const tone = code >= 500 ? 'critical' : code >= 400 ? 'warning' : 'good'
  return <span className={`status status-${tone}`}>{code}</span>
}
