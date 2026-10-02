# GitHub feedback loop

`GET /api/feedback/campaigns/{id}/preview` builds a reviewed post record, publication ledger and native analytics summary for the selected social workspace. Post records must live under `posts/` with `.md` extension. Default paths are `posts/{campaign-id}.md`, `docs/CONTENT_LEDGER.md` and `docs/ANALYTICS.md`.

Generated campaign blocks have stable markers. Repeated syncs replace those blocks without duplicating entries or changing surrounding owner-written text. Broken/duplicate markers are a conflict. Ordinary UTF-8 files up to 1 MiB are supported; symlink paths/parents and truncated trees are rejected. Provider credentials, encrypted processing state and signed media URLs are excluded. Metric meanings and missing availability remain explicit.

`POST /api/feedback/campaigns/{id}/sync` takes the preview's `revision`, `digest` and `base_commit`. It rejects changed campaign copy, publication/analytics output, owner-written workspace text or branch head. All three files enter one Git tree and commit; the branch update is fast-forward-only. Unchanged reviewed feedback returns the current commit without writing. A lost response can be resolved with a fresh preview; identical generated content is a no-op.

The dashboard editor exposes preview and Write reviewed feedback. Agents need campaign/analytics/GitHub read scopes and `github:write`; default workspace writes require approval. Submit managed `feedback.sync` with campaign target ID and the three preview fields. Approval binds the reviewed digest and revision too. The maintainer can run the loop after publishing and again after analytics collection; no automatic LLM or filler content is generated.

Official API contracts: [Git trees](https://docs.github.com/en/rest/git/trees), [Git commits](https://docs.github.com/en/rest/git/commits), [Git references](https://docs.github.com/en/rest/git/refs). The implementation supplies the existing base tree and commit parent, preserving unrelated files and concurrent changes.
