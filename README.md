# Hashtag Cinema Bot

Aiogram 3 asosida yozilgan kino sotuvchi Telegram bot. Foydalanuvchilar kino kodi
yoki nomi orqali qidiradi, pullik kinolarni karta orqali sotib oladi (chek Gemini AI
orqali avtomatik tekshiriladi), Premium tarif xarid qiladi va do'stlarini taklif
qiladi. Admin panel orqali kinolar, kanallar, narxlar va statistikalar boshqariladi.

## Fayllar tuzilishi

- `config.py` — bot tokeni, admin ID, Gemini API kaliti va boshqa sozlamalar
- `database.py` — SQLite (aiosqlite) bilan ishlash: users, movies, purchases,
  admins, kanallar, sozlamalar, to'lovlar
- `keyboard.py` — barcha reply/inline klaviaturalar
- `main.py` — botning asosiy logikasi (handlerlar, FSM holatlar, ishga tushirish)

## O'rnatish

```bash
pip install -r requirements.txt
```

`config.py` faylida (yoki muhit o'zgaruvchilari orqali) quyidagilarni sozlang:

- `BOT_TOKEN` — @BotFather dan olingan token
- `SUPER_ADMIN_ID` — sizning Telegram ID raqamingiz (birinchi admin)
- `GEMINI_API_KEY` — https://aistudio.google.com/app/apikey dan olingan kalit

## Ishga tushirish

```bash
python main.py
```

## Muhim eslatmalar

- Majburiy kanal qo'shishda botni albatta o'sha kanalga **admin** qilib qo'yish
  kerak, aks holda obunani tekshira olmaydi (Instagram linklari tekshirilmaydi).
- To'lov cheklari avtomatik Gemini AI orqali tekshiriladi, lekin har doim
  adminlarga ham yuboriladi — agar chek soxta bo'lsa, admin uni "Bekor qilish"
  tugmasi orqali qaytarib olishi mumkin.
- Statistika bo'limidagi "o'rtacha javob berish vaqti" bot ishga tushgandan
  keyingi so'nggi 500 ta xabar asosida hisoblanadi (xotirada saqlanadi, doimiy
  emas).
- Ma'lumotlar bazasi standart holatda `cinema_bot.db` nomli SQLite fayliga
  yoziladi (`DB_PATH` orqali o'zgartirish mumkin).
