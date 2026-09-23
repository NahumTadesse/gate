import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'
import { isApiError } from '../api/client'
import { useAddMember, useMembers, useRemoveMember, useUpdateMemberRole } from '../api/hooks'
import type { Member, Role } from '../api/types'
import { useOrg } from '../app/org'
import { ConfirmDialog, Modal } from '../components/dialogs'
import { EmptyState, ErrorState, LoadingRows } from '../components/states'
import { Button, Field, PageHeader, Panel } from '../components/ui'
import { formatDate } from '../lib/format'
import { can } from '../lib/roles'

const ROLES: Role[] = ['owner', 'admin', 'member']
const ROLE_HELP: Record<Role, string> = {
  owner: 'Manages members, keys and settings',
  admin: 'Manages API keys',
  member: 'Read-only',
}

const inviteSchema = z.object({
  email: z.email('Enter a valid email address.'),
  role: z.enum(['owner', 'admin', 'member'], { error: 'Choose a role.' }),
})
type InviteValues = z.infer<typeof inviteSchema>

export function MembersPage() {
  const { org, me } = useOrg()
  const members = useMembers(org.id)
  const manage = can.manageMembers(org.role)
  const [inviting, setInviting] = useState(false)
  const [removing, setRemoving] = useState<Member | null>(null)
  const updateRole = useUpdateMemberRole(org.id)

  return (
    <>
      <PageHeader
        title="Members"
        actions={
          manage && (
            <Button variant="primary" onClick={() => setInviting(true)}>
              Add member
            </Button>
          )
        }
      />
      {updateRole.isError && (
        <p className="alert" role="alert">
          The role wasn’t changed: {updateRole.error.message}
        </p>
      )}
      <Panel title="People">
        {members.isPending ? (
          <LoadingRows label="Loading members" columns={4} />
        ) : members.isError ? (
          <ErrorState title="Couldn’t load members" error={members.error} onRetry={() => members.refetch()} />
        ) : members.data.length === 0 ? (
          <EmptyState title="No members">
            <p>{manage ? 'Add people by the email they registered with.' : 'Nobody has been added yet.'}</p>
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <caption className="visually-hidden">Members</caption>
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Role</th>
                  <th scope="col">Added</th>
                  {manage && (
                    <th scope="col" className="actions">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {members.data.map((member) => {
                  const isMe = member.user_id === me.id
                  return (
                    <tr key={member.user_id}>
                      <td>
                        {member.email} {isMe && <span className="badge">You</span>}
                      </td>
                      <td>
                        {manage ? (
                          <select
                            className="input"
                            style={{ width: 120 }}
                            aria-label={`Role for ${member.email}`}
                            value={member.role}
                            disabled={updateRole.isPending}
                            onChange={(event) =>
                              updateRole.mutate({ userId: member.user_id, role: event.target.value as Role })
                            }
                          >
                            {ROLES.map((role) => (
                              <option key={role} value={role}>
                                {role}
                              </option>
                            ))}
                          </select>
                        ) : (
                          member.role
                        )}
                      </td>
                      <td>{formatDate(member.created_at)}</td>
                      {manage && (
                        <td className="actions">
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Remove ${member.email}`}
                            onClick={() => setRemoving(member)}
                          >
                            Remove
                          </Button>
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {manage && <InviteDialog orgId={org.id} open={inviting} onOpenChange={setInviting} />}
      {manage && <RemoveDialog orgId={org.id} member={removing} onClose={() => setRemoving(null)} />}
    </>
  )
}

function InviteDialog({
  orgId,
  open,
  onOpenChange,
}: {
  orgId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const add = useAddMember(orgId)
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: '', role: 'member' },
  })
  const { errors } = form.formState
  const role = useWatch({ control: form.control, name: 'role' })

  function close(next: boolean) {
    if (next) return onOpenChange(true)
    form.reset()
    add.reset()
    onOpenChange(false)
  }

  const onSubmit = form.handleSubmit((values) =>
    add.mutate(values, {
      onSuccess: () => close(false),
      onError: (error) => {
        if (isApiError(error, 404)) {
          form.setError('email', {
            message: 'No Gate account uses this email. Ask them to register first, then add them.',
          })
        } else if (isApiError(error, 409)) {
          form.setError('email', { message: 'This person is already a member.' })
        }
      },
    }),
  )

  const unexpected = add.isError && !isApiError(add.error, 404) && !isApiError(add.error, 409)

  return (
    <Modal
      open={open}
      onOpenChange={close}
      title="Add member"
      description="They need a Gate account first; add them by the email they registered with."
    >
      <form className="form" onSubmit={onSubmit} noValidate>
        {unexpected && (
          <p className="alert" role="alert">
            The member wasn’t added: {add.error?.message}
          </p>
        )}
        <Field label="Email" error={errors.email?.message}>
          {(props) => <input className="input" type="email" autoFocus {...props} {...form.register('email')} />}
        </Field>
        <Field label="Role" error={errors.role?.message} hint={ROLE_HELP[role ?? 'member']}>
          {(props) => (
            <select className="input" {...props} {...form.register('role')}>
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="form-actions">
          <Button onClick={() => close(false)}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={add.isPending}>
            {add.isPending ? 'Adding…' : 'Add member'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function RemoveDialog({
  orgId,
  member,
  onClose,
}: {
  orgId: string
  member: Member | null
  onClose: () => void
}) {
  const remove = useRemoveMember(orgId)
  return (
    <ConfirmDialog
      open={member !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset()
          onClose()
        }
      }}
      title={`Remove ${member?.email ?? ''}?`}
      description="They lose access to this organization and its API keys immediately."
      confirmLabel="Remove member"
      pending={remove.isPending}
      error={remove.isError ? `They weren’t removed: ${remove.error.message}` : undefined}
      onConfirm={() => member && remove.mutate(member.user_id, { onSuccess: onClose })}
    />
  )
}
