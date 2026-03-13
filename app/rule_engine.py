"""
Deterministic Rule Engine for visa eligibility, documents, and pricing.

Evaluates rules from destination_market configs against user context.
Rules are applied in priority order (higher priority = evaluated later, overrides earlier).
"""

import json
import logging
from typing import Optional
from sqlalchemy import create_engine, text
from app.config import settings
from app.models import UserContext, DocumentRef

logger = logging.getLogger(__name__)


class RuleEngineResult:
    """Result from the rule engine evaluation."""

    def __init__(self):
        self.eligible: bool = True
        self.visa_mode: str = "evisa"
        self.destinations: list[str] = []
        self.sku_codes: list[str] = []
        self.documents: list[DocumentRef] = []
        self.processing_time_days: int = 0
        self.base_price: float = 0
        self.final_price: float = 0
        self.price_currency: str = "AED"
        self.matched_rules: list[str] = []
        self.applied_adjustments: list[str] = []
        self.ineligibility_reason: str = ""


def _matches_condition(condition: dict, context: UserContext) -> bool:
    """Check if a single rule's conditions match the user context."""
    # If no conditions, rule matches all
    if not condition:
        return True

    # Check nationality
    if "nationalityIn" in condition:
        if not context.nationality or context.nationality not in condition["nationalityIn"]:
            return False

    # Check residency country
    if "residencyCountryIn" in condition:
        if not context.residencyCountry or context.residencyCountry not in condition["residencyCountryIn"]:
            return False

    # Check visa/permit holdings
    if "hasVisaOrPermitIn" in condition:
        user_permits = context.hasVisaOrPermit or []
        if not any(p in condition["hasVisaOrPermitIn"] for p in user_permits):
            return False

    # Check staying with family
    if "stayingWithFamily" in condition:
        if context.stayingWithFamily != condition["stayingWithFamily"]:
            return False

    # Check travel group
    if "travelGroupIn" in condition:
        if not context.travelGroup or context.travelGroup not in condition["travelGroupIn"]:
            return False

    return True


def evaluate_for_destination(
    destination_country_code: str,
    context: UserContext,
    purpose: Optional[str] = None,
) -> RuleEngineResult:
    """
    Evaluate all rules for a given destination and user context.

    Args:
        destination_country_code: e.g. "AE", "SA", "TR"
        context: user context from the harness
        purpose: visa purpose (tourist, student). If None, defaults to "tourist".

    Returns:
        RuleEngineResult with eligibility, docs, pricing, matched rules.
    """
    result = RuleEngineResult()
    result.destinations = [destination_country_code]

    if purpose is None:
        purpose = "tourist"

    sync_url = settings.sync_database_url
    engine = create_engine(sync_url)

    with engine.connect() as conn:
        # ── 1. Get destination-market config ──
        row = conn.execute(
            text("""
                SELECT minimum_documents, visa_mode_rules, document_rules, pricing_adjustments
                FROM destination_market
                WHERE destination_country_code = :cc AND status = 'active'
                ORDER BY version DESC LIMIT 1
            """),
            {"cc": destination_country_code},
        ).fetchone()

        if not row:
            result.eligible = False
            result.ineligibility_reason = f"No configuration found for destination {destination_country_code}"
            engine.dispose()
            return result

        min_docs = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        visa_mode_rules = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        document_rules = json.loads(row[2]) if isinstance(row[2], str) else row[2]
        pricing_adjustments = json.loads(row[3]) if isinstance(row[3], str) else row[3]

        # ── 2. Get matching SKUs ──
        sku_rows = conn.execute(
            text("""
                SELECT id, sku_code, processing_time_days, processing_speed,
                       base_price_currency, base_price_amount
                FROM visa_skus
                WHERE country_code = :cc AND purpose = :purpose AND is_active = true
                ORDER BY processing_speed ASC
            """),
            {"cc": destination_country_code, "purpose": purpose},
        ).fetchall()

        if not sku_rows:
            result.eligible = False
            result.ineligibility_reason = f"No visa SKUs found for {destination_country_code} with purpose '{purpose}'"
            engine.dispose()
            return result

        all_sku_codes = [r[1] for r in sku_rows]
        result.sku_codes = list(all_sku_codes)

        # Use standard speed SKU as default
        default_sku = sku_rows[0]
        for sku in sku_rows:
            if sku[3] == "standard":
                default_sku = sku
                break

        result.processing_time_days = default_sku[2]
        result.base_price = float(default_sku[5])
        result.final_price = result.base_price
        result.price_currency = default_sku[4]

        # ── 3. Evaluate visa mode rules (sorted by priority) ──
        sorted_vm_rules = sorted(visa_mode_rules, key=lambda r: r.get("priority", 0))
        for rule in sorted_vm_rules:
            conditions = rule.get("conditions", {})
            applicable_skus = rule.get("applicableSkuCodes", [])

            # Check if rule applies to any of our SKUs
            if applicable_skus and not any(s in applicable_skus for s in all_sku_codes):
                continue

            if _matches_condition(conditions, context):
                visa_mode = rule.get("visaMode", "evisa")
                result.matched_rules.append(rule.get("ruleId", rule.get("ruleName", "unknown")))

                if visa_mode == "not_applicable":
                    result.eligible = False
                    result.ineligibility_reason = rule.get("ruleName", "Visa not applicable")
                    result.visa_mode = "not_applicable"
                    # Filter out blocked SKUs
                    result.sku_codes = [s for s in result.sku_codes if s not in applicable_skus]
                    if not result.sku_codes:
                        engine.dispose()
                        return result
                else:
                    result.visa_mode = visa_mode
                    # If rule specifies applicable SKUs, narrow down
                    if applicable_skus:
                        result.sku_codes = [s for s in result.sku_codes if s in applicable_skus]

        # ── 4. Build document list ──
        # Start with minimum documents
        doc_map: dict[str, DocumentRef] = {}
        for doc in min_docs:
            doc_map[doc["docCode"]] = DocumentRef(
                docCode=doc["docCode"],
                mandatory=doc.get("mandatory", True),
            )

        # Apply document rules sorted by priority
        sorted_doc_rules = sorted(document_rules, key=lambda r: r.get("priority", 0))
        for rule in sorted_doc_rules:
            conditions = rule.get("conditions", {})
            applicable_skus = rule.get("applicableSkuCodes", [])

            # Check SKU applicability
            if applicable_skus and not any(s in applicable_skus for s in all_sku_codes):
                continue

            if _matches_condition(conditions, context):
                result.matched_rules.append(rule.get("ruleId", rule.get("ruleName", "unknown")))

                # Add additional documents
                for doc in rule.get("additionalDocuments", []):
                    doc_map[doc["docCode"]] = DocumentRef(
                        docCode=doc["docCode"],
                        mandatory=doc.get("mandatory", True),
                    )

                # Remove documents
                for doc_code in rule.get("removeDocuments", []):
                    if doc_code in doc_map:
                        del doc_map[doc_code]

                # Set mandatory flag
                for doc in rule.get("setMandatory", []):
                    if doc["docCode"] in doc_map:
                        doc_map[doc["docCode"]] = DocumentRef(
                            docCode=doc["docCode"],
                            mandatory=doc.get("mandatory", False),
                        )

                # Modify document notes (we track docCode, mandatory only)
                for doc in rule.get("modifyDocuments", []):
                    if doc["docCode"] in doc_map:
                        # Keep existing mandatory, just note the modification
                        pass

        result.documents = list(doc_map.values())

        # ── 5. Apply pricing adjustments ──
        sorted_pricing = sorted(pricing_adjustments, key=lambda r: r.get("priority", 0))
        for rule in sorted_pricing:
            conditions = rule.get("conditions", {})
            applicable_skus = rule.get("applicableSkuCodes", [])

            if applicable_skus and not any(s in applicable_skus for s in all_sku_codes):
                continue

            if _matches_condition(conditions, context):
                adjustment = rule.get("adjustment", {})
                adj_type = adjustment.get("type", "")
                adj_value = adjustment.get("value", 0)

                if adj_type == "add_amount":
                    result.final_price += adj_value
                elif adj_type == "subtract_amount":
                    result.final_price -= adj_value

                result.applied_adjustments.append(
                    f"{rule.get('ruleId', 'unknown')}: {adj_type} {adj_value} {adjustment.get('currency', '')}"
                )
                result.matched_rules.append(rule.get("ruleId", rule.get("ruleName", "unknown")))

    engine.dispose()
    return result


def get_destinations_for_interests(interests: list[str]) -> list[dict]:
    """Find destinations matching user interests."""
    sync_url = settings.sync_database_url
    engine = create_engine(sync_url)

    results = []
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT country_code, country_name, interests, popularity_score,
                       min_processing_days, starting_price_currency, starting_price_amount,
                       has_skus_in_poc
                FROM destinations
                ORDER BY popularity_score DESC
            """)
        ).fetchall()

        for row in rows:
            dest_interests = row[2] if isinstance(row[2], list) else []
            # Check overlap with user interests
            overlap = set(interests) & set(dest_interests)
            if overlap:
                results.append({
                    "countryCode": row[0],
                    "countryName": row[1],
                    "interests": dest_interests,
                    "matchedInterests": list(overlap),
                    "popularityScore": float(row[3]) if row[3] else 0,
                    "minProcessingDays": row[4],
                    "startingPrice": f"{row[5]} {row[6]}" if row[5] else None,
                    "hasSkusInPoc": row[7],
                })

    engine.dispose()
    return results
