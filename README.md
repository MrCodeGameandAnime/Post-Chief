# Post Chief

Self-hosted social planning, publishing, analytics and agent control for 404 Builds. GitHub remains the durable content workspace; Post Chief stores operational state and executes publishing.

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

From `root/`, configure `.env` and run `docker compose up --build -d`. PostgreSQL and Redis stay on the private container network. Deploy the API behind HTTPS before connecting provider accounts. Use migrations before application upgrades, back up PostgreSQL and the media volume, and keep the encryption key with the backup. Provider tokens cannot be decrypted with a different key.

Provider account setup and live publishing validation are separate from local tests. Never treat transport fixtures as proof of a successful public post. See [operations](docs/operations.md), [provider setup](docs/providers.md), [GitHub setup](docs/github-setup.md), [agent controls](docs/agents.md) and [feedback](docs/feedback.md).

Gates 1–11 are implemented, and [Gate 12 MVP acceptance](docs/dogfood.md) is complete for the live Facebook and Instagram workflow. The owner verified scheduled publication, native analytics and reviewed GitHub feedback on October 2, 2026. This workflow runs without Metricool; retiring its other schedules and integrations remains a separate owner decision. See `docs/plan.md` for the full product plan.
