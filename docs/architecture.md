# Post Chief architecture

The application lives in `root/`, with source under `root/src/`. The `apps/` and `packages/` structure from `stack.md` is nested there to follow the project conventions.

React 19 + Vite uses a shared typed API client and TanStack Query for server state. FastAPI exposes normalized organization-scoped operations. SQLAlchemy maps operational data to PostgreSQL; Alembic owns schema migrations. Celery executes jobs through Redis. PostgreSQL publication records are the durable source of job state, so broker redelivery must never determine whether content was already published.

Campaigns contain master copy, assets and provider overrides. Each connected account has an independent publication record, status, provider ID, failure and retry history. Providers implement the shared contract and advertise actual capabilities. Unsupported metrics are absent rather than reported as zero.

GitHub App installation tokens authorize only installed repositories. Post Chief further restricts access to the selected social workspace and explicitly selected source repositories. Only the social workspace is writable. Optimistic SHA checks preserve concurrent edits. OAuth credentials stay encrypted on the server; short-lived GitHub tokens are never returned to the dashboard or agent.

Public provider APIs can return ambiguous results after a timeout. Publication processing must persist progress and either reconcile using a stable provider key or require human reconciliation. Automatically retrying an uncertain non-idempotent create call risks a duplicate public post.

The self-hosted owner manages connections, organization autonomy and agent keys. Agent actions are scoped and audited; approval binds a specific action and payload. Later providers, ads, billing, public SaaS onboarding and multi-agent orchestration are outside the MVP.

API references: [GitHub App REST](https://docs.github.com/en/rest/apps/apps), [SQLAlchemy declarative mapping](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html). Provider-specific contracts will cite their official API documentation as adapters are implemented.
