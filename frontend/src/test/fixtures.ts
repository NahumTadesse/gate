import type { ApiKey, Me, Member, RequestRow, Role } from '../api/types'

export const ORG_ID = '0b5a3c1e-0000-4000-8000-000000000001'
export const ME_ID = '0b5a3c1e-0000-4000-8000-0000000000aa'

export function me(role: Role = 'owner'): Me {
  return {
    id: ME_ID,
    email: 'ada@example.com',
    created_at: '2026-01-05T10:00:00Z',
    orgs: [{ id: ORG_ID, name: 'Acme', role }],
  }
}

export function apiKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: '0b5a3c1e-0000-4000-8000-0000000000b1',
    name: 'billing-service',
    prefix: 'gk_live_ab12',
    rpm_limit: 60,
    monthly_budget_micros: null,
    last_used_at: null,
    revoked_at: null,
    created_at: '2026-02-01T09:00:00Z',
    ...overrides,
  }
}

export function member(overrides: Partial<Member> = {}): Member {
  return {
    user_id: ME_ID,
    email: 'ada@example.com',
    role: 'owner',
    created_at: '2026-01-05T10:00:00Z',
    ...overrides,
  }
}

export function requestRow(overrides: Partial<RequestRow> = {}): RequestRow {
  return {
    id: '0b5a3c1e-0000-4000-8000-0000000000c1',
    request_id: 'req_1',
    api_key_id: apiKey().id,
    model: 'gpt-4o-mini',
    provider: 'openai',
    status_code: 200,
    prompt_tokens: 1200,
    completion_tokens: 340,
    cost_micros: 1_250,
    latency_ms: 812,
    streamed: false,
    created_at: '2026-09-20T12:00:00Z',
    ...overrides,
  }
}
