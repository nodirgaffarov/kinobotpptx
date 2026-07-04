# keyboard.py
# Botdagi barcha reply va inline klaviaturalar shu yerda yig'ilgan.

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# =====================================================================
# MATNLAR (tugma nomlari) - bir joyda saqlansa handlerlarda solishtirish qulay
# =====================================================================

BTN_SEARCH = "🔍 Kino qidirish"
BTN_MY_MOVIES = "🎬 Sotib olingan kinolar"
BTN_PROFILE = "👤 Profile"
BTN_FRIENDS = "👥 Do'stlarim"
BTN_ADMIN_PANEL = "🔧 Admin panel"
BTN_BACK = "⬅️ Orqaga"
BTN_CANCEL = "❌ Bekor qilish"

# Admin reply tugmalari
BTN_ADD_MOVIE = "➕ Kino qo'shish"
BTN_EDIT_MOVIE = "✏️ Kinoni tahrirlash"
BTN_DELETE_MOVIE = "🗑 Kinoni o'chirish"
BTN_MOVIE_LIST = "📋 Kinolar ro'yxati"
BTN_ASSIGN_ADMIN = "➕👤 Admin tayinlash"
BTN_MAIN_CHANNELS = "📢 Asosiy kanallar"
BTN_MANDATORY_CHANNELS = "🔒 Majburiy kanallar"
BTN_CARD_INFO = "💳 Karta ma'lumotlari"
BTN_PREMIUM_PRICE = "💎 Premium narxi"
BTN_STATISTICS = "📊 Statistika"
BTN_BROADCAST = "📣 Habar yuborish"

# =====================================================================
# ASOSIY (user) REPLY MENYU
# =====================================================================

def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_SEARCH)
    kb.button(text=BTN_MY_MOVIES)
    kb.button(text=BTN_PROFILE)
    kb.button(text=BTN_FRIENDS)
    kb.adjust(2, 2)
    if is_admin:
        kb.row(KeyboardButton(text=BTN_ADMIN_PANEL))
    return kb.as_markup(resize_keyboard=True)


def back_only_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_BACK)
    return kb.as_markup(resize_keyboard=True)


def cancel_only_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_CANCEL)
    return kb.as_markup(resize_keyboard=True)


# =====================================================================
# ADMIN REPLY MENYU
# =====================================================================

def admin_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_ADD_MOVIE)
    kb.button(text=BTN_EDIT_MOVIE)
    kb.button(text=BTN_DELETE_MOVIE)
    kb.button(text=BTN_MOVIE_LIST)
    kb.button(text=BTN_ASSIGN_ADMIN)
    kb.button(text=BTN_MAIN_CHANNELS)
    kb.button(text=BTN_MANDATORY_CHANNELS)
    kb.button(text=BTN_CARD_INFO)
    kb.button(text=BTN_PREMIUM_PRICE)
    kb.button(text=BTN_STATISTICS)
    kb.button(text=BTN_BROADCAST)
    kb.adjust(2)
    kb.row(KeyboardButton(text=BTN_BACK))
    return kb.as_markup(resize_keyboard=True)


def safe_channel_url(ch: dict) -> str:
    """
    Bazada saqlangan link har doim ham to'g'ri https:// URL bo'lmasligi mumkin
    (masalan eski yozuvlar '@username' shaklida qolib ketgan bo'lishi mumkin).
    Bu funksiya har qanday holatda ham Telegram qabul qiladigan to'g'ri URL
    qaytarishini kafolatlaydi, aks holda inline tugma yuborishda xatolik chiqadi.
    """
    link = (ch.get("link") or "").strip()
    if link.lower().startswith("http://") or link.lower().startswith("https://"):
        return link
    if ch.get("platform") == "instagram":
        return "https://" + link.lstrip("/")
    # telegram: '@username', 'username', yoki 't.me/username' bo'lishi mumkin
    username = link
    for prefix in ("t.me/",):
        if username.lower().startswith(prefix):
            username = username[len(prefix):]
            break
    username = username.lstrip("@").strip("/")
    return f"https://t.me/{username}"


# =====================================================================
# MAJBURIY OBUNA TEKSHIRUV (inline)
# =====================================================================

def subscription_keyboard(missing_channels: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in missing_channels:
        title = "📢 Telegram kanal" if ch["platform"] == "telegram" else "📸 Instagram"
        kb.row(InlineKeyboardButton(text=title, url=safe_channel_url(ch)))
    kb.row(InlineKeyboardButton(text="✅ Tekshirdim", callback_data="check_subscription"))
    return kb.as_markup()


# =====================================================================
# KINO QIDIRISH
# =====================================================================

def search_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🗂 Barcha kinolar kodlari", callback_data="all_movie_codes"))
    kb.row(InlineKeyboardButton(text="🔤 Nomi orqali qidirish", callback_data="search_by_name"))
    kb.row(InlineKeyboardButton(text="🔢 Kodi orqali qidirish", callback_data="search_by_code"))
    return kb.as_markup()


def main_channels_inline(main_channels: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in main_channels:
        title = "📢 Telegram kanalimiz" if ch["platform"] == "telegram" else "📸 Instagram sahifamiz"
        kb.row(InlineKeyboardButton(text=title, url=safe_channel_url(ch)))
    return kb.as_markup()


def not_found_keyboard(main_channels: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ch in main_channels:
        title = "📢 Telegram kanalimiz" if ch["platform"] == "telegram" else "📸 Instagram sahifamiz"
        kb.row(InlineKeyboardButton(text=title, url=safe_channel_url(ch)))
    kb.row(InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="back_to_main"))
    return kb.as_markup()


def buy_movie_keyboard(code: str, price: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"💳 Kinoni sotib olish – {price} so'm", callback_data=f"buy_movie:{code}"))
    return kb.as_markup()


def payment_method_keyboard(code: str, has_balance: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_balance:
        kb.row(InlineKeyboardButton(
            text="👛 Do'stlar hisobidagi puldan yechish",
            callback_data=f"pay_with_balance:{code}",
        ))
    kb.row(InlineKeyboardButton(text=f"❌ {BTN_CANCEL}", callback_data="cancel_payment"))
    return kb.as_markup()


def cancel_receipt_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=BTN_CANCEL, callback_data="cancel_payment"))
    return kb.as_markup()


# =====================================================================
# ADMINGA CHEK KELGANDA
# =====================================================================

def admin_receipt_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish (soxta chek)", callback_data=f"reject_payment:{payment_id}"))
    return kb.as_markup()


# =====================================================================
# PROFILE
# =====================================================================

def profile_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 Premium olish", callback_data="buy_premium"))
    return kb.as_markup()


# =====================================================================
# ADMIN: KINO TAHRIRLASH BO'LIMLARI
# =====================================================================

def edit_movie_fields_keyboard(code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎞 Kino fayli", callback_data=f"edit_field:{code}:file_id"),
        InlineKeyboardButton(text="🔢 Kodi", callback_data=f"edit_field:{code}:code"),
    )
    kb.row(
        InlineKeyboardButton(text="📝 Nomi", callback_data=f"edit_field:{code}:name"),
        InlineKeyboardButton(text="📄 Tavsifi", callback_data=f"edit_field:{code}:description"),
    )
    kb.row(
        InlineKeyboardButton(text="🎭 Janri", callback_data=f"edit_field:{code}:genre"),
        InlineKeyboardButton(text="🌐 Tili", callback_data=f"edit_field:{code}:language"),
    )
    kb.row(InlineKeyboardButton(text="💰 Narxi", callback_data=f"edit_field:{code}:price"))
    return kb.as_markup()


# =====================================================================
# ADMIN: KANALLAR RO'YXATI
# =====================================================================

def channels_list_keyboard(channels: list[dict], prefix: str, add_callback: str) -> InlineKeyboardMarkup:
    """prefix: 'main_ch' yoki 'mand_ch' - o'chirish callbacklari uchun."""
    kb = InlineKeyboardBuilder()
    for ch in channels:
        label = f"{'📢' if ch['platform']=='telegram' else '📸'} {ch['link']}"
        kb.row(InlineKeyboardButton(text=label, url=safe_channel_url(ch)))
        kb.row(InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"{prefix}_del:{ch['id']}"))
    kb.row(InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data=add_callback))
    return kb.as_markup()


# =====================================================================
# ADMIN: KINOLAR RO'YXATI (paginatsiya)
# =====================================================================

def movie_list_pagination(page: int, total_pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️", callback_data=f"movie_list_page:{page-1}"))
    row.append(InlineKeyboardButton(text=f"{page+1}/{max(total_pages,1)}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="➡️", callback_data=f"movie_list_page:{page+1}"))
    kb.row(*row)
    return kb.as_markup()


# =====================================================================
# ADMIN: HABAR YUBORISH TASDIQLASH
# =====================================================================

def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="broadcast_confirm"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="broadcast_cancel"),
    )
    return kb.as_markup()


# =====================================================================
# UMUMIY: FAQAT "Orqaga" inline tugma
# =====================================================================

def back_inline(callback_data: str = "back_to_main") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=BTN_BACK, callback_data=callback_data))
    return kb.as_markup()