-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Visa SKUs
CREATE TABLE IF NOT EXISTS visa_skus (
    id TEXT PRIMARY KEY,
    sku_code TEXT NOT NULL,
    country_code TEXT NOT NULL,
    country_name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    traveler_type TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    validity_days INTEGER NOT NULL,
    stay_days INTEGER NOT NULL,
    processing_mode TEXT NOT NULL,
    processing_speed TEXT NOT NULL,
    processing_time_days INTEGER NOT NULL,
    min_lead_time_days INTEGER NOT NULL,
    base_price_currency TEXT NOT NULL,
    base_price_amount NUMERIC NOT NULL,
    cta_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ
);

CREATE INDEX idx_visa_skus_country ON visa_skus (country_code);

CREATE INDEX idx_visa_skus_purpose ON visa_skus (purpose);

-- Destinations
CREATE TABLE IF NOT EXISTS destinations (
    id TEXT PRIMARY KEY,
    country_code TEXT NOT NULL,
    country_name TEXT NOT NULL,
    interests TEXT[] NOT NULL,
    popularity_score NUMERIC,
    min_processing_days INTEGER,
    starting_price_currency TEXT,
    starting_price_amount NUMERIC,
    has_skus_in_poc BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ
);

CREATE INDEX idx_destinations_country ON destinations (country_code);

-- Destination Market Configs (rules stored as JSONB)
CREATE TABLE IF NOT EXISTS destination_market (
    id TEXT PRIMARY KEY,
    destination_country_code TEXT NOT NULL,
    market TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    effective_from DATE,
    effective_to DATE,
    minimum_documents JSONB NOT NULL,
    visa_mode_rules JSONB NOT NULL,
    document_rules JSONB NOT NULL,
    pricing_adjustments JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

CREATE INDEX idx_dest_market_country ON destination_market (destination_country_code);

-- Knowledge Sources
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id TEXT PRIMARY KEY,
    destination_country_code TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    text TEXT NOT NULL,
    trust_score NUMERIC
);

CREATE INDEX idx_knowledge_country ON knowledge_sources (destination_country_code);

-- Embeddings for RAG (pgvector)
CREATE TABLE IF NOT EXISTS embeddings (
    id SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL, -- 'knowledge', 'destination', 'sku'
    source_id TEXT NOT NULL, -- reference to source table ID
    content TEXT NOT NULL, -- the text that was embedded
    embedding vector (3072), -- Gemini embedding dimension
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_embeddings_source ON embeddings (source_type, source_id);