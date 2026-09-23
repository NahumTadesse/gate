// Friendly names for the generated schema types. Nothing is hand-written:
// every shape comes from the backend's OpenAPI document (npm run gen:api).
import type { components } from './schema'

type Schemas = components['schemas']

export type User = Schemas['UserOut']
export type Me = Schemas['MeOut']
export type Org = Schemas['OrgOut']
export type Role = Org['role']
export type ApiKey = Schemas['ApiKeyOut']
export type CreatedApiKey = Schemas['CreatedApiKeyOut']
export type ApiKeyInput = Schemas['ApiKeyIn']
export type Member = Schemas['MemberOut']
export type MemberInput = Schemas['MemberIn']
export type RequestRow = Schemas['RequestOut']
export type RequestPage = Schemas['RequestPage']
export type UsageReport = Schemas['UsageReport']
export type UsageRow = Schemas['UsageRow']
