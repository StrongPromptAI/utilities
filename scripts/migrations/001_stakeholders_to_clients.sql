-- Migration: Rename stakeholders → clients, add participants table
-- Run as single transaction

BEGIN;

-- Rename stakeholders → clients
ALTER TABLE stakeholders RENAME TO clients;
ALTER TABLE clients RENAME CONSTRAINT stakeholders_pkey TO clients_pkey;
ALTER TABLE clients RENAME CONSTRAINT stakeholders_name_key TO clients_name_key;
ALTER TABLE clients RENAME CONSTRAINT stakeholders_type_check TO clients_type_check;
ALTER INDEX idx_stakeholders_type RENAME TO idx_clients_type;
ALTER SEQUENCE stakeholders_id_seq RENAME TO clients_id_seq;

-- Rename FK column on calls
ALTER TABLE calls RENAME COLUMN stakeholder_id TO client_id;
ALTER TABLE calls RENAME CONSTRAINT calls_stakeholder_id_fkey TO calls_client_id_fkey;
ALTER INDEX idx_calls_stakeholder_id RENAME TO idx_calls_client_id;

-- Create participants table
CREATE TABLE participants (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_participants_call_id ON participants(call_id);
CREATE INDEX idx_participants_name ON participants(name);

-- Migrate participants data from text[] to table
INSERT INTO participants (call_id, name)
SELECT c.id, unnest(c.participants)
FROM calls c
WHERE c.participants IS NOT NULL;

-- Drop old participants column
ALTER TABLE calls DROP COLUMN participants;

-- Recreate view
DROP VIEW chunks_with_context;
CREATE VIEW chunks_with_context AS
SELECT c.id, c.chunk_idx, c.text, c.speaker, c.embedding, c.search_vector,
       cl.name AS client_name,
       p.name AS project_name,
       ca.call_date, ca.summary
FROM chunks c
JOIN calls ca ON c.call_id = ca.id
JOIN clients cl ON ca.client_id = cl.id
LEFT JOIN projects p ON ca.project_id = p.id;

COMMIT;
