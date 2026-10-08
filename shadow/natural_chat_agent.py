"""Conversation-style agent instructions shared by every reply path.

This is a prompt layer, not a second model call: it makes replies feel less like
templates without adding latency or asking the model to rewrite its own answer.
"""
from __future__ import annotations

import re


_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_LATIN = re.compile(r"[A-Za-z]")
_CASUAL = re.compile(
    r"\b(nma|nima gap|qalesan|qalesiz|qalay|zo'r|zor|ok|xo'p|xop|bo'pti|"
    r"raxmat|rahmat|tinchlikmi|brat|aka|opa|aka\?|ха|нма|яхшими)\b", re.I,
)
_TASK = re.compile(
    r"\b(yozib ber|tuzib ber|hisobla|tarjima qil|qilib ber|tayyorla|"
    r"создай|напиши|переведи|сделай|calculate|write|translate)\b", re.I,
)
_EMOTION = re.compile(
    r"\b(charchadim|xafa|siqildim|qo'rqyapman|qo‘rqyapman|xursandman|"
    r"устал|обидно|переживаю|радуюсь|afsus|voy)\b", re.I,
)


def conversation_style_agent(history: str, message: str) -> str:
    """Return a compact, context-aware style layer for the current reply."""
    text = (message or "").strip()
    context = f"{history or ''}\n{text}"
    cyrillic = len(_CYRILLIC.findall(text)) > len(_LATIN.findall(text))
    casual = bool(_CASUAL.search(context)) or len(text) < 90
    if _EMOTION.search(text):
        situation = "Hissiy gap: avval qisqa va mos reaksiya bildiring, keyin kerak bo‘lsa aniq yordam bering."
    elif _TASK.search(text):
        situation = "Vazifa so‘raldi: natijani darrov bering, kirish va yakuniy reklama gaplarini qo‘shmang."
    elif "?" in text or "？" in text:
        situation = "Savol berildi: javobni birinchi jumlada ayting; faqat kerakli tafsilotni qo‘shing."
    else:
        situation = "Oddiy suhbat: xabarning o‘ziga tabiiy reaksiya bering, mavzuni sun’iy kengaytirmang."
    register = "Xabar qaysi tilda bo‘lsa, shu tilda javob bering; suhbatdoshning yozish uslubi va sen/siz darajasini so‘nggi xabarlardan aniqlang."
    if casual:
        register += " Qisqa, og‘zaki va oddiy yozing; kitobiy ibora yoki mijozlarga xizmat ko‘rsatish ohangidan qoching."
    if cyrillic:
        register += " So‘nggi xabar kirillda, javobni ham kirillda yozing."
    else:
        register += " Lotin yozuvidagi xabarga lotinda javob bering."
    return (
        "\n\n[Tabiiy muloqot agenti — har bir chat va vaziyat uchun]\n"
        + situation + " " + register + "\n"
        + "Shablon boshlanishlar ('Albatta', 'Ajoyib savol'), qayta bayon, sarlavha, "
        + "bo‘sh xushmuomalalik va javob oxiridagi avtomatik savolni ishlatmang. "
        + "Oddiy yozishmada qisqa yozing, lekin izoh, hujjat, kod, tarjima yoki ro‘yxat so‘ralsa "
        + "so‘ralgan format va kerakli to‘liqlikni saqlang. Fakt, ism, raqam, narx va ma’noni "
        + "uslub uchun o‘zgartirmang. Insondek yozish — odam ekaningizni yoki akkaunt egasi ekaningizni "
        + "da’vo qilish emas; bot ekaningizni so‘rashsa rost ayting."
    )
