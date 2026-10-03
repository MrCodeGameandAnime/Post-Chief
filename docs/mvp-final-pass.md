# MVP final pass

The owner subsequently expanded implementation scope to the remaining integrations in the reference catalog. Track those unfinished gates in [the expanded integration roadmap](integration-roadmap.md). This checklist records the accepted foundation and its remaining owner validations; it is not a completion claim for the expanded product.

The owner requested continued implementation without waiting for app setup or consent. Core networks are Facebook, Instagram, Threads and X. LinkedIn and Bluesky are deferred. This checklist separates shipped wiring from live owner acceptance; the historical provider phases in `plan.md` do not expand current MVP scope.

## Implementation status

| Workflow | Wiring | Live evidence |
|---|---|---|
| Owner sign-in, campaigns, assets, planner, scheduling, independent outcomes | Implemented | Accepted Facebook/Instagram/Threads campaign |
| GitHub workspace reads, reviewed post/ledger/analytics writes, unchanged previews | Implemented | Owner screenshots and recorded commits |
| Agent scopes, autonomy controls and action audit | Implemented | Automated coverage from original gates; owner operational review remains |
| Facebook/Instagram/Threads consent, publishing and native metrics | Implemented | Accepted; reconnect if provider access changes |
| Add a destination after publication without republishing other networks | Implemented | Owner added Threads to the accepted campaign |
| Reviewed Facebook feed-text edits; native Instagram editing link | Implemented | Facebook replacement acceptance remains |
| X OAuth, text/images, scheduler and explicit paid metrics refresh | Implemented | Credentials, consent and reviewed live acceptance remain |
| X manual handoff, owner-reported URL, copy/media history and reviewed GitHub feedback | Implemented | Owner handoff acceptance remains; no API billing required |
| Production backup procedure and synthetic recovery mechanics | Documented/covered by existing CI | Actual encrypted off-host backup/recovery remains |

## One owner session at the end

Expanded Pinterest setup: configure its app ID/secret, register `PUBLIC_URL/api/connections/oauth/pinterest/callback`, complete consent and choose a public board in Connections before creating a draft. Review one JPEG/PNG Pin, its native result, 30-day metrics and reviewed feedback. This remains pending owner acceptance.

Expanded YouTube setup: enable Data API v3 and Analytics API, configure `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, register `PUBLIC_URL/api/connections/oauth/youtube/callback` and complete channel consent. Review one intended MP4, title, visibility and audience declaration before delivery. Confirm the existing video's processing and actual visibility, native counters and feedback. Google consent/project audit restrictions, including private-only uploads, remain owner-side acceptance checks. No live YouTube upload has been performed during implementation.

Expanded TikTok personal setup: configure Login Kit credentials and the exact `/api/connections/oauth/tiktok/callback`, enable inbox upload/profile/video-list scopes and verify the final HTTPS media URL prefix. Review the displayed creator and intended MP4 before consenting to transfer. Finish caption/privacy/disclosures and posting in the TikTok inbox, then refresh completion in Post Chief and review native counters/feedback. Business-account authorization is tracked separately. No live TikTok transfer has been performed during implementation.

Expanded TikTok business setup: configure the separate business client ID/secret and app-generated account holder authorization URL. Register the exact `/api/connections/oauth/tiktok_business/callback/` including its trailing slash, obtain Accounts API access and grant `user.info.basic`, `user.info.stats`, `user.insights` and `video.list`. Enable native account analytics, connect the intended account, collect profile/recent post reporting, inspect missing/lagging values and history, and export JSON. Confirm reporting connections cannot become campaign destinations. No business account or reporting request has been submitted during implementation.

1. **Deployment/app URLs.** Confirm the final public HTTPS origin. If the temporary tunnel changes, update `PUBLIC_URL`, `FRONTEND_URL`, provider app domains and exact callbacks, then restart API/worker/scheduler. Do not reuse consent URLs from earlier attempts.
2. **Meta.** Resolve any developer-account review. Verify the existing Facebook/Instagram and Threads app settings, requested scopes and accepted tester invitations. Reconnect only where access requires it. See [provider setup](providers.md). Reconnection preserves existing account/publication records.
3. **GitHub.** Confirm the installed App can still read the selected workspace and project repositories and write only the selected social workspace. Preview feedback before approving a write. Retain existing history outside generated blocks.
4. **Choose the X operating mode.** For no API charges, open a saved campaign, expand **X · manual handoff** under Delivery, adjust the X copy and select up to four JPEG/PNG/WebP images (5 MB each). Copy the caption, download images, open X and publish there. Paste the specific post URL and actual publication time, confirm the copy/media were posted and record the URL. Unsaved handoff changes are temporary until recorded. Post Chief records your report without fetching X or collecting metrics. Alternatively, configure OAuth 2.0 `X_CLIENT_ID`/`X_CLIENT_SECRET`, register the exact X callback, review credit/spending settings yourself, reconnect and review intended live content before delivery. Do not add both modes to one campaign.
5. **Content acceptance.** Review a new intended campaign or explicitly selected new destination; confirm native output and independent outcomes. Do not repeat the already accepted Facebook/Instagram campaign. If accepting Facebook edits, review a specific replacement and confirm its native result and edit audit.
6. **Feedback/metrics.** Request native metrics for connected platforms, preserving zero versus unavailable values. X API metrics require an explicit paid refresh; manual X records have unavailable native metrics. Preview and approve GitHub feedback; refresh the preview afterward to confirm zero changes.
7. **UI.** Inspect campaign maintenance and the feedback dialog on desktop and a mobile viewport. Check long captions, URLs, horizontal diffs, downloads and clipboard fallback.
8. **Operations.** Choose an off-host backup destination, take an encrypted production backup with keys/source revision/media, and rehearse restoration into an isolated deployment with dispatch disabled. Follow [operations](operations.md). Existing synthetic recovery evidence does not establish an actual production backup.

## Google Business Profile owner acceptance

- Configure Google web OAuth credentials and register the exact public `/api/connections/oauth/gbp/callback`. Obtain Business Profile project API access and enable Account Management, Business Information, Performance and Google My Business v4 APIs. Grant `business.manage` from the intended business owner/manager account.
- Connect, inspect location names/resource IDs and select the intended location. Confirm the business is verified and eligible for local posts. Review one new STANDARD English post and optional compliant image before publishing; confirm Google's native result and that Post Chief waits for `LIVE`.
- Collect post insights and a daily location report, inspect dates/missing counters, history and JSON export. Confirm location metrics stay distinct from post metrics and that feedback previews preserve the accepted resource/state.
- No Google business location has been contacted or post created during implementation. OAuth, API eligibility and live performance availability remain unvalidated until this pass.

## Manual X record contract

`POST /api/campaigns/{id}/external/x` requires an owner session, CSRF protection, the current campaign revision, actual `body`, `asset_ids`, an HTTPS X/Twitter status URL, timezone-aware `published_at` and `confirmed_published: true`. URLs are normalized to `x.com`, with tracking parameters removed; no external URL is fetched. Future timestamps and unsupported/cross-organization assets are rejected.

Records are immutable audit entries returned as optional `external_posts` in campaign responses. Identical submissions are idempotent. Each campaign supports one manual X record; campaigns with an API X destination cannot record a handoff, and recorded handoffs block subsequent API X additions. Campaign deletion is blocked when a manual publication exists, and its referenced media is retained. Native scheduling/status and other publications are unaffected. Feedback contains the owner-reported record and explicitly unavailable metrics, never invented counters or a verified API outcome.

Manual X does not provide unattended scheduling, native edits, automatic analytics or an X-only campaign creation flow. Create a normal campaign for the connected core networks, then use its handoff. These limits do not block implementing the other MVP gates.
