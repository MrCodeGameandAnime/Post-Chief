**React 19 + TypeScript + Vite**

```text
Frontend
React
Vite
TypeScript
React Router
TanStack Query
Tailwind
shadcn/ui

Backend
FastAPI
PostgreSQL
Redis
Celery
```

Application runtime and manifests live in `root/`; source packages live in `root/src/`:

```text
apps/
  web/                 # React + Vite
  api/                 # FastAPI
  worker/              # publishing / analytics workers

packages/
  api-client/          # typed frontend API client
  shared-types/
  provider-contracts/
```

For the frontend specifically:

```text
src/
├── app/
├── components/
├── features/
│   ├── agent/
│   ├── analytics/
│   ├── campaigns/
│   ├── connections/
│   ├── github/
│   ├── inbox/
│   └── planner/
├── hooks/
├── lib/
├── routes/
└── types/
```

TanStack Query is especially useful here because nearly everything is server state:

```text
campaigns
publication statuses
analytics
connections
GitHub repos
agent runs
approvals
```

So polling/refetching things like:

> LinkedIn: PROCESSING → PUBLISHED

becomes straightforward.

And Vite keeps the UI side pleasantly boring:

```text
React dashboard
      ↓ HTTPS
FastAPI
      ↓
Postgres / Redis / Workers
      ↓
GitHub + Social APIs
```
