# Flipr

An AI-powered listing assistant for thrift resellers. Snap a photo of a clothing item and get back a priced, platform-ready listing in seconds — no manual research, no writing from scratch.

---

## What it does

Resellers spend most of their time on two things: figuring out what an item is worth and writing the listing. Flipr automates both.

1. **Upload a photo** — image goes to S3
2. **AI identifies the item** — GPT-4o Vision reads brand, type, condition, color, material, and notable details
3. **Clothing guard** — AWS Rekognition confirms it's actually a clothing item before burning API tokens
4. **Live pricing** — eBay Browse API pulls recent sold listings and calculates a suggested price at 95th-percentile median (priced to move, not to sit)
5. **Platform-specific listings** — GPT-4o drafts separate listings for Poshmark and eBay, each following that platform's voice, format rules, and character limits
6. **Quality enforcement** — a deterministic post-processing pass strips marketing language, enforces hashtag format, and maps category fields — no hallucinated categories or banned phrases ever reach the user

Supports single-item analysis and batch jobs (multiple photos processed concurrently).

---

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI (async) |
| AI / Vision | OpenAI GPT-4o (`gpt-4o`) with structured outputs |
| Image guard | AWS Rekognition |
| Image storage | AWS S3 |
| Pricing data | eBay Browse API (sold listings) |
| Job persistence | AWS DynamoDB |
| Config | Pydantic Settings |
| Logging | structlog (structured JSON) |

---

## Architecture

```
POST /api/v1/items/analyze          POST /api/v1/batch/uploads → PUT {presigned_url}
         │                          POST /api/v1/batch/analyze
         ▼                                    │
   [FastAPI route]                   [Create job → DynamoDB]
         │                                    │
         ▼                                    │ (per image, concurrent)
   S3 download                               ▼
 Rekognition pre-filter ──────────► clothing? ──no──► FAILED
         │ yes
         ▼
 GPT-4o Vision → ItemMetadata
         │
         ▼
 eBay API → CompResult (pricing stats)
         │
         ▼
 GPT-4o Text → ListingDraft × 2
 (Poshmark + eBay, concurrent)
         │
         ▼
 Post-processing validation
 (banned phrases, hashtag format,
  category mapping, 80-char titles)
         │
         ▼
 DynamoDB update → poll /jobs/{id}
```

---

## Key engineering decisions

**Structured outputs throughout** — both vision and listing generation use `response_format=Pydantic model` via the OpenAI SDK's `.parse()` method. The model is constrained to valid JSON matching the schema; no regex parsing, no hallucinated field names.

**Rekognition as a cheap gate** — image classification via AWS Rekognition runs before the GPT-4o call. A non-clothing image (receipts, furniture, faces) is rejected early, saving ~$0.01/image in OpenAI spend.

**Deterministic post-processing** — listing quality is enforced programmatically after generation. A regex-based banned-phrase filter strips marketing language (`"elevate your wardrobe"`, `"must-have"`, etc.). A category map overrides the model's category guess with a known-good value. Hashtag count and format are corrected if needed. This separates "does the model understand the item" from "does the output meet spec."

**Retry with exponential backoff** — all external API calls (OpenAI, eBay) use `tenacity` with `wait_exponential` and `retry_if_exception_type`. Transient failures (timeouts, rate limits) retry up to 3 times before propagating.

**Concurrent batch processing** — `asyncio.gather` runs all images in a batch in parallel. One item failing does not cancel others; each updates its own DynamoDB record independently.

**eBay fallback query** — if a specific search query returns no sold comps, pricing drops the last word and retries with a broader query, preventing total failure on obscure items.

---

## Project structure

```
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
        ├── vision.py         # GPT-4o image analysis
        ├── listing.py        # GPT-4o listing generation + validation
        ├── pricing.py        # eBay sold comps + price calculation
        ├── rekognition.py    # AWS clothing detection
        └── storage.py        # S3 download
```

---

## Local setup

**Prerequisites:** Python 3.11+, AWS account with S3 + Rekognition + DynamoDB, OpenAI API key, eBay developer account.

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

---

## Environment variables

See [`backend/.env.example`](backend/.env.example) for the full list. Required:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `EBAY_APP_ID` | eBay production App ID |
| `EBAY_CERT_ID` | eBay production Cert ID |
| `AWS_ACCESS_KEY_ID` | AWS IAM key |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret |
| `AWS_REGION` | AWS region (e.g. `us-east-1`) |
| `S3_BUCKET_NAME` | S3 bucket for image uploads |
| `DYNAMODB_TABLE_NAME` | DynamoDB table for job state |
