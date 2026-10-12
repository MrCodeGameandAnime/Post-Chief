# Post Chief

Self-hosted social planning, publishing, analytics, and agent control for
404 Builds. GitHub holds durable content; Post Chief stores operational state
and executes publishing through native provider APIs.

## Documentation

**[USAGE.md](docs/USAGE.md)** is the comprehensive guide for setup, deployment,
the dashboard, scheduling, providers, GitHub feedback, reporting, agents/MCP,
development, and troubleshooting.

- Dashboard: [postchief.404builds.com](https://postchief.404builds.com)
- Remote MCP: `https://postchief.404builds.com/mcp`
- Historical documents: [docs/old/](docs/old/README.md)

## Layout

- `docs/USAGE.md`: current documentation.
- `docs/old/`: archived guides, plans, rollout notes, and acceptance records.
- `root/`: application runtime, manifests, Compose files, and migrations.
- `root/src/apps/`: FastAPI, Celery workers, React dashboard, and Looker connector.
- `root/src/packages/`: provider contracts, typed API client, and shared types.
- `root/mcp/`: remote MCP adapter.
- Sibling `.Post Chief/.codex/`: planning and support artifacts.

Start with [Getting started](docs/USAGE.md#getting-started),
[Deployment](docs/USAGE.md#deployment-and-operations), or
[Local development](docs/USAGE.md#development-and-ci).

Provider grants and live publishing acceptance are separate from local checks.
The guide records capabilities, permissions, approval rules, and limitations.
