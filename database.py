import sqlite3
import datetime

DB_NAME = "bot_database.db"

def get_connection():
    return sqlite3.connect(DB_NAME)

# Veritabanı tablolarını başlatma ve genişletme
def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Ayarlar tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_message TEXT
        )
    ''')
    
    # Kullanıcı veritabanı (Username -> User ID eşleşmesi için)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    ''')
    
    # Gelişmiş Loglar Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action_type TEXT,
            user_id INTEGER,
            username TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')
    
    # Staff / Yönetici Yönetim Tablosu (Genişletilmiş Yetki Kolonları ile)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff_members (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            can_ban INTEGER DEFAULT 1,
            can_speak INTEGER DEFAULT 1,
            can_write INTEGER DEFAULT 1,
            is_muted INTEGER DEFAULT 0,
            added_by INTEGER
        )
    ''')

    # Golden Chat / Haberleşme Portu Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            message TEXT,
            msg_type TEXT DEFAULT 'text',
            timestamp TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

# Uygulama açılışında tabloları hazırla
init_db()

# Bellek içi (In-Memory) uyarı sistemi
user_warnings = {}

def add_warning(chat_id: int, user_id: int) -> int:
    key = (chat_id, user_id)
    user_warnings[key] = user_warnings.get(key, 0) + 1
    return user_warnings[key]

def reset_warnings(chat_id: int, user_id: int):
    key = (chat_id, user_id)
    if key in user_warnings:
        user_warnings[key] = 0

def get_warnings(chat_id: int, user_id: int) -> int:
    return user_warnings.get((chat_id, user_id), 0)

# Hoş geldin mesajı fonksiyonları
def set_welcome_message(chat_id: int, message: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO settings (chat_id, welcome_message)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET welcome_message=excluded.welcome_message
    ''', (chat_id, message))
    conn.commit()
    conn.close()

def get_welcome_message(chat_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT welcome_message FROM settings WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return "👋 Hoş geldin {user}!\n\nGrup kurallarına uymayı ve saygılı bir sohbet ortamı sürdürmeyi unutma."

# Kullanıcı adı (@username) kaydetme ve sorgulama fonksiyonları
def save_user(chat_id: int, user_id: int, username: str):
    if not username:
        return
    clean_username = username.lstrip("@").lower()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO users (chat_id, user_id, username)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username
    ''', (chat_id, user_id, clean_username))
    conn.commit()
    conn.close()

def get_user_id_by_username(chat_id: int, username: str):
    if not username:
        return None
    clean_username = username.lstrip("@").lower()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id FROM users WHERE chat_id = ? AND LOWER(username) = ?
    ''', (chat_id, clean_username))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute('''
            SELECT user_id FROM users WHERE LOWER(username) = ?
        ''', (clean_username,))
        row = cursor.fetchone()
        
    conn.close()
    return row[0] if row else None

# --- STAFF / YÖNETİCİ YÖNETİM & YETKİLENDİRME FONKSİYONLARI ---

def is_authorized_staff(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT can_ban FROM staff_members WHERE user_id = ?', (int(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_staff_permissions(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT can_ban, can_speak, can_write, is_muted FROM staff_members WHERE user_id = ?', (int(user_id),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"can_ban": row[0], "can_speak": row[1], "can_write": row[2], "is_muted": row[3]}
    return {"can_ban": 0, "can_speak": 0, "can_write": 0, "is_muted": 0}

def set_staff_permission(user_id: int, permission_type: str, status: int):
    """permission_type: 'can_ban', 'can_speak', 'can_write', veya 'is_muted'"""
    conn = get_connection()
    cursor = conn.cursor()
    if permission_type in ['can_ban', 'can_speak', 'can_write', 'is_muted']:
        cursor.execute(f'UPDATE staff_members SET {permission_type} = ? WHERE user_id = ?', (status, int(user_id)))
        conn.commit()
    conn.close()

def add_staff_member(user_id: int, username: str, full_name: str, added_by: int):
    conn = get_connection()
    cursor = conn.cursor()
    clean_username = username.lstrip("@").lower() if username else ""
    cursor.execute('''
        INSERT OR REPLACE INTO staff_members (user_id, username, full_name, can_ban, can_speak, can_write, is_muted, added_by)
        VALUES (?, ?, ?, 1, 1, 1, 0, ?)
    ''', (int(user_id), clean_username, full_name, added_by))
    conn.commit()
    conn.close()

def remove_staff_member(identifier):
    conn = get_connection()
    cursor = conn.cursor()
    if isinstance(identifier, int) or str(identifier).isdigit() or (str(identifier).startswith("-") and str(identifier)[1:].isdigit()):
        cursor.execute('DELETE FROM staff_members WHERE user_id = ?', (int(identifier),))
    else:
        clean_username = str(identifier).lstrip("@").lower()
        cursor.execute('DELETE FROM staff_members WHERE LOWER(username) = ?', (clean_username,))
    conn.commit()
    conn.close()

def get_all_staff():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, full_name, can_ban, can_speak, can_write, is_muted FROM staff_members')
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- LOGLAMA VE İSTATİSTİK FONKSİYONLARI ---

def log_action(chat_id: int, action_type: str, user_id: int, username: str, details: str):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO logs (chat_id, action_type, user_id, username, details, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, action_type, user_id, username or "Bilinmiyor", details, timestamp))
    conn.commit()
    conn.close()

def get_recent_logs(limit: int = 50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT chat_id, action_type, user_id, username, details, timestamp 
        FROM logs ORDER BY id DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    logs = []
    for r in rows:
        logs.append({
            "chat_id": r[0],
            "action_type": r[1],
            "user_id": r[2],
            "username": r[3],
            "details": r[4],
            "timestamp": r[5]
        })
    return logs

def get_bot_statistics():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM logs")
    total_actions = cursor.fetchone()[0]
    cursor.execute("SELECT action_type, COUNT(*) FROM logs GROUP BY action_type")
    action_breakdown = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return {
        "total_actions": total_actions,
        "action_breakdown": action_breakdown
    }

def get_admin_performance_stats():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, action_type, COUNT(*) 
        FROM logs 
        GROUP BY user_id, action_type
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    stats = {}
    for r in rows:
        uid, uname, action, count = r[0], r[1], r[2], r[3]
        if uid not in stats:
            stats[uid] = {"username": uname, "actions": {}}
        stats[uid]["actions"][action] = count
    return stats

# --- GOLDEN CHAT (HABERLEŞME PORTU) FONKSİYONLARI ---

def save_chat_message(user_id: int, username: str, full_name: str, message: str, msg_type: str = "text"):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().strftime("%H:%M")
    cursor.execute('''
        INSERT INTO chat_messages (user_id, username, full_name, message, msg_type, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, username, full_name, message, msg_type, timestamp))
    conn.commit()
    conn.close()

def get_chat_messages(limit: int = 50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, full_name, message, msg_type, timestamp 
        FROM chat_messages ORDER BY id DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for r in reversed(rows):
        messages.append({
            "user_id": r[0],
            "username": r[1],
            "full_name": r[2],
            "message": r[3],
            "msg_type": r[4],
            "timestamp": r[5]
        })
    return messages
