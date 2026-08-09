#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE SCHEMA IF NOT EXISTS sec_financial;
CREATE SCHEMA IF NOT EXISTS sec_form345;
CREATE SCHEMA IF NOT EXISTS sec_form13f;

CREATE TABLE IF NOT EXISTS public.import_manifest (
  schema_name text NOT NULL,
  table_name text NOT NULL,
  source_path text NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now(),
  row_count bigint NOT NULL,
  PRIMARY KEY (schema_name, table_name)
);
SQL

quote_ident() {
  printf '%s' "$1" | sed 's/"/""/g; s/.*/"&"/'
}

create_and_load() {
  schema_name=$1
  table_name=$2
  source_path=$3

  header=$(head -n 1 "$source_path" | tr -d '\r')
  cols_sql=""
  copy_cols=""

  old_ifs=$IFS
  IFS='	'
  set -- $header
  IFS=$old_ifs

  for col in "$@"; do
    normalized=$(printf '%s' "$col" | tr '[:upper:]' '[:lower:]')
    qcol=$(quote_ident "$normalized")
    if [ -n "$cols_sql" ]; then
      cols_sql="$cols_sql, "
      copy_cols="$copy_cols, "
    fi
    cols_sql="$cols_sql$qcol text"
    copy_cols="$copy_cols$qcol"
  done

  qschema=$(quote_ident "$schema_name")
  qtable=$(quote_ident "$table_name")

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DROP TABLE IF EXISTS $qschema.$qtable;
CREATE TABLE $qschema.$qtable ($cols_sql);
COPY $qschema.$qtable ($copy_cols)
FROM '$source_path'
WITH (FORMAT csv, DELIMITER E'\t', HEADER true);
INSERT INTO public.import_manifest (schema_name, table_name, source_path, row_count)
SELECT '$schema_name', '$table_name', '$source_path', COUNT(*) FROM $qschema.$qtable
ON CONFLICT (schema_name, table_name)
DO UPDATE SET source_path = EXCLUDED.source_path, imported_at = now(), row_count = EXCLUDED.row_count;
SQL
}

create_and_load sec_financial sub /data/sec-edgar/extracted/2026q1-financial-statement-data-sets/sub.txt
create_and_load sec_financial num /data/sec-edgar/extracted/2026q1-financial-statement-data-sets/num.txt
create_and_load sec_financial tag /data/sec-edgar/extracted/2026q1-financial-statement-data-sets/tag.txt
create_and_load sec_financial pre /data/sec-edgar/extracted/2026q1-financial-statement-data-sets/pre.txt

for file in /data/sec-edgar/extracted/2026q1-form345-insider-transactions/*.tsv; do
  table_name=$(basename "$file" .tsv | tr '[:upper:]' '[:lower:]')
  create_and_load sec_form345 "$table_name" "$file"
done

for file in /data/sec-edgar/extracted/2025dec-2026jan-feb-form13f/*.tsv; do
  table_name=$(basename "$file" .tsv | tr '[:upper:]' '[:lower:]')
  create_and_load sec_form13f "$table_name" "$file"
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /scripts/create_indexes.sql
