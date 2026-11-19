import os
import json
import time
from datetime import datetime, timedelta
import pytz
import firebase_admin
from firebase_admin import credentials, db
from dotenv import load_dotenv

load_dotenv()

TZ = pytz.timezone(os.getenv("TIMEZONE", "Asia/Tashkent"))
CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "serviceAccountKey.json")
DB_URL = os.getenv("FIREBASE_DB_URL")

possible_paths = [
    CRED_PATH,
    os.path.join("/etc/secrets", os.path.basename(CRED_PATH)),
]

cred_file = next((p for p in possible_paths if os.path.exists(p)), None)

if not firebase_admin._apps:
    if cred_file:
        cred = credentials.Certificate(cred_file)
    elif os.getenv("FIREBASE_CREDENTIALS_JSON"):
        cred = credentials.Certificate(json.loads(os.getenv("FIREBASE_CREDENTIALS_JSON")))
    else:
        raise FileNotFoundError(f"Firebase kalit fayli topilmadi: {CRED_PATH}")
    firebase_admin.initialize_app(cred, {"databaseURL": DB_URL})

DEFAULT_SETTINGS = {
    "shop_info": {
        "name": "Elite Barbershop",
        "address": "Toshkent sh., Amir Temur ko'chasi, 15-uy",
        "phone": "+998 90 123 45 67",
        "map_url": "https://maps.google.com"
    },
    "work_hours": {
        "start": "09:00",
        "end": "21:00",
        "lunch_start": "13:00",
        "lunch_end": "14:00",
        "slot_step_minutes": 30,
        "closed_weekdays": [6]  # 0=Du ... 6=Ya (Yakshanba dam olish)
    },
    "barbers": {
        "b1": {
            "id": "b1",
            "name": "Jasur (Top Master)",
            "specialty": "Fade, Klassik soch turmaklari va Soqol dizayni",
            "experience": "6 yillik tajriba",
            "active": True
        },
        "b2": {
            "id": "b2",
            "name": "Sardor (Barber)",
            "specialty": "Zamonaviy uslublar va Qirollik soqol olish",
            "experience": "4 yillik tajriba",
            "active": True
        }
    },
    "services": {
        "s1": {"id": "s1", "name": "✂️ Soch olish", "price": 60000, "duration": 30},
        "s2": {"id": "s2", "name": "🧔 Soqol tekislash", "price": 40000, "duration": 30},
        "s3": {"id": "s3", "name": "🔥 Kompleks (Soch + Soqol)", "price": 90000, "duration": 60},
        "s4": {"id": "s4", "name": "👦 Bolalar soch olishi", "price": 50000, "duration": 30}
    }
}


def get_settings() -> dict:
    ref = db.reference("settings")
    data = ref.get()
    if not data:
        ref.set(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS

    # Normalize services & barbers if needed
    for key in ["services", "barbers"]:
        raw = data.get(key, {})
        if isinstance(raw, list):
            norm = {}
            for idx, item in enumerate(raw):
                if isinstance(item, dict):
                    item_id = item.get("id") or f"{key[0]}{idx+1}"
                    item["id"] = item_id
                    norm[item_id] = item
            data[key] = norm
            ref.child(key).set(norm)
    return data


def format_price(price_val) -> str:
    try:
        num = int(str(price_val).replace(" ", "").replace("so'm", ""))
        return f"{num:,}".replace(",", " ") + " so'm"
    except Exception:
        return str(price_val)


def get_user_profile(user_id: int) -> dict:
    return db.reference(f"users/{user_id}").get() or {}


def save_user_profile(user_id: int, full_name: str, username: str, phone: str):
    ref = db.reference(f"users/{user_id}")
    existing = ref.get() or {}
    ref.update({
        "user_id": user_id,
        "full_name": full_name,
        "username": username or "",
        "phone": phone,
        "banned": existing.get("banned", False),
        "updated_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    })


def is_user_banned(user_id: int) -> bool:
    prof = get_user_profile(user_id)
    return bool(prof.get("banned", False))


def get_available_slots(barber_id: str, date_str: str, duration_minutes: int = 30) -> list[str]:
    cfg = get_settings()
    wh = cfg.get("work_hours", {})
    start_str = wh.get("start", "09:00")
    end_str = wh.get("end", "21:00")
    lunch_start = wh.get("lunch_start", "")
    lunch_end = wh.get("lunch_end", "")
    step = int(wh.get("slot_step_minutes", 30))
    closed_days = wh.get("closed_weekdays", []) or []

    now = datetime.now(TZ)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    if target_date.weekday() in closed_days:
        return []

    start_dt = TZ.localize(datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M"))
    end_dt = TZ.localize(datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M"))

    lunch_s_dt = TZ.localize(datetime.strptime(f"{date_str} {lunch_start}", "%Y-%m-%d %H:%M")) if lunch_start else None
    lunch_e_dt = TZ.localize(datetime.strptime(f"{date_str} {lunch_end}", "%Y-%m-%d %H:%M")) if lunch_end else None

    booked_data = db.reference(f"slots/{date_str}/{barber_id}").get() or {}

    all_slots = []
    curr = start_dt
    while curr < end_dt:
        all_slots.append(curr)
        curr += timedelta(minutes=step)

    needed_blocks = max(1, duration_minutes // step)
    available = []

    for i in range(len(all_slots)):
        slot_dt = all_slots[i]

        # O'tib ketgan yoki 15 daqiqa yaqin vaqtlar
        if target_date == now.date() and slot_dt <= now + timedelta(minutes=15):
            continue

        if i + needed_blocks > len(all_slots):
            continue

        is_free = True
        for b in range(needed_blocks):
            blk_dt = all_slots[i + b]
            # Tushlik vaqtiga tushmasligini tekshirish
            if lunch_s_dt and lunch_e_dt and (lunch_s_dt <= blk_dt < lunch_e_dt):
                is_free = False
                break

            check_time = blk_dt.strftime("%H:%M")
            if check_time in booked_data and booked_data[check_time].get("status") != "cancelled":
                is_free = False
                break

        if is_free:
            available.append(slot_dt.strftime("%H:%M"))

    return available


def count_user_day_bookings(user_id: int, date_str: str) -> int:
    bookings = get_user_bookings(user_id)
    return sum(1 for b in bookings if b.get("date") == date_str and b.get("status") == "confirmed")


def book_slot_atomic(
    date_str: str,
    barber_id: str,
    barber_name: str,
    time_str: str,
    duration_minutes: int,
    service_name: str,
    service_price: int,
    user_id: int,
    user_name: str,
    phone: str,
) -> tuple[bool, str]:
    if is_user_banned(user_id):
        return False, "🚫 Sizning hisobingiz ma'muriyat tomonidan cheklangan."

    if count_user_day_bookings(user_id, date_str) >= 2:
        return False, "⚠️ Bir kunda maksimal 2 ta navbat olishingiz mumkin."

    cfg = get_settings()
    step = int(cfg.get("work_hours", {}).get("slot_step_minutes", 30))
    needed_blocks = max(1, duration_minutes // step)
    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

    block_times = [
        (start_dt + timedelta(minutes=step * b)).strftime("%H:%M")
        for b in range(needed_blocks)
    ]

    barber_slots_ref = db.reference(f"slots/{date_str}/{barber_id}")
    booking_id = f"BK-{int(datetime.now(TZ).timestamp())}-{abs(user_id) % 1000}"

    def transaction_update(current_data):
        if current_data is None:
            current_data = {}

        for bt in block_times:
            if bt in current_data and current_data[bt].get("status") != "cancelled":
                raise ValueError("SLOT_TAKEN")

        for idx, bt in enumerate(block_times):
            current_data[bt] = {
                "booking_id": booking_id,
                "status": "confirmed",
                "is_main_slot": idx == 0,
                "duration": duration_minutes,
                "service": service_name,
                "price": service_price,
                "user_id": user_id,
                "user_name": user_name,
                "phone": phone,
                "reminded": False,
                "source": "telegram",
                "created_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            }
        return current_data

    try:
        barber_slots_ref.transaction(transaction_update)
    except ValueError as e:
        if str(e) == "SLOT_TAKEN":
            return False, "⚠️ Kechirasiz, bu vaqt hozirgina band qilindi! Iltimos, boshqa bo'sh vaqt tanlang."
        return False, "Xatolik yuz berdi."
    except Exception as e:
        return False, f"Tizim xatoligi: {e}"

    db.reference(f"user_bookings/{user_id}/{booking_id}").set({
        "booking_id": booking_id,
        "date": date_str,
        "time": time_str,
        "duration": duration_minutes,
        "barber_id": barber_id,
        "barber_name": barber_name,
        "service": service_name,
        "price": service_price,
        "phone": phone,
        "status": "confirmed",
    })

    return True, booking_id


def get_user_bookings(user_id: int) -> list[dict]:
    data = db.reference(f"user_bookings/{user_id}").get() or {}
    bookings = list(data.values())
    bookings.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)
    return bookings


def cancel_booking(date_str: str, barber_id: str, booking_id: str, user_id: int = None) -> bool:
    ref = db.reference(f"slots/{date_str}/{barber_id}")
    slots = ref.get() or {}
    updated = False
    for time_key, info in slots.items():
        if info.get("booking_id") == booking_id:
            ref.child(time_key).delete()
            if not user_id:
                user_id = info.get("user_id")
            updated = True

    if user_id and user_id > 0:
        db.reference(f"user_bookings/{user_id}/{booking_id}").update({"status": "cancelled"})

    return updated


def get_due_reminders() -> list[dict]:
    """Navbatiga 45-65 daqiqa qolgan va hali eslatma yuborilmagan mijozlarni topadi."""
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")
    day_data = db.reference(f"slots/{today_str}").get() or {}
    cfg = get_settings()
    barbers = cfg.get("barbers", {})

    due = []
    for b_id, slots in day_data.items():
        if not isinstance(slots, dict):
            continue
        barber_name = barbers.get(b_id, {}).get("name", "Barber")
        for time_str, info in slots.items():
            if not info.get("is_main_slot") or info.get("reminded") or info.get("status") != "confirmed":
                continue
            uid = info.get("user_id", 0)
            if not uid or uid <= 0:
                continue
            try:
                appt_dt = TZ.localize(datetime.strptime(f"{today_str} {time_str}", "%Y-%m-%d %H:%M"))
                diff_min = (appt_dt - now).total_seconds() / 60.0
                if 30 <= diff_min <= 65:
                    due.append({
                        "date": today_str,
                        "time": time_str,
                        "barber_id": b_id,
                        "barber_name": barber_name,
                        "booking_id": info.get("booking_id"),
                        "user_id": uid,
                        "service": info.get("service"),
                    })
            except Exception:
                continue
    return due


def mark_reminded(date_str: str, barber_id: str, time_str: str):
    db.reference(f"slots/{date_str}/{barber_id}/{time_str}").update({"reminded": True})
