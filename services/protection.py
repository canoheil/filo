"""Fixed-cardinality aggregate counters. No IDs, message text or error labels."""
from collections import Counter, OrderedDict
import time

EVENTS = frozenset({
    'input.accepted', 'input.rate', 'input.capacity',
    'safety.accepted', 'safety.rate', 'safety.capacity',
    'ai.accepted', 'ai.global_busy', 'ai.owner_busy', 'ai.chat_rate',
    'ai.owner_rate', 'ai.global_rate', 'ai.capacity', 'ai.oversized',
    'notice.allowed', 'notice.suppressed', 'out.accepted', 'out.deferred',
    'out.wait_full', 'out.deadline', 'out.success', 'out.flood',
    'out.persistence_failed', 'out.failed', 'out.cancelled',
})
CAP = 10**12


class ProtectionMetrics:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.total = Counter()
        self.minutes = OrderedDict()

    def _purge(self, minute):
        while self.minutes and next(iter(self.minutes)) <= minute - 15:
            self.minutes.popitem(last=False)

    def record(self, event):
        # Unknown labels are ignored, never retained. No dynamic cardinality.
        if event not in EVENTS:
            return
        minute = int(self.clock() // 60)
        self._purge(minute)
        self.total[event] = min(CAP, self.total[event] + 1)
        bucket = self.minutes.setdefault(minute, Counter())
        bucket[event] = min(CAP, bucket[event] + 1)

    def snapshot(self):
        self._purge(int(self.clock() // 60))
        window = Counter()
        for bucket in self.minutes.values():
            window.update(bucket)
        return dict(self.total), dict(window), int(max(0, self.clock() - self.started))


metrics = ProtectionMetrics()
_input = None


def bind_input(limiter):
    global _input
    _input = limiter


async def report(actor):
    from services.admin import authorized
    if not authorized(actor):
        raise PermissionError()
    from services.ai_guard import ai_guard
    from services.outbound import outbound
    total, window, uptime = metrics.snapshot()
    lines = ["محافظت و ضداسپم فیلو", f"زمان این فرایند: {uptime} ثانیه",
             "شمارنده‌ها: از شروع فرایند / ۱۵ سطل دقیقه‌ای اخیر (حدود ۱۴ تا ۱۵ دقیقه)",
             "آمار تجمیعی است؛ نه تعداد کاربران یکتا، نه اثبات حمله و نه شمار مصرف AI."]
    labels = {
        'input.accepted': 'پذیرش ورودی عادی', 'input.rate': 'رد نرخ ورودی',
        'input.capacity': 'رد ظرفیت ورودی', 'safety.accepted': 'پذیرش مسیر ایمنی',
        'safety.rate': 'رد نرخ ایمنی', 'safety.capacity': 'رد ظرفیت ایمنی',
        'ai.accepted': 'پذیرش محافظ AI', 'ai.global_busy': 'ظرفیت هم‌زمان کل AI',
        'ai.owner_busy': 'ظرفیت هم‌زمان مالک', 'ai.chat_rate': 'نرخ گفت‌وگو',
        'ai.owner_rate': 'نرخ مالک', 'ai.global_rate': 'نرخ کل AI',
        'ai.capacity': 'ظرفیت حافظهٔ محافظ AI', 'ai.oversized': 'متن بیزینسی بلند',
        'notice.allowed': 'اجازهٔ اعلان', 'notice.suppressed': 'اعلان مهارشده',
        'out.accepted': 'پذیرش خروجی', 'out.deferred': 'تعویق محلی خروجی',
        'out.wait_full': 'ظرفیت انتظار خروجی', 'out.deadline': 'مهلت انتظار خروجی',
        'out.success': 'پاسخ موفق API خروجی', 'out.flood': 'پاسخ واقعی RetryAfter',
        'out.persistence_failed': 'خطای ذخیرهٔ مهلت', 'out.failed': 'خطای API خروجی',
        'out.cancelled': 'لغو درخواست خروجی',
    }
    for event, label in labels.items():
        lines.append(f"{label}: {total.get(event,0)} / {window.get(event,0)}")
    ai_guard.purge(ai_guard.clock())
    outbound.purge()
    lines += [f"عملیات فعال AI: {ai_guard.total}",
              f"خروجی در حال انجام: {outbound.inflight} | منتظر: {outbound.waiters}",
              f"مهلت توقف خروجی: {outbound.remaining():.1f} ثانیه",
              f"کلیدهای محافظ AI: {sum(len(b) for b in ai_guard.history.values())}",
              f"کلیدهای مقصد خروجی: {len(outbound.chats)}"]
    if _input is not None:
        _input.purge()
        lines.append(f"کلیدهای ورودی عادی / ایمنی: {len(_input._buckets[False])} / {len(_input._buckets[True])}")
    else:
        lines.append("محافظ ورودی به گزارش متصل نیست؛ وضعیت آن نامشخص است.")
    from core.config import get_settings
    cfg = get_settings()
    lines += [f"سقف فعال AI کل / مالک: {cfg.AI_MAX_CONCURRENT} / {cfg.AI_OWNER_MAX_CONCURRENT}",
              f"سقف خروجی فعال / انتظار: {cfg.OUTBOUND_MAX_INFLIGHT} / {cfg.OUTBOUND_MAX_WAITERS}",
              f"فاصلهٔ خروجی کل / خصوصی: {cfg.OUTBOUND_GLOBAL_INTERVAL_SECONDS} / {cfg.OUTBOUND_CHAT_INTERVAL_SECONDS} ثانیه"]
    lines += ["آمار RAM با ری‌استارت صفر می‌شود؛ مهلت flood ذخیره‌شده مستقل است.",
              "این گزارش پیام آزمایشی یا درخواست AI نمی‌فرستد. نرخ‌ها را بدون بررسی علت، افزایش نده."]
    return '\n'.join(lines)
