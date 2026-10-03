# Expanded integration gates

The owner expanded the requested wiring beyond the original twelve implementation gates on October 2, 2026, using Metricool's integration catalog as the reference. The previous four-network core is an accepted foundation, not completion of this expanded roadmap. OAuth, app approval and live owner acceptance remain one final pass. Do not require owner account setup before implementing each integration.

## Completion rule

A connection card is not a completed integration. Each gate needs authenticated discovery/configuration, the relevant operational API, durable state, permissions/errors, dashboard and agent/API exposure, durable feedback, and accurate documentation. Preserve organization boundaries, encrypted credentials, rotating-token safety, independent outcomes and uncertain-write reconciliation. Advertising integrations initially expose account discovery and read-only reporting; ad creation, budgets and spending remain disabled under the existing autonomy policy.

| Gate | Integration | Intended operations | Wiring status |
|---|---|---|---|
| 13 | Pinterest | OAuth, public board selection, image Pins, scheduled delivery, Pin analytics | Implemented; build passed; live owner acceptance pending |
| 14 | YouTube | OAuth/channel discovery, resumable video upload, processing/privacy status, metadata and native statistics | Implemented and deployed; CI passed; live owner acceptance pending |
| 15 | TikTok personal | Login, current creator information, reviewed inbox upload handoff, native privacy/editing, persisted upload/publish status | Implemented inbox path and deployed; CI passed; live owner acceptance pending |
| 16 | TikTok business | Business authentication/discovery and supported organic/account reporting; separate from personal and ads | Implemented and deployed; CI passed; live owner acceptance pending |
| 17 | Google Business Profile | OAuth, account/location selection, supported local-post creation/status and business performance | Implemented and deployed; CI passed; live owner acceptance pending |
| 18 | Web and blog | Website analytics connection/data, RSS/Atom discovery and bounded ingestion into agent context; no fake social publication | Implemented; typecheck/build passed; deployment and live owner acceptance pending |
| 19 | Twitch | OAuth, channel/account context, supported analytics/reporting; no generic feed-post capability | Pending |
| 20 | Meta Ads, Google Ads, TikTok Ads | OAuth/account selection, read-only account/campaign performance with explicit metric semantics and dates | Pending |
| 21 | Looker Studio | Authenticated reporting/export contract or connector, access controls and stable schemas | Pending |

LinkedIn and Bluesky already have adapters but remain deferred for owner acceptance, per prior direction. Their presence does not substitute for any newly requested integration. Facebook, Instagram, Threads and X remain supported throughout expansion.

## Delivery sequence and boundaries

- Complete one provider through the existing campaign, worker, dashboard, analytics and feedback paths before claiming its wiring complete.
- Use official current provider documentation to determine actual capabilities. Preserve provider-specific review/verification restrictions as visible outcomes.
- Do not buy API credits, create public posts, apply native edits, write the social workspace or change ad campaigns without the relevant owner action/review.
- Keep native zero values separate from missing/unavailable counters. Different reporting windows and metric meanings must remain explicit.
- Maintain one final owner checklist covering configured credentials, exact redirects, scopes, app review, consent and intended live content.
- Under current developer instructions, do not add or run tests unless requested. Use type checking, production builds, Python syntax checks and source review; inspect existing CI when pushes trigger it.

## Current execution plan

- [x] Gate 13: implement Pinterest adapter and OAuth; protect refresh-token rotation using durable account intent.
- [x] Gate 13: expose public board discovery/selection without exposing tokens; retain board identity in publication intent.
- [x] Gate 13: connect campaign types, dashboard, native metrics and GitHub feedback. Typecheck, production build and API syntax checks passed; deployment evidence remains separate from live acceptance.
- [x] Gate 14: implement YouTube channel consent, resumable upload/checkpoints, processing and actual privacy checks, video/audience controls, durable public metadata and native statistics. Build/typecheck and Python syntax checks passed; deployment and live acceptance remain separately evidenced.
- [x] Gate 15: Login Kit and rotating grants, current creator/preview/explicit owner consent, bounded MP4 inbox transfer, durable task/status and awaiting-owner completion, public video counters and feedback. Native TikTok completes caption/privacy and posting; Direct Post is not claimed.
- [x] Gate 16: separate account-holder authorization/inspection and rotating grants, seven-day UTC profile metrics and bounded public post pagination, durable scoped reports/history/export and dashboard/agent exposure. Reporting-only campaign guards and final owner checklist added; live acceptance remains pending.
- [x] Gate 17: implement Google business.manage consent and bounded location discovery, STANDARD local posts with persisted accepted identity/LIVE polling, scoped post insights and daily location performance reports. Local verification and deployment evidence follow separately; live acceptance remains pending.
- [x] Gate 18: implement readonly GA4 property discovery/totals/daily/channel reports and bounded public RSS/Atom discovery/source snapshots, shared history/export/agent scopes and reporting-only campaign guards. Local verification and deployment evidence follow separately; live acceptance remains pending.
- [ ] Gates 19–21: continue with Twitch, read-only ads reports and Looker Studio.

Official initial sources: [Pinterest OpenAPI](https://github.com/pinterest/api-description), [Pinterest authorization](https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/), [YouTube uploads](https://developers.google.com/youtube/v3/docs/videos/insert), [TikTok publishing requirements](https://developers.tiktok.com/doc/content-sharing-guidelines).
