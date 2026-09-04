DROP TABLE IF EXISTS loans;
DROP TABLE IF EXISTS devices;
DROP TABLE IF EXISTS contract_plans;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, department TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
 retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE contract_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE devices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, device_number TEXT NOT NULL UNIQUE,
 device_type TEXT NOT NULL CHECK(device_type IN ('USB','WiFi')), phone_number TEXT NOT NULL,
 plan_id INTEGER NOT NULL REFERENCES contract_plans(id), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE loans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL REFERENCES devices(id),
 borrower_id INTEGER NOT NULL REFERENCES users(id), lent_by_id INTEGER NOT NULL REFERENCES users(id),
 checkout_date TEXT NOT NULL, due_date TEXT NOT NULL, returned_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('borrowed','returned','lost')), note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 CHECK(due_date >= checkout_date)
);
CREATE UNIQUE INDEX one_active_loan_per_device ON loans(device_id) WHERE status='borrowed';
