# Flipr Deployment Guide

## Prerequisites

- AWS CLI configured (`aws configure`)
- SAM CLI installed (`pip install aws-sam-cli`)
- SSM parameters set (see below)

## SSM Parameters (one-time setup)

```bash
aws ssm put-parameter --name /flipr/ebay-app-id               --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/ebay-cert-id              --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/ebay-fulfillment-policy-id --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/ebay-payment-policy-id    --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/ebay-return-policy-id     --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/ebay-merchant-location-key --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/openai-api-key            --value "YOUR_VALUE" --type SecureString
aws ssm put-parameter --name /flipr/api-key                   --value "YOUR_VALUE" --type SecureString
```

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
  -d '{"marketplace": "ebay"}'
```

Response (202 Accepted):
```json
{
  "job_id": "abc-123",
  "upload_url": "https://s3.amazonaws.com/...presigned...",
  "status_url": "/jobs/abc-123",
  "expires_in": 300
}
```

### Step 2 — Upload image directly to S3

No Lambda involved — client uploads directly to the presigned URL.

```bash
curl -X PUT "<upload_url>" \
  -H "Content-Type: image/jpeg" \
  --data-binary @path/to/image.jpg
```

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
