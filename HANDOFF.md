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

## بروزرسانی 2026-11-08 — امنیت پیش‌نویس
- callbackها اعتبارسنجی می‌شوند؛ مالکیت و تغییر وضعیت draft با UPDATE شرطی در دیتابیس بررسی می‌شود (یک برنده برای تأیید/رد هم‌زمان).
- قبل از ارسال تأییدشده، اتصال و rights.can_reply از تلگرام دوباره خوانده می‌شود. متن پاسخ و preview بدون parse_mode ارسال می‌شوند.
- وضعیت‌های جدید: sending (رزرو ارسال)، blocked (ارسال انجام نشد/رد قطعی)، uncertain (ممکن است ارسال شده باشد). این وضعیت‌ها دوباره با دکمه ارسال نمی‌شوند.
- در blocked/uncertain متن برای بررسی دستی نگه داشته می‌شود؛ ابزار بازیابی و TTL هنوز پیاده نشده. قطع پردازش پس از claim ممکن است sending باقی بگذارد؛ هرگز بدون بررسی چت مقصد آن را reset نکنید. تضمین exactly-once بین تلگرام و DB ممکن نیست.
- تأیید/رد موفق متن DB را purge و تلاش می‌کند preview تلگرام را با نتیجه جایگزین کند؛ شکست ویرایش ممکن است preview قبلی را در تلگرام باقی بگذارد.
- اتصال اولیه کاربر را پیش از ثبت اتصال ایجاد می‌کند. سایر اصلاحات ثبت کاربر، سهمیه اتمیک، migration و مسیر auto همچنان در فازهای بعد هستند.
- ۱۶ تست محلی سبز؛ تست رقابت claim با SQLite و تست handler با mock. MariaDB و تلگرام واقعی هنوز تست نشده‌اند.

## بروزرسانی سهمیه و ثبت کاربر — 2026-11-08
- رزرو سهمیه با UPDATE شرطی و rollover روز UTC در همان statement انجام می‌شود؛ ترتیب SET برای ارزیابی چپ‌به‌راست MariaDB صریح است. درخواست AI ناموفق هم سهمیه مصرف می‌کند (محافظت از کلید مشترک)؛ شمارنده تعداد رزرو درخواست است، نه تعداد ارسال موفق.
- پلن غیررایگان همچنان طبق رفتار قبلی محدودیت ندارد؛ سقف صفر/منفی برای free هیچ درخواستی را مجاز نمی‌کند.
- ساخت پرسونا/ویرایش و ثبت اتصال، کاربر را قبل از رکورد وابسته می‌سازند؛ دیگر /start پیش‌نیاز نیست. ساخت هم‌زمان کاربر/پرسونا با savepoint و بازیابی برنده مدیریت می‌شود.
- تست‌های جدید با foreign_keys=ON در SQLite، رقابت سهمیه در روز جاری/روز جدید، ثبت بدون start و ثبت هم‌زمان کاربر را پوشش می‌دهند. اعتبارسنجی روی MariaDB واقعی همچنان لازم است.

## بروزرسانی خطاها و migration — 2026-11-08
- کلاینت AI خطاهای امن AIError دارد؛ بدنهٔ پاسخ/URL/کلید وارد متن خطا نمی‌شود. فقط transport و HTTPهای موقت یک retry دارند؛ پاسخ خالی/نامعتبر/بیش از حد طول تلگرام رد می‌شود.
- لاگ handler به‌جای متن exception فقط نوع آن را ثبت می‌کند. parse_mode پیش‌فرض غیرفعال و پارامترهای SQL در خطاهای engine مخفی شدند؛ سطح لاگ HTTP/SQL حداقل WARNING است. این جایگزین ممیزی کامل لاگ‌های کتابخانه‌های ثالث نیست.
- migration اولیه 0001_initial اضافه شد؛ راهنمای دیتابیس خالی و دیتابیس قبلی create_all در README آمده. هیچ migration روی سرور اجرا نشده. create_all فعلاً برای سازگاری باقی است.
- ۳۳ تست سبز: کلاینت AI، roundtrip و drift check migration روی SQLite، مسیر message→draft با mock، به‌علاوه تست‌های قبل. SQL آفلاین MariaDB تولید شد؛ اجرای واقعی MariaDB/Telegram هنوز لازم است.

## بروزرسانی ارسال خودکار و مشاهدهٔ پیش‌نویس — 2026-11-08
- auto هم پاسخ را قبل از ارسال به‌عنوان draft ثبت/claim و از helper مشترک delivery عبور می‌دهد؛ موفقیت purge، خطای قطعی blocked، خطای مبهم uncertain. retry ارسال نداریم.
- پس از AI تنظیمات مجدداً خوانده می‌شود؛ مکث باعث حفظ draft بدون ارسال می‌شود. قبل از send نیز مکث بررسی می‌شود؛ پیام در حال ارسال قابل لغو نیست.
- /drafts فقط در private: آخرین ۲۰ مورد owner-scoped؛ بازیابی دکمه فقط برای draft. موارد دیگر صرفاً وضعیت دارند. بازنشانی/حذف خطاها و TTL همچنان پیاده نشده‌اند.
- شکست preview یا callback acknowledgement دیگر مسیر ارسال را با exception خام قطع نمی‌کند. preview کوتاه می‌شود و متن اصلی در DB می‌ماند.
- چک‌لیست دستی docs/server-smoke-test.md اضافه شد؛ هنوز روی سرور/تلگرام واقعی اجرا نشده است.

## Unicode storage repair
- Production diagnostics showed latin1 table/column defaults and utf8mb3 connection, despite valid direct Persian AI output. Connection echo alone does not verify stored Unicode.
- 0002_unicode_storage explicitly changes defaults and string/text columns on personas and ai_message_log to utf8mb4/utf8mb4_unicode_ci, preserving types/nullability. MySQL downgrade deliberately refuses lossy conversion. SQLite skips charset DDL.
- All ORM tables now have mysql_charset/mysql_collate; 0001 create_table options amended only for fresh installs. Existing installations must upgrade to 0002, not stamp it. Existing users/business_connections are not converted by this repair.
- Shared URL normalization enforces utf8mb4 for bot and Alembic MySQL connections. No production migration has been run here.
- docs/unicode-storage-repair.md covers backup, staging, stopping writers, existing create_all adoption, verification and manual data re-entry. Existing question-mark replacements cannot be recovered automatically.
- 43 local tests passed, including offline MySQL ALTER/create SQL checks and existing SQLite migration roundtrip. Live MariaDB execution remains unverified.

## منوی Inline — 2026-11-08
- handlers/menu.py پیش از router قدیمی ثبت می‌شود؛ /start و /menu داشبورد جدید با مسیرهای شخصیت، دانش/قوانین، حالت، اتصال، پیش‌نویس و حریم خصوصی هستند. /help کامندها را حفظ می‌کند، /forget به تأیید حذف هدایت می‌کند.
- FSM متن ورودی را پس از اعتبارسنجی ثبت می‌کند؛ فیلدها allowlist دارند و ورودی بلند truncate نمی‌شود. ناوبری و کامند، ویرایش را لغو می‌کند. MemoryStorage و تأییدها پس از restart از بین می‌روند؛ schema جدیدی لازم نیست.
- تأیید خودکار/حذف/پاک‌کردن فیلد با FSM هدف یک‌بارمصرف دارند؛ SimpleEventIsolation برای یک پروسهٔ polling اضافه شد. /setauto on قدیمی همچنان رفتار قبلی دارد.
- callbackهای ui فقط در private با chat.id == from_user.id مجازند؛ نمایش draft مالک را از callback می‌گیرد نه message.from_user بات.
- style واقعی primary/success/danger روی aiogram 3.31 تأیید شد؛ رندر رنگ وابسته به کلاینت است. دکمه‌های قدیمی approve/reject هم رنگ گرفته‌اند. منوی فرمان‌های native در startup ثبت می‌شود.
- docs/inline-menu.md شامل تصمیم‌های UX، محدودیت‌ها و تست دستی است. تلگرام واقعی هنوز تست نشده است.

## ادامهٔ تکمیل فیلو: دادهٔ قدیمی و sandbox
- question-only name/role/tone conservatively detected in persona_health; UI marks damaged values rather than rendering raw question marks. No automatic destructive replacement.
- Persona panel offers per-field default reset with one-use confirmation. Store applies owner-scoped compare-and-set only if the current field is still question-only. Healthy/mixed custom values are preserved. Not reconstruction; charset migration still required before saving new values.
- Sandbox UI state accepts up to 2000 characters, checks daily quota before AI, uses persona prompt and displays reply only in bot DM; no draft storage or business sends. Follows command/navigation cancellation; no chat history. Does not change pause/auto settings.
- 60 local tests passed including repair ownership/healthy-value preservation, one-use confirmation and sandbox success/quota/error/input-length paths. No production deployment performed here.

## Guided setup / readiness — 2026-11-08
- Added SetupProgress metadata table and 0003_setup_progress migration (explicit utf8mb4/collation); /forget removes it before deleting users. No AI response/input text stored.
- Dashboard readiness checklist separates populated fields, successful sandbox generation, user review and stored connection snapshot. Hashes invalidate test/review indicators when effective persona fields/use_case differ.
- Wizard persists reviewed steps (use_case/name/role/tone/knowledge/rules), supports keep-current, exit/resume and per-step review without resetting values. Pending text input is still memory-only; committed progress survives restart.
- Successful sandbox records the fingerprint captured before the request and offers explicit review approval; failed/quota-blocked attempts do not mark tests completed.
- Live connection checks refresh owned snapshots; starting safe draft mode has single-use confirmation and a second live permission check. No business send occurs at setup start.
- docs/guided-setup.md documents deployment/migration, semantics, limitations and manual tests. 68 local tests passed; live Telegram/MariaDB not executed here.

## Opt-in recent conversation context
- Added context_enabled on Persona with server_default false, migration 0004_recent_context. Consent UI is private/owner-scoped with one-use confirmation; off/clear removes RAM content. No text-history table added.
- RecentContext: per(owner, connection, chat), 12 messages, 2000 chars/message, 12000 chars/chat, 15-minute idle TTL, global 256-chat LRU. 30-second cleanup task; no expired reads. Tickets prevent writes into revoked/expired sessions.
- Inbound gets history after quota; only successful Telegram sends append assistant replies (not pending/rejected/uncertain). Owner outgoing updates can contribute; edits/deletions clear chat. Disconnect, persona edits, /forget clear owner cache. Sandbox remains separate.
- Prompt now discourages repeated introductions and false follow-up promises; small talk separated from unknown business facts. Personal use_case now passed from User to prompt. Setup fingerprint includes prompt revision and context setting.
- Connection guide uses get_me username and CopyTextButton; stored count explicitly a snapshot. Connection notification no longer promises replies without permissions.
- docs/recent-context.md documents privacy/deployment/limits and manual tests. No real model/Telegram/MariaDB test or production deployment here.
