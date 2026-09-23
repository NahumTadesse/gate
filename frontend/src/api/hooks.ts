import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { ApiKeyInput, MemberInput, Role } from './types'

export const keys = {
  me: ['me'] as const,
  org: (orgId: string) => ['org', orgId] as const,
  apiKeys: (orgId: string) => ['org', orgId, 'keys'] as const,
  members: (orgId: string) => ['org', orgId, 'members'] as const,
  requests: (orgId: string, params: object) => ['org', orgId, 'requests', params] as const,
  usage: (orgId: string, params: object) => ['org', orgId, 'usage', params] as const,
}

// --- session ---

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(api.GET('/api/v1/me')),
    staleTime: 60_000,
  })
}

type Credentials = { email: string; password: string }

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: Credentials) => unwrap(api.POST('/api/v1/auth/login', { body })),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.me }),
  })
}

export function useRegister() {
  return useMutation({
    mutationFn: async (body: Credentials) => {
      await unwrap(api.POST('/api/v1/auth/register', { body }))
      // Registering doesn't start a session; log straight in.
      return unwrap(api.POST('/api/v1/auth/login', { body }))
    },
  })
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/auth/logout')),
    onSettled: () => client.clear(),
  })
}

// --- API keys ---

const orgPath = (orgId: string) => ({ path: { org_id: orgId } })

export function useApiKeys(orgId: string) {
  return useQuery({
    queryKey: keys.apiKeys(orgId),
    queryFn: () => unwrap(api.GET('/api/v1/orgs/{org_id}/keys', { params: orgPath(orgId) })),
  })
}

export function useCreateApiKey(orgId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ApiKeyInput) =>
      unwrap(api.POST('/api/v1/orgs/{org_id}/keys', { params: orgPath(orgId), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.apiKeys(orgId) }),
  })
}

export function useRevokeApiKey(orgId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) =>
      unwrap(
        api.DELETE('/api/v1/orgs/{org_id}/keys/{key_id}', {
          params: { path: { org_id: orgId, key_id: keyId } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.apiKeys(orgId) }),
  })
}

// --- members ---

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: keys.members(orgId),
    queryFn: () => unwrap(api.GET('/api/v1/orgs/{org_id}/members', { params: orgPath(orgId) })),
  })
}

export function useAddMember(orgId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: MemberInput) =>
      unwrap(api.POST('/api/v1/orgs/{org_id}/members', { params: orgPath(orgId), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.members(orgId) }),
  })
}

export function useUpdateMemberRole(orgId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
      unwrap(
        api.PATCH('/api/v1/orgs/{org_id}/members/{user_id}', {
          params: { path: { org_id: orgId, user_id: userId } },
          body: { role },
        }),
      ),
    onSettled: () => {
      client.invalidateQueries({ queryKey: keys.members(orgId) })
      // Changing your own role changes what /me reports.
      client.invalidateQueries({ queryKey: keys.me })
    },
  })
}

export function useRemoveMember(orgId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) =>
      unwrap(
        api.DELETE('/api/v1/orgs/{org_id}/members/{user_id}', {
          params: { path: { org_id: orgId, user_id: userId } },
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.members(orgId) })
      client.invalidateQueries({ queryKey: keys.me })
    },
  })
}

// --- reporting ---

export type RequestFilters = {
  model?: string
  status_code?: number
  api_key_id?: string
  from?: string
  to?: string
}

export function useRequests(
  orgId: string,
  filters: RequestFilters,
  cursor: string | undefined,
  limit = 50,
) {
  const query = { ...filters, cursor, limit }
  return useQuery({
    queryKey: keys.requests(orgId, query),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/orgs/{org_id}/requests', {
          params: { path: { org_id: orgId }, query },
        }),
      ),
    // Keep the current page on screen while the next one loads.
    placeholderData: keepPreviousData,
  })
}

export function useUsage(orgId: string, from: string, to: string) {
  const query = { from, to, bucket: 'day' as const }
  return useQuery({
    queryKey: keys.usage(orgId, query),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/orgs/{org_id}/usage', { params: { path: { org_id: orgId }, query } }),
      ),
    placeholderData: keepPreviousData,
  })
}
