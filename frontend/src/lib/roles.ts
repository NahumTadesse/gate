import type { Role } from '../api/types'

const rank: Record<Role, number> = { member: 0, admin: 1, owner: 2 }

// Mirrors the API's rules; the API enforces them; this only hides controls
// that would be refused.
export const can = {
  manageKeys: (role: Role) => rank[role] >= rank.admin,
  manageMembers: (role: Role) => role === 'owner',
}
