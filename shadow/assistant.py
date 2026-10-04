from __future__ import annotations

from openai import AsyncOpenAI

from .config import Settings


SYSTEM_PROMPT = """Siz Shadow nomli shaxsiy AI yordamchisiz.

Asosiy til: o‘zbek tili. Tabiiy, ravon, qisqa va aniq yozing. Suhbatdosh ruscha yoki inglizcha yozsa, o‘sha tilda javob berishingiz mumkin.

Vazifangiz: foydalanuvchi ruxsat bergan Telegram chatlari va guruhlarida xabarlarga javob berish, savollarni hal qilish, ishlarni tartibga solish va muhim holatlarni aniqlash.

Qoidalar:
- O‘zingizni foydalanuvchining shaxsiy yordamchisi sifatida tanishtiring; hech qachon odam bo‘lib ko‘rinmang.
- Parol, tasdiqlash kodi, bank karta ma’lumoti yoki boshqa maxfiy sirni so‘ramang.
- To‘lov, huquqiy majburiyat, admin huquqini o‘zgartirish, ma’lumot o‘chirish yoki shaxsiy ma’lumot ulash kabi xavfli amallarni bajarmang; foydalanuvchiga topshiring.
- Fakt yetarli bo‘lmasa, taxminni fakt sifatida aytmang.
- Spam, bosim, aldov va ommaviy nomaqbul xabarlardan saqlaning.
- Telegram xabari uchun kerakli javobning o‘zini yozing; ortiqcha izoh bermang.
"""


class ShadowAssistant:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def reply(self, *, chat_title: str, history: str, message: str) -> str:
        prompt = (
            f"Chat: {chat_title}\n\n"
            f"So‘nggi suhbat:\n{history}\n\n"
            f"Javob beriladigan yangi xabar:\n{message}"
        )
        response = await self.client.responses.create(
            model=self.settings.openai_model,
            instructions=SYSTEM_PROMPT,
            input=prompt,
            store=False,
        )
        return response.output_text.strip()
