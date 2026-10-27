# Flipr Deployment Guide

## Prerequisites

- AWS CLI configured (`aws configure`)
- SAM CLI installed (`pip install aws-sam-cli`)
- SSM parameters set (see below)

## SSM Parameters (one-time setup)

```bash
aws ssm put-parameter --name /flipr/ebay-app-id               --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/ebay-cert-id              --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/ebay-fulfillment-policy-id --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/ebay-payment-policy-id    --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/ebay-return-policy-id     --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/ebay-merchant-location-key --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/api-key                   --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/invite-code               --value "YOUR_VALUE" --type String
aws ssm put-parameter --name /flipr/demo-website-domain       --value "<demo-bucket>.s3-website-us-east-1.amazonaws.com" --type String
```

`/flipr/invite-code` gates `POST /jobs` (sent as the `X-Invite-Code` header). It is a speed bump against casual abuse, not real authentication. To rotate it, run `put-parameter` again with `--overwrite`; warm Lambdas pick up the new value after their next cold start. The daily cap on jobs is the `DailyJobLimit` stack parameter (default 50 per UTC day).

`/flipr/demo-website-domain` is the S3 website endpoint (no `http://`) of the demo bucket. The bucket was created outside this stack and the stack never modifies it; CloudFront only reads from it.

## Alarm notifications

The stack creates a CloudWatch alarm on failed jobs and an SNS topic for it. To get an email when it fires, deploy with your address (don't commit it to `samconfig`):

```bash
sam deploy --config-file samconfig.dev.toml --parameter-overrides Stage=dev Environment=dev AlarmEmail=you@example.com
```

AWS then emails a confirmation link, and notifications start only after you click it. `--parameter-overrides` replaces the list in the samconfig file, so repeat `Stage` and `Environment` as shown.

## Serving the demo over HTTPS

The stack creates a CloudFront distribution in front of the demo bucket (S3 website endpoints are HTTP only). After `sam deploy`, the `DemoUrl` and `DemoDistributionId` outputs give the HTTPS URL and the distribution ID. The first deploy takes 5 to 15 minutes while CloudFront provisions.

The deploy user needs CloudFront permissions for distributions and cache policies (`CreateDistribution`, `UpdateDistribution`, `DeleteDistribution`, `GetDistribution*`, `TagResource`, `CreateInvalidation`, plus `CreateCachePolicy`, `UpdateCachePolicy`, `DeleteCachePolicy`, `GetCachePolicy*`). Add them as an inline or customer-managed policy, since the user is at its managed-policy limit.

```bash
curl -I https://<DemoUrl domain>/     # 200
curl -I http://<DemoUrl domain>/      # 301 to https
```

After each frontend change, upload `index.html` and invalidate the cache (the cache TTL is 60 seconds, so the invalidation only makes it instant):

```bash
aws s3 cp index.html s3://<demo-bucket>/index.html --content-type "text/html"
aws cloudfront create-invalidation --distribution-id <DemoDistributionId> --paths "/*"
```

The API and image bucket already allow any origin in CORS, so the new HTTPS origin needs no CORS change. No custom domain or WAF is configured.

## Build and Deploy

```bash
cd flipr/

sam build

sam deploy \
  --stack-name flipr-prod \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides Stage=prod
```

Note the API URL from the deploy output — you'll need it below.

---

## Testing the async job flow

### Step 1 — Create a job

```bash
curl -X POST https://YOUR_API_URL/prod/jobs \
  -H "Content-Type: application/json" \
  -H "X-Invite-Code: YOUR_INVITE_CODE" \
  -d '{"marketplace": "ebay"}'
```

Response (202 Accepted):
```json
{
  "job_id": "abc-123",
  "upload_url": "https://s3.amazonaws.com/...presigned-post...",
  "upload_fields": {"key": "...", "Content-Type": "image/jpeg", "policy": "..."},
  "status_url": "/jobs/abc-123",
  "expires_in": 300
}
```

### Step 2 — Upload image directly to S3

No Lambda involved — client uploads directly to S3 with a presigned POST. S3 rejects files over 3.5 MiB (3,670,016 bytes). Send every `upload_fields` entry as a form field, with `file` last.

```bash
curl -X POST "<upload_url>" \
  -F "key=<upload_fields.key>" \
  -F "Content-Type=image/jpeg" \
  -F "<other upload_fields entries>" \
  -F "file=@path/to/image.jpg"
```

Errors from step 1: `401` for a missing or wrong invite code, `429` once the daily job cap is reached.

### Step 3 — Poll job status

```bash
curl https://YOUR_API_URL/prod/jobs/{job_id}
```

Response while processing:
```json
{ "job_id": "abc-123", "status": "processing", "created_at": 1749600000 }
```

### Step 4 — Retrieve results when complete

When `status` is `"complete"`, the full pipeline output is included:

```json
{
  "job_id": "abc-123",
  "status": "complete",
  "created_at": 1749600000,
  "marketplace": "ebay",
  "metadata": { "brand": "Levi's", "item_type": "denim jacket", ... },
  "comps": { "median_price": 42.00, "suggested_price": 39.90, ... },
  "listings": [
    { "platform": "ebay", "title": "...", "description": "...", ... },
    { "platform": "poshmark", "title": "...", "description": "...", ... }
  ]
}
```

If `status` is `"failed"`, the `error` field contains the reason.

---

## Admin endpoints (API key required)

Retrieve the generated API key value:

```bash
aws apigateway get-api-key \
  --api-key $(aws cloudformation describe-stacks \
    --stack-name flipr-prod \
    --query "Stacks[0].Outputs[?OutputKey=='AdminApiKeyId'].OutputValue" \
    --output text) \
  --include-value \
  --query "value" \
  --output text
```

### Post a listing to eBay

The endpoint is idempotent — generate a UUID client-side before the first attempt and pass it as `idempotency_key`. If you retry the exact same request with the same key, the cached eBay URL is returned without posting again.

```bash
curl -X POST https://YOUR_API_URL/prod/admin/post-to-ebay \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_ADMIN_API_KEY" \
  -d '{
    "idempotency_key": "client-generated-uuid-here",
    "listing": {
      "title": "Levi'\''s 501 Jeans Dark Wash 32x30",
      "description": "...",
      "suggested_price": 39.90
    },
    "metadata": {
      "brand": "Levi'\''s",
      "item_type": "jeans",
      "condition": "good",
      "size": "32x30",
      "color": "dark wash",
      "material": "denim"
    }
  }'
```

Response (first call — `cached: false`):
```json
{
  "ebay_url": "https://www.ebay.com/itm/...",
  "item_id": "v1|...",
  "idempotency_key": "client-generated-uuid-here",
  "cached": false
}
```

Retry with the same `idempotency_key` returns the same response immediately (`cached: true`) without calling eBay again. A `409` means the first request is still in flight — retry after a few seconds.

---

## Teardown

```bash
aws s3 rm s3://flipr-images-ACCOUNT_ID-prod --recursive
sam delete --stack-name flipr-prod
```
