PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_migrations VALUES (1);
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, nickname TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS local_users (id TEXT PRIMARY KEY REFERENCES profiles(id), password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS local_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS problem_sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, year INTEGER NOT NULL, group_name TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', rights_note TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS problems (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES problem_sources(id), title TEXT NOT NULL, type TEXT NOT NULL, year INTEGER NOT NULL, group_name TEXT NOT NULL, subject TEXT NOT NULL, tags TEXT NOT NULL, difficulty INTEGER, status TEXT NOT NULL DEFAULT 'published', version INTEGER NOT NULL DEFAULT 1, grading_mode TEXT NOT NULL, question_images TEXT NOT NULL, choice_options TEXT NOT NULL, multiple INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0, answer_verified INTEGER NOT NULL DEFAULT 0, manual_revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS problem_answers (problem_id TEXT PRIMARY KEY REFERENCES problems(id), images TEXT NOT NULL, correct_options TEXT NOT NULL, provenance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_problem_progress (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), problem_id TEXT NOT NULL REFERENCES problems(id), result TEXT NOT NULL, result_source TEXT NOT NULL, ever_correct INTEGER NOT NULL DEFAULT 0, ever_incorrect INTEGER NOT NULL DEFAULT 0, correct_version INTEGER, revision INTEGER NOT NULL, problem_version INTEGER NOT NULL, text TEXT NOT NULL, image_ids TEXT NOT NULL, last_at TEXT NOT NULL, UNIQUE(user_id,problem_id));
CREATE TABLE IF NOT EXISTS daily_assignments (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), local_date TEXT NOT NULL, problem_id TEXT NOT NULL REFERENCES problems(id), filters TEXT NOT NULL, completed_at TEXT, UNIQUE(user_id,local_date));
CREATE TABLE IF NOT EXISTS answer_access (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), problem_id TEXT NOT NULL REFERENCES problems(id), created_at TEXT NOT NULL, UNIQUE(user_id,problem_id));
INSERT OR IGNORE INTO schema_migrations VALUES (3);
CREATE TABLE IF NOT EXISTS media_assets (id TEXT PRIMARY KEY, owner_id TEXT REFERENCES profiles(id), purpose TEXT NOT NULL, path TEXT NOT NULL, content_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, problem_id TEXT REFERENCES problems(id), source_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solutions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), problem_id TEXT NOT NULL REFERENCES problems(id), body TEXT NOT NULL, image_ids TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id,problem_id));
CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, solution_id TEXT NOT NULL REFERENCES solutions(id), user_id TEXT NOT NULL REFERENCES profiles(id), body TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS likes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), target_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id,target_id,kind));
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), actor_id TEXT NOT NULL REFERENCES profiles(id), target_id TEXT NOT NULL, event TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS moderation_logs (id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES profiles(id), target_id TEXT NOT NULL, kind TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS problem_feedback (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES profiles(id), problem_id TEXT NOT NULL REFERENCES problems(id), problem_version INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('incomplete','answer_mismatch','other')), detail TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','resolved','dismissed')), review_note TEXT NOT NULL DEFAULT '', reviewed_by TEXT REFERENCES profiles(id), created_at TEXT NOT NULL, reviewed_at TEXT);
CREATE INDEX IF NOT EXISTS feedback_queue ON problem_feedback(status,created_at);
CREATE INDEX IF NOT EXISTS problems_filter ON problems(status, year, group_name, type);
CREATE INDEX IF NOT EXISTS progress_history ON user_problem_progress(user_id, last_at);
CREATE INDEX IF NOT EXISTS solution_visibility ON solutions(problem_id,status);
CREATE INDEX IF NOT EXISTS comment_visibility ON comments(solution_id,status);
CREATE INDEX IF NOT EXISTS notification_inbox ON notifications(user_id,is_read);

-- The review list is independent of historical results and answer permissions.
CREATE TABLE IF NOT EXISTS user_mistakes (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES profiles(id),
 problem_id TEXT NOT NULL REFERENCES problems(id),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 updated_at TEXT NOT NULL,
 UNIQUE(user_id,problem_id)
);
CREATE INDEX IF NOT EXISTS mistakes_active ON user_mistakes(user_id,active,updated_at);
CREATE INDEX IF NOT EXISTS mistakes_problem ON user_mistakes(problem_id);
CREATE TRIGGER IF NOT EXISTS mistake_give_up_insert AFTER INSERT ON answer_access
BEGIN
 INSERT INTO user_mistakes VALUES (NEW.id,NEW.user_id,NEW.problem_id,1,NEW.created_at)
 ON CONFLICT(user_id,problem_id) DO UPDATE SET active=1,updated_at=excluded.updated_at;
END;
CREATE TRIGGER IF NOT EXISTS mistake_give_up_update AFTER UPDATE ON answer_access
BEGIN
 INSERT INTO user_mistakes VALUES (NEW.id,NEW.user_id,NEW.problem_id,1,NEW.created_at)
 ON CONFLICT(user_id,problem_id) DO UPDATE SET active=1,updated_at=excluded.updated_at;
END;
CREATE TRIGGER IF NOT EXISTS mistake_incorrect_insert AFTER INSERT ON user_problem_progress
WHEN NEW.result='incorrect'
BEGIN
 INSERT INTO user_mistakes VALUES (NEW.id,NEW.user_id,NEW.problem_id,1,NEW.last_at)
 ON CONFLICT(user_id,problem_id) DO UPDATE SET active=1,updated_at=excluded.updated_at;
END;
CREATE TRIGGER IF NOT EXISTS mistake_incorrect_update AFTER UPDATE ON user_problem_progress
WHEN NEW.result='incorrect' AND (OLD.result<>NEW.result OR OLD.revision<>NEW.revision)
BEGIN
 INSERT INTO user_mistakes VALUES (NEW.id,NEW.user_id,NEW.problem_id,1,NEW.last_at)
 ON CONFLICT(user_id,problem_id) DO UPDATE SET active=1,updated_at=excluded.updated_at;
END;
-- Preserve mastery on restarts; only seed records that have never been tracked.
INSERT OR IGNORE INTO user_mistakes
 SELECT id,user_id,problem_id,1,last_at FROM user_problem_progress WHERE ever_incorrect=1;
INSERT OR IGNORE INTO user_mistakes
 SELECT id,user_id,problem_id,1,created_at FROM answer_access;
INSERT OR IGNORE INTO schema_migrations VALUES (6);

