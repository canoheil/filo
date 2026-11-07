# HANDOFF — secretary-bot (سکرتر هوشمند تلگرام)
_آخرین بروزرسانی: 2026-11-07 · این فایل را برای هر عامل/توسعه‌دهندهٔ بعدی نگه دار_

## یک خطی
باتی که هر کاربر از **Settings → Profile/Account → Chat Automation** (تلگرام ۱۲.۷+) به اکانت شخصی‌اش وصل می‌کند تا از طرف خودش به دایرکت‌ها جواب دهد — با پرسونای کاملاً شخصی‌سازی‌شده (شخصی یا بیزینس). موتور: Gemma 4 روی Google AI Studio.

## لینک‌ها
- ریپو: https://github.com/canoheil/filo (public؛ اگر private شد آدرس همان است)
- طرح کسب‌وکار کامل: `/home/user/business-plan-secretary-bot.md` (بیرون ریپو ولی در همین workspace)
- مرجع معماری/انضباط: ریپوی `sib` (https://github.com/canoheil/sib) — پروژهٔ بازی قبلی همین کاربر؛ ازش استخراج کنیم نه fork.

## تصمیم‌های قطعی (چرایی مهم است)
1. **بازار:** ایران/فارسی‌زبان. تلگرامِ ایران غالب؛ پرداخت فعلاً **Stars اول** (send_invoice، واحد XTR)؛ درگاه ریالی فاز بعد.
2. **محصول عمومی است نه نیچ‌محور** — هر کس پرسونای خودش را آزادانه می‌سازد (/setinfo /setrules ...). تمپلیت‌های آمادهٔ نیچ‌ها = لایهٔ Pro آینده است، نه هسته.
3. **کلید مشترک رایگان AI Studio** برای لانچ (صفر هزینه) + **سقف سخت ۳۰ جواب/روز** برای پلن free؛ مسیر بعدی: BYOK (کاربر کلید خودش) یا API پولی/self-host (Gemma 4 = Apache 2.0) برای Proها.
4. **Draft-first پیش‌فرض** — هیچ جوابی بدون تأیید مالک ارسال نمی‌شود تا /setauto on. بات با اعتبار خودِ کاربر حرف می‌زند؛ این امن‌ترین حالت شروع است.
5. **حریم خصوصی:** متن پیام ذخیره نمی‌شود؛ فقط metadata (chat_id/message_id/زمان/جهت). متن پیش‌نویس فقط تا تأیید/رد می‌ماند سپس purge. لاگ‌ها هرگز محتوا نباید داشته باشند (مگر Feature پرمیوم آینده، opt-in).
6. **MariaDB** (نه Postgres) — هاست اشتراکی cPanel هلند با Database Wizard؛ utf8mb4 اجباری (فارسی)؛ **NullPool** عمدی (اتصال محدود).
7. **Polling برای حالا** (وارچ‌داگ + کرون یک‌دقیقه‌ای، الگوی sib)؛ هندلرها transport-agnostic هستند؛ مهاجرت آینده به webhook/Cloudflare فقط با front جدید است، نه بازنویسی منطق.
8. **idempotent writes** در برابر retry تلگرام: `log_inbound_once()` روی (connection, chat, message)؛ الگوی کلید مثل sib ledger.

## وضعیت امروز (چه چیزی کار می‌کند / چه چیزی نه)
✅ تست‌شدهٔ محلی: import همهٔ ماژول‌ها؛ ۶ تست سبز (pytest)؛ امضاهای aiogram 3.31 تأیید شدند (`router.business_connection`, `router.business_message`, `Bot.send_message(business_connection_id=...)`)؛ پایتون ۳.۱۳.
⚠️ **تست‌نشده با تلگرام واقعی:** ساخت بات با BotFather + فعال‌سازی **Secretary Mode** (همان Business Mode قدیمی)؛ دریافت آپدیت business_connection/business_message واقعی؛ BusinessBotRights (can_reply، بازهٔ ۲۴ ساعته).
⚠️ **نام مدل نامشخص:** در .env نمونه `gemma-4-26b-it` گذاشته‌ام — شناسهٔ دقیق را از AI Studio خودت بگیر.
❌ پیاده‌نشده: Stars invoice؛ حافظهٔ گفتگو (آگاهانه stateless شد)؛ داشبورد Mini App؛ migration اولیهٔ Alembic (فعلاً `init_db()` با create_all).

## تله‌ها (نری)
- requirements.txt قبلاً «pyymysql» بود — تایپو بود، اصلاح شد؛ هنگام ویرایش حواس.
- پیام‌های خودِ صاحب‌حساب و پیام‌های ارسالی از طریق یک Business Bot دیگر (`sender_business_bot`) باید نادیده گرفته شوند وگرنه حلقهٔ بی‌نهایت می‌سازی.
- `sender_business_bot` فیلد روی Message است، `can_reply` و `rights` روی BusinessConnection.
- روی هاست اشتراکی: پروسه‌ها کشته می‌شوند → watchdog با cron (`deploy/watchdog.sh`)؛ لاگ را سبک نگه دار.
- quota را **قبل** از فراخوانی AI چک کن؛ کلید مشترک رایگان = ۱ تقریباً ~۱۵۰۰–۱۴۴۰۰ درخواست/روز گوگل.
- متن پیام را در لاگ/دیتابیس نگه ندار؛ فقط متادیتا.
- ایرانی‌ها Stars را داخل ایران سخت می‌خرند؛ درگاه زرین‌پال در فاز بعد.

## معماری فعلی (فایل‌ها)
bot.py (entry, polling, allowed_updates شامل business_*) · core/config.py · core/logging.py · database/{base,connection,models}.py (User, BusinessConnection, Persona, AiMessageLog) · middleware/rate_limit.py · services/{ai,persona,quota,store}.py · handlers/start.py (onboarding+پرسونا) · handlers/secretary.py (business events) · migrations/ (alembic.ini آماده، env.py آماده) · deploy/{watchdog,backup_db}.sh · tests/

## قدم‌های بعدی (به ترتیب)
1. BotFather: ساخت بات + Secretary Mode + `/setjoindate`? — فقط Secretary Mode؛ آپدیت تلگرام کاربر **۱۲.۷+** است؛ اتصال از Settings → Profile → Chat Automation.
2. دیپلوی روی هاست cPanel (env: BOT_TOKEN, DATABASE_URL, GEMINI_API_KEY, GEMMA_MODEL) → بالا آوردن بات → اتصال اکانت تست (یک حساب فیک، نه اکانت شخصی اصلی!).
3. تست business_message واقعی + کنترل‌های ضدحلقه + سقف quota.
4. Stars: `send_invoice` XTR برای Pro (سقف بالاتر + auto mode)؛ /whoami نمایش پلن.
5. migration اولیه با alembic revision --autogenerate.
6. (اختیاری) BYOK؛ (اختیاری) حافظهٔ گفتگو opt-in برای Pro؛ تمپلیت‌های آماده.

## قراردادهای کاری با صاحب پروژه
- زبان دامنه و مستندات فارسی؛ کد و کمیت‌های فنی انگلیسی.
- صاحب پروژه روی هاست اشتراکی cPanel هلند کار می‌کند؛ ریپوی sib الگوی اجرا (NO systemd روی هاست اشتراکی).
- هرگز توکن/کلید را commit نکن (.env در .gitignore است؛ .env.example فقط placeholder).
- قبل از پوش: `git ls-files` را برای .env/secrets چک کن؛ بعد از کار: توکن fine-grained کوتاه‌عمر را revoke کن.
