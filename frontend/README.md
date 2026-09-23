# Gate dashboard

React dashboard for Gate: sign in, org overview (spend and recent activity),
API keys, the request log and members.

Vite, React, TypeScript, TanStack Query, React Router, react-hook-form with
zod, and Recharts. Tests use Vitest, React Testing Library and MSW.

## Development

Run the API on port 8000 (see the root README), then:

```sh
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000`, so the session cookie is
same-origin in development.

## API types

The types in `src/api/schema.d.ts` are generated from the backend's OpenAPI
document; don't edit them by hand. After changing the API:

```sh
npm run gen:api     # rewrite openapi.json and src/api/schema.d.ts
npm run check:api   # fail if they're out of date
```

## Checks

```sh
npm run check       # lint, typecheck, tests, build
```
