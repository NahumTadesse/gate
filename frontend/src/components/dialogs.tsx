import * as Dialog from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import { Button } from './ui'

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{title}</Dialog.Title>
          {description ? (
            <Dialog.Description className="dialog-description">{description}</Dialog.Description>
          ) : (
            <Dialog.Description className="visually-hidden">{title}</Dialog.Description>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** A destructive action, confirmed in place (never window.confirm). */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pending,
  error,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  pending: boolean
  error?: string
  onConfirm: () => void
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={title} description={description}>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <Dialog.Close asChild>
          <Button>Cancel</Button>
        </Dialog.Close>
        <Button variant="danger" onClick={onConfirm} disabled={pending}>
          {pending ? 'Working…' : confirmLabel}
        </Button>
      </div>
    </Modal>
  )
}
