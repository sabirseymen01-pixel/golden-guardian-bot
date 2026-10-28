import sqlite3
import datetime

DB_NAME = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER,
            username TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action_type TEXT,
            target_id INTEGER,
            username TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            can_ban INTEGER DEFAULT 1,
            can_speak INTEGER DEFAULT 1,
            can_write INTEGER DEFAULT 1,
            is_muted INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            added_by INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

def save_user(chat_id, user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (user_id, chat_id, username) VALUES (?, ?, ?)', (user_id, chat_id, username))
    conn.commit()
    conn.close()

def log_action(chat_id, action_type, target_id, username, details):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    cursor.execute('''
        INSERT INTO logs (chat_id, action_type, target_id, username, details, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, action_type, target_id, username, details, now))
    conn.commit()
    conn.close()

def get_recent_logs(limit=50):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT action_type, username, details, timestamp FROM logs ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"action_type": r[0], "username": r[1], "details": r[2], "timestamp": r[3]} for r in rows]

def get_bot_statistics():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM logs')
    total_actions = cursor.fetchone()[0]
    
    cursor.execute('SELECT action_type, COUNT(*) FROM logs GROUP BY action_type')
    breakdown = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return {"total_actions": total_actions, "action_breakdown": breakdown}

def get_admin_performance_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT username, COUNT(*) as cnt FROM logs GROUP BY username ORDER BY cnt DESC LIMIT 5')
    rows = cursor.fetchall()
    conn.close()
    return [{"username": r[0], "count": r[1]} for r in rows]

def is_authorized_staff(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM staff WHERE user_id = ?', (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def get_staff_permissions(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT can_ban, can_speak, can_write, is_muted, warnings FROM staff WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"can_ban": row[0], "can_speak": row[1], "can_write": row[2], "is_muted": row[3], "warnings": row[4]}
    return {"can_ban": 1, "can_speak": 1, "can_write": 1, "is_muted": 0, "warnings": 0}

def add_warning(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE staff SET warnings = warnings + 1 WHERE user_id = ?', (user_id,))
    cursor.execute('SELECT warnings FROM staff WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else 1

def reset_warnings(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE staff SET warnings = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_staff():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, full_name, can_ban, can_speak, can_write, is_muted, warnings FROM staff')
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_staff_member(user_id, username, full_name, added_by):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO staff (user_id, username, full_name, can_ban, can_speak, can_write, is_muted, warnings, added_by)
        VALUES (?, ?, ?, 1, 1, 1, 0, 0, ?)
    ''', (user_id, username, full_name, added_by))
    conn.commit()
    conn.close()

def remove_staff_member(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM staff WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_id_by_username(chat_id, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE username = ? COLLATE NOCASE', (username,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None
