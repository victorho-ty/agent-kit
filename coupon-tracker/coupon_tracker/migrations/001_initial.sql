-- One account row per user. `id` is what every other table references; the
-- Telegram user id is a unique column on the same row, not a separate table.
CREATE TABLE account (
  id               TEXT PRIMARY KEY,      -- ULID
  display_name     TEXT NOT NULL,         -- CLI/agent output only; not an identity
  telegram_user_id TEXT UNIQUE,           -- TEXT: telegram ids exceed 2^32
  chat_id          TEXT,                  -- where alerts go; usually the same DM
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE INDEX idx_account_telegram ON account(telegram_user_id);

CREATE TABLE media (
  id           TEXT PRIMARY KEY,          -- ULID
  account_id   TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  sha256       TEXT NOT NULL,             -- content hash
  path         TEXT NOT NULL,             -- relative to media/<account_id>/
  mime         TEXT NOT NULL,
  bytes        INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  -- Dedupe is PER ACCOUNT. A global unique hash would let one account's coupon
  -- hold another's image alive through purge's reference count.
  UNIQUE (account_id, sha256)
);

CREATE INDEX idx_media_account ON media(account_id);

CREATE TABLE coupon (
  id                 TEXT PRIMARY KEY,    -- ULID
  account_id         TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  merchant           TEXT NOT NULL,
  title              TEXT NOT NULL,
  code               TEXT,
  value_text         TEXT,                -- "$80 off", "buy 1 get 1" — display only
  expires_on         TEXT NOT NULL,       -- ISO date, app timezone
  expiry_precision   TEXT NOT NULL CHECK (expiry_precision IN ('exact','end_of_month','inferred')),
  expiry_assumed     INTEGER NOT NULL DEFAULT 0,
  status             TEXT NOT NULL CHECK (status IN ('needs_review','active','used','expired','void')),
  uses_total         INTEGER NOT NULL DEFAULT 1,
  uses_remaining     INTEGER NOT NULL DEFAULT 1,
  conditions_json    TEXT NOT NULL DEFAULT '[]',
  notes              TEXT,
  raw_text           TEXT,                -- provenance: original text as received
  source_kind        TEXT NOT NULL CHECK (source_kind IN ('telegram_photo','telegram_text','manual','file')),
  source_ref         TEXT,                -- telegram message id, filename, etc.
  media_id           TEXT REFERENCES media(id),
  extraction_confidence REAL,
  dedupe_key         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  used_at            TEXT,
  expired_at         TEXT
);

-- Every hot index leads with account_id: it is in the WHERE clause of every query.
CREATE INDEX idx_coupon_acct_status_expiry ON coupon(account_id, status, expires_on);
CREATE INDEX idx_coupon_acct_media         ON coupon(account_id, media_id);
CREATE INDEX idx_coupon_acct_dedupe        ON coupon(account_id, dedupe_key);

CREATE TABLE alerts_sent (
  account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  coupon_id  TEXT NOT NULL REFERENCES coupon(id) ON DELETE CASCADE,
  alert_kind TEXT NOT NULL,               -- 'pre_expiry' | 'expiry_day' | 'late_digest'
  sent_at    TEXT NOT NULL,
  PRIMARY KEY (coupon_id, alert_kind)
);

CREATE INDEX idx_alerts_account ON alerts_sent(account_id);

CREATE TABLE inbox_item (
  id           TEXT PRIMARY KEY,
  account_id   TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  received_at  TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('photo','text')),
  payload_path TEXT,                      -- relative to inbox/<account_id>/
  payload_text TEXT,
  telegram_msg_id TEXT,
  state        TEXT NOT NULL CHECK (state IN ('queued','processing','done','failed')),
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT
);

CREATE INDEX idx_inbox_acct_state ON inbox_item(account_id, state, received_at);
