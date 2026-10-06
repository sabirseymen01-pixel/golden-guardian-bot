# Basit bellek içi (In-Memory) uyarı veritabanı
# Üretim ortamında kalıcılık için SQLite veya PostgreSQL önerilir.
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

# Hoş geldin mesajını kaydetme ve alma fonksiyonları
def set_welcome_message(chat_id: int, message: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_message TEXT
        )
    ''')
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
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER PRIMARY KEY, welcome_message TEXT)')
    cursor.execute('SELECT welcome_message FROM settings WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    # Varsayılan mesaj
    return "👋 Hoş geldin {user}!\n\nGrup kurallarına uymayı ve saygılı bir sohbet ortamı sürdürmeyi unutma."
