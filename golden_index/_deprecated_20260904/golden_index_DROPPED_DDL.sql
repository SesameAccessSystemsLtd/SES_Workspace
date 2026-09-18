CREATE TABLE golden_index (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sesame_part_no      TEXT UNIQUE NOT NULL,
    kan_number          TEXT,
    asana_task_gid      TEXT,
    asana_task_url      TEXT,
    stock_project       TEXT,
    description         TEXT,
    stock_qty           INTEGER,
    trigger_order_qty   INTEGER,
    stock_location      TEXT,
    drawing_url         TEXT,
    match_confidence    INTEGER,
    last_synced         TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
