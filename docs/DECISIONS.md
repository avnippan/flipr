# Decision log

One row per decision. Add rows; don't rewrite history. Status: Done, Proposed, Parked.

| # | Decision | Why | Status |
|---|---|---|---|
| 1 | Async jobs: presigned S3 upload + SQS + polling | API Gateway has a 29s limit and the pipeline takes ~11s+; keeps images out of Lambda memory | Done |
| 2 | Idempotent eBay posting (client key + conditional DynamoDB write) | A retry could create duplicate listings, which eBay penalizes | Done |
| 3 | Lazy eBay token refresh with a DynamoDB lock; weekly expiry monitor | The daily cron raced, and eBay access tokens last about 2 hours | Done |
| 4 | Token key `USER#{id}` / `TOKEN#{marketplace}` | Adding a marketplace shouldn't need a migration | Done |
| 5 | One template, `Environment` parameter, separate dev/prod stacks | Isolation without config drift | Done (only dev deployed) |
| 6 | Stay on Bedrock; model ID in an env var with no default | Bedrock Claude pricing matches Anthropic's API; missing config should fail loudly | Done |
| 7 | boto3 default credential chain, no explicit keys | Empty explicit keys overrode the Lambda role | Done |
| 8 | Construct third-party clients lazily | Import-time construction crashed every cold start | Done |
| 9 | Pricing from eBay sold comps only; Poshmark +10% shown as an estimate; scrapers archived | No public sold-comp API elsewhere; scraping carries ToS and ban risk | Done |
| 10 | "Post to eBay" disabled in the public demo; auth deferred | An admin key can't live in client JS; no use case yet for per-user OAuth | Done |
| 11 | Scan history in localStorage | One user's device needs no backend or auth | Done |
| 12 | `item_specifics` separate from description; render the backend `hashtags` field | Fields map to eBay's form; the frontend had read a field that never existed | Done |
| 13 | Retry the S3 fetch with backoff (1, 2, 4, 8s) | The upload can finish after the Lambda starts | Done (mitigation only) |
| 14 | Resize photos in the browser; worker rejects over 3.5 MiB raw | Bedrock limit is ~3.75 MiB raw (5 MiB base64); smaller files also upload faster | Proposed |
| 15 | Retry only transient errors; re-raise the original exception | Permanent errors were retried and hidden as RetryError | Proposed |
| 16 | Padding-top trick instead of CSS `aspect-ratio` in grids | iOS Safari miscomputes row heights | Done |
| 17 | Clipboard fallback via execCommand | `navigator.clipboard` needs HTTPS and the demo is HTTP | Done |
| 18 | Invite code + daily scan cap before sharing; real auth designed later | Cheap protection against bill abuse | Proposed |
| 19 | HTTPS through CloudFront | "Not Secure" warning hurts trust | Proposed |
| 20 | Don't compete on photo-to-listing; test visible comps, confidence flags, and niche accuracy | The free eBay tool and paid tools already cover the core loop | Unvalidated |
| 21 | Price from active eBay listings (0.95 x median) and label them asking prices; supersedes the "sold comps" wording in #9 | Browse API has no sold filter; sold data needs Marketplace Insights or a paid source | Done |
