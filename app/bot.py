import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from .config import settings
from .db import DB
from .ai_client import AIClient
from .ocr import ocr_image
from io import BytesIO
from PIL import Image

bot = Bot(token=settings.TELEGRAM_TOKEN)
dp = Dispatcher()

db = DB(settings.DB_PATH)
ai = AIClient(settings.OPENAI_API_KEY)

@dp.message(Command('start'))
async def cmd_start(message: Message):
    db.ensure_chat(message.chat.id)
    await message.reply("Привет! Я AI-бот. Используй /setprompt, /settings, /image <описание> или просто отправь сообщение для чата. Пришли фото — я попробую прочитать текст (OCR).")

@dp.message(Command('setprompt'))
async def cmd_setprompt(message: Message):
    text = message.get_args()
    if not text:
        await message.reply('Использование: /setprompt ТВОЙ_ПРОМПТ')
        return
    db.set_prompt(message.chat.id, text)
    await message.reply('Промпт сохранён для этого чата.')

@dp.message(Command('settings'))
async def cmd_settings(message: Message):
    s = db.get_settings(message.chat.id)
    await message.reply(f"Настройки:\n{ s }")

@dp.message(Command('image'))
async def cmd_image(message: Message):
    prompt = message.get_args()
    if not prompt:
        await message.reply('Использование: /image ОПИСАНИЕ')
        return
    await message.reply('Генерирую изображение...')
    img_bytes = ai.generate_image(prompt, size=settings.IMAGE_DEFAULT_SIZE)
    if img_bytes:
        await message.reply_photo(img_bytes)
    else:
        await message.reply('Ошибка при генерации изображения.')

@dp.message()
async def echo_message(message: Message):
    # If message has photo -> OCR
    if message.photo:
        await message.reply('Получил фото — запускаю OCR...')
        file = await bot.get_file(message.photo[-1].file_id)
        bio = BytesIO()
        await file.download_to(bio)
        bio.seek(0)
        text = ocr_image(bio)
        await message.reply(f'Распознанный текст:\n{text}')
        return

    # Chat flow
    db.add_message(message.chat.id, 'user', message.text)
    prompt = db.get_prompt(message.chat.id)
    history = db.get_history(message.chat.id)
    reply = ai.chat_reply(prompt, history, temperature=db.get_setting(message.chat.id, 'temperature') or settings.DEFAULT_TEMPERATURE)
    if reply:
        db.add_message(message.chat.id, 'assistant', reply)
        await message.reply(reply)
    else:
        await message.reply('Ошибка от AI. Попробуй позже.')


def run_bot():
    from aiogram import executor
    executor.start_polling(dp, bot=bot)
