import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '../api/hooks'
import type { ApiKey, CreatedApiKey } from '../api/types'
import { useOrg } from '../app/org'
import { CopyField } from '../components/CopyField'
import { ConfirmDialog, Modal } from '../components/dialogs'
import { EmptyState, ErrorState, LoadingRows } from '../components/states'
import { Button, Field, PageHeader, Panel } from '../components/ui'
import { formatCost, formatDate, formatDateTime, formatInt } from '../lib/format'
import { can } from '../lib/roles'

const INT4_MAX = 2_147_483_647

// Optional number inputs arrive as '' when blank.
const optionalNumber = (schema: z.ZodNumber) =>
  z.preprocess((value) => (value === '' || value == null ? undefined : Number(value)), schema.optional())

const createSchema = z.object({
  name: z.string().trim().min(1, 'Name the key, e.g. after the service that uses it.').max(200, 'Use at most 200 characters.'),
  rpm_limit: optionalNumber(
    z.number({ error: 'Enter a whole number.' }).int('Enter a whole number.').min(1, 'Must be at least 1.').max(INT4_MAX, 'That limit is too large.'),
  ),
  budget_dollars: optionalNumber(
    z.number({ error: 'Enter an amount in dollars.' }).min(0, 'Can’t be negative.').max(9_000_000_000_000, 'That budget is too large.'),
  ),
})

type CreateInput = z.input<typeof createSchema>
type CreateValues = z.output<typeof createSchema>

export function ApiKeysPage() {
  const { org } = useOrg()
  const apiKeys = useApiKeys(org.id)
  const manage = can.manageKeys(org.role)
  const [creating, setCreating] = useState(false)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)

  return (
    <>
      <PageHeader
        title="API keys"
        actions={
          manage && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              Create key
            </Button>
          )
        }
      />
      <Panel title="Keys">
        {apiKeys.isPending ? (
          <LoadingRows label="Loading API keys" columns={7} />
        ) : apiKeys.isError ? (
          <ErrorState title="Couldn’t load API keys" error={apiKeys.error} onRetry={() => apiKeys.refetch()} />
        ) : apiKeys.data.length === 0 ? (
          <EmptyState title="No API keys yet">
            <p>
              {manage
                ? 'Create a key to call the proxy. Send it as “Authorization: Bearer <key>”.'
                : 'An admin or owner of this organization can create one.'}
            </p>
          </EmptyState>
        ) : (
          <KeysTable keys={apiKeys.data} onRevoke={manage ? setRevoking : undefined} />
        )}
      </Panel>
      {manage && <CreateKeyDialog orgId={org.id} open={creating} onOpenChange={setCreating} />}
      {manage && (
        <RevokeKeyDialog orgId={org.id} apiKey={revoking} onClose={() => setRevoking(null)} />
      )}
    </>
  )
}

function KeysTable({ keys, onRevoke }: { keys: ApiKey[]; onRevoke?: (key: ApiKey) => void }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <caption className="visually-hidden">API keys</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Key</th>
            <th scope="col">Status</th>
            <th scope="col" className="right">Rate limit</th>
            <th scope="col" className="right">Monthly budget</th>
            <th scope="col">Last used</th>
            <th scope="col">Created</th>
            {onRevoke && (
              <th scope="col" className="actions">
                <span className="visually-hidden">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id}>
              <td>{key.name}</td>
              <td className="mono">{key.prefix}…</td>
              <td>
                {key.revoked_at ? (
                  <span className="badge" title={`Revoked ${formatDateTime(key.revoked_at)}`}>
                    Revoked
                  </span>
                ) : (
                  <span className="status status-good">Active</span>
                )}
              </td>
              <td className="right">{formatInt(key.rpm_limit)}/min</td>
              <td className="right">
                {key.monthly_budget_micros == null ? (
                  <span className="muted">None</span>
                ) : (
                  formatCost(key.monthly_budget_micros, 2)
                )}
              </td>
              <td>{key.last_used_at ? formatDateTime(key.last_used_at) : <span className="muted">Never</span>}</td>
              <td>{formatDate(key.created_at)}</td>
              {onRevoke && (
                <td className="actions">
                  {!key.revoked_at && (
                    <Button size="sm" variant="ghost" onClick={() => onRevoke(key)} aria-label={`Revoke ${key.name}`}>
                      Revoke
                    </Button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CreateKeyDialog({
  orgId,
  open,
  onOpenChange,
}: {
  orgId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const create = useCreateApiKey(orgId)
  const [created, setCreated] = useState<CreatedApiKey | null>(null)
  const form = useForm<CreateInput, unknown, CreateValues>({ resolver: zodResolver(createSchema) })
  const { errors } = form.formState

  function close(next: boolean) {
    if (next) return onOpenChange(true)
    // The plaintext is dropped here and never shown again.
    setCreated(null)
    form.reset()
    create.reset()
    onOpenChange(false)
  }

  const onSubmit = form.handleSubmit((values) =>
    create.mutate(
      {
        name: values.name,
        rpm_limit: values.rpm_limit,
        monthly_budget_micros:
          values.budget_dollars === undefined ? undefined : Math.round(values.budget_dollars * 1_000_000),
      },
      { onSuccess: setCreated },
    ),
  )

  if (created) {
    return (
      <Modal open={open} onOpenChange={close} title="Copy your new key">
        <p className="notice" role="note">
          This is the only time the full key is shown. Store it somewhere safe now; you won’t be able to see it again.
        </p>
        <CopyField label={`Key for ${created.name}`} value={created.key} />
        <div className="form-actions">
          <Button variant="primary" onClick={() => close(false)}>
            I’ve saved the key
          </Button>
        </div>
      </Modal>
    )
  }

  return (
    <Modal open={open} onOpenChange={close} title="Create API key">
      <form className="form" onSubmit={onSubmit} noValidate>
        {create.isError && (
          <p className="alert" role="alert">
            The key wasn’t created: {create.error.message}
          </p>
        )}
        <Field label="Name" error={errors.name?.message}>
          {(props) => <input className="input" autoFocus {...props} {...form.register('name')} />}
        </Field>
        <Field label="Rate limit (requests per minute)" error={errors.rpm_limit?.message} hint="Leave blank for the default, 60.">
          {(props) => <input className="input" inputMode="numeric" {...props} {...form.register('rpm_limit')} />}
        </Field>
        <Field label="Monthly budget (USD)" error={errors.budget_dollars?.message} hint="Leave blank for no budget.">
          {(props) => <input className="input" inputMode="decimal" {...props} {...form.register('budget_dollars')} />}
        </Field>
        <div className="form-actions">
          <Button onClick={() => close(false)}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create key'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function RevokeKeyDialog({
  orgId,
  apiKey,
  onClose,
}: {
  orgId: string
  apiKey: ApiKey | null
  onClose: () => void
}) {
  const revoke = useRevokeApiKey(orgId)
  return (
    <ConfirmDialog
      open={apiKey !== null}
      onOpenChange={(open) => {
        if (!open) {
          revoke.reset()
          onClose()
        }
      }}
      title={`Revoke “${apiKey?.name ?? ''}”?`}
      description="Requests using this key are rejected immediately. This can’t be undone."
      confirmLabel="Revoke key"
      pending={revoke.isPending}
      error={revoke.isError ? `The key wasn’t revoked: ${revoke.error.message}` : undefined}
      onConfirm={() => apiKey && revoke.mutate(apiKey.id, { onSuccess: onClose })}
    />
  )
}
