"""
LLM integration using Gemini 2.0 Flash.

Generates grounded natural language responses from rule engine output
and RAG-retrieved context. Strictly prevents hallucination.
All responses are warm, conversational, and free of internal identifiers.
"""

import json
import logging
from app.config import settings
from app.models import UserContext, ChatMessage
from app.rule_engine import RuleEngineResult

logger = logging.getLogger(__name__)

_gemini_client = None

# ── Country code → friendly name mapping ──
_COUNTRY_NAMES = {
    "AE": "the United Arab Emirates",
    "SA": "Saudi Arabia",
    "TR": "Turkey",
    "IN": "India",
    "PK": "Pakistan",
    "NG": "Nigeria",
    "BD": "Bangladesh",
    "EG": "Egypt",
    "GB": "the United Kingdom",
    "US": "the United States",
    "IT": "Italy",
    "FR": "France",
    "TH": "Thailand",
    "MY": "Malaysia",
    "JP": "Japan",
    "SG": "Singapore",
    "AU": "Australia",
    "GE": "Georgia",
    "AZ": "Azerbaijan",
    "ID": "Indonesia",
    "KE": "Kenya",
    "MA": "Morocco",
    "VN": "Vietnam",
}


def _country_name(code: str) -> str:
    """Convert a country code to a human-friendly name."""
    return _COUNTRY_NAMES.get(code, code)


def _humanize_doc(doc_code: str) -> str:
    """Convert a document code like 'bank_statement' → 'Bank statement'."""
    return doc_code.replace("_", " ").capitalize()


def _get_client():
    """Lazy-load the Gemini client."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


SYSTEM_PROMPT = """You are a friendly, knowledgeable visa travel assistant. Your job is to help travelers understand their visa options in a warm, conversational tone.

## ABSOLUTE RULES — never break these:

1. **Ground every answer** in the VERIFIED DATA provided below. NEVER invent facts, prices, processing times or document requirements.
2. **Never expose internal identifiers.** This means:
   - NO SKU codes (e.g. AE_TOUR_30D_SGL_STD_001) — instead say "a 30-day UAE tourist visa"
   - NO rule IDs (e.g. AE_DR_001) — instead describe what the rule means
   - NO database column names, JSON keys, or raw formatting
3. **Write like a helpful travel advisor**, not a database. Use complete sentences, natural phrasing, and a warm tone.
4. **Use country names** instead of country codes (say "United Arab Emirates" not "AE").
5. **Format prices clearly**: say "339 AED" or "approximately 339 AED", never "AED 339.0".
6. **Format documents as a clean list** with friendly names: say "Passport copy" not "passport_copy".
7. **If multiple visa options exist** (e.g. standard vs express), compare them naturally: "You can choose standard processing (3 days, 299 AED) or express processing (1 day, 339 AED)."
8. If the user is **not eligible**, explain the reason compassionately without using internal rule references.
9. If the question is **not about visas or travel**, politely decline.
10. **Do not ask follow-up questions** — answer directly with the information available.
11. Keep responses concise but complete — aim for 2-4 sentences for simple queries, up to a short paragraph for complex ones."""


REWRITER_PROMPT = """You are a query contextualizer. You will be given a conversation history and a new follow-up query.
Your task is to rewrite the new query into a standalone query that retains all necessary context (locations, document types, intentions) from the history.
Do NOT answer the question. ONLY return the rewritten standalone query.
If the query is already standalone, return it as is."""


def rewrite_query(message: str, history: list[ChatMessage]) -> str:
    """Rewrite a follow-up query using conversation context."""
    if not history or not settings.gemini_api_key:
        return message

    client = _get_client()

    hist_text = "\n".join([f"{msg.role}: {msg.content}" for msg in history[-4:]]) # Last 4 turns
    
    prompt = f"Conversation History:\n{hist_text}\n\nNew Follow-up Query: {message}\n\nRewritten Standalone Query:"

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={
                "system_instruction": REWRITER_PROMPT,
                "temperature": 0.0,
                "max_output_tokens": 128,
            },
        )
        rewritten = response.text.strip()
        logger.info(f"Query rewritten: '{message}' -> '{rewritten}'")
        return rewritten
    except Exception as e:
        logger.error(f"Failed to rewrite query: {e}")
        return message


def _build_human_readable_context(
    context: UserContext | None,
    rule_result: RuleEngineResult | None,
    rag_chunks: list[dict] | None,
    destination_recommendations: list[dict] | None,
) -> str:
    """
    Pre-process all structured data into human-readable summaries
    so the LLM doesn't need to interpret raw JSON.
    """
    parts = []

    # ── Traveler context ──
    if context:
        traveler_info = []
        if context.nationality:
            traveler_info.append(f"Nationality: {_country_name(context.nationality)}")
        if context.residencyCountry:
            traveler_info.append(f"Currently residing in: {_country_name(context.residencyCountry)}")
        if context.travelMonth:
            traveler_info.append(f"Planning to travel: {context.travelMonth}")
        if context.travelInDays:
            traveler_info.append(f"Trip duration: {context.travelInDays} days")
        if context.budgetBand:
            traveler_info.append(f"Budget: {context.budgetBand}")
        if context.travelGroup:
            traveler_info.append(f"Traveling: {context.travelGroup}")
        if context.interests:
            traveler_info.append(f"Interests: {', '.join(context.interests)}")
        if context.hasVisaOrPermit:
            traveler_info.append(f"Existing visas/permits: {', '.join(context.hasVisaOrPermit)}")
        if context.stayingWithFamily:
            traveler_info.append("Staying with family: Yes")
        if traveler_info:
            parts.append("TRAVELER PROFILE:\n" + "\n".join(f"  • {x}" for x in traveler_info))

    # ── Rule engine results (pre-processed) ──
    if rule_result:
        if rule_result.eligible:
            dest_names = ", ".join(_country_name(c) for c in rule_result.destinations)
            section = [f"VISA ELIGIBILITY: ✅ Eligible for travel to {dest_names}"]

            # Visa options (from SKU data — humanized)
            if rule_result.sku_codes:
                section.append(f"  Available visa options found: {len(rule_result.sku_codes)}")

            # Pricing
            if rule_result.final_price and rule_result.price_currency:
                price_str = f"  Price: {rule_result.final_price} {rule_result.price_currency}"
                if rule_result.base_price and rule_result.base_price != rule_result.final_price:
                    price_str += f" (base price was {rule_result.base_price} {rule_result.price_currency}, adjusted)"
                section.append(price_str)

            # Processing time
            if rule_result.processing_time_days:
                section.append(f"  Processing time: {rule_result.processing_time_days} business days")

            # Documents (humanized)
            if rule_result.documents:
                mandatory = [_humanize_doc(d.docCode) for d in rule_result.documents if d.mandatory]
                optional = [_humanize_doc(d.docCode) for d in rule_result.documents if not d.mandatory]
                if mandatory:
                    section.append("  Required documents: " + ", ".join(mandatory))
                if optional:
                    section.append("  Optional documents: " + ", ".join(optional))

            # Pricing adjustments (humanized)
            if rule_result.applied_adjustments:
                adj_descriptions = []
                for adj in rule_result.applied_adjustments:
                    # Parse "AE_PA_001: add_amount 17 AED" into human-readable
                    if "add_amount" in adj:
                        try:
                            amt_part = adj.split("add_amount")[1].strip()
                            adj_descriptions.append(f"Additional fee of {amt_part} applied")
                        except Exception:
                            adj_descriptions.append("A pricing adjustment was applied")
                    elif "discount" in adj.lower():
                        adj_descriptions.append("A discount was applied")
                    else:
                        adj_descriptions.append("A pricing adjustment was applied")
                if adj_descriptions:
                    section.append("  Price notes: " + "; ".join(adj_descriptions))

            parts.append("\n".join(section))
        else:
            reason = rule_result.ineligibility_reason or "No matching visa configuration found"
            parts.append(f"VISA ELIGIBILITY: ❌ Not currently eligible\n  Reason: {reason}")

    # ── RAG knowledge chunks (cleaned) ──
    if rag_chunks:
        clean_chunks = []
        for c in rag_chunks:
            content = c.get("content", "")
            # Strip any raw "Visa SKU:" prefixes that leak internal formatting
            if content.startswith("Visa SKU:"):
                # Re-format: "Visa SKU: AE_TOUR... for UAE. Purpose: tourist..." → cleaner version
                continue  # SKU details are already covered by rule engine
            clean_chunks.append(content)
        if clean_chunks:
            parts.append("ADDITIONAL KNOWLEDGE:\n" + "\n".join(f"  • {c}" for c in clean_chunks))

    # ── Destination recommendations ──
    if destination_recommendations:
        recs = []
        for d in destination_recommendations:
            name = d.get("countryName", d.get("countryCode", "Unknown"))
            interests = d.get("matchedInterests", d.get("interests", []))
            price = d.get("startingPriceAmount")
            currency = d.get("startingPriceCurrency", "")
            rec_str = f"{name}"
            if interests:
                rec_str += f" — great for {', '.join(interests)}"
            if price:
                rec_str += f" (starting from {price} {currency})"
            recs.append(rec_str)
        if recs:
            parts.append("MATCHING DESTINATIONS:\n" + "\n".join(f"  • {r}" for r in recs))

    return "\n\n".join(parts)


def generate_response(
    question: str,
    context: UserContext | None,
    rule_result: RuleEngineResult | None = None,
    rag_chunks: list[dict] | None = None,
    destination_recommendations: list[dict] | None = None,
    history: list[ChatMessage] | None = None,
) -> str:
    """
    Generate a grounded natural language response using Gemini.

    Args:
        question: the user's question
        context: user context (nationality, residency, etc.)
        rule_result: output from the rule engine (if eligibility query)
        rag_chunks: retrieved text chunks (if travel/general query)
        destination_recommendations: matched destinations (if travel recommendation)
        history: recent chat history to maintain conversational flow

    Returns:
        Natural language answer text
    """
    client = _get_client()

    # Pre-process all data into human-readable summaries
    verified_data = _build_human_readable_context(
        context, rule_result, rag_chunks, destination_recommendations
    )
    
    hist_text = ""
    if history:
        hist_text = "--- RECENT CHAT HISTORY ---\n" + "\n".join([f"{msg.role}: {msg.content}" for msg in history[-4:]]) + "\n\n"

    full_prompt = f"""{hist_text}User's question: "{question}"

--- VERIFIED DATA (base your answer ONLY on this) ---
{verified_data}
--- END VERIFIED DATA ---

Write a helpful, conversational response using only the verified data above. Remember: no SKU codes, no rule IDs, no database formatting."""

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=full_prompt,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.3,
                "max_output_tokens": 1024,
            },
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        # Fallback: generate a conversational response from rule data
        if rule_result:
            return _fallback_response(rule_result, question)
        return "I'm sorry, I encountered an issue processing your request. Please try again in a moment."


def _fallback_response(result: RuleEngineResult, question: str = "") -> str:
    """Generate a conversational fallback response without LLM."""
    if not result.eligible:
        reason = result.ineligibility_reason or "no matching visa configuration was found"
        return (
            f"Unfortunately, you're not currently eligible for this visa. "
            f"The reason is that {reason.lower()}. "
            f"If your circumstances change, feel free to check again!"
        )

    dest_names = ", ".join(_country_name(c) for c in result.destinations)
    parts = [f"Great news! You're eligible for a visa to {dest_names}."]

    if result.documents:
        mandatory = [_humanize_doc(d.docCode) for d in result.documents if d.mandatory]
        if mandatory:
            parts.append(f"You'll need to prepare the following documents: {', '.join(mandatory)}.")

    if result.processing_time_days:
        parts.append(f"Processing typically takes about {result.processing_time_days} business days.")

    if result.final_price and result.price_currency:
        parts.append(f"The visa costs {result.final_price} {result.price_currency}.")

    return " ".join(parts)

