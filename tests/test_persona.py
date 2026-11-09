from database.models import Persona
from services.persona import build_system_prompt


def make_persona(**kw):
    base = dict(user_id=1, name="سارا", role="فروشندهٔ فروشگاه X", tone="صمیمی",
                language="fa", knowledge_base="قیمت تی‌شرت: ۲۵۰ هزار تومان\nارسال: تهران ۱ روزه",
                instructions="به مشتری‌های عمده تخفیف بده", signature="— فروشگاه X",
                auto_reply=False, is_paused=False)
    base.update(kw)
    return Persona(**base)


def test_prompt_contains_owner_facts_and_rules():
    prompt = build_system_prompt(make_persona())
    assert "قیمت تی‌شرت" in prompt
    assert "مشتری‌های عمده" in prompt
    assert "سارا" in prompt
    assert "سخت" in prompt  # hard rules section present


def test_prompt_never_invents_when_unknown():
    prompt = build_system_prompt(make_persona(knowledge_base=""))
    assert "اطلاعات این مورد را ندارم" in prompt  # fallback behaviour instructed


def test_signature_included_when_set():
    assert "— فروشگاه X" in build_system_prompt(make_persona())
    assert "امضای" not in build_system_prompt(make_persona(signature=""))


def test_prompt_avoids_repeated_intro_and_false_followup():
    prompt = build_system_prompt(make_persona())
    assert "تکرار نکن" in prompt
    assert "قول" in prompt and "ابزار پیگیری نداری" in prompt
    assert "احوال‌پرسی" in prompt
    assert "نوع استفاده: شخصی" in build_system_prompt(make_persona(), use_case="personal")
