"""Seed the database with data from JSON files."""

import json
import os
import logging
from sqlalchemy import create_engine, text
from app.config import settings

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_json(filename: str) -> list:
    """Load a JSON file from the data directory."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_database():
    """Seed all tables from JSON files. Skips if data already exists."""
    sync_url = settings.sync_database_url
    engine = create_engine(sync_url)

    with engine.begin() as conn:
        # Check if already seeded
        result = conn.execute(text("SELECT COUNT(*) FROM visa_skus"))
        if result.scalar() > 0:
            logger.info("Database already seeded, skipping.")
            return

        logger.info("Seeding database from JSON files...")

        # ── Visa SKUs ──
        skus = _load_json("visasku.json")
        for sku in skus:
            conn.execute(
                text("""
                    INSERT INTO visa_skus (
                        id, sku_code, country_code, country_name, purpose,
                        traveler_type, entry_type, validity_days, stay_days,
                        processing_mode, processing_speed, processing_time_days,
                        min_lead_time_days, base_price_currency, base_price_amount,
                        cta_url, is_active, updated_at
                    ) VALUES (
                        :id, :sku_code, :country_code, :country_name, :purpose,
                        :traveler_type, :entry_type, :validity_days, :stay_days,
                        :processing_mode, :processing_speed, :processing_time_days,
                        :min_lead_time_days, :currency, :amount,
                        :cta_url, :is_active, :updated_at
                    )
                """),
                {
                    "id": sku["_id"],
                    "sku_code": sku["skuCode"],
                    "country_code": sku["countryCode"],
                    "country_name": sku["countryName"],
                    "purpose": sku["purpose"],
                    "traveler_type": sku["travelerType"],
                    "entry_type": sku["entryType"],
                    "validity_days": sku["validityDays"],
                    "stay_days": sku["stayDays"],
                    "processing_mode": sku["processingMode"],
                    "processing_speed": sku["processingSpeed"],
                    "processing_time_days": sku["processingTimeDays"],
                    "min_lead_time_days": sku["minLeadTimeDays"],
                    "currency": sku["basePrice"]["currency"],
                    "amount": sku["basePrice"]["amount"],
                    "cta_url": sku.get("ctaUrl"),
                    "is_active": sku.get("isActive", True),
                    "updated_at": sku.get("updatedAt"),
                },
            )

        # ── Destinations ──
        destinations = _load_json("destination.json")
        for dest in destinations:
            conn.execute(
                text("""
                    INSERT INTO destinations (
                        id, country_code, country_name, interests,
                        popularity_score, min_processing_days,
                        starting_price_currency, starting_price_amount,
                        has_skus_in_poc, updated_at
                    ) VALUES (
                        :id, :country_code, :country_name, :interests,
                        :popularity_score, :min_processing_days,
                        :currency, :amount,
                        :has_skus_in_poc, :updated_at
                    )
                """),
                {
                    "id": dest["_id"],
                    "country_code": dest["destinationCountryCode"],
                    "country_name": dest["destinationCountryName"],
                    "interests": dest["interests"],
                    "popularity_score": dest.get("popularityScore"),
                    "min_processing_days": dest.get("minProcessingDays"),
                    "currency": dest.get("startingPrice", {}).get("currency"),
                    "amount": dest.get("startingPrice", {}).get("amount"),
                    "has_skus_in_poc": dest.get("hasSkusInPoc", False),
                    "updated_at": dest.get("updatedAt"),
                },
            )

        # ── Destination Market Configs ──
        configs = _load_json("desitnationmarket.json")
        for cfg in configs:
            conn.execute(
                text("""
                    INSERT INTO destination_market (
                        id, destination_country_code, market, version, status,
                        effective_from, effective_to,
                        minimum_documents, visa_mode_rules,
                        document_rules, pricing_adjustments, updated_at
                    ) VALUES (
                        :id, :country_code, :market, :version, :status,
                        :effective_from, :effective_to,
                        :min_docs, :visa_rules,
                        :doc_rules, :pricing, :updated_at
                    )
                """),
                {
                    "id": cfg["_id"],
                    "country_code": cfg["destinationCountryCode"],
                    "market": cfg["market"],
                    "version": cfg["version"],
                    "status": cfg["status"],
                    "effective_from": cfg.get("effectiveFrom"),
                    "effective_to": cfg.get("effectiveTo"),
                    "min_docs": json.dumps(cfg["minimumDocuments"]),
                    "visa_rules": json.dumps(cfg.get("visaModeRules", [])),
                    "doc_rules": json.dumps(cfg.get("documentRules", [])),
                    "pricing": json.dumps(cfg.get("pricingAdjustments", [])),
                    "updated_at": cfg.get("updatedAt"),
                },
            )

        # ── Knowledge Sources ──
        sources = _load_json("knowledgesources.json")
        for src in sources:
            conn.execute(
                text("""
                    INSERT INTO knowledge_sources (
                        id, destination_country_code, source_type,
                        title, chunk_id, text, trust_score
                    ) VALUES (
                        :id, :country_code, :source_type,
                        :title, :chunk_id, :text, :trust_score
                    )
                """),
                {
                    "id": src["_id"],
                    "country_code": src["destinationCountryCode"],
                    "source_type": src["sourceType"],
                    "title": src["title"],
                    "chunk_id": src["chunkId"],
                    "text": src["text"],
                    "trust_score": src.get("trustScore"),
                },
            )

        logger.info("Database seeded successfully!")

    engine.dispose()


def copy_data_files():
    """Ensure data files exist in the data directory."""
    project_root = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    files = ["visasku.json", "destination.json", "desitnationmarket.json", "knowledgesources.json"]
    for f in files:
        src = os.path.join(project_root, f)
        dst = os.path.join(data_dir, f)
        if os.path.exists(src) and not os.path.exists(dst):
            import shutil
            shutil.copy2(src, dst)
