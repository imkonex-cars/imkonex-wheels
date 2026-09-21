CREATE TABLE IF NOT EXISTS products (
 id text PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('tires','wheels')),
 brand text NOT NULL, payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS products_category_brand ON products(kind,brand);
CREATE TABLE IF NOT EXISTS offers (
 id text PRIMARY KEY, product_id text NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 stock integer NOT NULL CHECK(stock>=0), price bigint NOT NULL CHECK(price>0),
 days integer NOT NULL CHECK(days>=0), payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS offers_product ON offers(product_id);
CREATE TABLE IF NOT EXISTS catalog_state (id integer PRIMARY KEY CHECK(id=1),metadata jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS import_runs (
 id bigserial PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz, product_count integer NOT NULL, is_demo boolean NOT NULL
);
