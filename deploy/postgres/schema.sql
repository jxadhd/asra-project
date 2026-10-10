-- Signature table and grants. Run as asra_owner (setup.sql does this).
-- Safe to run again.

CREATE TABLE IF NOT EXISTS signatures (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    petition    text        NOT NULL,
    name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    town        text        NOT NULL CHECK (length(town) BETWEEN 1 AND 100),
    country     char(2)     NOT NULL,
    email       text        CHECK (email IS NULL OR length(email) <= 254),
    updates     boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    dedupe_key  text        NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS signatures_petition_country ON signatures (petition, country);

GRANT USAGE ON SCHEMA public TO asra_app, asra_admin;

-- The website can add signatures and count them by country. It cannot read
-- names, towns or emails, so a compromised web container can't leak them.
GRANT INSERT ON signatures TO asra_app;
GRANT SELECT (petition, country) ON signatures TO asra_app;

-- Organisers export signatures and handle removal requests.
GRANT SELECT, DELETE ON signatures TO asra_admin;
