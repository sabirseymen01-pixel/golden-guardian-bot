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
    
    # Gelişmiş Loglar Tablosu (Tüm hareketler, silinen mesajlar, banlar, şikayetler)
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
    
    # Staff / Yönetici Yönetim Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff_members (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            can_ban INTEGER DEFAULT 1,
            added_by INTEGER
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

# --- STAFF / YÖNETİCİ YÖNETİM FONKSİYONLARI ---

def is_authorized_staff(user_id: int) -> bool:
    """Kullanıcının veritabanında kayıtlı aktif bir staff olup olmadığını kontrol eder."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT can_ban FROM staff_members WHERE user_id = ?', (int(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_staff_ban_permission(user_id: int) -> bool:
    """Staff'ın ban yetkisinin (can_ban) açık olup olmadığını kontrol eder."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT can_ban FROM staff_members WHERE user_id = ?', (int(user_id),))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] == 1:
        return True
    return False

def set_staff_ban_permission(user_id: int, status: int):
    """Belirli bir staff'ın ban yetkisini açar (1) veya kapatır (0)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE staff_members SET can_ban = ? WHERE user_id = ?', (status, int(user_id)))
    conn.commit()
    conn.close()

def add_staff_member(user_id: int, username: str, full_name: str, added_by: int):
    """Yeni bir staff ekler (ID veya username ile)."""
    conn = get_connection()
    cursor = conn.cursor()
    clean_username = username.lstrip("@").lower() if username else ""
    cursor.execute('''
        INSERT OR REPLACE INTO staff_members (user_id, username, full_name, can_ban, added_by)
        VALUES (?, ?, ?, 1, ?)
    ''', (int(user_id), clean_username, full_name, added_by))
    conn.commit()
    conn.close()

def remove_staff_member(identifier):
    """Staff'ı ID veya kullanıcı adı ile listeden tamamen çıkarır."""
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
    """Tüm staff listesini getirir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, full_name, can_ban FROM staff_members')
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- LOGLAMA VE İSTATİSTİK FONKSİYONLARI ---

def log_action(chat_id: int, action_type: str, user_id: int, username: str, details: str):
    """Gruptaki her olayı (küfür silme, ban, uyarı, @admin şikayeti) veritabanına kaydeder."""
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
    """Mini App arayüzünde son hareketleri listelemek için logları çeker."""
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
    """Mini App için gelişmiş istatistik verilerini hesaplar."""
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
