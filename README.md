# secretary-bot — سکرتر هوشمند تلگرام (MVP)

باتی که هر کاربر آن را از **Settings → Profile/Account → Chat Automation** به اکانت خودش وصل می‌کند تا از طرف او به پیام‌های خصوصی جواب بدهد — با پرسونای کاملاً شخصی‌سازی‌شده (هم برای کسب‌وکار، هم استفادهٔ شخصی). موتور: **Gemma 4** روی Google AI Studio (فعلاً کلید مشترک رایگان — سقف آگاهانهٔ ۳۰ جواب/روز برای کاربران رایگان). پرداخت اولیه: **Telegram Stars**.

> وضعیت: اسکلت عملیاتی v0.1 — onboarding، تنظیمات پرسونا، حالت Draft/Auto، اتصال Business، quota روزانه، ایده‌ام‌پوتنسی.

## چرا اینطوری ساخته شده
- **MariaDB + SQLAlchemy async + Alembic** — هم‌داستان با sib و cPanel هلند.
- **Polling فعلاً** (الگوی واچ‌داگ + کرون یک‌دقیقه‌ای sib). هندلرها transport-agnostic هستند؛ مهاجرت آینده به webhook (مثلاً Cloudflare Workers) فقط با افزودن یک front ممکن است — شرطش بازنویسی به runtime مناسب است، نه تغییر منطق.
- **حریم خصوصی:** متن پیام‌ها ذخیره نمی‌شود؛ فقط metadata. در حالت Draft متن پیش‌نویس تا لحظهٔ تأیید/رد نگه داشته و بعدش purge می‌شود.
- **Draft-first (پیش‌فرض):** هیچ جوابی بدون تأیید صاحب‌حساب ارسال نمی‌شود تا /setauto on.

## راه‌اندازی (روی cPanel هلند)
```bash
git clone <this repo> && cd secretary-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # BOT_TOKEN و DATABASE_URL و GEMINI_API_KEY را پر کن
.venv/bin/python bot.py
```
برای دائم بالا نگه داشتن: `deploy/watchdog.sh` را به کرون یک‌دقیقه‌ای و `deploy/backup_db.sh` را به کرون روزانه اضافه کن.

در BotFather: یک بات بساز و **Secretary Mode** (قبلاً Business Mode) را فعال کن؛ تلگرام کاربر باید ۱۲.۷+ باشد.

## دستورات
`/start` `/whoami` `/setname` `/setrole` `/settone` `/setinfo` `/setrules` `/setauto on|off` `/pause` `/resume` `/forget`

## قدم‌های بعدی (roadmap)
1. پرداخت Stars: `send_invoice` با واحد XTR؛ پلن Pro (سقف بالاتر + Auto mode) و Business (چند اتصال/RAG).
2. لایهٔ انتزاع مدل (swap Gemma ↔ Gemini ↔ self-host) + BYOK برای کاربران حرفه‌ای.
3. حافظهٔ گفتگو (اختیاری، opt-in) — فعلاً آگاهانهٔ stateless.
4. داشبورد Mini App (سؤالات پرتکرار، تعداد جواب) + تمپلیت‌های آماده (فروشگاه/آرایشگاه/کلینیک).
5. تست‌های یکپارچهٔ business_message با mock تلگرام.
