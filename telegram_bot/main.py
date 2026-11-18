import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from database import (
    TZ,
    get_settings,
    format_price,
    get_user_profile,
    save_user_profile,
    is_user_banned,
    get_available_slots,
    book_slot_atomic,
    get_user_bookings,
    cancel_booking,
    get_due_reminders,
    mark_reminded,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
router = Router()


class BookingState(StatesGroup):
    choosing_service = State()
    choosing_barber = State()
    choosing_date = State()
    choosing_time = State()
    entering_phone = State()


def client_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💈 Navbatga yozilish")],
            [KeyboardButton(text="✂️ Barberlar"), KeyboardButton(text="💰 Xizmatlar va Narxlar")],
            [KeyboardButton(text="📋 Mening navbatlarim"), KeyboardButton(text="📍 Manzil va Aloqa")],
        ],
        resize_keyboard=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    cfg = get_settings()
    shop_name = cfg.get("shop_info", {}).get("name", "Barbershop")
    await message.answer(
        f"Assalomu alaykum, <b>{message.from_user.first_name}</b>! 👋\n\n"
        f"<b>{shop_name}</b> rasmiy mijozlar botiga xush kelibsiz.\n"
        "Uydan turib o'zingizga ma'qul usta va bo'sh vaqtni tanlab navbat olishingiz mumkin:",
        reply_markup=client_menu_kb(),
        parse_mode="HTML",
    )


# ==================== 1. NAVBATGA YOZILISH OQIMI ====================

@router.message(F.text == "💈 Navbatga yozilish")
async def start_booking(message: Message, state: FSMContext):
    if is_user_banned(message.from_user.id):
        await message.answer("🚫 Sizning hisobingiz cheklangan. Ma'muriyat bilan bog'laning.")
        return

    await state.clear()
    cfg = get_settings()
    services = cfg.get("services", {})
    if not services:
        await message.answer("Hozircha xizmatlar mavjud emas.")
        return

    kb = [
        [
            InlineKeyboardButton(
                text=f"{s['name']} — {format_price(s['price'])} ({s['duration']} daq)",
                callback_data=f"srv:{s_id}",
            )
        ]
        for s_id, s in services.items()
    ]
    await state.set_state(BookingState.choosing_service)
    await message.answer(
        "1️⃣ <b>Xizmat turini tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("srv:"))
async def select_service(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    srv_id = callback.data.split(":", 1)[1]
    cfg = get_settings()
    service = cfg.get("services", {}).get(srv_id)
    if not service:
        await callback.message.answer("❗️ Bu xizmat yangilangan. Qaytadan «💈 Navbatga yozilish» ni bosing.")
        return

    active_barbers = {
        b_id: b for b_id, b in cfg.get("barbers", {}).items() if b.get("active", True)
    }
    if not active_barbers:
        await callback.message.edit_text("😔 Hozircha faol barberlar mavjud emas.")
        return

    await state.update_data(service=service)
    kb = [
        [InlineKeyboardButton(text=f"✂️ {b['name']}", callback_data=f"brb:{b_id}")]
        for b_id, b in active_barbers.items()
    ]

    await state.set_state(BookingState.choosing_barber)
    await callback.message.edit_text(
        f"💈 Xizmat: <b>{service['name']}</b> ({format_price(service['price'])})\n\n"
        "2️⃣ <b>Barberni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("brb:"))
async def select_barber(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    brb_id = callback.data.split(":", 1)[1]
    cfg = get_settings()
    barber = cfg.get("barbers", {}).get(brb_id)
    data = await state.get_data()

    if not barber or not data.get("service"):
        await callback.message.answer("❗️ Sessiya eskirgan. Qaytadan «💈 Navbatga yozilish» ni bosing.")
        return

    await state.update_data(barber=barber)

    closed_days = cfg.get("work_hours", {}).get("closed_weekdays", []) or []
    now = datetime.now(TZ)
    weekdays = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    kb = []
    row = []
    for i in range(10):
        day = now + timedelta(days=i)
        if day.weekday() in closed_days:
            continue
        d_str = day.strftime("%Y-%m-%d")
        label = "Bugun" if i == 0 else ("Ertaga" if i == 1 else f"{day.strftime('%d.%m')} ({weekdays[day.weekday()]})")
        row.append(InlineKeyboardButton(text=f"📅 {label}", callback_data=f"dt:{d_str}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    await state.set_state(BookingState.choosing_date)
    await callback.message.edit_text(
        f"✂️ Barber: <b>{barber['name']}</b>\n"
        f"<i>{barber.get('specialty', '')}</i>\n\n"
        "3️⃣ <b>Kunningizni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dt:"))
async def select_date(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    service = data.get("service")
    barber = data.get("barber")

    if not service or not barber:
        await callback.answer("Sessiya eskirgan, qaytadan boshlang.", show_alert=True)
        return

    slots = get_available_slots(barber["id"], date_str, int(service["duration"]))
    if not slots:
        await callback.answer("😔 Bu kunda bo'sh vaqt qolmadi! Boshqa kunni tanlang.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(date=date_str)
    kb = []
    row = []
    for t in slots:
        row.append(InlineKeyboardButton(text=f"🟢 {t}", callback_data=f"tm:{t}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    await state.set_state(BookingState.choosing_time)
    await callback.message.edit_text(
        f"📅 Sana: <b>{date_str}</b> | ✂️ Barber: <b>{barber['name']}</b>\n"
        f"💈 Xizmat: <b>{service['name']}</b>\n\n"
        "4️⃣ <b>Quyidagi BO'SH vaqtlardan birini tanlang:</b>\n"
        "<i>(Band, tushlik va o'tib ketgan vaqtlar yashirilgan)</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("tm:"))
async def select_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if not data.get("service") or not data.get("barber") or not data.get("date"):
        await callback.message.answer("❗️ Sessiya eskirgan. Qaytadan «💈 Navbatga yozilish» ni bosing.")
        return

    await state.update_data(time=time_str)
    await state.set_state(BookingState.entering_phone)

    prof = get_user_profile(callback.from_user.id)
    saved_phone = prof.get("phone")

    buttons = [[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]]
    if saved_phone:
        buttons.insert(0, [KeyboardButton(text=f"📞 Saqlangan raqam: {saved_phone}")])
    buttons.append([KeyboardButton(text="❌ Bekor qilish")])

    phone_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)
    await callback.message.delete()
    await callback.message.answer(
        f"⏰ Tanlangan vaqt: <b>{time_str}</b>\n\n"
        "5️⃣ Navbatni tasdiqlash uchun telefon raqamingizni tasdiqlang yoki yuboring:",
        reply_markup=phone_kb,
        parse_mode="HTML",
    )


@router.message(BookingState.entering_phone)
async def confirm_booking(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Buyurtma bekor qilindi.", reply_markup=client_menu_kb())
        return

    if message.contact:
        phone = message.contact.phone_number
    elif message.text and message.text.startswith("📞 Saqlangan raqam:"):
        phone = message.text.replace("📞 Saqlangan raqam:", "").strip()
    else:
        phone = (message.text or "").strip()

    if len(phone) < 7:
        await message.answer("❗️ Iltimos, to'g'ri telefon raqam kiriting yoki pastdagi tugmani bosing.")
        return

    save_user_profile(
        user_id=message.from_user.id,
        full_name=message.from_user.full_name,
        username=message.from_user.username,
        phone=phone,
    )

    data = await state.get_data()
    service = data["service"]
    barber = data["barber"]
    date_str = data["date"]
    time_str = data["time"]

    success, result = book_slot_atomic(
        date_str=date_str,
        barber_id=barber["id"],
        barber_name=barber["name"],
        time_str=time_str,
        duration_minutes=int(service["duration"]),
        service_name=service["name"],
        service_price=int(service["price"]),
        user_id=message.from_user.id,
        user_name=message.from_user.full_name,
        phone=phone,
    )
    await state.clear()

    if not success:
        await message.answer(result, reply_markup=client_menu_kb())
        return

    booking_id = result
    await message.answer(
        "✅ <b>NAVBATINGIZ TASDIQLANDI!</b>\n\n"
        f"🎫 Chek raqami: <code>{booking_id}</code>\n"
        f"📅 Sana: <b>{date_str}</b>\n"
        f"⏰ Vaqt: <b>{time_str}</b>\n"
        f"✂️ Barber: <b>{barber['name']}</b>\n"
        f"💈 Xizmat: <b>{service['name']}</b> ({format_price(service['price'])})\n"
        f"📞 Telefon: <b>{phone}</b>\n\n"
        "<i>Navbatdan 1 soat oldin sizga eslatma yuboramiz!</i>",
        reply_markup=client_menu_kb(),
        parse_mode="HTML",
    )


# ==================== 2. MA'LUMOTLAR VA MIJOZ KABINETI ====================

@router.message(F.text == "✂️ Barberlar")
async def show_barbers_info(message: Message):
    cfg = get_settings()
    barbers = [b for b in cfg.get("barbers", {}).values() if b.get("active", True)]
    if not barbers:
        await message.answer("Hozircha barberlar haqida ma'lumot yo'q.")
        return

    lines = ["✂️ <b>BIZNING BARBERLAR JAMOASI:</b>\n"]
    for b in barbers:
        lines.append(
            f"👤 <b>{b['name']}</b>\n"
            f"   🏅 Tajriba: {b.get('experience', '—')}\n"
            f"   💈 Yo'nalish: {b.get('specialty', '—')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text == "💰 Xizmatlar va Narxlar")
async def show_services_info(message: Message):
    cfg = get_settings()
    services = cfg.get("services", {}).values()
    if not services:
        await message.answer("Hozircha xizmatlar ro'yxati bo'sh.")
        return

    lines = ["💈 <b>XIZMATLAR VA NARXLAR:</b>\n"]
    for s in services:
        lines.append(f"• <b>{s['name']}</b> — {format_price(s['price'])} <i>({s['duration']} daqiqa)</i>")
    lines.append("\n<i>Navbat olish uchun «💈 Navbatga yozilish» tugmasini bosing.</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text == "📋 Mening navbatlarim")
async def my_bookings(message: Message):
    bookings = get_user_bookings(message.from_user.id)
    active = [b for b in bookings if b.get("status") == "confirmed"]
    if not active:
        await message.answer("Sizda hozircha faol navbatlar yo'q.")
        return

    for b in active[:5]:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Navbatni bekor qilish",
                        callback_data=f"cancel:{b['date']}:{b['barber_id']}:{b['booking_id']}",
                    )
                ]
            ]
        )
        await message.answer(
            f"🎫 <b>{b['booking_id']}</b>\n"
            f"📅 {b['date']} | ⏰ <b>{b['time']}</b>\n"
            f"✂️ {b['barber_name']} — {b['service']} ({format_price(b['price'])})",
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("cancel:"))
async def handle_cancel(callback: CallbackQuery):
    _, date_str, barber_id, booking_id = callback.data.split(":")
    ok = cancel_booking(date_str, barber_id, booking_id, callback.from_user.id)
    if ok:
        await callback.message.edit_text(
            f"❌ <b>{booking_id}</b> raqamli navbat bekor qilindi va vaqt boshqalar uchun bo'shatildi.",
            parse_mode="HTML",
        )
    else:
        await callback.answer("Navbat allaqachon bekor qilingan.", show_alert=True)


@router.callback_query(F.data.startswith("confirm_visit:"))
async def handle_confirm_visit(callback: CallbackQuery):
    await callback.answer("Rahmat! Sizni kutamiz.")
    await callback.message.edit_text(
        callback.message.text + "\n\n✅ <b>Tashrif tasdiqlandi!</b>",
        parse_mode="HTML",
    )


@router.message(F.text == "📍 Manzil va Aloqa")
async def contact_info(message: Message):
    cfg = get_settings()
    info = cfg.get("shop_info", {})
    wh = cfg.get("work_hours", {})
    await message.answer(
        f"🏢 <b>{info.get('name', 'Barbershop')}</b>\n\n"
        f"📍 <b>Manzil:</b> {info.get('address', 'Toshkent sh.')}\n"
        f"🕘 <b>Ish vaqti:</b> {wh.get('start', '09:00')} — {wh.get('end', '21:00')}\n"
        f"📞 <b>Telefon:</b> {info.get('phone', '+998 90 123 45 67')}",
        parse_mode="HTML",
    )


# ==================== 3. AVTOMATIK ESLATMA (BACKGROUND TASK) ====================

async def reminder_worker(bot: Bot):
    while True:
        try:
            due_list = get_due_reminders()
            for item in due_list:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="✅ Ha, boraman", callback_data=f"confirm_visit:{item['booking_id']}"),
                            InlineKeyboardButton(
                                text="❌ Bekor qilish",
                                callback_data=f"cancel:{item['date']}:{item['barber_id']}:{item['booking_id']}",
                            ),
                        ]
                    ]
                )
                await bot.send_message(
                    chat_id=item["user_id"],
                    text=(
                        "⏰ <b>NAVBAT ESLATMASI!</b>\n\n"
                        f"Bugun soat <b>{item['time']}</b> da <b>{item['barber_name']}</b> qabuliga yozilgansiz.\n"
                        f"💈 Xizmat: <b>{item['service']}</b>\n\n"
                        "Belgilangan vaqtda kela olasizmi?"
                    ),
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                mark_reminded(item["date"], item["barber_id"], item["time"])
        except Exception as e:
            logging.error(f"Reminder worker error: {e}")
        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN topilmadi!")
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(reminder_worker(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
