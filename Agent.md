# AI Visa Sourcing Chatbot — Project Context

## Overview

This is a **Proof of Concept (POC)** for an AI-powered visa sourcing chatbot. The chatbot answers visa-related questions (eligibility, required documents, processing times, pricing, travel recommendations) using a provided dataset. It exposes a single API endpoint that is tested via an external Streamlit harness.

**Harness URL:** https://visa-app-harness-jodgmznemkprpy72m5v479.streamlit.app/

---

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | **FastAPI** (Python 3.11) |
| Front-End UI | **HTML/CSS/JS** (Served from FastAPI static files) |
| Database | **PostgreSQL 16 + pgvector** (vector search) |
| LLM | **Google Gemini 2.0 Flash** (via `google-genai`) |
| Embeddings | **Gemini `gemini-embedding-001`** (3072 dimensions) |
| Containerization | **Docker + docker-compose** |

---

## API Contract

**Endpoint:** `POST /vendor/chat`

### Request
```json
{
  "message": "user question",
  "context": {
    "nationality": "IN",
    "residencyCountry": "IN",
    "travelMonth": "2026-03",
    "interests": ["shopping", "luxury"],
    "budgetBand": "mid",
    "hasVisaOrPermit": ["SCHENGEN"],
    "stayingWithFamily": false,
    "travelGroup": "solo",
    "travelInDays": 30
  }
}
```

### Response
```json
{
  "answerText": "Natural language answer...",
  "final": {
    "destinations": ["AE"],
    "skuCodes": ["AE_TOUR_30D_SGL_STD_001"],
    "documents": [
      {"docCode": "passport_copy", "mandatory": true}
    ],
    "processingTimeDays": 3
  },
  "trace": {
    "retrieved": {},
    "matchedRules": ["AE_DR_001"],
    "appliedAdjustments": ["AE_PA_001: add_amount 17 AED"]
  },
  "meta": {
    "latencyMs": 322
  }
}
```

---

## Data Collections (in `data/`)

| File | Records | Contents |
|---|---|---|
| `visasku.json` | 12 | Visa products: country, purpose, entry type, validity, stay days, processing, pricing |
| `destination.json` | 15 | Destinations: interests, popularity, starting price, processing days |
| `desitnationmarket.json` | 3 | Eligibility rules, document rules, pricing adjustments per destination+market |
| `knowledgesources.json` | 3 | Curated text snippets about visa requirements |

### Key Data Relationships
- SKUs link to destinations via `countryCode` (AE, SA, TR have SKUs)
- Destination-market configs contain **rules** with `conditions` and `priority` ordering
- Rules reference specific `applicableSkuCodes`
- Rule conditions match on: `nationalityIn`, `residencyCountryIn`, `hasVisaOrPermitIn`, `stayingWithFamily`, `travelGroupIn`

---

## Architecture

```
POST /vendor/chat
  → Intent Classifier (keyword-based)
      → eligibility / documents / pricing / processing_time
      → travel_recommendation
      → unsupported (non-visa questions refused)
  → Rule Engine (deterministic, priority-ordered)
      1. evaluate_visa_mode()  — eligible or blocked
      2. evaluate_documents()  — minimum docs + conditional add/remove/modify
      3. evaluate_pricing()    — base price + adjustments
  → RAG Retrieval (pgvector cosine similarity on embedded knowledge + destinations)
  → LLM Response Generator (Gemini Flash, strict grounding prompt)
  → Structured JSON Response
```

---

## Project Structure

```
visa-ragbot/
├── app/
│   ├── __init__.py
│   ├── config.py          # Env vars (GEMINI_API_KEY, DATABASE_URL)
│   ├── database.py        # Async SQLAlchemy engine + session
│   ├── models.py          # Pydantic models matching harness contract
│   ├── seed.py            # Loads JSON data into Postgres on startup
│   ├── rule_engine.py     # Deterministic eligibility, docs, pricing logic
│   ├── rag.py             # pgvector embedding + cosine similarity search
│   ├── llm.py             # Gemini 2.0 Flash with grounding system prompt
│   ├── main.py            # FastAPI app with POST /vendor/chat and UI serving
│   └── static/
│       └── index.html     # Custom dark-theme chat UI
├── data/                  # JSON data files (copied from root)
├── db/
│   └── init.sql           # PostgreSQL schema with pgvector
├── tests/
│   └── __init__.py
├── .env                   # GEMINI_API_KEY, DATABASE_URL (not committed)
├── .env.example           # Template
├── docker-compose.yml     # Postgres + FastAPI services
├── Dockerfile             # Python 3.11 slim
└── requirements.txt       # Python dependencies
```

---

## Key Design Decisions

1. **Hybrid RAG + Rule Engine:** Pure RAG would struggle with multi-condition eligibility logic (nationality × residency × purpose). The rule engine handles deterministic logic precisely, while RAG + LLM handles NL understanding and response generation.

2. **Priority-based rules:** Rules in `desitnationmarket.json` have priority values. Lower priority rules are evaluated first, higher priority rules override. This handles cascading rules like "Indian travelers need bank statement" (priority 50) being overridden by "GCC residents don't need bank statement" (priority 60).

3. **Grounding:** The LLM system prompt strictly forbids hallucination. Rule engine output is injected as "GROUND TRUTH" in the prompt. The LLM's job is only to format a natural language response from verified data.

4. **Fallback responses:** If the Gemini API fails or is not configured, the app generates basic template-based responses directly from rule engine output.

---

## Recent Changes & Fixes

*   **Embeddings Update**: Switched from `text-embedding-004` to `gemini-embedding-001` due to SDK v1beta compatibility.
*   **Vector DB Fix**: Increased pgvector embedding dimension from 768 to 3072 to match the new Gemini embedding model's output. Fixed a bug in SQLAlchemy where `::vector` parameter casting resulted in query failures; replaced with explicit `CAST(:var AS vector)`.
*   **UI Implementation**: Built a premium dark-themed Glassmorphism Chat UI at `app/static/index.html` with real-time API status, interactive tags for traveler contexts, result cards for SKUs/Processing Times, and visual document checklists.
*   **Static Serving**: Updated FastAPI `main.py` to serve the UI at the root `/` endpoint.
*   **LLM Response Humanization** *(2026-03-13)*: Completely rewrote `app/llm.py` to eliminate robotic, database-driven responses. Key changes:
    - System prompt now explicitly forbids raw SKU codes, rule IDs, JSON keys, and database column names.
    - Added a `_build_human_readable_context()` pre-processor that converts all structured data (rule engine output, RAG chunks, recommendations) into plain-English summaries *before* passing to the LLM — the model never sees raw JSON.
    - Added country-code-to-name mapping (`AE` → `"the United Arab Emirates"`) and document-code humanizer (`bank_statement` → `"Bank statement"`).
    - RAG chunks starting with `"Visa SKU:"` are filtered out to prevent internal formatting leaks.
    - Pricing adjustments are parsed into conversational descriptions (e.g. `"AE_PA_001: add_amount 17 AED"` → `"Additional fee of 17 AED applied"`).
    - Fallback responses (used if Gemini fails) are also now warm and conversational.
    - Temperature raised from 0.1 → 0.3 for more natural phrasing.
*   **UI Metadata Cleanup** *(2026-03-13)*: Removed all internal metadata rendering from the chat UI (`app/static/index.html`). Result cards (destinations, SKU codes, processing tags), document checklists, rule ID tags, and latency indicators are no longer displayed. Only the LLM's natural language `answerText` is shown in chat bubbles. Associated CSS classes (`.result-cards`, `.doc-list`, `.rule-tags`, etc.) were also removed.
*   **Chat Memory & Contextual Rewriting** *(2026-03-13)*: Solved chatbot statelessness without breaking the Streamlit contract. 
    - Introduced an optional `history` field (array of `ChatMessage` objects) to `ChatRequest` in `app/models.py`.
    - Implemented a `rewrite_query` step using Gemini `gemini-2.5-flash` in `app/llm.py` to convert vague follow-ups (e.g., "how much for that?") into explicit standalone queries (e.g., "what is the price of a UAE business visa?") before hitting the intent classifier.
    - Added the `history` object inside the POST request body coming from `app/static/index.html`.

---

## Running Locally

```bash
# 1. Ensure .env has GEMINI_API_KEY set
# 2. Start containers
docker-compose up -d --build

# 3. Test endpoint
curl -X POST http://localhost:8000/vendor/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Am I eligible for UAE tourist visa", "context": {"nationality": "IN", "residencyCountry": "IN"}}'

# 4. Check health
curl http://localhost:8000/health

# 5. View logs
docker logs visaragbot-app-1 --tail 50
```

---

## Evaluation

The solution is evaluated via the Streamlit harness:
- **Public tests:** Visible to vendors, used for development
- **Hidden tests:** Used for final evaluation
- **Criteria:** Response accuracy, correct eligibility/document rules, grounding, unsupported query handling, latency

---

## Current Status

- ✅ All application code complete and running in Docker
- ✅ Rule engine tested: eligibility, document rules, pricing adjustments
- ✅ Unsupported query refusal working
- ✅ Gemini API key configured and LLM grounded responses working perfectly
- ✅ RAG search functionality fully operational with `gemini-embedding-001` (3072 dims)
- ✅ Custom interactive web UI built and served at `http://localhost:8000/`
- 🔲 Deploy to cloud for public URL (needed for harness testing)
- 🔲 Pass public tests in Streamlit harness
- 🔲 Optimize overall system for hidden evaluation tests
