# Operating Post Chief

Run commands from `root/`. The compose deployment serves the built dashboard and API on loopback port 8000; PostgreSQL and Redis remain on the private container network. Put a trusted HTTPS reverse proxy in front of the API for provider access. Configure forwarded-header trust for the actual proxy, not arbitrary clients. Production cookies require HTTPS and `SECURE_COOKIES=true`.

## Startup and checks

After configuring `root/.env`, start the application:

```text
docker compose up -d --build --wait --wait-timeout 120 api worker scheduler
docker compose ps
docker compose exec -T worker celery -A postchief_worker.tasks:app inspect ping --timeout 10
```

Check `GET /api/health` for database-backed readiness and open `/` for the dashboard. The migrate service applies Alembic migrations before API/worker startup. Run exactly one scheduler per deployment. PostgreSQL is the durable source of pending publications and analytics deadlines; Redis provides immediate job delivery. Inspect per-publication outcomes in the dashboard and `docker compose logs --tail 100 api worker scheduler` for operational errors. Logs and backups may contain private content; share only redacted excerpts.

The local bootstrap owner is configured in `.env`. Bootstrap creates the owner only once; changing those fields does not change an existing password. Password rotation through the dashboard is not implemented in this release. Choose a strong permanent owner credential before the initial production bootstrap; do not expose a development database with a temporary password.

## Backup and restore

Back up PostgreSQL, the media volume, the exact running source revision and deployment secrets together. Keep an encrypted copy outside the Docker host; ignored `root/data/` alone is not a disaster-recovery backup. Preserve the encryption key with the database: account credentials and provider checkpoints cannot be recovered with a new key. Preserve the signing key to retain current sessions and unexpired media links. GitHub contains content records, not a replacement for operational database backups.

For a consistent application backup, stop scheduler, worker and API and wait for in-flight jobs to end before dumping PostgreSQL and copying media. An abrupt worker interruption during a public write can require reconciliation. Use `pg_dump` custom format inside the database container, then `docker compose cp` the dump out; avoid piping binary dump bytes through Windows PowerShell. Archive the media volume while application services are stopped. Record migration head, source commit and backup time, then restart services. Do not remove compose volumes when stopping the real deployment.

Restore first into a separate disposable deployment/database with the original keys and restored media. Keep its scheduler and worker stopped while validating record counts, migration head, owner sign-in, private asset downloads and credential decryption. A restored database may contain jobs that already published after the backup: reconcile those against the providers before enabling dispatch. Restore to production only after this review. Never run test migration downgrades or volume cleanup against production data.

## Credentials and upgrades

Revoke compromised agent keys in Agent and issue new scoped keys. Reconnect provider accounts through Connections to replace their encrypted credentials while preserving account identity/history. GitHub App private keys and OAuth client secrets are deployment configuration; rotate through the provider's supported flow and restart all application services using the updated configuration.

Changing `SIGNING_KEY` invalidates sessions and existing signed media links. Pause publishing during that change. Changing `ENCRYPTION_KEY` without re-encrypting every stored credential/checkpoint makes them unreadable. This release has no automated encryption-key rotation command: retain the existing key and backups; plan a verified migration before replacement. Reconnecting accounts alone does not recover pending encrypted publication state.

Before upgrading, record the current commit and take a recoverable backup. Review migration changes and successful remote CI. Stop scheduler/worker/API, build the reviewed revision, migrate, then restart and perform health/worker/dashboard checks. Code rollback may require restoring a compatible database; do not blindly downgrade a live schema.

## Publishing failures

Retry only the failed destination once its reported cause is resolved. Successful destinations remain published. A `RECONCILE` outcome requires checking the provider and explicitly asserting published/provider ID or not published in the owner dashboard. Reconciliation is an owner decision, not an automatic retry. Cancel pending schedules through the campaign editor; cancellation does not remove already published posts.

No service depends on Metricool. Retiring it still requires the live [Gate 12 acceptance](dogfood.md), including review of any existing schedules and owner confirmation.
