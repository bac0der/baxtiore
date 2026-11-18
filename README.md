# 💈 Barbershop Full System (Telegram Client Bot + Web Admin Dashboard)

## 1. Telegram Bot (`telegram_bot/`) — Mijozlar uchun
- `serviceAccountKey.json` va `.env` fayllarini `telegram_bot/` papkasiga qo'ying.
- Ishga tushirish: `py main.py`

## 2. Web Admin Dashboard (`admin_web/`) — Faqat Egasi/Admin uchun
- `admin_web/` papkasini GitHub'ga yuklab, **Vercel** ga deploy qiling (yoki `index.html` faylini brauzerda oching).
- Kirish oynasida **Firebase Realtime Database URL** ni (masalan: `https://loyiha-default-rtdb.firebaseio.com`) va o'zingiz xohlagan **Admin Parol**ni kiriting.
- **Muhim (Firebase Rules):** Web Admin Panel ma'lumotlarni o'qib-yoza olishi uchun Firebase Console -> Realtime Database -> **Rules** bo'limini quyidagicha saqlang:
```json
{
  "rules": {
    ".read": true,
    ".write": true
  }
}
```
