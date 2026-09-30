"""Tüm gizli ve temel ayarlar ortam değişkenlerinden okunur."""
import os

from dotenv import load_dotenv

load_dotenv()  # Lokal geliştirmede .env dosyasını okur; Render'da gerek yok.


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Eksik ortam değişkeni: {name}")
    return value


# --- Zorunlu ---
BOT_TOKEN = _required("BOT_TOKEN")
MONGO_URI = _required("MONGO_URI")
OWNER_ID = int(_required("OWNER_ID"))  # Botun sahibi (süper yetkili)

# --- Opsiyonel ---
DB_NAME = os.getenv("DB_NAME", "fedbot")
PORT = int(os.getenv("PORT", "10000"))  # Render PORT değişkenini kendisi verir
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

# Hızlı geçiş butonları için iki ana grup
GROUP_A_ID = int(os.getenv("GROUP_A_ID", "0"))
GROUP_B_ID = int(os.getenv("GROUP_B_ID", "0"))
GROUP_A_NAME = os.getenv("GROUP_A_NAME", "Grup A")
GROUP_B_NAME = os.getenv("GROUP_B_NAME", "Grup B")
