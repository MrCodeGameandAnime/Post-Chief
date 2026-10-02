# Owner dashboard

Gate 8 adds owner sign-in, overview, month/week/list planner, campaign editor, provider connections, assets and GitHub workspace selection. Campaigns appear once with independent destination outcomes. The editor supports master copy, provider copy/media overrides, saved drafts, local-time scheduling, immediate publish, cancellation, destination retry and explicit uncertain-result reconciliation.

Times use the browser's timezone; scheduling converts the local value to an offset-bearing UTC ISO timestamp. Save edits before scheduling. Publication attempts keep immutable content. New copy requires a new draft after an attempt.

The dashboard uses the shared typed API client, HttpOnly owner session and in-memory CSRF token. Tokens/passwords are never persisted by the browser. OAuth proceeds through server routes; app credentials and scopes must already be configured. Provider failures show their action requirement. LinkedIn company pages are discovered from an authorized member connection.

The production Docker image builds the React app and serves it at `/` from FastAPI, with API routes at `/api`. Set `FRONTEND_URL` and `PUBLIC_URL` to the HTTPS origin in production and `SECURE_COOKIES=true`. Development uses Vite's `/api` proxy. This gate does not yet include analytics, agent controls or approval views; those follow in Gates 9–10.
