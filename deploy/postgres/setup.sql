-- Creates the Asra database and its roles. Run once as a Postgres superuser
-- (deploy/postgres/setup.sh does this and generates the passwords):
--
--   psql -v ON_ERROR_STOP=1 -v app_password=... -v admin_password=... -f setup.sql
--
-- Optional: -v dbname=... (default asra). Safe to run again; it resets the
-- two passwords to the ones given.
--
--   asra_owner  owns the database and table; cannot log in
--   asra_app    used by the website: insert signatures, count by country
--   asra_admin  used by asra-admin: export and delete signatures

\set ON_ERROR_STOP on
\if :{?dbname}
\else
  \set dbname asra
\endif

SELECT 'CREATE ROLE asra_owner NOLOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'asra_owner') \gexec

SELECT format('CREATE ROLE asra_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'asra_app') \gexec
SELECT format('ALTER ROLE asra_app LOGIN PASSWORD %L', :'app_password') \gexec

SELECT format('CREATE ROLE asra_admin LOGIN PASSWORD %L', :'admin_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'asra_admin') \gexec
SELECT format('ALTER ROLE asra_admin LOGIN PASSWORD %L', :'admin_password') \gexec

SELECT format('CREATE DATABASE %I OWNER asra_owner', :'dbname')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'dbname') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'dbname') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO asra_app, asra_admin', :'dbname') \gexec

\connect :dbname
ALTER SCHEMA public OWNER TO asra_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE asra_owner;
\ir schema.sql
RESET ROLE;
