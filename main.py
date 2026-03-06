import os
import time
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
from openai import OpenAI

# Импорт твоей базы данных
from database import init_db, get_user, spend_request, add_balance, save_pending_response, get_pending_response

load_dotenv()
init_db()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher()

# Токен платежей из BotFather (для теста выбери любую платежку в режиме Test)
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")

class Form(StatesGroup):
    waiting_for_search = State()

user_timers = {}   

# ТВОЙ ПРОМПТ (БЕЗ ИЗМЕНЕНИЙ)
SYSTEM_PROMPT = """
Ты — ведущий российский автоадвокат. Твоя цель: минимизировать риск штрафа или лишения прав.

ПРАВИЛА СТРУКТУРЫ ОТВЕТА:
1. До маркера ===PAYWALL=== пиши ТОЛЬКО:
   - СТАТЬЯ: Номер статьи КоАП и какое наказание предусмотрено (штраф/лишение).

2. После маркера ===PAYWALL=== пиши детальную тактику:
   - СЛАБОЕ МЕСТО: Юридические нюансы, на чем можно поймать инспектора в этой ситуации.
   - ЦИТАТА ДЛЯ ИНСПЕКТОРА: Короткая фраза, которую водитель должен сказать прямо сейчас.
   - ИНСТРУКЦИЯ К ПРОТОКОЛУ: Что именно написать в графе "объяснения лица", чтобы потом оспорить дело.

Тон: Профессиональный, без воды.
"""

# --- КЛАВИАТУРЫ ---

def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🚀 ПОМОЩЬ (Голос/Фото)")
    builder.button(text="🔍 Найти ситуацию (Текст)")
    builder.button(text="⚖️ БАЗА SOS")
    builder.button(text="💎 Баланс / Купить") # Обновлено
    builder.button(text="⏱ Запустить таймер")
    builder.adjust(1) 
    return builder.as_markup(resize_keyboard=True)

def get_sos_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎥 Про видеосъемку")
    builder.button(text="🚗 Выходить из машины?")
    builder.button(text="📂 Досмотр vs Осмотр")
    builder.button(text="🔙 Назад в меню")
    builder.adjust(2) 
    return builder.as_markup(resize_keyboard=True)

# --- БЛОК ОПЛАТЫ ---

@dp.message(F.text == "💎 Баланс / Купить")
async def show_balance_buy(message: types.Message):
    balance, _ = get_user(message.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Пополнить (5 запросов - 290₽)", callback_data="buy_pack_5")
    await message.answer(f"👤 **Ваш кошелек:**\nДоступно запросов: **{balance}**", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "buy_pack_5")
async def checkout(callback: types.CallbackQuery):
    await bot.send_invoice(
        callback.from_user.id,
        title="Пакет запросов 'Защита'",
        description="5 полных консультаций по фото, голосу или тексту.",
        provider_token=PAYMENT_TOKEN,
        currency="rub",
        prices=[types.LabeledPrice(label="5 запросов", amount=29000)], # В копейках
        payload="5_req",
        start_parameter="pay"
    )

@dp.pre_checkout_query()
async def pre_checkout_query(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    add_balance(message.from_user.id, 5)
    await message.answer("✅ Оплата прошла успешно! Вам начислено 5 запросов.")

# --- ОБРАБОТЧИКИ ФОТО (VISION) ---

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    balance, _ = get_user(user_id)
    if balance <= 0:
        await message.answer("❌ Недостаточно запросов для анализа фото.")
        return

    status_msg = await message.answer("📸 Сканирую документ...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_TOKEN')}/{file.file_path}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": "Это фото протокола или документа ГИБДД. Найди ошибки, слабые места и дай совет по защите."},
                    {"type": "image_url", "image_url": {"url": file_url}}
                ]}
            ]
        )
        spend_request(user_id)
        await status_msg.edit_text(f"⚖️ **АНАЛИЗ ФОТО:**\n\n{response.choices[0].message.content}")
    except Exception:
        await status_msg.edit_text("❌ Ошибка при чтении фото. Убедитесь, что текст четкий.")

# --- ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (ТВОЯ ЛОГИКА) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user(message.from_user.id)
    welcome_text = (
        f"👋 Приветствую, {message.from_user.first_name}!\n\n"
        "Я — твой цифровой авто-юрист. База SOS доступна всегда, а глубокий анализ тактики требует баланса."
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())

@dp.message(F.text == "🚀 ПОМОЩЬ (Голос/Фото)")
async def voice_help_info(message: types.Message):
    await message.answer(
        "🎙 **Режим экстренной помощи активен!**\n\n"
        "Отправьте мне **голосовое сообщение** или **фотографию протокола**.\n"
        "Я мгновенно дам тактику защиты.\n\n"
        "⚠️ *Анализ списывает 1 запрос автоматически.*"
    )

@dp.message(F.text == "⏱ Запустить таймер")
async def start_timer(message: types.Message):
    user_id = message.from_user.id
    user_timers[user_id] = time.time()
    await message.answer("⏱ **Таймер запущен!**")

@dp.message(F.text.contains("Сколько я стою"))
async def check_timer(message: types.Message):
    user_id = message.from_user.id
    start_time = user_timers.get(user_id)
    if start_time:
        elapsed = time.time() - start_time
        await message.answer(f"⏳ Время контакта:\n**{int(elapsed // 60)} мин. {int(elapsed % 60)} сек.**")
    else:
        await message.answer("❌ Таймер не запущен.")

@dp.message(F.text == "🔍 Найти ситуацию (Текст)")
async def search_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Опишите кратко нарушение.")
    await state.set_state(Form.waiting_for_search)

@dp.message(Form.waiting_for_search)
async def process_search(message: types.Message, state: FSMContext):
    await state.clear()
    status_msg = await message.answer("🧠 Анализирую...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message.text}]
        )
        parts = response.choices[0].message.content.split("===PAYWALL===")
        free_text, pay_text = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "Готовлю тактику...")
        
        save_pending_response(message.from_user.id, free_text, pay_text) # БД КЭШ

        inline_kb = InlineKeyboardBuilder()
        inline_kb.button(text="🔓 Открыть тактику (1 запрос)", callback_data="unlock_info")
        await status_msg.edit_text(f"📋 **ИНФОРМАЦИЯ:**\n{free_text}", reply_markup=inline_kb.as_markup())
    except Exception:
        await status_msg.edit_text("❌ Ошибка поиска.")

@dp.callback_query(F.data == "unlock_info")
async def unlock_paywall(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance, _ = get_user(user_id)
    if balance <= 0:
        await callback.answer("У вас закончились запросы!", show_alert=True)
        return

    pay_text = get_pending_response(user_id) # Достаем из БД
    if pay_text:
        spend_request(user_id)
        await callback.message.answer(f"🔐 **ТАКТИКА ЗАЩИТЫ:**\n\n{pay_text}")
    else:
        await callback.answer("Данные устарели, повторите поиск.")
    await callback.answer()

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id
    balance, _ = get_user(user_id)
    if balance <= 0:
        await message.answer("❌ Недостаточно запросов.")
        return

    status_msg = await message.answer("🎙 Слушаю...")
    file_path = f"voice_{user_id}.ogg"
    try:
        await bot.download(message.voice, destination=file_path)
        with open(file_path, "rb") as audio:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": transcript.text}]
        )
        spend_request(user_id)
        full_response = response.choices[0].message.content.replace("===PAYWALL===", "\n\n**ТАКТИКА:**\n")
        await status_msg.edit_text(f"🎤 **Распознано:** _{transcript.text}_\n\n⚖️ **АНАЛИЗ:**\n{full_response}")
    except Exception:
        await status_msg.edit_text("❌ Ошибка звука.")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

@dp.message(F.text == "⚖️ БАЗА SOS")
async def show_sos(message: types.Message):
    await message.answer("🆘 **БЕСПЛАТНЫЕ СОВЕТЫ:**", reply_markup=get_sos_menu())

@dp.message(F.text == "🎥 Про видеосъемку")
async def rights_video(message: types.Message):
    await message.answer("🎥 Снимать можно по ст. 8 ФЗ «О полиции».")

@dp.message(F.text == "🔙 Назад в меню")
async def back_home(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_menu())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())