"""Conservative detection, not reconstruction, of legacy question-mark data."""
DEFAULTS = {"name": "فیلو", "role": "دستیار من", "tone": "مودب و رسمی"}


def is_question_only(value: str) -> bool:
    compact = "".join(value.split())
    return len(compact) >= 2 and set(compact) == {"?"}


def display_value(value: str) -> str:
    return "نیازمند اصلاح (دادهٔ قدیمی آسیب‌دیده)" if is_question_only(value) else value
