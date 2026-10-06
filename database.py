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
