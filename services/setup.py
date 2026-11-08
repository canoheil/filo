"""Pure readiness checks. A populated profile is not a quality guarantee."""
import hashlib
import json
from services.persona_health import is_question_only

STEPS = ("use_case", "name", "role", "tone", "knowledge_base", "instructions")


def fingerprint(persona, use_case):
    fields = ("name", "role", "tone", "language", "knowledge_base", "instructions", "signature")
    data = {key: getattr(persona, key) for key in fields}
    data["use_case"] = use_case
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def readiness(persona, user, progress, connections):
    current = fingerprint(persona, user.use_case)
    healthy = lambda field: bool(getattr(persona, field).strip()) and not is_question_only(getattr(persona, field))
    return {
        "شخصیت": all(healthy(f) for f in ("name", "role", "tone")),
        "اطلاعات و قوانین": all(healthy(f) for f in ("knowledge_base", "instructions")),
        "آزمایش تنظیمات فعلی": progress.tested_hash == current,
        "تأیید پاسخ توسط تو": progress.approved_hash == current,
        "اتصال ثبت‌شده با مجوز پاسخ": any(c.is_enabled and (c.rights or {}).get("can_reply") for c in connections),
    }
