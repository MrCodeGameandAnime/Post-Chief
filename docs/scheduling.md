# Scheduling and delivery

Schedules and retry deadlines live in PostgreSQL. The single Celery beat service asks a dispatcher to scan due publications every fifteen seconds. Redis carries immediate publication IDs; long-lived schedules are never stored as broker countdown tasks. A broker outage leaves database work due for a later sweep.

The worker atomically claims one publication, records a ten-minute lease, and commits before provider I/O. Duplicate deliveries cannot take an active lease. Provider checkpoints and credentials are encrypted; each checkpoint becomes another due database job. Successful destinations are terminal. Campaign status reflects the independent outcomes: scheduled, publishing, published, partial or failed.

Transient, known-safe errors back off from thirty seconds up to one hour with five automatic retries. Preparation and media processing have a twenty-four-hour deadline. A recovered Bluesky publication reconciles its stable record key. Recovered public-write intent on Facebook, Instagram, Threads or LinkedIn is marked `RECONCILE`; a timeout that might have published is never blindly retried.

Use `POST /api/campaigns/{id}/schedule` with an offset-bearing ISO timestamp, or `/publish` for immediate dispatch. `/cancel` stops pending/retrying destinations; an in-flight publication must reach an outcome first. `/api/publications/{id}/retry` retries only that failed destination. Attempted content retains its original copy/assets; create a new draft to revise it. Planner timestamps are returned in UTC for display in the owner's timezone.

Only the owner can `POST /api/publications/{id}/reconcile`. Inspect the provider first. Submit `{"resolution":"published","provider_id":"existing-provider-id"}` for an existing public post, or `{"resolution":"not_published"}` only after confirming no public post exists. The latter permits a separate explicit retry. The resolution is audited; it is an owner assertion, not an automatic provider verification.

Providers fetch private media through two-hour, asset/organization-bound signed URLs. Instagram/Threads and hosted Facebook video require externally reachable HTTPS `PUBLIC_URL`. Signed URLs remain credentials: protect access logs and do not share them. SVG can be stored privately but must be rasterized for publication. The queue rejects SVG delivery.

Run `docker compose up -d --build api worker scheduler` from `root/`. Run one beat instance. Production workers use Linux prefork with process time limits; a publication HTTP operation also has bounded network timeouts. Redis uses persistent AOF. Back up PostgreSQL, media and the encryption/signing keys together. Rotating the encryption key without re-encrypting stored state makes recovery unavailable.

Behavioral tests cover duplicate delivery, partial failures, retries/backoff, processing checkpoints, stale public-write recovery, cancellation and signed media boundaries. Native account publishing is a separate live acceptance check.
