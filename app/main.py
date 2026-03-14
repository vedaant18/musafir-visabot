"""
AI Visa Sourcing Chatbot — FastAPI Application.

Exposes POST /vendor/chat matching the Streamlit harness contract.
"""

import os
import json
import re
import time
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.models import ChatRequest, ChatResponse, FinalResult, Trace, Meta, DocumentRef
from app.seed import seed_database, copy_data_files
from app.rule_engine import evaluate_for_destination, get_destinations_for_interests, RuleEngineResult
from app.rag import build_embeddings, search_similar
from app.llm import generate_response, rewrite_query

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


SUPPORTED_COUNTRIES = set()

def load_supported_countries():
    """Load dynamically supported countries directly from the SKU file."""
    try:
        dest_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "visasku.json")
        if os.path.exists(dest_path):
            with open(dest_path, "r", encoding="utf-8") as f:
                skus = json.load(f)
                for sku in skus:
                    SUPPORTED_COUNTRIES.add(sku["countryCode"])
            logger.info(f"Loaded supported countries: {SUPPORTED_COUNTRIES}")
    except Exception as e:
        logger.error(f"Failed to load supported countries: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: seed database and build embeddings."""
    load_supported_countries()
    
    logger.info("Starting up — seeding database...")
    try:
        copy_data_files()
        seed_database()
    except Exception as e:
        logger.error(f"Database seeding failed: {e}")

    if settings.gemini_api_key:
        logger.info("Building embeddings...")
        try:
            build_embeddings()
        except Exception as e:
            logger.error(f"Embedding build failed: {e}")
    else:
        logger.warning("No GEMINI_API_KEY set — skipping embeddings.")

    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="AI Visa Sourcing Chatbot",
    description="POC chatbot for visa eligibility, documents, and travel recommendations",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Country name mapping ──
COUNTRY_NAMES = {
    "AE": "United Arab Emirates", "UAE": "United Arab Emirates",
    "SA": "Saudi Arabia", "TR": "Turkey",
    "GE": "Georgia", "AZ": "Azerbaijan",
    "TH": "Thailand", "SG": "Singapore",
    "ID": "Indonesia", "MY": "Malaysia",
    "LK": "Sri Lanka", "MV": "Maldives",
    "JP": "Japan", "KR": "South Korea",
    "IT": "Italy", "ES": "Spain",
}

COUNTRY_CODE_LOOKUP = {}
for code, name in COUNTRY_NAMES.items():
    COUNTRY_CODE_LOOKUP[name.lower()] = code
    COUNTRY_CODE_LOOKUP[code.lower()] = code
# Additional aliases
COUNTRY_CODE_LOOKUP["uae"] = "AE"
COUNTRY_CODE_LOOKUP["united arab emirates"] = "AE"
COUNTRY_CODE_LOOKUP["dubai"] = "AE"
COUNTRY_CODE_LOOKUP["abu dhabi"] = "AE"
COUNTRY_CODE_LOOKUP["saudi"] = "SA"
COUNTRY_CODE_LOOKUP["saudi arabia"] = "SA"
COUNTRY_CODE_LOOKUP["riyadh"] = "SA"
COUNTRY_CODE_LOOKUP["jeddah"] = "SA"
COUNTRY_CODE_LOOKUP["turkey"] = "TR"
COUNTRY_CODE_LOOKUP["turkiye"] = "TR"
COUNTRY_CODE_LOOKUP["istanbul"] = "TR"
COUNTRY_CODE_LOOKUP["ankara"] = "TR"


def _extract_destination(message: str) -> str | None:
    """Extract destination country code from user message."""
    msg_lower = message.lower()
    for name, code in sorted(COUNTRY_CODE_LOOKUP.items(), key=lambda x: -len(x[0])):
        if re.search(r'\b' + re.escape(name) + r'\b', msg_lower):
            return code
    return None


def _extract_purpose(message: str) -> str:
    """Extract visa purpose from user message."""
    msg_lower = message.lower()
    if any(word in msg_lower for word in ["student", "study", "university", "education", "college"]):
        return "student"
    return "tourist"


def _classify_intent(message: str, context) -> str:
    """
    Classify user intent from the message.

    Returns: 'eligibility', 'documents', 'pricing', 'travel_recommendation',
             'processing_time', 'general_visa', or 'unsupported'
    """
    msg_lower = message.lower()

    # ── First: detect clearly non-visa questions ──
    non_visa_keywords = ["weather", "temperature", "recipe", "cook", "football",
                         "cricket", "movie", "song", "music", "stock market",
                         "programming", "code", "math", "science", "politics",
                         "news", "joke", "game", "sport"]
    visa_related_words = ["visa", "travel", "trip", "visit", "tourism", "tourist",
                          "eligible", "document", "passport", "apply", "entry",
                          "processing", "price", "cost", "fee", "stay", "destination",
                          "recommend", "suggest", "country", "vacation", "holiday",
                          "flight", "beach", "city", "nature", "historical", "somewhere",
                          "somewhere with", "want to go"]
    if any(kw in msg_lower for kw in non_visa_keywords) and not any(kw in msg_lower for kw in visa_related_words):
        return "unsupported"

    # Travel recommendation queries
    travel_keywords = ["recommend", "suggest", "where should", "best destination",
                       "where can i go", "travel to", "which country",
                       "interested in", "looking for", "want to go", "somewhere",
                       "some where", "places to", "vacation"]
    if any(kw in msg_lower for kw in travel_keywords) and not _extract_destination(message):
        return "travel_recommendation"

    # Eligibility queries
    eligibility_keywords = ["eligible", "can i", "am i able", "qualify", "allowed",
                           "apply for", "get a visa", "visa for", "can i get",
                           "do i need", "is it possible", "available"]
    if any(kw in msg_lower for kw in eligibility_keywords):
        return "eligibility"

    # Document queries
    doc_keywords = ["document", "require", "need to submit", "paperwork",
                    "what do i need", "passport", "bank statement", "photograph"]
    if any(kw in msg_lower for kw in doc_keywords):
        return "documents"

    # Processing time queries
    time_keywords = ["how long", "processing time", "how many days", "when will",
                     "how fast", "duration", "timeline"]
    if any(kw in msg_lower for kw in time_keywords):
        return "processing_time"

    # Pricing queries
    price_keywords = ["price", "cost", "how much", "fee", "charge", "pay", "expensive"]
    if any(kw in msg_lower for kw in price_keywords):
        return "pricing"

    # General visa query (has a destination mentioned)
    if _extract_destination(message):
        return "general_visa"

    # Check if it's at least visa-related
    if any(kw in msg_lower for kw in visa_related_words):
        return "general_visa"

    return "unsupported"


@app.post("/vendor/chat", response_model=ChatResponse)
async def vendor_chat(request: ChatRequest):
    """Main chat endpoint called by the Streamlit harness."""
    start_time = time.time()

    # Original message for the response
    original_message = request.message
    
    # Contextualize message if history is provided
    message = original_message
    if request.history:
        message = rewrite_query(original_message, request.history)

    context = request.context
    intent = _classify_intent(message, context)

    logger.info(f"Question: {original_message} | Rewritten: {message} | Intent: {intent}")

    # ── Restrict to Supported Countries ──
    extracted_dest = _extract_destination(message)
    if extracted_dest and extracted_dest not in SUPPORTED_COUNTRIES:
        latency = int((time.time() - start_time) * 1000)
        return ChatResponse(
            answerText="Sorry, I currently only have visa information for a limited set of countries. I cannot provide visa details for that destination yet.",
            final=FinalResult(),
            trace=Trace(retrieved={"unsupported_destination": extracted_dest}),
            meta=Meta(latencyMs=latency),
        )

    # ── Handle unsupported queries ──
    if intent == "unsupported":
        latency = int((time.time() - start_time) * 1000)
        return ChatResponse(
            answerText="I can only help with visa-related questions such as eligibility, required documents, processing times, pricing, and travel destination recommendations. Please ask me a visa-related question.",
            final=FinalResult(),
            trace=Trace(retrieved={"intent": intent}),
            meta=Meta(latencyMs=latency),
        )

    # ── Handle travel recommendations ──
    if intent == "travel_recommendation":
        msg_lower = message.lower()
        interests = context.interests if context and context.interests else []

        # Fallback if no explicit interests but user mentioned something
        if not interests:
            if "cheap" in msg_lower or "budget" in msg_lower or "low" in msg_lower:
                interests = ["city", "shopping"]
            elif "fast" in msg_lower or "quick" in msg_lower:
                interests = ["city", "beach"]
            else:
                interests = ["city", "nature", "beach", "historical", "shopping", "luxury"]

        # Use RAG to find relevant destinations
        rag_chunks = []
        if settings.gemini_api_key:
            rag_chunks = search_similar(message, top_k=5, source_type="destination")

        # Also use interest-based matching
        all_dest_matches = get_destinations_for_interests(interests) if interests else []
        dest_matches = [d for d in all_dest_matches if d['countryCode'] in SUPPORTED_COUNTRIES]

        # Evaluate eligibility for each matched destination
        final_dests = []
        final_skus = []
        final_docs = []
        for d in dest_matches[:5]:
            code = d['countryCode']
            rule_res = evaluate_for_destination(code, context, "tourist")
            if rule_res.eligible:
                final_dests.append(code)
                final_skus.extend(rule_res.sku_codes)
                final_docs.extend(rule_res.documents)
                
        # Generate LLM response
        answer_text = ""
        if settings.gemini_api_key:
            answer_text = generate_response(
                question=original_message,
                context=context,
                rag_chunks=rag_chunks,
                destination_recommendations=dest_matches[:5],
                history=request.history,
            )
        else:
            if final_dests:
                parts = ["Based on your interests, here are some recommended destinations:"]
                for d in dest_matches[:5]:
                    if d['countryCode'] in final_dests:
                        parts.append(f"- {d['countryName']} ({d['countryCode']}): {', '.join(d['matchedInterests'])}")
                answer_text = "\\n".join(parts)
            else:
                answer_text = "I couldn't find any eligible destinations matching your interests."

        latency = int((time.time() - start_time) * 1000)
        return ChatResponse(
            answerText=answer_text,
            final=FinalResult(destinations=final_dests, skuCodes=final_skus, documents=final_docs),
            trace=Trace(
                retrieved={"interests": interests, "rag_results": len(rag_chunks)},
                matchedRules=[],
            ),
            meta=Meta(latencyMs=latency),
        )

    # ── Handle eligibility, documents, pricing, processing time, general visa queries ──
    destination = _extract_destination(message)
    purpose = _extract_purpose(message)
    
    speed_mode = "standard"
    msg_l = message.lower()
    if "express" in msg_l or "fast" in msg_l or "quick" in msg_l:
        speed_mode = "express"

    if not destination:
        # Try to infer from context interests or just provide general info
        rag_chunks = []
        if settings.gemini_api_key:
            rag_chunks = search_similar(message, top_k=5)

        answer_text = ""
        if settings.gemini_api_key and rag_chunks:
            answer_text = generate_response(
                question=original_message,
                context=context,
                rag_chunks=rag_chunks,
                history=request.history,
            )
        else:
            answer_text = "Please specify a destination country so I can provide visa information. I have information about UAE, Saudi Arabia, and Turkey."

        latency = int((time.time() - start_time) * 1000)
        return ChatResponse(
            answerText=answer_text,
            final=FinalResult(),
            trace=Trace(retrieved={"rag_results": len(rag_chunks)}),
            meta=Meta(latencyMs=latency),
        )

    # ── Run rule engine ──
    rule_result = evaluate_for_destination(destination, context, purpose, mode=speed_mode)

    # ── Get RAG chunks for additional context ──
    rag_chunks = []
    if settings.gemini_api_key:
        rag_chunks = search_similar(message, top_k=3)

    # ── Generate LLM response ──
    answer_text = ""
    if settings.gemini_api_key:
        answer_text = generate_response(
            question=original_message,
            context=context,
            rule_result=rule_result,
            rag_chunks=rag_chunks,
            history=request.history,
        )
    else:
        # Fallback without LLM
        if not rule_result.eligible:
            answer_text = f"You are not eligible for a {purpose} visa to {destination}. Reason: {rule_result.ineligibility_reason}"
        else:
            doc_list = ", ".join([d.docCode.replace("_", " ") for d in rule_result.documents if d.mandatory])
            answer_text = (
                f"Yes, you are eligible for a {purpose} visa to {destination}. "
                f"Required documents: {doc_list}. "
                f"Processing time: {rule_result.processing_time_days} days. "
                f"Price: {rule_result.price_currency} {rule_result.final_price}."
            )

    # ── Build response ──
    latency = int((time.time() - start_time) * 1000)
    
    final_dests = rule_result.destinations if rule_result.eligible else [destination]
    
    return ChatResponse(
        answerText=answer_text,
        final=FinalResult(
            destinations=final_dests,
            skuCodes=rule_result.sku_codes if rule_result.eligible else [],
            documents=rule_result.documents if rule_result.eligible else [],
            processingTimeDays=rule_result.processing_time_days if rule_result.eligible else 0,
            minLeadTimeDays=rule_result.min_lead_time_days if rule_result.eligible else None
        ),
        trace=Trace(
            retrieved={"destination": destination, "purpose": purpose, "intent": intent},
            matchedRules=rule_result.matched_rules,
            appliedAdjustments=rule_result.applied_adjustments,
        ),
        meta=Meta(latencyMs=latency),
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "gemini_configured": bool(settings.gemini_api_key), "supported_countries": list(SUPPORTED_COUNTRIES)}


@app.get("/")
async def root():
    """Serve the chat UI."""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    return FileResponse(os.path.join(static_dir, "index.html"))
