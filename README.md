# Post Chief

Self-hosted social planning, publishing, analytics and agent control for 404 Builds. GitHub remains the durable content workspace; Post Chief stores operational state and executes publishing.

Core networks: Facebook, Instagram, Threads and X. Bluesky and LinkedIn are optional follow-ups. X setup is documented in [provider setup](docs/providers.md#x-current-priority); live X acceptance remains pending owner consent and reviewed content.

## Layout

- `docs/`: product plan, stack and architecture.
- `root/`: application runtime, dependency manifests and deployment.
- `root/src/apps/api/postchief/`: FastAPI application.
- `root/src/apps/worker/`: Celery publishing and analytics jobs.
- `root/src/apps/web/`: React dashboard.
- `root/src/packages/`: provider contracts, API client and shared types.
- Sibling `.Post Chief/.codex/`: implementation plan and overnight log (outside this repository).

## Development

From `root/`, create a Python 3.12+ virtual environment and install `pip install -r requirements.lock`, then `pip install --no-deps -e .`. Run `npm ci` for the dashboard. Copy `.env.example` to `.env` and generate unique keys (see the example). Set `DATABASE_URL` to a PostgreSQL database; SQLite is supported for isolated development tests.

Run `alembic upgrade head`, then `uvicorn postchief.main:create_app --factory --reload`. In another terminal run `npm run dev`. Run backend checks with `python -m pytest` and frontend checks with `npm run typecheck` and `npm run build`.

## Deployment

The owner expanded the product beyond the accepted core workflow. The [expanded integration gates](docs/integration-roadmap.md) track remaining publishing, web/blog and reporting APIs. Those unfinished integrations are not covered by the original gate-completion claim.

The core MVP networks are Facebook, Instagram, Threads and X. X supports either direct OAuth/API delivery (paid credits) or a manual caption/image handoff with owner-reported post URLs. LinkedIn and Bluesky are deferred. Account setup and live owner acceptance are consolidated in the [MVP final pass](docs/mvp-final-pass.md).

From `root/`, configure `.env` and run `docker compose up --build -d`. PostgreSQL and Redis stay on the private container network. Deploy the API behind HTTPS before connecting provider accounts. Use migrations before application upgrades, back up PostgreSQL and the media volume, and keep the encryption key with the backup. Provider tokens cannot be decrypted with a different key.

Provider account setup and live publishing validation are separate from local tests. Never treat transport fixtures as proof of a successful public post. See [operations](docs/operations.md), [provider setup](docs/providers.md), [GitHub setup](docs/github-setup.md), [agent controls](docs/agents.md) and [feedback](docs/feedback.md).

Gates 1–11 are implemented, and [Gate 12 MVP acceptance](docs/dogfood.md) is complete for the live Facebook, Instagram and Threads workflow. On October 2, 2026, the owner verified scheduled Facebook/Instagram publication, added Threads to the same campaign, collected native analytics and wrote reviewed GitHub feedback. Existing campaigns support adding a destination with its own copy, media and delivery time. Published Facebook feed text has a separate review flow; its live acceptance remains pending. Bluesky and LinkedIn also require live account acceptance. This workflow runs without Metricool; retiring its other schedules and integrations remains a separate owner decision. See `docs/plan.md` for the full product plan.
