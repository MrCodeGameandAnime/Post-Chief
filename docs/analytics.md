# Analytics ingestion and history

Celery beat scans published destinations once per minute. Collection has an independent database lease and refresh deadline. Successful reads append immutable snapshots and are repeated hourly. Safe transient failures retry after ten minutes; permission/auth failures retain a visible error and retry after one day. Manual refresh records due work in the database without depending on Redis availability.

`GET /api/analytics` returns latest snapshots per destination with campaign/provider/account context, error and next collection time. It is paginated. `GET /api/analytics/publications/{id}` returns history. `POST /api/analytics/publications/{id}/refresh` requests collection. All routes enforce organization boundaries and `analytics:read` or `analytics:collect` scopes.

Snapshots preserve provider-specific data alongside normalized numeric values and explicit metric meanings. Missing metrics stay absent, while a native zero stays zero. Bluesky replies map to comments and reposts map to shares with those meanings attached. Reactions remain distinct from likes; reach, views and impressions remain distinct. Reports do not sum unlike metrics across providers. Current adapters expose only native endpoints supported by their actual scopes; missing approved LinkedIn analytics access is shown as a permission issue.

The owner Analytics page shows latest metrics, timestamps, collection errors, refresh requests, provider-specific data and historical snapshots. Collection tests use provider fixtures and real database state. Live counters still require configured accounts and published posts.
