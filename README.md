# Flipr

An AI-powered resale automation tool. Upload a clothing photo and get back platform-specific listings for Poshmark and eBay with real comp pricing — then publish directly to eBay in one tap via the Sell API.

---

## What it does

Resellers spend most of their time on two things: figuring out what an item is worth and writing the listing. Flipr automates both.

1. **Upload a photo** — image goes to S3
2. **AI identifies the item** — AWS Bedrock Claude Sonnet 4.5 Vision reads brand, type, condition, color, material, and notable details
3. **Content safety filter** — Bedrock Guardrails block inappropriate images before processing
4. **Live pricing** — eBay Browse API pulls recent sold listings and calculates a suggested price at 95th-percentile median (priced to move, not to sit)
5. **Platform-specific listings** — Bedrock Claude generates separate listings for Poshmark and eBay, each following that platform's voice, format rules, and character limits
6. **Quality enforcement** — a deterministic post-processing pass strips marketing language, enforces hashtag format, and maps category fields — no hallucinated categories or banned phrases ever reach the user
7. **Direct eBay posting** — one-tap publish to eBay via the Sell API (createOrReplaceInventoryItem → createOffer → publishOffer). Returns a live listing URL. Poshmark direct posting coming in Sprint 7.

Supports single-item analysis and batch jobs (multiple photos processed concurrently).

---

## Tech stack

| Layer           | Technology                                       |
| --------------- | ------------------------------------------------ |
| API             | FastAPI (async)                                  |
| AI / Vision     | AWS Bedrock Claude Sonnet 4.5 with Converse API  |
| Content Safety  | AWS Bedrock Guardrails (Standard tier)           |
| Image storage   | AWS S3                                           |
| Pricing data    | eBay Browse API (sold listings)                  |
| eBay Posting    | eBay Sell API (Inventory + Account + Fulfillment) |
| Job persistence | AWS DynamoDB                                     |
| Config          | Pydantic Settings                                |
| Logging         | structlog (structured JSON)                      |

---

## Architecture
POST /api/v1/items/analyze          POST /api/v1/batch/uploads → PUT {presigned_url}
│                          POST /api/v1/batch/analyze
▼                                    │
[FastAPI route]                   [Create job → DynamoDB]
│                                    │
▼                                    │ (per image, concurrent)
S3 download                               ▼
│
▼
Bedrock Claude 4.5 Vision → ItemMetadata
(with Guardrails content filter)
│
▼
eBay API → CompResult (pricing stats)
│
▼
Bedrock Claude 4.5 Text → ListingDraft × 2
(Poshmark + eBay, concurrent, with Guardrails)
│
▼
Post-processing validation
(banned phrases, hashtag format,
category mapping, 80-char titles)
│
▼
DynamoDB update → poll /jobs/{id}
│
▼
POST /batch/jobs/{job_id}/items/{index}/post-ebay → eBay Sell API → live listing URL

---

## Key engineering decisions

**AWS Bedrock with guardrails** — vision and listing generation use Claude Sonnet 4.5 via Bedrock's Converse API. Content safety is enforced at the API level via Bedrock Guardrails (Standard tier) with filters for hate speech, sexual content, violence, and prompt injection. Inappropriate images are blocked before processing; flagged listing outputs are rejected with custom error messages. Guardrail configuration is conditional — if `BEDROCK_GUARDRAIL_ID` is empty, the system runs without guardrails (useful for testing).

**Cross-region inference** — the model ID uses the `us.` prefix (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`), routing requests across multiple AWS regions for better availability and lower latency. Bedrock's inference profiles handle failover automatically.

**Deterministic post-processing** — listing quality is enforced programmatically after generation. A regex-based banned-phrase filter strips marketing language (`"elevate your wardrobe"`, `"must-have"`, etc.). A category map overrides the model's category guess with a known-good value. Hashtag count and format are corrected if needed. This separates "does the model understand the item" from "does the output meet spec."

**Retry with exponential backoff** — all external API calls (Bedrock, eBay) use `tenacity` with `wait_exponential` and `retry_if_exception_type`. Transient failures (timeouts, rate limits) retry up to 3 times before propagating.

**Concurrent batch processing** — `asyncio.gather` runs all images in a batch in parallel. One item failing does not cancel others; each updates its own DynamoDB record independently.

**eBay fallback query** — if a specific search query returns no sold comps, pricing drops the last word and retries with a broader query, preventing total failure on obscure items.

---

## Project structure
backend/
├── main.py                   # FastAPI app, routers, CORS
├── requirements.txt
├── .env.example
└── app/
├── config.py             # Pydantic Settings
├── api/
│   ├── schemas.py        # Request/response models
│   └── routes/
│       ├── items.py      # POST /items/analyze
│       └── batch.py      # POST /batch/uploads, POST /batch/analyze, GET /batch/jobs/{id}
├── core/
│   ├── job_models.py     # JobStatus, ItemStatus enums
│   ├── job_store.py      # Abstract job store interface
│   └── dynamo_job_store.py  # DynamoDB implementation
├── models/
│   └── item.py           # ItemMetadata, CompResult, ListingDraft
└── services/
├── bedrock_vision.py # Bedrock Claude 4.5 image analysis
├── bedrock_listing.py # Bedrock Claude 4.5 listing generation
├── vision.py         # Vision service router (Bedrock/OpenAI)
├── listing.py        # Listing service router + validation
├── pricing.py        # eBay sold comps + price calculation
└── storage.py        # S3 download

---

## Local setup

**Prerequisites:** Python 3.11+, AWS account with S3 + DynamoDB + Bedrock, eBay developer account.

```bash
git clone https://github.com/YOUR_USERNAME/flipr.git
cd flipr/backend

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# fill in your keys in .env

# start the API
uvicorn main:app --reload
```

The API will be at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Quick test

```bash
curl -X POST http://localhost:8000/api/v1/items/analyze \
  -F "image=@item_images/mens/tops/t_shirts/thrasher-001.jpg"
```

---

## Demo

See the full pipeline in action without running the backend:

Open `flipr-demo/index.html` directly in any browser. 
No server or build step needed.

The interactive demo shows:

- Upload screen: add up to 3 clothing items
- Processing screen: real-time batch analysis progress
- Results screen: platform-specific listings (Poshmark + eBay)
  - Poshmark: title, category, size, condition, brand, color, 
  style tags, description, price & earnings (80% of listing price)
  - eBay: title (with character count), category, condition, 
  item specifics, description, format & price
  - Copy listings as formatted text ready to paste into either platform
  - **Post to eBay** button publishes the listing live via the Sell API and returns a clickable listing URL

Tech: Single-file React app, no dependencies, Tailwind-inspired 
design system matching the wireframes.

---

## API reference

### `POST /api/v1/items/analyze`

Analyze a single item image synchronously.

**Form fields:**

- `image` (file, required) — JPEG or PNG
- `chest_width_inches` (float, optional) — laid-flat chest measurement
- `body_length_inches` (float, optional) — laid-flat body length

**Response:**

```json
{
  "metadata": {
    "brand": "Thrasher",
    "item_type": "graphic tee",
    "color": "green",
    "size": "M",
    "condition": "good",
    "material": "cotton",
    "notable_details": ["front graphic: Thrasher Skateboard Magazine logo"],
    "search_query": "Thrasher skateboard magazine tee green M"
  },
  "comps": {
    "search_query": "Thrasher skateboard magazine tee green M",
    "sample_size": 18,
    "low_price": 12.00,
    "median_price": 22.00,
    "high_price": 45.00,
    "suggested_price": 20.90
  },
  "listings": [
    {
      "platform": "poshmark",
      "title": "Thrasher Green Skateboard Magazine Tee M",
      "description": "Classic Thrasher tee in green...",
      "suggested_price": 20.90,
      "category_hint": "Men's T-Shirts",
      "hashtags": ["#Thrasher", "#streetwear", "#graphictee", "#thrifted", "#mensclothing"]
    },
    {
      "platform": "ebay",
      "title": "Thrasher Skateboard Magazine Graphic Tee Green Cotton M",
      "description": "Thrasher Skateboard Magazine graphic tee...",
      "suggested_price": 20.90,
      "category_hint": "Men's T-Shirts",
      "hashtags": []
    }
  ]
}
```

### `POST /api/v1/batch/uploads`

Request presigned S3 URLs for direct client-side uploads. Returns one URL per file.

**Body:**

```json
{
  "files": [
    { "mime_type": "image/jpeg" }
  ]
}
```

**Response:**

```json
{
  "job_id": "uuid",
  "uploads": [
    { "index": 0, "presigned_url": "https://...", "s3_key": "uploads/uuid/0.jpg" }
  ]
}
```

### `PUT {presigned_url}`

Upload image bytes directly to S3 — this request goes to S3, not your server.

**Headers:** `Content-Type: image/jpeg`

**Body:** raw image bytes

### `POST /api/v1/batch/analyze`

Trigger analysis for a job after all images have been uploaded.

**Body:**

```json
{
  "job_id": "uuid",
  "images": [
    { "s3_key": "uploads/uuid/0.jpg" }
  ]
}
```

**Response:** `{ "job_id": "uuid", "status": "processing" }`

### `GET /api/v1/batch/jobs/{job_id}`

Poll job status and per-item results.

### `POST /api/v1/batch/jobs/{job_id}/items/{item_index}/post-ebay`

Post a completed item directly to eBay as a live listing.

**No request body needed.**

**Response:**

```json
{
  "success": true,
  "listing_id": "198365240579",
  "listing_url": "https://www.ebay.com/itm/198365240579",
  "error": null
}
```

---

## Environment variables

See `backend/.env.example` for the full list. Required:

| Variable                     | Description                                      |
| ---------------------------- | ------------------------------------------------ |
| `AWS_ACCESS_KEY_ID`          | AWS IAM key                                      |
| `AWS_SECRET_ACCESS_KEY`      | AWS IAM secret                                   |
| `AWS_REGION`                 | AWS region (e.g. `us-east-1`)                    |
| `S3_BUCKET_NAME`             | S3 bucket for image uploads                      |
| `DYNAMODB_TABLE_NAME`        | DynamoDB table for job state                     |
| `BEDROCK_MODEL_ID`           | Bedrock model ID (e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`) |
| `USE_BEDROCK`                | Boolean flag to enable Bedrock (default: `true`) |
| `BEDROCK_GUARDRAIL_ID`       | Bedrock Guardrails ID (optional, leave empty to disable) |
| `BEDROCK_GUARDRAIL_VERSION`  | Guardrail version (`DRAFT` or version number)    |
| `EBAY_APP_ID`                | eBay production App ID                           |
| `EBAY_CERT_ID`               | eBay production Cert ID                          |
| `EBAY_USER_TOKEN`            | eBay OAuth user token for Sell API posting       |
| `EBAY_FULFILLMENT_POLICY_ID` | eBay fulfillment policy ID                       |
| `EBAY_PAYMENT_POLICY_ID`     | eBay payment policy ID                           |
| `EBAY_RETURN_POLICY_ID`      | eBay return policy ID                            |
| `EBAY_MERCHANT_LOCATION_KEY` | eBay merchant location key                       |

---

## Sprint Roadmap

**Sprint 1-4** ✅ Complete
- Batch processing with asyncio
- AWS S3 presigned uploads
- eBay Browse API pricing with fallback queries
- DynamoDB persistent job storage
- eBay Sell API direct posting (createOrReplaceInventoryItem → createOffer → publishOffer)
- One-tap publish from UI returns a live eBay listing URL

**Sprint 5** ✅ Complete
- Cross-market pricing via Playwright scrapers (Poshmark, Mercari, Depop sold comps)
- Per-platform price recommendations with confidence scoring
- Comp aggregation across all 4 sources (eBay + 3 scrapers)

**Sprint 6A+6B** ✅ Complete
- AWS Bedrock migration (Claude Sonnet 4.5 via Converse API)
- Bedrock Guardrails integration (content safety filtering)
- Cross-region inference for high availability
- Graceful error handling for blocked content

**Sprint 6C** 🚀 In Progress
- CloudWatch structured logging and metrics
- AWS hosting (Amplify frontend + Lambda/EC2 backend + Route53)
- Live demo URL for job applications

**Sprint 7** Planned
- OAuth token refresh flow (eBay user tokens)
- Poshmark direct posting API integration
- Multi-platform publishing

**Sprint 8+** Roadmap
- Inventory management
- Multi-account seller support
- Pricing optimization engine
- React Native mobile app
