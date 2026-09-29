def _setup(self):
    schema = [
        """CREATE TABLE IF NOT EXISTS federations (
            fed_id TEXT PRIMARY KEY,
            fed_name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS fed_chats (
            chat_id INTEGER PRIMARY KEY,
            fed_id TEXT,
            chat_name TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS fed_bans (
            fed_id TEXT,
            user_id INTEGER,
            reason TEXT,
            banned_by INTEGER,
            date TEXT,
            PRIMARY KEY (fed_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER,
            user_id INTEGER,
            warn_count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_text TEXT,
            welcome_del INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS portals (
            chat_id INTEGER PRIMARY KEY,
            target TEXT,
            text TEXT,
            button TEXT,
            photo TEXT,
            interval INTEGER DEFAULT 0,
            last_msg_id INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            file_id TEXT,
            content TEXT,
            interval INTEGER NOT NULL,
            delete_after INTEGER DEFAULT 0,
            pin INTEGER DEFAULT 0,
            del_prev INTEGER DEFAULT 0,
            last_msg_id INTEGER,
            last_run INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )""",
    ]
    for q in schema:
        self.run(q)
    logger.info("✅ Veritabanı hazır.")

