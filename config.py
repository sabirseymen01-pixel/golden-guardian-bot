import os

# Render Environment Variables (Çevre Değişkenleri) üzerinden okunur
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN çevre değişkeni bulunamadı! Lütfen Render panelinden ekleyin.")
