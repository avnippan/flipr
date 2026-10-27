# Flipr

An AI-powered resale automation tool. Upload a clothing photo and get back platform-specific listings for eBay and Poshmark, priced from comparable active eBay listings (asking prices, not confirmed sales).

**[Live demo →](http://flipr-demo-821122422427.s3-website-us-east-1.amazonaws.com/)**

---

## What it does

Resellers spend most of their time on two things: figuring out what an item is worth and writing the listing. Flipr automates both.

1. **Upload a photo** — goes straight to S3 via a presigned URL, never through the API server
2. **AI identifies the item** — AWS Bedrock (Claude Sonnet 4.5, Converse API) reads brand, type, condition, color, material, and notable details
3. **Active-listing pricing** — eBay Browse API pulls comparable *active* listings and sets a suggested price at 95% of their median, priced to move rather than sit. These are asking prices, not what items actually sold for, and the UI says so on every price. The source sits behind a swappable comps provider that labels its data as sold or active, so a sold-price source can be added without changing the pipeline
4. **Platform-specific listings** — Bedrock generates separate copy for eBay and Poshmark, each following that platform's voice, structured item specifics, and format rules
5. **Quality enforcement** — a deterministic post-processing pass strips marketing filler, enforces hashtag format, and maps categories — no hallucinated fields reach the user
6. **Review and edit** — every field is user-editable before use; nothing gets copied without a chance to correct it

Live eBay posting is implemented (idempotent, rate-limited, three-step Sell API flow) but disabled in the public demo — it requires an authenticated admin endpoint that can't safely be exposed client-side. See [Architecture](#architecture) for why.

### What the pricing is and isn't

- **Is:** the median of comparable active eBay listings, discounted 5%, with the sample size, range and individual comps shown next to the price.
- **Isn't:** a sold-price or market-value model. eBay's Browse API has no sold filter, and no sold-price data is used anywhere today.
- **Poshmark:** the listed price is the eBay figure plus 10%, a disclosed heuristic and not Poshmark data.

---

## Architecture

Production runs serverless on AWS. A local FastAPI server exists for development but is not what's deployed.

```
Client
  |
  |-- POST /jobs ------------------------> CreateJobFunction
  |                                          |
  |                                          |-- writes job record -> DynamoDB (JobsTable)
  |                                          |-- generates presigned S3 URL
  |                                          `-- enqueues -> SQS (AnalysisQueue)
  |
  |-- PUT {presigned_url} ----------------> S3 (direct upload, bypasses API)
  |                                          |
  |                                          SQS triggers --> ProcessJobFunction
  |                                                              |
  |                                                              |-- Bedrock Claude -> vision (ItemMetadata)
  |                                                              |-- eBay Browse API -> active-listing comps (CompResult)
  |                                                              |-- Bedrock Claude -> listings x2, concurrent
  |                                                              |     (eBay + Poshmark, item_specifics separated
  |                                                              |      from prose description)
  |                                                              `-- writes results -> DynamoDB
  |
  `-- GET /jobs/{id} ---------------------> GetJobFunction (poll for status/results)

Failed jobs retry 3x via SQS, then land in AnalysisDLQ instead of looping forever.

POST /admin/post-to-ebay --------------> PostToEbayFunction
  (API-key gated, idempotent via conditional DynamoDB write, 50/day rate limit,
   lazy OAuth token refresh with a distributed lock against concurrent refresh)
```

### Why async, not synchronous

The pipeline (vision + pricing + two listing generations) takes 8-15+ seconds. API Gateway's synchronous limits and Lambda's per-request model don't hold an open connection well for that long. `POST /jobs` returns immediately with a presigned upload URL; the client uploads directly to S3, and a queue-triggered worker does the real work. The client polls for completion.

### Why idempotency keys on posting

A retried `POST /admin/post-to-ebay` call (client timeout, flaky network) could otherwise create a duplicate live eBay listing, and eBay penalizes accounts for duplicates. A conditional DynamoDB write claims the request before calling eBay; a repeated call with the same key returns the cached result instead of posting again.

### Why the demo's posting button is disabled

Live posting needs an authenticated admin API key. Shipping that key in client-side JS would let anyone who opens dev tools post live listings under a real eBay seller account. The demo shows the full review/edit/confirm flow and stops short of the live call, the mechanism is real and tested, just not exposed publicly.

---

## Tech stack

| Layer              | Technology                                            |
| ------------------ | ------------------------------------------------------ |
| Compute            | AWS Lambda (Python 3.14)                                |
| API                | API Gateway (REST)                                       |
| Async processing   | SQS + dead-letter queue                                  |
| AI / Vision        | AWS Bedrock, Claude Sonnet 4.5 (Converse API)             |
| Content safety     | AWS Bedrock Guardrails                                    |
| Image storage      | S3 (presigned uploads, bypasses the API server)           |
| Job / token state  | DynamoDB (3 tables: jobs, eBay tokens, rate limits)        |
| Pricing data       | eBay Browse API (active listings; asking prices)             |
| eBay posting       | eBay Sell API (Inventory to Offer to Publish)               |
| Secrets            | SSM Parameter Store                                          |
| IaC                | AWS SAM / CloudFormation                                       |
| Local dev API      | FastAPI (async), for the batch/sync dev workflow only            |

---

## Project structure

```
template.yaml              # SAM/CloudFormation -- all AWS resources
samconfig.dev.toml         # dev stack config
samconfig.prod.toml        # prod stack config
index.html                 # Interactive demo (single-file React, no build step)

backend/
|-- main.py                    # FastAPI app -- local dev only, not what's deployed
|-- requirements.txt
`-- app/
    |-- config.py               # Pydantic Settings
    |-- lambda_handlers/        # Production entry points
    |   |-- create_job.py       # POST /jobs
    |   |-- process_job.py      # SQS-triggered: vision -> pricing -> listings
    |   |-- get_job.py          # GET /jobs/{id}
    |   |-- post_to_ebay.py     # POST /admin/post-to-ebay (idempotent, rate-limited)
    |   |-- refresh_ebay_token.py  # Weekly cron -- monitors refresh-token expiry
    |   `-- analyze_item.py     # Deprecated synchronous path, kept for reference
    |-- services/
    |   |-- bedrock_vision.py   # Item identification
    |   |-- bedrock_listing.py  # Listing generation (eBay/Poshmark), routes to listing.py
    |   |-- listing.py          # Prompts, few-shot examples, deterministic validation
    |   |-- pricing.py          # comps provider -> price stats + suggested price
    |   |-- ebay_poster.py      # Sell API -- inventory item -> offer -> publish
    |   |-- ebay_token.py       # Lazy refresh, distributed-lock via conditional write
    |   `-- scrapers/           # Research prototype -- see note below, not used in production
    |-- models/
    |   `-- item.py             # ItemMetadata, CompResult, ListingDraft
    `-- core/
        `-- dynamo_job_store.py # DynamoDB job store implementation
```

### A note on `services/scrapers/`

Early versions of Flipr explored cross-marketplace pricing by scraping Mercari, Depop, and Poshmark directly. That code is kept for reference but is **not used in production and never will be as-is** — none of those platforms offer a public API for sold-price data, and scraping them carries real ToS/account-ban risk at any scale beyond a one-off personal check. Pricing is sourced exclusively from eBay's sanctioned Browse API. Poshmark's listing price is a disclosed heuristic (+10% over the eBay comp, reflecting that platform's negotiation culture), not scraped Poshmark data.

---

## Local setup (FastAPI dev server)

The deployed product is Lambda; this is for local development and testing the pipeline without a full AWS deploy.

**Prerequisites:** Python 3.11+, AWS account with S3 + DynamoDB + Bedrock access, eBay developer account.

```bash
git clone https://github.com/avnippan/flipr.git
cd flipr/backend

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env       # fill in your keys

uvicorn main:app --reload
```

API at `http://localhost:8000`, interactive docs at `/docs`.

---

## Deploying to AWS

See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full SAM/CloudFormation deploy process, including SSM parameter setup.

```bash
sam build
sam deploy --config-file samconfig.dev.toml
```

---

## Sprint history

**Sprints 1-5** — Batch processing, S3 presigned uploads, eBay Browse API pricing with fallback queries, DynamoDB job persistence, eBay Sell API direct posting, cross-market scraper research (later archived, see note above).

**Sprint 6** — Migrated from OpenAI to AWS Bedrock (Claude Sonnet 4.5, Converse API), added Bedrock Guardrails, cross-region inference.

**Sprint 6C** — Migrated to a fully async, event-driven Lambda architecture: presigned S3 + SQS replacing the synchronous endpoint, idempotent eBay posting via conditional DynamoDB writes, lazy OAuth token refresh with a distributed lock (replacing a racy daily cron), marketplace-agnostic token schema, dev/prod stack separation, externalized model configuration. Deployed live to AWS. Frontend rebuilt: batch scan history (localStorage), per-field editable listings with an explicit review/confirm state, per-platform pricing reflecting real fee structures, and a redesigned interface.

**Planned** — Real per-user eBay OAuth and live posting (currently deliberately deferred, no auth layer exists yet, and building one has no current use case beyond this single-user demo). Multi-photo-per-item uploads (front/back/tag), using Bedrock's multi-image input to improve identification accuracy.
