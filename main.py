# main.py
# "Hashtag Cinema Bot" — asosiy ishga tushirish fayli.
# Talab qilinadigan kutubxonalar: aiogram>=3.7, aiosqlite, httpx
#   pip install aiogram aiosqlite httpx
#
# Ishga tushirish:  python main.py
# Tokenlarni config.py da yoki muhit o'zgaruvchilarida sozlang.

import asyncio
import base64
import logging
import time
from datetime import datetime

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Message,
    BufferedInputFile,
)

import config
import database as db
import keyboard as kb

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hashtag_cinema_bot")

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

user_router = Router()
admin_router = Router()

RESPONSE_TIMES_MS: list[float] = []  # oddiy statistikadagi "o'rtacha javob vaqti" uchun


# =====================================================================
# FSM STATE GURUHLARI
# =====================================================================

class UserSearch(StatesGroup):
    waiting_name = State()
    waiting_code = State()


class BuyFlow(StatesGroup):
    waiting_receipt = State()


class AdminAddMovie(StatesGroup):
    waiting_video = State()
    waiting_code = State()
    waiting_name = State()
    waiting_description = State()
    waiting_genre = State()
    waiting_language = State()
    waiting_year = State()
    waiting_price = State()


class AdminEditMovie(StatesGroup):
    waiting_code = State()
    waiting_new_value = State()


class AdminDeleteMovie(StatesGroup):
    waiting_code = State()


class AdminAssignAdmin(StatesGroup):
    waiting_id = State()


class AdminMainChannel(StatesGroup):
    waiting_link = State()


class AdminMandatoryChannel(StatesGroup):
    waiting_link = State()


class AdminCardInfo(StatesGroup):
    waiting_number = State()
    waiting_owner = State()


class AdminPremiumPrice(StatesGroup):
    waiting_price = State()


class AdminBroadcast(StatesGroup):
    waiting_message = State()


# =====================================================================
# YORDAMCHI FUNKSIYALAR
# =====================================================================

def normalize_channel_link(link: str) -> tuple[str, str, str]:
    """
    Admin turli formatda link kiritishi mumkin: '@username', 'username',
    't.me/username', 'https://t.me/username', to'liq instagram.com havolasi va h.k.
    Bu funksiya har doim to'g'ri https:// URL va (agar telegram bo'lsa) API uchun
    '@username' formatini qaytaradi.

    Returns: (platform, url_for_button, api_username_or_url)
    """
    raw = link.strip()

    if "instagram.com" in raw.lower():
        if not raw.lower().startswith("http"):
            raw = "https://" + raw.lstrip("/")
        return "instagram", raw, raw

    # Telegram: t.me/xxx, https://t.me/xxx, @xxx yoki shunchaki xxx bo'lishi mumkin
    username = raw
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if username.lower().startswith(prefix):
            username = username[len(prefix):]
            break
    username = username.lstrip("@").strip("/").strip()

    url = f"https://t.me/{username}"
    api_username = f"@{username}"
    return "telegram", url, api_username


async def check_user_subscription(user_id: int) -> list[dict]:
    """Obuna bo'lmagan majburiy kanallar ro'yxatini qaytaradi (bo'sh bo'lsa hammasiga obuna)."""
    channels = await db.list_mandatory_channels()
    missing = []
    for ch in channels:
        if ch["platform"] != "telegram" or not ch["chat_id"]:
            # Instagram linklarini tekshirish shart emas (spec bo'yicha)
            continue
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            # Bot kanalga admin qilinmagan yoki boshqa xatolik — xavfsiz tomondan obuna bo'lmagan deb hisoblaymiz
            missing.append(ch)
    return missing


async def send_main_menu(chat_id: int, user_id: int, text: str = "🏠 Asosiy menyu") -> None:
    is_adm = await db.is_admin(user_id)
    await bot.send_message(chat_id, text, reply_markup=kb.main_menu(is_admin=is_adm))


def format_movie_caption(movie: dict) -> str:
    price_txt = "Bepul" if movie["price"] == 0 else f"{movie['price']} so'm"
    return (
        f"🎬 <b>{movie['name']}</b>\n"
        f"🔢 Kod: <code>{movie['code']}</code>\n"
        f"🎭 Janr: {movie['genre']}\n"
        f"🌐 Til: {movie['language']}\n"
        f"📅 Yil: {movie['year']}\n"
        f"📄 Tavsif: {movie['description']}\n"
        f"💰 Narx: {price_txt}"
    )


async def deliver_movie(message: Message, movie: dict, user_id: int) -> None:
    """Kinoni foydalanuvchiga yuboradi (bepul / premium / oldin sotib olingan holatlarda)."""
    await message.answer_video(
        video=movie["file_id"],
        caption=format_movie_caption(movie),
    )


async def offer_purchase(message: Message, movie: dict) -> None:
    await message.answer(
        format_movie_caption(movie),
        reply_markup=kb.buy_movie_keyboard(movie["code"], movie["price"]),
    )


async def handle_found_movie(message: Message, movie: dict, user_id: int) -> None:
    if movie["price"] == 0 or await db.is_premium(user_id) or await db.has_purchased(user_id, movie["code"]):
        await deliver_movie(message, movie, user_id)
    else:
        await offer_purchase(message, movie)


async def send_not_found(message: Message) -> None:
    main_channels = await db.list_main_channels()
    await message.answer(
        "❌ Bunday kodli kino topilmadi.\nKodlarni ushbu kanallardan topishingiz mumkin:",
        reply_markup=kb.not_found_keyboard(main_channels),
    )


# =====================================================================
# GEMINI ORQALI CHEKNI TEKSHIRISH
# =====================================================================

async def verify_receipt_with_gemini(image_bytes: bytes, amount: int, card_number: str) -> bool:
    """
    Google Gemini API yordamida to'lov chekini (skrinshot) tahlil qiladi.
    Quyidagilarni tekshiradi:
      - rasm haqiqatan ham to'lov cheki/skrinshotimi
      - to'langan summa `amount` ga mos keladimi
      - o'tkazma vaqti hozirgi vaqtga yaqinmi (bir necha soat ichida)
      - qabul qiluvchi karta raqami oxirgi 4 raqami `card_number` ga mos keladimi
    Xatolik yuz bersa yoki ishonch past bo'lsa, xavfsizlik uchun False qaytaradi.
    """
    if not config.GEMINI_API_KEY or config.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        logger.warning("GEMINI_API_KEY sozlanmagan — chek avtomatik tekshirilmadi.")
        return False

    card_last4 = "".join(ch for ch in card_number if ch.isdigit())[-4:]
    now_txt = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = (
        "Sen to'lov chekini (bank ilovasi skrinshoti) tekshiruvchi yordamchisan. "
        f"Hozirgi sana va vaqt: {now_txt}. "
        f"Ushbu rasmda quyidagi shartlar bajarilganligini tekshir:\n"
        f"1) Bu haqiqatan ham muvaffaqiyatli to'lov/pul o'tkazma cheki.\n"
        f"2) To'langan summa {amount} so'm ga teng.\n"
        f"3) Qabul qiluvchi karta raqamining oxirgi 4 ta raqami '{card_last4}' ga mos keladi "
        "(agar rasmda karta raqami umuman ko'rinmasa, bu shartni e'tiborsiz qoldir).\n"
        "4) To'lov vaqti hozirgi vaqtdan bir necha soatdan ko'p oldin bo'lmasligi kerak.\n\n"
        'Faqat quyidagi formatda JSON qaytar, boshqa hech qanday matn yozma: '
        '{"valid": true} yoki {"valid": false}'
    )
    

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(image_bytes).decode("utf-8"),
                        }
                    },
                ]
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                config.GEMINI_API_URL,
                params={"key": config.GEMINI_API_KEY},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().strip("`").replace("json", "", 1).strip()
            import json as _json
            parsed = _json.loads(text)
            return bool(parsed.get("valid", False))
    except Exception as e:
        logger.error(f"Gemini tekshiruvida xatolik: {e}")
        return False


# =====================================================================
# /start VA OBUNA TEKSHIRUVI
# =====================================================================

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].removeprefix("ref_"))
            if ref_id != user_id:
                referred_by = ref_id
        except ValueError:
            pass

    is_new = await db.create_user_if_not_exists(
        user_id, message.from_user.full_name, message.from_user.username, referred_by
    )

    if is_new and referred_by:
        existing_referrer = await db.get_user(referred_by)
        if existing_referrer:
            await db.increment_friends(referred_by)
            bonus = int(await db.get_setting("referral_bonus") or "0")
            if bonus > 0:
                await db.add_balance(referred_by, bonus)
            try:
                await bot.send_message(referred_by, "🎉 Sizning taklifingiz orqali botga yangi do'st qo'shildi!")
            except Exception:
                pass

    missing = await check_user_subscription(user_id)
    if missing:
        await message.answer(
            "📢 Botdan foydalanish uchun homiylarimizga obuna bo'ling:",
            reply_markup=kb.subscription_keyboard(missing),
        )
        return

    await message.answer("👋 Xush kelibsiz! Botimiz ayrim nosozliklar tufayli kinolar joylanmagan iltimos sabr qiling.")
    await send_main_menu(message.chat.id, user_id)


@user_router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery) -> None:
    missing = await check_user_subscription(callback.from_user.id)
    if missing:
        await callback.answer("❗️ Siz hali barcha kanallarga obuna bo'lmadingiz.", show_alert=True)
        return
    await callback.message.delete()
    await callback.message.answer("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
    await send_main_menu(callback.message.chat.id, callback.from_user.id)
    await callback.answer()


@user_router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await send_main_menu(callback.message.chat.id, callback.from_user.id)
    await callback.answer()


# =====================================================================
# KINO QIDIRISH
# =====================================================================

@user_router.message(F.text == kb.BTN_SEARCH)
async def btn_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🔍 Kinolarni qidirishingiz mumkin:", reply_markup=kb.search_menu_keyboard()
    )


@user_router.callback_query(F.data == "all_movie_codes")
async def cb_all_codes(callback: CallbackQuery) -> None:
    main_channels = await db.list_main_channels()
    await callback.message.edit_text(
        "🗂 Barcha kinolar bizning ijtimoiy tarmoqlarimizda:",
        reply_markup=kb.main_channels_inline(main_channels),
    )
    await callback.answer()


@user_router.callback_query(F.data == "search_by_name")
async def cb_search_by_name(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserSearch.waiting_name)
    await callback.message.edit_text("✏️ Nomini kiriting:")
    await callback.answer()


@user_router.message(StateFilter(UserSearch.waiting_name))
async def process_search_by_name(message: Message, state: FSMContext) -> None:
    await state.clear()
    results = await db.search_movies_by_name(message.text.strip())
    if not results:
        await send_not_found(message)
        return
    for movie in results:
        await handle_found_movie(message, movie, message.from_user.id)


@user_router.callback_query(F.data == "search_by_code")
async def cb_search_by_code(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserSearch.waiting_code)
    await callback.message.edit_text("🔢 Kino kodini kiriting:")
    await callback.answer()


@user_router.message(StateFilter(UserSearch.waiting_code))
async def process_search_by_code(message: Message, state: FSMContext) -> None:
    await state.clear()
    movie = await db.get_movie(message.text.strip())
    if not movie:
        await send_not_found(message)
        return
    await handle_found_movie(message, movie, message.from_user.id)


# =====================================================================
# KINO SOTIB OLISH
# =====================================================================

@user_router.callback_query(F.data.startswith("buy_movie:"))
async def cb_buy_movie(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    movie = await db.get_movie(code)
    if not movie:
        await callback.answer("Kino topilmadi.", show_alert=True)
        return

    if await db.is_premium(user_id):
        await callback.message.answer("✅ Kino siz uchun ochiq!")
        await deliver_movie(callback.message, movie, user_id)
        await callback.answer()
        return

    card_number = await db.get_setting("card_number") or "—"
    card_owner = await db.get_setting("card_owner") or "—"
    user = await db.get_user(user_id)

    text = (
        f"🎬 Kino nomi: {movie['name']}\n"
        f"💰 Narxi: {movie['price']} so'm\n"
        f"💳 Karta: {card_number} ({card_owner})\n\n"
        f"To'lagandan keyin chekni (skrinshot) shu yerga yuboring, "
        f"biz uni {config.CHECK_WAIT_MINUTES} daqiqada tekshiramiz."
    )
    await callback.message.answer(
        text,
        reply_markup=kb.payment_method_keyboard(code, has_balance=bool(user and user["balance"] > 0)),
    )
    await state.set_state(BuyFlow.waiting_receipt)
    await state.update_data(kind="movie", code=code, amount=movie["price"])
    await callback.answer()


@user_router.callback_query(F.data.startswith("pay_with_balance:"))
async def cb_pay_with_balance(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    movie = await db.get_movie(code)
    if not movie:
        await callback.answer("Kino topilmadi.", show_alert=True)
        return
    ok = await db.deduct_balance(user_id, movie["price"])
    if not ok:
        await callback.answer("Hisobingizda yetarli mablag' yo'q.", show_alert=True)
        return
    await db.add_purchase(user_id, code)
    await state.clear()
    await callback.message.answer(
        f"✅ Siz kinoni sotib oldingiz. Kodi: <code>{code}</code>\n"
        "Siz kinoni «Sotib olingan kinolar» bo'limidan topishingiz mumkin."
    )
    await deliver_movie(callback.message, movie, user_id)
    await callback.answer()


@user_router.callback_query(F.data == "cancel_payment")
async def cb_cancel_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("❌ To'lov bekor qilindi.")
    await callback.answer()


@user_router.callback_query(F.data == "buy_premium")
async def cb_buy_premium(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if await db.is_premium(user_id):
        await callback.answer("Siz allaqachon Premium foydalanuvchisiz!", show_alert=True)
        return
    price = int(await db.get_setting("premium_price") or "0")
    card_number = await db.get_setting("card_number") or "—"
    card_owner = await db.get_setting("card_owner") or "—"
    text = (
        "💎 Sabab: Premium uchun to'lov\n"
        f"💰 Narxi: {price} so'm\n"
        f"💳 Karta: {card_number} ({card_owner})\n\n"
        f"To'lagandan keyin chekni shu yerga yuboring, biz uni {config.CHECK_WAIT_MINUTES} daqiqada tekshiramiz."
    )
    await callback.message.answer(text, reply_markup=kb.cancel_receipt_keyboard())
    await state.set_state(BuyFlow.waiting_receipt)
    await state.update_data(kind="premium", code=None, amount=price)
    await callback.answer()


@user_router.message(StateFilter(BuyFlow.waiting_receipt), F.photo)
async def process_receipt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("kind")
    code = data.get("code")
    amount = data.get("amount", 0)
    user_id = message.from_user.id

    file = await bot.get_file(message.photo[-1].file_id)
    file_bytes = (await bot.download_file(file.file_path)).read()

    card_number = await db.get_setting("card_number") or ""
    valid = await verify_receipt_with_gemini(file_bytes, amount, card_number)

    payment_id = await db.create_pending_payment(user_id, kind, code, amount)

    if valid:
        if kind == "movie" and code:
            await db.add_purchase(user_id, code)
            movie = await db.get_movie(code)
            await message.answer(
                f"✅ Siz kinoni sotib oldingiz. Kodi: <code>{code}</code>\n"
                "Siz kinoni «Sotib olingan kinolar» bo'limidan topishingiz mumkin."
            )
            if movie:
                await deliver_movie(message, movie, user_id)
        elif kind == "premium":
            await db.set_premium(user_id)
            until = (await db.get_user(user_id))["premium_until"]
            await message.answer(
                f"✅ Siz {datetime.now().strftime('%Y-%m-%d')} sanada Hashtag kino botdan "
                f"premium sotib oldingiz. Bu paket {until} sanagacha amal qiladi."
            )
        await db.update_payment_status(payment_id, "approved")
    else:
        await message.answer(
            "⚠️ Chekni avtomatik tasdiqlab bo'lmadi. Iltimos to'g'ri va aniq skrinshot yuboring "
            "yoki admin tekshirib chiqquncha kuting."
        )

    # Har qanday holatda chek adminlarga yuboriladi (soxta bo'lsa bekor qilish imkoniyati bilan)
    caption = (
        f"🧾 Yangi chek\n"
        f"👤 User ID: <code>{user_id}</code>\n"
        f"🗂 Turi: {'Kino' if kind == 'movie' else 'Premium'}"
        + (f" ({code})" if code else "")
        + f"\n💰 Summa: {amount} so'm\n"
        f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"🤖 AI natijasi: {'✅ Tasdiqlandi' if valid else '❌ Tasdiqlanmadi'}"
    )
    admin_ids = {config.SUPER_ADMIN_ID}
    db_admin_ids = []
    try:
        import aiosqlite
        async with aiosqlite.connect(config.DB_PATH) as conn:
            cur = await conn.execute("SELECT user_id FROM admins")
            db_admin_ids = [r[0] for r in await cur.fetchall()]
    except Exception:
        pass
    admin_ids.update(db_admin_ids)

    for admin_id in admin_ids:
        try:
            sent = await bot.send_photo(
                admin_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=kb.admin_receipt_keyboard(payment_id),
            )
            await db.set_pending_admin_message(payment_id, sent.message_id)
        except Exception:
            continue

    await state.clear()


@admin_router.callback_query(F.data.startswith("reject_payment:"))
async def cb_reject_payment(callback: CallbackQuery) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return
    payment_id = int(callback.data.split(":", 1)[1])
    payment = await db.get_pending_payment(payment_id)
    if not payment:
        await callback.answer("Topilmadi.", show_alert=True)
        return

    await db.update_payment_status(payment_id, "rejected")

    if payment["kind"] == "premium":
        await db.set_freemium(payment["user_id"])
    # kino uchun purchase yozuvini ham bekor qilamiz
    if payment["kind"] == "movie" and payment["movie_code"]:
        import aiosqlite
        async with aiosqlite.connect(config.DB_PATH) as conn:
            await conn.execute(
                "DELETE FROM purchases WHERE user_id=? AND movie_code=?",
                (payment["user_id"], payment["movie_code"]),
            )
            await conn.commit()

    try:
        await bot.send_message(payment["user_id"], "❌ Sizning chekingiz soxta deb topildi va to'lovingiz bekor qilindi.")
    except Exception:
        pass

    await callback.message.edit_caption(caption=(callback.message.caption or "") + "\n\n❌ ADMIN TOMONIDAN BEKOR QILINDI")
    await callback.answer("Bekor qilindi.")


# =====================================================================
# SOTIB OLINGAN KINOLAR
# =====================================================================

@user_router.message(F.text == kb.BTN_MY_MOVIES)
async def btn_my_movies(message: Message) -> None:
    user_id = message.from_user.id
    if await db.is_premium(user_id):
        await message.answer("💎 Sizga hamma kinolar tekin!")
        return
    purchases = await db.get_user_purchases(user_id)
    if not purchases:
        await message.answer("Siz hali birorta kino sotib olmagansiz.")
        return
    lines = [f"{m['name']} — <code>{m['code']}</code>" for m in purchases]
    await message.answer("🎬 Sotib olingan kinolaringiz:\n\n" + "\n".join(lines))


# =====================================================================
# PROFILE
# =====================================================================

@user_router.message(F.text == kb.BTN_PROFILE)
async def btn_profile(message: Message) -> None:
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if not user:
        await message.answer("Xatolik: foydalanuvchi topilmadi. /start bosing.")
        return
    purchases = await db.get_user_purchases(user_id)
    movies_txt = ", ".join(f"{i+1}.{m['name']}" for i, m in enumerate(purchases)) or "—"
    status = "Premium 💎" if await db.is_premium(user_id) else "Freemium"
    username = f"@{user['username']}" if user["username"] else "unknown"

    text = (
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Ism: {user['full_name']}\n"
        f"🔗 Username: {username}\n"
        f"⭐️ Status: {status}\n"
        f"📅 Botga qo'shilgan: {user['joined_at']}\n"
        f"🎬 Ko'rgan kinolari: {movies_txt}\n"
        f"👥 Do'stlar soni: {user['friends_count']}\n"
        f"👛 Hamyon: {user['balance']} so'm"
    )
    await message.answer(text, reply_markup=kb.profile_keyboard())


# =====================================================================
# DO'STLARIM
# =====================================================================

@user_router.message(F.text == kb.BTN_FRIENDS)
async def btn_friends(message: Message) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"👥 Do'stlaringizni taklif qiling va bonus mablag' oling!\n\n"
        f"🔗 Sizning referral havolangiz:\n{link}"
    )


# =====================================================================
# ADMIN PANEL — KIRISH / CHIQISH
# =====================================================================

@admin_router.message(F.text == kb.BTN_ADMIN_PANEL)
async def btn_admin_panel(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🔧 Admin panel:", reply_markup=kb.admin_menu())


@admin_router.message(F.text == kb.BTN_BACK, StateFilter(None))
async def btn_back_to_user_menu(message: Message) -> None:
    await send_main_menu(message.chat.id, message.from_user.id)


@user_router.message(F.text == kb.BTN_CANCEL)
async def btn_cancel_any(message: Message, state: FSMContext) -> None:
    await state.clear()
    is_adm = await db.is_admin(message.from_user.id)
    if is_adm:
        await message.answer("Bekor qilindi.", reply_markup=kb.admin_menu())
    else:
        await message.answer("Bekor qilindi.", reply_markup=kb.main_menu(is_admin=False))


# =====================================================================
# ADMIN: KINO QO'SHISH
# =====================================================================

@admin_router.message(F.text == kb.BTN_ADD_MOVIE)
async def btn_add_movie(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminAddMovie.waiting_video)
    await message.answer("🎞 Kino videosini yuboring:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminAddMovie.waiting_video), F.video)
async def add_movie_video(message: Message, state: FSMContext) -> None:
    await state.update_data(file_id=message.video.file_id)
    await state.set_state(AdminAddMovie.waiting_code)
    await message.answer("🔢 Kino uchun kod yuboring:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_code))
async def add_movie_code(message: Message, state: FSMContext) -> None:
    code = message.text.strip()
    if await db.get_movie(code):
        await message.answer("⚠️ Bu kod band. Boshqa kod kiriting:")
        return
    await state.update_data(code=code)
    await state.set_state(AdminAddMovie.waiting_name)
    await message.answer("📝 Kino nomini kiriting:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_name))
async def add_movie_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminAddMovie.waiting_description)
    await message.answer("📄 Tavsif kiriting:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_description))
async def add_movie_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip())
    await state.set_state(AdminAddMovie.waiting_genre)
    await message.answer("🎭 Janrini kiriting:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_genre))
async def add_movie_genre(message: Message, state: FSMContext) -> None:
    await state.update_data(genre=message.text.strip())
    await state.set_state(AdminAddMovie.waiting_language)
    await message.answer("🌐 Tilini kiriting:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_language))
async def add_movie_language(message: Message, state: FSMContext) -> None:
    await state.update_data(language=message.text.strip())
    await state.set_state(AdminAddMovie.waiting_year)
    await message.answer("📅 Kino yilini kiriting:")


@admin_router.message(StateFilter(AdminAddMovie.waiting_year))
async def add_movie_year(message: Message, state: FSMContext) -> None:
    await state.update_data(year=message.text.strip())
    await state.set_state(AdminAddMovie.waiting_price)
    await message.answer("💰 Narxini kiriting (bepul bo'lsa 0):")


@admin_router.message(StateFilter(AdminAddMovie.waiting_price))
async def add_movie_price(message: Message, state: FSMContext) -> None:
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Faqat raqam kiriting (masalan 0 yoki 15000):")
        return
    data = await state.get_data()
    await db.add_movie(
        code=data["code"],
        file_id=data["file_id"],
        name=data["name"],
        description=data["description"],
        genre=data["genre"],
        language=data["language"],
        year=data["year"],
        price=price,
    )
    await state.clear()
    await message.answer(f"✅ Kino botga qo'shildi! Kodi: <code>{data['code']}</code>", reply_markup=kb.admin_menu())


# =====================================================================
# ADMIN: KINONI TAHRIRLASH
# =====================================================================

@admin_router.message(F.text == kb.BTN_EDIT_MOVIE)
async def btn_edit_movie(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminEditMovie.waiting_code)
    await message.answer("🔢 Tahrirlamoqchi bo'lgan kino kodini yuboring:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminEditMovie.waiting_code))
async def edit_movie_code(message: Message, state: FSMContext) -> None:
    code = message.text.strip()
    movie = await db.get_movie(code)
    if not movie:
        await message.answer("❌ Bunday kodli kino topilmadi. Qaytadan kiriting:")
        return
    await state.update_data(code=code)
    await message.answer("Qaysi bo'limni tahrirlaysiz?", reply_markup=kb.edit_movie_fields_keyboard(code))


@admin_router.callback_query(F.data.startswith("edit_field:"))
async def cb_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    _, code, field = callback.data.split(":", 2)
    await state.update_data(code=code, field=field)
    await state.set_state(AdminEditMovie.waiting_new_value)
    prompts = {
        "file_id": "🎞 Yangi kino videosini yuboring:",
        "code": "🔢 Yangi kodni kiriting:",
        "name": "📝 Yangi nomni kiriting:",
        "description": "📄 Yangi tavsifni kiriting:",
        "genre": "🎭 Yangi janrni kiriting:",
        "language": "🌐 Yangi tilni kiriting:",
        "price": "💰 Yangi narxni kiriting:",
    }
    await callback.message.answer(prompts.get(field, "Yangi qiymatni kiriting:"))
    await callback.answer()


@admin_router.message(StateFilter(AdminEditMovie.waiting_new_value))
async def edit_movie_new_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code, field = data["code"], data["field"]

    if field == "file_id":
        if not message.video:
            await message.answer("Iltimos video yuboring.")
            return
        value = message.video.file_id
    elif field == "price":
        try:
            value = int(message.text.strip())
        except ValueError:
            await message.answer("Faqat raqam kiriting:")
            return
    else:
        value = message.text.strip()

    await db.update_movie_field(code, field, value)
    await state.clear()
    await message.answer("✅ Yangilandi!", reply_markup=kb.admin_menu())


# =====================================================================
# ADMIN: KINONI O'CHIRISH
# =====================================================================

@admin_router.message(F.text == kb.BTN_DELETE_MOVIE)
async def btn_delete_movie(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminDeleteMovie.waiting_code)
    await message.answer("🔢 O'chirmoqchi bo'lgan kino kodini yuboring:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminDeleteMovie.waiting_code))
async def delete_movie_code(message: Message, state: FSMContext) -> None:
    code = message.text.strip()
    ok = await db.delete_movie(code)
    await state.clear()
    if ok:
        await message.answer(f"✅ <code>{code}</code> kodli kino o'chirildi.", reply_markup=kb.admin_menu())
    else:
        await message.answer("❌ Bunday kodli kino topilmadi.", reply_markup=kb.admin_menu())


# =====================================================================
# ADMIN: KINOLAR RO'YXATI
# =====================================================================

PAGE_SIZE = 20


async def build_movie_list_text(page: int) -> tuple[str, int]:
    total = await db.count_movies()
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    movies = await db.list_movies(offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    if not movies:
        return "Hozircha kinolar yo'q.", total_pages
    lines = [f"{i+1+page*PAGE_SIZE}. {m['name']} — <code>{m['code']}</code>" for i, m in enumerate(movies)]
    return "📋 Kinolar ro'yxati:\n\n" + "\n".join(lines), total_pages


@admin_router.message(F.text == kb.BTN_MOVIE_LIST)
async def btn_movie_list(message: Message) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    text, total_pages = await build_movie_list_text(0)
    await message.answer(text, reply_markup=kb.movie_list_pagination(0, total_pages))


@admin_router.callback_query(F.data.startswith("movie_list_page:"))
async def cb_movie_list_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":", 1)[1])
    text, total_pages = await build_movie_list_text(page)
    await callback.message.edit_text(text, reply_markup=kb.movie_list_pagination(page, total_pages))
    await callback.answer()


@admin_router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# =====================================================================
# ADMIN: ADMIN TAYINLASH
# =====================================================================

@admin_router.message(F.text == kb.BTN_ASSIGN_ADMIN)
async def btn_assign_admin(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminAssignAdmin.waiting_id)
    await message.answer("🆔 Admin ID sini yuboring:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminAssignAdmin.waiting_id))
async def process_assign_admin(message: Message, state: FSMContext) -> None:
    try:
        new_admin_id = int(message.text.strip())
    except ValueError:
        await message.answer("Faqat raqamli ID kiriting:")
        return
    await db.add_admin(new_admin_id)
    await state.clear()
    await message.answer("✅ Yangi admin tayinlandi.", reply_markup=kb.admin_menu())
    try:
        await bot.send_message(new_admin_id, "🎉 Siz Hashtag Cinema Bot administratori etib tayinlandingiz!")
    except Exception:
        pass


# =====================================================================
# ADMIN: ASOSIY KANALLAR
# =====================================================================

@admin_router.message(F.text == kb.BTN_MAIN_CHANNELS)
async def btn_main_channels(message: Message) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    channels = await db.list_main_channels()
    await message.answer(
        "📢 Kanallar ro'yxati:",
        reply_markup=kb.channels_list_keyboard(channels, "main_ch", "add_main_channel"),
    )


@admin_router.callback_query(F.data == "add_main_channel")
async def cb_add_main_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminMainChannel.waiting_link)
    await callback.message.answer("🔗 Kanal linkini yuboring:")
    await callback.answer()


@admin_router.message(StateFilter(AdminMainChannel.waiting_link))
async def process_add_main_channel(message: Message, state: FSMContext) -> None:
    link = message.text.strip()
    platform, url, _ = normalize_channel_link(link)
    await db.add_main_channel(url, platform)
    await state.clear()
    await message.answer("✅ Kanal asosiy kanallar ro'yxatiga qo'shildi.", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data.startswith("main_ch_del:"))
async def cb_del_main_channel(callback: CallbackQuery) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    channel_id = int(callback.data.split(":", 1)[1])
    await db.remove_main_channel(channel_id)
    channels = await db.list_main_channels()
    await callback.message.edit_text(
        "📢 Kanallar ro'yxati:",
        reply_markup=kb.channels_list_keyboard(channels, "main_ch", "add_main_channel"),
    )
    await callback.answer("O'chirildi.")


# =====================================================================
# ADMIN: MAJBURIY KANALLAR
# =====================================================================

@admin_router.message(F.text == kb.BTN_MANDATORY_CHANNELS)
async def btn_mandatory_channels(message: Message) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    channels = await db.list_mandatory_channels()
    await message.answer(
        "🔒 Kanallar ro'yxati:",
        reply_markup=kb.channels_list_keyboard(channels, "mand_ch", "add_mandatory_channel"),
    )


@admin_router.callback_query(F.data == "add_mandatory_channel")
async def cb_add_mandatory_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminMandatoryChannel.waiting_link)
    await callback.message.answer(
        "🔗 Kanal linkini yuboring.\n"
        "⚠️ Eslatma: Botni o'sha kanalga admin qilib qo'ying (Instagram uchun shart emas)."
    )
    await callback.answer()


@admin_router.message(StateFilter(AdminMandatoryChannel.waiting_link))
async def process_add_mandatory_channel(message: Message, state: FSMContext) -> None:
    link = message.text.strip()
    platform, url, api_username = normalize_channel_link(link)
    chat_id = None
    if platform == "telegram":
        try:
            chat = await bot.get_chat(api_username)
            chat_id = str(chat.id)
        except Exception:
            await message.answer(
                "⚠️ Botni ushbu kanalga admin qilib qo'ying va linkni qaytadan yuboring."
            )
            return
    await db.add_mandatory_channel(url, chat_id, platform)
    await state.clear()
    await message.answer("✅ Kanal majburiy kanallar ro'yxatiga qo'shildi.", reply_markup=kb.admin_menu())


@admin_router.callback_query(F.data.startswith("mand_ch_del:"))
async def cb_del_mandatory_channel(callback: CallbackQuery) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    channel_id = int(callback.data.split(":", 1)[1])
    await db.remove_mandatory_channel(channel_id)
    channels = await db.list_mandatory_channels()
    await callback.message.edit_text(
        "🔒 Kanallar ro'yxati:",
        reply_markup=kb.channels_list_keyboard(channels, "mand_ch", "add_mandatory_channel"),
    )
    await callback.answer("O'chirildi.")


# =====================================================================
# ADMIN: KARTA MA'LUMOTLARI
# =====================================================================

@admin_router.message(F.text == kb.BTN_CARD_INFO)
async def btn_card_info(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminCardInfo.waiting_number)
    await message.answer("💳 Karta raqamini kiriting:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminCardInfo.waiting_number))
async def process_card_number(message: Message, state: FSMContext) -> None:
    await state.update_data(card_number=message.text.strip())
    await state.set_state(AdminCardInfo.waiting_owner)
    await message.answer("👤 Karta egasi ismini kiriting:")


@admin_router.message(StateFilter(AdminCardInfo.waiting_owner))
async def process_card_owner(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await db.set_setting("card_number", data["card_number"])
    await db.set_setting("card_owner", message.text.strip())
    await state.clear()
    await message.answer("✅ Karta ma'lumotlari saqlandi.", reply_markup=kb.admin_menu())


# =====================================================================
# ADMIN: PREMIUM NARXI
# =====================================================================

@admin_router.message(F.text == kb.BTN_PREMIUM_PRICE)
async def btn_premium_price(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminPremiumPrice.waiting_price)
    await message.answer("💎 Narxni (10000) shu formatda yuboring:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminPremiumPrice.waiting_price))
async def process_premium_price(message: Message, state: FSMContext) -> None:
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Faqat raqam kiriting:")
        return
    await db.set_setting("premium_price", str(price))
    await state.clear()
    await message.answer("✅ Premium narxi yangilandi.", reply_markup=kb.admin_menu())


# =====================================================================
# ADMIN: STATISTIKA
# =====================================================================

@admin_router.message(F.text == kb.BTN_STATISTICS)
async def btn_statistics(message: Message) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    total_users = await db.count_users()
    monthly_users = await db.count_users(30)
    total_movies = await db.count_movies()
    total_messages = await db.get_total_messages()
    total_admins = await db.count_admins()
    mandatory_channels = len(await db.list_mandatory_channels())
    monthly_revenue = await db.get_monthly_revenue()
    avg_response = (
        sum(RESPONSE_TIMES_MS) / len(RESPONSE_TIMES_MS) if RESPONSE_TIMES_MS else 0
    )

    text = (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Bir oylik foydalanuvchilar: {monthly_users}\n"
        f"👥 Jami foydalanuvchilar: {total_users}\n"
        f"🎬 Kinolar soni: {total_movies}\n"
        f"✉️ Jami xabarlar soni: {total_messages}\n"
        f"🔧 Jami adminlar soni: {total_admins}\n"
        f"🔒 Majburiy kanallar soni: {mandatory_channels}\n"
        f"💰 Oxirgi bir oydagi jami tushum: {monthly_revenue} so'm\n"
        f"⚡️ Botning o'rtacha javob berish vaqti: {avg_response:.1f} ms"
    )
    await message.answer(text)


# =====================================================================
# ADMIN: HABAR YUBORISH (BROADCAST)
# =====================================================================

@admin_router.message(F.text == kb.BTN_BROADCAST)
async def btn_broadcast(message: Message, state: FSMContext) -> None:
    if not await db.is_admin(message.from_user.id):
        return
    await state.set_state(AdminBroadcast.waiting_message)
    await message.answer("✉️ Yubormoqchi bo'lgan xabaringizni kiriting:", reply_markup=kb.cancel_only_menu())


@admin_router.message(StateFilter(AdminBroadcast.waiting_message))
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id)
    await message.answer("Shu habar yuborilsinmi?", reply_markup=kb.broadcast_confirm_keyboard())


@admin_router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await db.is_admin(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    user_ids = await db.get_all_user_ids()
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=data["from_chat_id"], message_id=data["message_id"])
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # flood limitidan saqlanish
    await callback.message.edit_text(f"✅ Yuborildi: {sent} ta\n❌ Yetib bormadi: {failed} ta")
    await callback.answer()


@admin_router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# =====================================================================
# STATISTIKA UCHUN MIDDLEWARE (xabarlar sonini va javob vaqtini hisoblash)
# =====================================================================

@dp.message.middleware()
async def stats_middleware(handler, event: Message, data: dict):
    start = time.monotonic()
    result = await handler(event, data)
    elapsed_ms = (time.monotonic() - start) * 1000
    RESPONSE_TIMES_MS.append(elapsed_ms)
    if len(RESPONSE_TIMES_MS) > 500:
        RESPONSE_TIMES_MS.pop(0)
    try:
        await db.increment_total_messages()
    except Exception:
        pass
    return result


# =====================================================================
# ISHGA TUSHIRISH
# =====================================================================

async def main() -> None:
    await db.init_db()
    dp.include_router(admin_router)
    dp.include_router(user_router)
    logger.info("Hashtag Cinema Bot ishga tushdi...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
