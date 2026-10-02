# Gate 12 — 404 Builds acceptance

## Status

Gates 1–11 are implemented and pushed. **Gate 12 MVP acceptance is complete for Facebook and Instagram**, accepted by the owner on October 2, 2026. The owner reviewed and scheduled a JPEG campaign for 09:17 America/New_York. Both publication records completed at 09:17:43, and the owner supplied screenshots of the image visible on both platforms and confirmed the workflow after the once-only verification request. Native Instagram analytics collected at 09:18:10 preserve actual zero likes and comments. After granting `pages_read_user_content` and completing fresh consent, Facebook analytics collected at 09:50:11 preserve actual zero reactions and comments.

The reviewed GitHub feedback was written to `MrCodeGameandAnime/404-builds-social` on `main` in commit `95517c87c219f5e9d6c308bf0ce6626342c73a17`: the post record, content ledger and analytics were committed together. A fresh preview returned `unchanged=true` with the same digest. The owner accepted the create, schedule, publish, status, analytics and GitHub feedback workflow for MVP. Non-secret live IDs, permalink and timing evidence are in the sibling overnight log. Fixture results remain separate from live evidence.

This accepted Facebook/Instagram workflow does not require Metricool. Retiring Metricool's other schedules and integrations is a separate owner decision; no history, schedules or integrations were removed. Other provider implementations have automated coverage but were not included in this live acceptance.

## Follow-up improvements

- Implemented after acceptance: bounded GitHub review dialog with file tabs, readable diffs and a change summary. Browser visual verification remains pending because the local browser preview could not attach.
- Implemented after acceptance: validated Instagram permalinks from native analytics, including a stored-snapshot fallback for existing publications. The originally accepted GitHub record contains a null URL; updating that external record requires reviewing and writing a fresh preview.
- Explain publication attempt counts as adapter preparation stages and distinguish them from public post counts.
- Make the original published caption distinguishable from later owner edits made directly on a platform; the accepted feedback records the original campaign caption.

`root/tests/test_dogfood.py` exercises owner authentication, JPEG upload, campaign creation, future scheduling, immediate dispatch, the native Instagram container adapter, persisted processing and publish-intent checkpoints, duplicate execution protection, native analytics with a real zero, and reviewed GitHub feedback with an idempotent rerun. All external responses are fixtures. Separate Celery tests verify actual Redis queue delivery; both suites run on SQLite and PostgreSQL in CI.

## Setup for repeating live acceptance

1. Configure secrets locally in `root/.env`: Meta App ID/secret and GitHub App ID/slug/PEM/webhook secret. Keep the existing signing/encryption keys. Never send credentials in chat or commit them.
2. Expose the deployment through public HTTPS. Set `PUBLIC_URL` and the dashboard's `FRONTEND_URL` to their exact HTTPS origins, and `SECURE_COOKIES=true`. Register the exact Meta callback and GitHub webhook URLs described in [provider setup](providers.md) and [GitHub setup](github-setup.md). Restart services after configuration changes.
3. Connect the professional Instagram account linked to the authorized Facebook Page through Connections. Verify the displayed account identity and authorization. App roles/review and permissions must cover that account.
4. Install the GitHub App on the intended 404 Builds social workspace and authorized source repositories. Select them in Connections. The workspace should contain the owner's `BRAND.md`, `CONTENT_PLAN.md`, existing templates and approved assets. Test reads before authorizing writes.
5. Identify one intended JPEG post, exact caption, destination account and publication time. The owner must review that concrete campaign before the live write. Public signed media links need to be reachable by Meta without an owner session.

## Live acceptance sequence

Resume only after the owner says setup is ready. Use the selected social workspace and professional Instagram account; do not create filler content for acceptance.

1. Read the workspace brand/content instructions, existing ledger and analytics, and any selected project context. Prepare a warranted draft using approved assets. Record its campaign ID and revision.
2. Review the draft, asset and destination in the dashboard. If the external maintainer uses an agent key, give only required scopes and retain APPROVAL for publishing and GitHub writes. Review the exact managed scheduling/publishing action before approving it.
3. Schedule the intended publication. Observe the scheduler and worker complete the container processing and publish-intent stages. Confirm the returned provider ID against the actual post visible in Instagram. Record the publication time and public permalink; a successful API request alone is insufficient.
4. Verify one public post exists for that campaign. Do not cause another public write to test deduplication. If the result is uncertain, inspect Instagram first and use owner reconciliation; never blindly retry.
5. Collect available native metrics and retain a historical snapshot. Confirm zero versus missing availability. Missing analytics permission is an explicit incomplete acceptance check, not invented metrics.
6. Preview the post record, ledger and analytics changes. Review surrounding owner text and submit the exact preview (or approve `feedback.sync`). Confirm all three files in one GitHub commit; preview again and confirm it is unchanged.
7. Have the owner confirm that creating, scheduling, publishing, checking status, collecting analytics and recording feedback can be performed through Post Chief in normal operation. Only then record Gate 12 complete and decide whether to retire Metricool. Do not delete existing Metricool history or schedules as part of the test.

## Evidence to record

Keep non-secret acceptance evidence in the overnight log: source commit and CI URL, workspace/branch, campaign and publication IDs, reviewed revision, approved destination and time, Instagram provider ID/permalink, observed post result, analytics collection time/availability, GitHub feedback commit, unchanged follow-up preview and owner acceptance. Record any incomplete checks explicitly. Never record tokens, passwords, PEM keys, signed media URLs or hidden chain-of-thought.

See [operations](operations.md) for startup, backups and recovery, [agent controls](agents.md) for approval behavior, and [feedback](feedback.md) for write conflicts.
