# FLIPR notes for Claude Code

Read `docs/STATUS.md` first (local file, gitignored) for current state and known problems.

## What this is
Photo of a clothing item -> Bedrock Claude vision -> eBay sold comps -> price -> eBay and Poshmark listing drafts. Serverless on AWS (Lambda, API Gateway, SQS, DynamoDB, S3). Frontend is a single `index.html` (React via UMD, no build step) hosted on S3.

## Layout
- `template.yaml`: all AWS resources (SAM). `samconfig.dev.toml`, `samconfig.prod.toml`.
- `backend/app/lambda_handlers/`: create_job, process_job, get_job, post_to_ebay, refresh_ebay_token (analyze_item is deprecated).
- `backend/app/services/`: bedrock_vision, bedrock_listing, listing (prompts + validation), pricing, ebay_poster, ebay_token.
- `backend/app/services/scrapers/`: archived research code. Not used. Don't wire it in.
- `index.html`: the whole frontend.
- `scripts/`: test helpers. `docs/`: decisions, prompts.

## Commands (repo root, PowerShell)
- Build and deploy: `sam build` then `sam deploy --config-file samconfig.dev.toml`. Always build first; deploy ships whatever is in `.aws-sam/build`.
- Frontend deploy: `aws s3 cp index.html s3://flipr-demo-821122422427/index.html --content-type "text/html"`
- Logs: `aws logs tail /aws/lambda/flipr-process-job-dev --since 10m`
- Upload test: `scripts/race-test.ps1`
- Tests: check `tests/` before assuming; integration tests may call real AWS.

## Rules
- Official marketplace APIs only. No scraping.
- Show the diff before applying changes. Don't commit or push unless asked.
- Commit messages: plain imperative subject, short body about why, one logical change per commit, matching the existing history. Never commit secrets or scratch files.
- At the end of a task, state what you verified (command + result) and what you didn't. Update `docs/STATUS.md` if the state changed.
- Config comes from environment variables. Don't hardcode model IDs, table names, or account IDs in code.
- Don't read `.aws-sam/` or `item_images/` (build output and test photos). They cost tokens and tell you nothing.

## Gotchas (each cost real debugging time)
- Bedrock Converse rejects images over ~3.75 MiB raw (it measures base64, 5 MiB). Reject over 3.5 MiB (3,670,016 bytes) before calling it.
- boto3: never pass explicit credentials. Explicit empty values override the Lambda role. Use the default chain.
- Retries (tenacity): retry only transient error codes, use `reraise=True`. Permanent errors like ValidationException must fail on the first attempt.
- Create third-party clients lazily inside functions. Import-time construction crashes every cold start.
- Each Lambda has its own IAM role. New AWS calls need policy changes in `template.yaml`, not on the IAM user.
- SSM parameters referenced by CloudFormation must be type `String`, not `SecureString`.
- DynamoDB returns `Decimal`; JSON encoding needs a default encoder.
- A failed first deploy leaves the stack in ROLLBACK_COMPLETE. Delete the stack before redeploying.
- Env var for the jobs table is `DYNAMODB_TABLE_NAME` (not JOBS_TABLE_NAME).
- Backend field is `hashtags` (there is no `style_tags`). `item_specifics` stays separate from the prose `description`.
- Poshmark price is a +10% heuristic over the eBay comp. Keep the on-screen disclosure.
- Frontend: no CSS `aspect-ratio` in grids (iOS Safari bug), use the padding-top trick. Clipboard needs the execCommand fallback on HTTP. Blob URLs die on reload.

## Don't change without asking
- `copyToClipboard` fallback.
- The disabled "Post to eBay (demo mode)" button and its label.
- eBay posting idempotency and the daily rate limit.
- `services/scrapers/` (stays archived and unused).
