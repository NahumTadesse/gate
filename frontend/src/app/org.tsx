import { createContext, useContext } from 'react'
import type { Me, Org } from '../api/types'

export type OrgContextValue = { org: Org; me: Me }

export const OrgContext = createContext<OrgContextValue | null>(null)

export function useOrg(): OrgContextValue {
  const value = useContext(OrgContext)
  if (!value) throw new Error('useOrg must be used inside an org route')
  return value
}
