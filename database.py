import sqlite3

DB_NAME = "bot_database.db"

def get_connection():
    return sqlite3.connect(DB_NAME)

# Veritabanı tablolarını başlatma
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
    
    # Kullanıcı kullanıcı adını değiştirdiyse eski kaydı aynı chat içinde güncelle veya çakışmaları engelle
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
    
    # Öncelikli olarak aynı sohbet grubundaki kayda bak
    cursor.execute('''
        SELECT user_id FROM users WHERE chat_id = ? AND LOWER(username) = ?
    ''', (chat_id, clean_username))
    row = cursor.fetchone()
    
    # Grup verisinde yoksa genel kayıt verisinden ID yakala
    if not row:
        cursor.execute('''
            SELECT user_id FROM users WHERE LOWER(username) = ?
        ''', (clean_username,))
        row = cursor.fetchone()
        
    conn.close()
    return row[0] if row else None
