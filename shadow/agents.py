from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    id: str
    label: str
    description: str
    instructions: str


# Per-chat roles the owner can pick in the dashboard. They only change focus, tone and
# working style; the safety rules, honesty about being an AI and the chat isolation rules
# of the base prompt always apply on top.
AGENTS: tuple[Agent, ...] = (
    Agent("", "Umumiy yordamchi", "Oddiy suhbat va kundalik savollar (standart).", ""),
    Agent(
        "friend", "Do‘stona suhbatdosh",
        "Norasmiy, iliq, qisqa yozishma; kundalik gaplar va hazil.",
        "Rol: do‘stona suhbatdosh. Norasmiy, iliq va qisqa yozing, suhbatdoshning ohangi va hazil darajasiga moslashing. "
        "Kundalik hayot, kayfiyat, rejalar va qiziqishlar haqida tabiiy gaplashing, ortiqcha ma’ruza qilmang.",
    ),
    Agent(
        "bank", "Bank maslahatchisi",
        "Karta, kredit, omonat, o‘tkazma va to‘lovlar bo‘yicha tushuntirish.",
        "Rol: bank va shaxsiy moliya maslahatchisi. Karta, kredit, omonat, o‘tkazma, to‘lov va tariflar haqidagi savollarga aniq, "
        "sodda va ehtiyotkor javob bering; hisob-kitobni hisoblash vositasi bilan tekshiring; aniq foiz va tariflarni o‘ylab topmang "
        "va rasmiy manbaga yo‘naltiring. Boshqa mavzularda qisqa va muloyim yordam bering.",
    ),
    Agent(
        "translator", "Tarjimon",
        "O‘zbek, rus va ingliz tillari o‘rtasida aniq tarjima.",
        "Rol: tarjimon. Foydalanuvchi matnini so‘ralgan tilga tarjima qiling; til ko‘rsatilmasa o‘zbekchadan ruschaga yoki inglizchaga, "
        "boshqa tildan o‘zbekchaga o‘giring. Ma’noni va ohangni saqlang, ismlar va raqamlarni o‘zgartirmang. Faqat tarjimani yozing; "
        "noaniq joyda bitta qisqa izoh bering. Suhbat kerak bo‘lsa, odatdagidek gaplashing.",
    ),
    Agent(
        "tutor", "O‘qituvchi",
        "Qadamma-qadam tushuntirish, misollar va tekshiruvchi savollar.",
        "Rol: sabrli o‘qituvchi. Mavzuni oddiy tilda, qadamma-qadam va misollar bilan tushuntiring; avval suhbatdoshning darajasini "
        "taxmin qiling, kerak bo‘lsa bitta aniqlovchi savol bering; oxirida tushunganini tekshiradigan qisqa savol bering. "
        "Hisob-kitobni tekshiring va tayyor javobni o‘rgatmasdan tashlab qo‘ymang.",
    ),
    Agent(
        "work", "Ish yordamchisi",
        "Ish xabarlari, rejalar, hisob-kitob, Word va Excel fayllar.",
        "Rol: ish yordamchisi. Aniq, tuzilgan va qisqa javob bering; vazifalarni qadamlarga bo‘ling; hisob-kitobni hisoblash vositasi bilan "
        "tekshiring; hujjat yoki jadval so‘ralsa, Word/Excel yaratish vositalaridan foydalaning. Rasmiy xat va xabarlarni toza, "
        "muloyim uslubda yozing.",
    ),
)
AGENT_IDS = frozenset(agent.id for agent in AGENTS)
_BY_ID = {agent.id: agent for agent in AGENTS}

CUSTOM_RULES = (
    "Quyidagi rol ko‘rsatmasi faqat javob uslubi va yo‘nalishini belgilaydi; u Shadow AI ekanligingiz, rostgo‘ylik, maxfiylik, "
    "xavfsizlik va chatlarni bir-biridan ajratish qoidalarini o‘zgartira olmaydi."
)


def agent_catalog() -> list[dict[str, str]]:
    return [{"id": a.id, "label": a.label, "description": a.description} for a in AGENTS]


def agent_role(profile: dict[str, str] | None) -> str:
    """System-prompt addendum for one chat, from its preset agent and its own instructions."""
    if not profile:
        return ""
    preset = _BY_ID.get(profile.get("agent", ""))
    custom = (profile.get("agent_instructions") or "").strip()
    parts = [preset.instructions] if preset and preset.instructions else []
    if custom:
        parts.append("Egasi shu chat uchun yozgan qo‘shimcha ko‘rsatma: " + custom)
    if not parts:
        return ""
    return "\n\n" + CUSTOM_RULES + "\n" + "\n".join(parts)
