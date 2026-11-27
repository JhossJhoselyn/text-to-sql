\set ON_ERROR_STOP on

CREATE ROLE text_to_sql_lector
WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

ALTER ROLE text_to_sql_lector
SET default_transaction_read_only = on;

ALTER ROLE text_to_sql_lector
SET statement_timeout = '10s';

GRANT CONNECT ON DATABASE text_to_sql_baja_v2 TO text_to_sql_lector;
GRANT CONNECT ON DATABASE text_to_sql_alta_v2 TO text_to_sql_lector;
