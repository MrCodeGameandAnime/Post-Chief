# Owner dashboard

Gate 8 adds owner sign-in, overview, month/week/list planner, campaign editor, provider connections, assets and GitHub workspace selection. Campaigns appear once with independent destination outcomes. The editor supports master copy, provider copy/media overrides, saved drafts, local-time scheduling, immediate publish, cancellation, destination retry and explicit uncertain-result reconciliation.

Times use the browser's timezone; scheduling converts the local value to an offset-bearing UTC ISO timestamp. Save edits before scheduling. Publication attempts keep immutable content. New copy requires a new draft after an attempt. The editor labels retained text Original master copy; provider overrides still apply. Later edits made directly on a platform are not synced into this record or its GitHub feedback.

Each destination shows its current status and published-post link when available. Delivery details contains the worker run count: media preparation, processing checks and retries can each add a run. Multiple runs can produce one published post. This counter is retained as `attempts` in API and GitHub records.

An open editor follows the campaign query's fifteen-second refresh for delivery state, errors, post links and reported publication times. Unsaved draft fields keep their local values and opened revision. If a newer saved revision appears, the form retains local text and asks the owner to return to Content and reopen the campaign before editing or scheduling. A missing scheduled time is labelled Unscheduled draft only for drafts; other states show No scheduled time. Reported publication times appear independently for each destination.

The dashboard uses the shared typed API client, HttpOnly owner session and in-memory CSRF token. Tokens/passwords are never persisted by the browser. OAuth proceeds through server routes; app credentials and scopes must already be configured. Provider failures show their action requirement. LinkedIn company pages are discovered from an authorized member connection.

The production Docker image builds the React app and serves it at `/` from FastAPI, with API routes at `/api`. Set `FRONTEND_URL` and `PUBLIC_URL` to the HTTPS origin in production and `SECURE_COOKIES=true`. Development uses Vite's `/api` proxy. Native analytics, agent controls and approval views are also available; see the corresponding runbooks.
