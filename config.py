# config.py
# Botga tegishli barcha maxfiy va statik sozlamalar shu yerda saqlanadi.
# Barcha maxfiy qiymatlar FAQAT environment variable orqali beriladi —
# kodning ichida (va GitHub'da) hech qanday token/kalit saqlanmaydi.
#
# Lokal (o'z kompyuteringizda) test qilish uchun: shu papkada `.env` fayl
# yarating (`.env.example`dan nusxa oling) — u avtomatik o'qiladi.
# Railway'da esa "Variables" bo'limiga shu nomlar bilan qo'shasiz.

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ------------------------------------------------------------------
# Telegram bot tokeni (@BotFather dan olinadi)
# Railway'da "Variables" bo'limiga BOT_TOKEN nomi bilan qo'shing.
# ------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# ------------------------------------------------------------------
# Botni birinchi marta ishga tushirganda admin bo'ladigan Telegram ID.
# Keyinchalik botdagi "Admin tayinlash" bo'limi orqali yangi adminlar
# qo'shilishi mumkin (ular database.py dagi admins jadvalida saqlanadi).
# ------------------------------------------------------------------
SUPER_ADMIN_ID: int = int(os.environ["SUPER_ADMIN_ID"])

# ------------------------------------------------------------------
# Google Gemini API kaliti — to'lov chek(rasm)larini avtomatik
# tekshirish (OCR + tahlil) uchun ishlatiladi.
# https://aistudio.google.com/app/apikey dan olinadi.
# ------------------------------------------------------------------
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_API_URL: str = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

# ------------------------------------------------------------------
# Ma'lumotlar bazasi fayli (SQLite)
#
# MUHIM: yo'l doim shu config.py fayli joylashgan papkaga nisbatan
# MUTLAQ (absolute) qilib hisoblanadi. Aks holda, agar bot boshqa
# "working directory"dan (masalan systemd/pm2/screen orqali serverni
# qayta yoqqanda) ishga tushirilsa, u har safar YANGI, BO'SH baza
# fayli yaratib yuboradi va foydalanuvchilar/kinolar "o'chib ketgandek"
# ko'rinadi — aslida ular eski joyda saqlanib qolgan bo'ladi.
# ------------------------------------------------------------------
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
_DB_PATH_ENV: str = os.getenv("DB_PATH", "cinema_bot.db")
DB_PATH: str = (
    _DB_PATH_ENV if os.path.isabs(_DB_PATH_ENV) else os.path.join(BASE_DIR, _DB_PATH_ENV)
)

# ------------------------------------------------------------------
# Chek yuborilgandan keyin admin necha daqiqada tekshirishi kerakligi
# (foydalanuvchiga ko'rsatiladigan matn uchun)
# ------------------------------------------------------------------
CHECK_WAIT_MINUTES: int = 5

# Premium tarif necha kunga beriladi (1 oy)
PREMIUM_DURATION_DAYS: int = 30
