-- Migration: Add license activation tracking columns to org_licenses
-- Date: 2026-09-10

ALTER TABLE org_licenses 
ADD COLUMN IF NOT EXISTS activated_node_id TEXT,
ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS activated_hostname TEXT;
