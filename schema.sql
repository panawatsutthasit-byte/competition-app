-- เปิดใช้งาน foreign keys (SQLite)
PRAGMA foreign_keys = ON;

-- ประเภทการแข่งขัน
CREATE TABLE IF NOT EXISTS category(
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL
);

-- โรงเรียน
CREATE TABLE IF NOT EXISTS school(
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL
);

-- ผู้เข้าแข่งขัน (เพิ่ม competition_date)
CREATE TABLE IF NOT EXISTS participant(
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name      TEXT NOT NULL,
  last_name       TEXT NOT NULL,
  school_id       INTEGER NOT NULL,
  category_id     INTEGER NOT NULL,
  competition_date TEXT NOT NULL,              -- YYYY-MM-DD วันที่แข่งขัน
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(school_id)   REFERENCES school(id),
  FOREIGN KEY(category_id) REFERENCES category(id)
);

-- ผลการแข่งขัน (เพิ่ม event_date)
CREATE TABLE IF NOT EXISTS result(
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  rank           INTEGER CHECK(rank BETWEEN 1 AND 10),
  score          REAL,
  note           TEXT,
  event_date     TEXT NOT NULL,                -- YYYY-MM-DD วันที่แข่ง/วันที่บันทึกผล
  FOREIGN KEY(participant_id) REFERENCES participant(id)
);

-- =======================
-- Indexes & Constraints
-- =======================

-- กันลงทะเบียนซ้ำ: คนเดิม + โรงเรียนเดิม + ประเภทเดิม + วันแข่งเดียวกัน
CREATE UNIQUE INDEX IF NOT EXISTS uq_participant_unique
ON participant(first_name, last_name, school_id, category_id, competition_date);

-- เร่งความเร็วการกรองตามวันที่
CREATE INDEX IF NOT EXISTS idx_participant_date ON participant(competition_date);
CREATE INDEX IF NOT EXISTS idx_result_date      ON result(event_date);

-- ดัชนีช่วยสรุปตามประเภท/โรงเรียน
CREATE INDEX IF NOT EXISTS idx_participant_cat    ON participant(category_id);
CREATE INDEX IF NOT EXISTS idx_participant_school ON participant(school_id);
