from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Contact, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from datetime import datetime

from config import ADMIN_IDS
from database import get_user, save_user, get_channels, add_channel, remove_channel, get_all_users, load_db, save_db
from keyboards import (
    lang_keyboard, subscribe_keyboard, phone_keyboard,
    main_menu_keyboard, settings_keyboard, settings_inline_keyboard,
    settings_info_text, admin_keyboard, back_keyboard, referral_inline_keyboard, partners_keyboard
)
from states import RegisterState, AdminState, SettingsState, ReferralState, PartnersState, SupportState
from texts import t, TEXTS
from exchange_config import CURRENCIES
from referral_service import (
    parse_referrer_from_start_text,
    apply_referred_by_for_new_user,
    ensure_user_referral_fields_by_id,
    get_referrals_count,
    format_money,
    create_withdraw_request,
    update_referral_card,
    approve_withdraw_request,
    reject_withdraw_request,
)

router = Router()
REFERRAL_CARD_BUTTONS = ["💳 Карта кушиш/Янгилаш", "💳 Добавить/обновить карту"]
REFERRAL_WITHDRAW_BUTTONS = ["💰 Бонусни ечиб олиш", "💰 Вывести бонус"]
REFERRAL_HOME_BUTTONS = ["🏠 Бош меню", "🏠 Главное меню"]
PARTNERS_ADD_BUTTONS = ["✏️ Кушиш / узгартириш", "✏️ Добавить / изменить"]
PARTNERS_DELETE_BUTTONS = ["❌ учириш", "❌ Удалить"]
SUPPORT_MENU_TEXTS = [
    "💱 Валюта айирбошлаш", "💱 Обмен валют",
    "📊 Курс", "📊 Курс",
    "👥 Хаменлар", "👥 Партнёры",
    "👥 Реферал", "👥 Реферал",
    "⚙️ Созламалар", "⚙️ Настройки",
    "📞 Кайта алока", "📞 Обратная связь",
    "🔄 Алмашувлар", "🔄 Переводы",
    "📖 Кулланма", "📖 Руководство",
    "🔙 Оркага", "🔙 Назад",
]


def referral_withdraw_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тасдиклаш", callback_data=f"RWD_OK_{req_id}")],
        [InlineKeyboardButton(text="❌ Бекор килиш", callback_data=f"RWD_NO_{req_id}")],
    ])


def support_admin_reply_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ жавоб езиш", callback_data=f"SUP_REPLY_{user_id}")]
    ])


def _support_header_text(message: Message) -> str:
    user_id = message.from_user.id
    user = get_user(user_id) or {}
    full_name = f"{user.get('name', '')} {user.get('surname', '')}".strip() or message.from_user.full_name
    username = f"@{user.get('username')}" if user.get("username") else "—"
    phone = user.get("phone", "—")
    created = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return (
        "📞 Кайта алока хабари\n\n"
        f"👤 {full_name} ({username})\n"
        f"🆔 {user_id}\n"
        f"📞 {phone}\n"
        f"🕐 {created}"
    )


async def _send_support_to_admins(message: Message, bot: Bot):
    header = _support_header_text(message)
    uid = message.from_user.id
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, header, reply_markup=support_admin_reply_kb(uid))
            await bot.copy_message(aid, message.chat.id, message.message_id)
        except Exception:
            pass


async def send_referral_panel(message: Message, bot: Bot):
    user_id = message.from_user.id
    lang = get_lang(user_id)
    user = ensure_user_referral_fields_by_id(user_id) or get_user(user_id) or {}
    me = await bot.get_me()
    username = me.username or "bot"
    link = f"https://t.me/{username}?start=ref_{user_id}"
    referrals = get_referrals_count(user_id)
    bonus = format_money(user.get("referral_bonus", 0.0))
    card = (user.get("referral_card") or "kiritilmagan") if lang == "uz" else (user.get("referral_card") or "не указана")

    if lang == "ru":
        text = (
            "👥 Ваш реферальный раздел\n\n"
            f"🔗 Ссылка: {link}\n\n"
            f"👤 Кол-во рефералов: {referrals}\n"
            f"💰 Бонусный баланс: {bonus} сум\n"
            f"💳 Карта: {card}"
        )
    else:
        text = (
            "👥 Сизнинг реферал хаволангиз\n\n"
            f"🔗 хавола: {link}\n\n"
            f"👤 Рефераллар сони: {referrals}\n"
            f"💰 Бонус баланси: {bonus} so'm\n"
            f"💳 Карта: {card}"
        )
    await message.answer(text, reply_markup=referral_inline_keyboard(lang))


def _currency_help_text() -> str:
    lines = [f"• {c['name']} ({c['id']})" for c in CURRENCIES]
    return "\n".join(lines)


def _resolve_currency(text: str | None) -> dict | None:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    compact = raw.replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
    for cur in CURRENCIES:
        if raw == cur["id"].lower():
            return cur
        if raw == cur["name"].lower():
            return cur
        cur_compact = cur["name"].lower().replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
        if compact == cur_compact:
            return cur
    return None


def _get_user_wallets(user_id: int) -> dict:
    user = get_user(user_id) or {}
    wallets = user.get("wallets", {})
    return wallets if isinstance(wallets, dict) else {}


def _save_user_wallet(user_id: int, cur_id: str, value: str) -> bool:
    db = load_db()
    users = db.get("users", {})
    user = users.get(str(user_id))
    if not user:
        return False
    wallets = user.get("wallets", {})
    if not isinstance(wallets, dict):
        wallets = {}
    wallets[cur_id] = value.strip()
    user["wallets"] = wallets
    save_db(db)
    return True


def _delete_user_wallet(user_id: int, cur_id: str) -> bool:
    db = load_db()
    users = db.get("users", {})
    user = users.get(str(user_id))
    if not user:
        return False
    wallets = user.get("wallets", {})
    if not isinstance(wallets, dict):
        wallets = {}
    existed = cur_id in wallets
    wallets.pop(cur_id, None)
    user["wallets"] = wallets
    save_db(db)
    return existed


def _partners_text(user_id: int, lang: str) -> str:
    wallets = _get_user_wallets(user_id)
    empty = "пусто" if lang == "ru" else "bo'sh"
    title = "📁 Список ваших кошельков:" if lang == "ru" else "📁 Сизнинг хаменларингиз:"
    lines = [title, ""]
    for cur in CURRENCIES:
        val = wallets.get(cur["id"], empty)
        lines.append(f"💸 {cur['name']}: {val}")
    return "\n".join(lines)


async def send_partners_panel(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(_partners_text(message.from_user.id, lang), reply_markup=partners_keyboard(lang))


def _mask_payment_value(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "—"
    digits_only = "".join(ch for ch in raw if ch.isdigit())
    if len(digits_only) >= 12:
        tail = digits_only[-4:]
        return f"**** **** **** {tail}"
    if len(raw) <= 8:
        return raw
    return f"{raw[:6]}...{raw[-4:]}"


def _normalize_created_at(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return "—"
    from datetime import datetime as _dt
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = _dt.strptime(v, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return v


def _order_status_label(status: str, lang: str) -> str:
    st = (status or "").strip()
    if st in ("pending_payment", "receipt_sent"):
        return "New"
    if st == "completed":
        return "Tasdiqlangan" if lang == "uz" else "Подтверждено"
    if st == "cancelled":
        return "Bekor qilingan" if lang == "uz" else "Отменено"
    return st or ("Noma'lum" if lang == "uz" else "Неизвестно")


def _get_user_orders(user_id: int) -> list[dict]:
    db = load_db()
    orders = list(db.get("orders", {}).values())
    result = []
    for o in orders:
        try:
            if int(o.get("user_id", 0)) == int(user_id):
                result.append(o)
        except Exception:
            continue
    result.sort(key=lambda x: int(x.get("order_id", 0)), reverse=True)
    return result


def _format_order_block(order: dict, lang: str) -> str:
    send_amount = order.get("send_amount", 0)
    recv_amount = order.get("recv_amount", order.get("receive_amount", 0))
    sender = _mask_payment_value(order.get("sender_card", ""))
    receiver = _mask_payment_value(order.get("receiver_card", ""))
    status = _order_status_label(order.get("status", ""), lang)
    created_at = _normalize_created_at(order.get("created_at", ""))
    return (
        f"🆔 ИД: {order.get('order_id', '—')}\n"
        f"🔁 {order.get('from_name', '—')} → {order.get('to_name', '—')}\n"
        f"💰 {send_amount} → {recv_amount}\n"
        f"📤 Юборувчи: {sender}\n"
        f"📥 Кабул килувчи: {receiver}\n"
        f"📅 Яратилган: {created_at}\n"
        f"📌 {status}"
    )


def _transfers_inline_kb(lang: str) -> InlineKeyboardMarkup:
    text = "📣 Барча алмашувларни кориш" if lang == "uz" else "📣 Показать все обмены"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="TR_ALL")]
    ])


def _paginate_order_blocks(blocks: list[str], lang: str, first_title: str) -> list[str]:
    if not blocks:
        return [first_title]
    sep = "\n\n——————————\n\n"
    pages: list[str] = []
    current_blocks: list[str] = []
    current_len = 0
    limit = 3800
    for block in blocks:
        add = len(block) + (len(sep) if current_blocks else 0)
        if current_blocks and (current_len + add) > limit:
            prefix = first_title if not pages else ("🔄 Давоми:" if lang == "uz" else "🔄 Продолжение:")
            pages.append(prefix + "\n\n" + sep.join(current_blocks))
            current_blocks = [block]
            current_len = len(block)
        else:
            current_blocks.append(block)
            current_len += add
    if current_blocks:
        prefix = first_title if not pages else ("🔄 Давоми:" if lang == "uz" else "🔄 Продолжение:")
        pages.append(prefix + "\n\n" + sep.join(current_blocks))
    return pages



async def check_subscriptions(bot: Bot, user_id: int) -> bool:
    """Check if user is subscribed to all required channels"""
    channels = get_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch["channel_id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True


def get_lang(user_id: int) -> str:
    user = get_user(user_id)
    if user and "lang" in user:
        return user["lang"]
    return "uz"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    referred_by = parse_referrer_from_start_text(message.text or "", user_id)
    if referred_by:
        await state.update_data(referred_by=referred_by)

    # Admin check
    if user_id in ADMIN_IDS:
        user = get_user(user_id)
        if user and user.get("registered"):
            lang = user.get("lang", "uz")
            await message.answer("👨‍💼 Хуш келибсиз, Админ!", reply_markup=main_menu_keyboard(lang))
            return

    user = get_user(user_id)

    if user and user.get("registered"):
        lang = user.get("lang", "uz")
        await message.answer(t(lang, "main_menu"), reply_markup=main_menu_keyboard(lang))
        return

    channels = get_channels()
    if channels:
        subscribed = await check_subscriptions(bot, user_id)
        if not subscribed:
            await message.answer(
                t("uz", "subscribe_required"),
                reply_markup=subscribe_keyboard(channels)
            )
            return

    # Ask language
    await state.set_state(RegisterState.choosing_lang)
    await message.answer(t("uz", "choose_lang"), reply_markup=lang_keyboard())



@router.callback_query(F.data == "check_subscribe")
async def check_subscribe_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    subscribed = await check_subscriptions(bot, user_id)

    if not subscribed:
        channels = get_channels()
        await callback.answer(t("uz", "not_subscribed"), show_alert=True)
        return

    await callback.message.delete()

    user = get_user(user_id)
    if user and user.get("registered"):
        lang = user.get("lang", "uz")
        await callback.message.answer(t(lang, "main_menu"), reply_markup=main_menu_keyboard(lang))
        return

    await state.set_state(RegisterState.choosing_lang)
    await callback.message.answer(t("uz", "choose_lang"), reply_markup=lang_keyboard())

@router.callback_query(RegisterState.choosing_lang, F.data.startswith("lang_"))
async def choose_language(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]  # "uz" or "ru"

    await state.update_data(lang=lang)
    await callback.message.delete()
    await callback.answer(t(lang, "lang_selected"))

    await state.set_state(RegisterState.entering_name)
    await callback.message.answer(t(lang, "enter_name"))


# =================== REGISTRATION ===================

@router.message(RegisterState.entering_name)
async def enter_name(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    name = message.text.strip()
    if not name or len(name) < 2:
        await message.answer("❌ Илтимос, тогри исм киритинг (камида 2 та харф):")
        return

    await state.update_data(name=name)
    await state.set_state(RegisterState.entering_surname)
    await message.answer(t(lang, "enter_surname"))


@router.message(RegisterState.entering_surname)
async def enter_surname(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    surname = message.text.strip()
    if not surname or len(surname) < 2:
        await message.answer("❌ Илтимос, Тугри фамилия киритинг (kamida 2 ta harf):")
        return

    await state.update_data(surname=surname)
    await state.set_state(RegisterState.entering_phone)
    await message.answer(t(lang, "enter_phone"), reply_markup=phone_keyboard(lang))


@router.message(RegisterState.entering_phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    contact: Contact = message.contact
    phone = contact.phone_number

    await finish_registration(message, state, data, phone, lang)


@router.message(RegisterState.entering_phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    phone = message.text.strip()

    # Basic phone validation
    cleaned = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not cleaned.isdigit() or len(cleaned) < 9:
        await message.answer("❌ Iltimos, to'g'ri telefon raqam kiriting:")
        return

    await finish_registration(message, state, data, phone, lang)


async def finish_registration(message: Message, state: FSMContext, data: dict, phone: str, lang: str):
    user_id = message.from_user.id
    name = data.get("name")
    surname = data.get("surname")
    referred_by = data.get("referred_by")

    user_data = {
        "user_id": user_id,
        "username": message.from_user.username,
        "lang": lang,
        "name": name,
        "surname": surname,
        "phone": phone,
        "registered": True
    }
    apply_referred_by_for_new_user(user_data, referred_by)
    save_user(user_id, user_data)
    ensure_user_referral_fields_by_id(user_id)

    await state.clear()
    await message.answer(
        t(lang, "registration_done", name=name, surname=surname, phone=phone),
        reply_markup=main_menu_keyboard(lang)
    )

@router.message(F.text.in_(["💱 Valyuta ayirboshlash", "💱 Обмен валют"]))
async def menu_exchange(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t(lang, "exchange_menu"))


@router.message(F.text.in_(["📊 Kurs", "📊 Курс"]))
async def menu_rates(message: Message, bot: Bot):
    lang = get_lang(message.from_user.id)
    from database import load_db
    from exchange_config import CURRENCIES
    db = load_db()
    rates = db.get("crypto_rates", {})
    if not rates:
        await message.answer("⏳ Курслар ҳали киритилмаган." if lang == "uz" else "⏳ Курсы ещё не введены.")
        return
    sell_lines = []
    buy_lines = []
    for cur in CURRENCIES:
        if cur["type"] != "crypto":
            continue
        r = rates.get(cur["id"])
        if not r:
            continue
        if r.get("sell_rate"):
            sell_lines.append(f"1 {cur['name']} = {int(r['sell_rate'])} СЎМ")
        if r.get("buy_rate"):
            buy_lines.append(f"1 {cur['name']} = {int(r['buy_rate'])} СЎМ")
    text = ""
    if sell_lines:
        text += "📉 Сотиш курси\n" + "\n".join(sell_lines) + "\n\n"
    if buy_lines:
        text += "📈 Сотиб олиш курси\n" + "\n".join(buy_lines)
    if not text:
        text = "⏳ Курслар ҳали киритилмаган."
    await message.answer(text)


@router.message(F.text.in_(["👥 Hamënlar", "👥 Партнёры"]))
async def menu_partners(message: Message):
    await send_partners_panel(message)


@router.message(F.text.in_(PARTNERS_ADD_BUTTONS))
async def partners_add_start(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.set_state(PartnersState.waiting_currency_add)
    if lang == "ru":
        await message.answer("✏️ Какую валюту хотите добавить/изменить?\n\n" + _currency_help_text())
    else:
        await message.answer("✏️ Qaysi valyuta hamyonini qo'shmoqchi/o'zgartirmoqchisiz?\n\n" + _currency_help_text())


@router.message(PartnersState.waiting_currency_add)
async def partners_add_currency(message: Message, state: FSMContext):
    cur = _resolve_currency(message.text)
    lang = get_lang(message.from_user.id)
    if not cur:
        if lang == "ru":
            await message.answer("❌ Валюта не найдена. Повторите:\n\n" + _currency_help_text())
        else:
            await message.answer("❌ Valyuta topilmadi. Qayta kiriting:\n\n" + _currency_help_text())
        return
    await state.update_data(partners_currency=cur["id"])
    await state.set_state(PartnersState.waiting_wallet_add)
    if lang == "ru":
        await message.answer(f"💳 {cur['name']} для вашего кошелька:\n\nВведите номер карты/адрес:")
    else:
        await message.answer(f"💳 {cur['name']} uchun hamyon manzilini kiriting:")


@router.message(PartnersState.waiting_wallet_add)
async def partners_add_wallet(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 4:
        await message.answer("❌ Qiymat juda qisqa. Qayta kiriting:")
        return
    data = await state.get_data()
    cur_id = data.get("partners_currency")
    if not cur_id:
        await state.clear()
        await message.answer("❌ Jarayon tugadi. Qaytadan urinib ko'ring.")
        return
    ok = _save_user_wallet(message.from_user.id, cur_id, value)
    await state.clear()
    if not ok:
        await message.answer("❌ Saqlashda xatolik bo'ldi.")
        return
    await message.answer("✅ Hamyon saqlandi.")
    await send_partners_panel(message)


@router.message(F.text.in_(PARTNERS_DELETE_BUTTONS))
async def partners_delete_start(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.set_state(PartnersState.waiting_currency_delete)
    if lang == "ru":
        await message.answer("❌ Какую валюту удалить?\n\n" 
