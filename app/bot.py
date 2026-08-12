import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from .config import settings
from .db import DB
from .ai_client import AIClient
from .ocr import ocr_image
from io import BytesIO
from PIL import Image

bot = Bot(token=settings.TELEGRAM_TOKEN)
dp = Dispatcher()

db = DB(settings.DB_PATH)
ai = AIClient(settings)

# simple in-memory state for expecting the next message after an inline button
pending_actions = {}  # chat_id -> action string

@dp.message(Command('start'))
async def cmd_start(message: Message):
    db.ensure_chat(message.chat.id)
    await message.reply("Привет! Я AI-бот. Используй /settings для управления, /on и /off для включения/выключения бота в чате. /setprompt работает тоже.")

@dp.message(Command('setprompt'))
async def cmd_setprompt(message: Message):
    text = message.get_args()
    if not text:
        await message.reply('Использование: /setprompt ТВОЙ_ПРОМПТ')
        return
    db.set_prompt(message.chat.id, text)
    await message.reply('Промпт сохранён для этого чата.')

@dp.message(Command('on'))
async def cmd_on(message: Message):
    db.ensure_chat(message.chat.id)
    db.set_enabled(message.chat.id, True)
    await message.reply('Бот включён в этом чате.')

@dp.message(Command('off'))
async def cmd_off(message: Message):
    db.ensure_chat(message.chat.id)
    db.set_enabled(message.chat.id, False)
    await message.reply('Бот выключен в этом чате. Используйте /on чтобы включить.')

@dp.message(Command('settings'))
async def cmd_settings(message: Message):
    db.ensure_chat(message.chat.id)
    s = db.get_settings(message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Промпт', callback_data='settings:prompt')],
        [InlineKeyboardButton(text='Модель', callback_data='settings:model'), InlineKeyboardButton(text='Параметры', callback_data='settings:params')],
        [InlineKeyboardButton(text='Показать настройки', callback_data='settings:show')],
        [InlineKeyboardButton(text='Сбросить в дефолт', callback_data='settings:reset')]
    ])
    enabled_text = 'Включён' if s.get('enabled') else 'Выключен'
    await message.reply(f"Настройки для этого чата:\nПромпт: {s.get('prompt') or '<не задан>'}\nМодель: {s.get('model') or settings.GEMINI_MODEL_CHAT}\nТемпература: {s.get('temperature')}\nРазмер картинки: {s.get('image_size')}\nСтатус: {enabled_text}", reply_markup=kb)

@dp.callback_query()
async def callback_settings(cb: CallbackQuery):
    data = cb.data or ''
    chat_id = cb.message.chat.id
    if not data.startswith('settings:'):
        await cb.answer()
        return
    cmd = data.split(':', 1)[1]
    if cmd == 'prompt':
        pending_actions[chat_id] = 'set_prompt'
        await cb.message.reply('Отправь новый системный промпт для этого чата. Отправь /cancel чтобы отменить.')
        await cb.answer()
    elif cmd == 'model':
        pending_actions[chat_id] = 'set_model'
        await cb.message.reply('Отправь имя модели (например gemini-pro или оставь пустым для дефолта).')
        await cb.answer()
    elif cmd == 'params':
        pending_actions[chat_id] = 'set_params'
        await cb.message.reply('Отправь параметры в формате: temperature=0.7 image_size=1024x1024 history_length=10\nПример: temperature=0.5 image_size=512x512')
        await cb.answer()
    elif cmd == 'show':
        s = db.get_settings(chat_id)
        enabled_text = 'Включён' if s.get('enabled') else 'Выключен'
        await cb.message.reply(f"Текущие настройки:\nПромпт: {s.get('prompt') or '<не задан>'}\nМодель: {s.get('model') or settings.GEMINI_MODEL_CHAT}\nТемпература: {s.get('temperature')}\nРазмер картинки: {s.get('image_size')}\nИстория: {s.get('history_length')}\nСтатус: {enabled_text}")
        await cb.answer()
    elif cmd == 'reset':
        db.reset_settings(chat_id)
        await cb.message.reply('Настройки сброшены в дефолт для этого чата.')
        await cb.answer()
    else:
        await cb.answer()

@dp.message(Command('cancel'))
async def cmd_cancel(message: Message):
    chat_id = message.chat.id
    if chat_id in pending_actions:
        pending_actions.pop(chat_id)
        await message.reply('Действие отменено.')
    else:
        await message.reply('Нет активного действия.')

@dp.message()
async def handle_message(message: Message):
    chat_id = message.chat.id
    db.ensure_chat(chat_id)

    # If user is in pending action flow
    if chat_id in pending_actions:
        action = pending_actions.pop(chat_id)
        text = message.text or ''
        if action == 'set_prompt':
            db.set_prompt(chat_id, text)
            await message.reply('Промпт сохранён.')
            return
        if action == 'set_model':
            db.set_setting(chat_id, 'model', text.strip() or None)
            await message.reply(f'Модель установлена: {text or "(дефолт)"}')
            return
        if action == 'set_params':
            # parse simple key=value pairs
            parts = text.split()
            changed = []
            for p in parts:
                if '=' not in p:
                    continue
                k, v = p.split('=', 1)
                k = k.strip()
                v = v.strip()
                if k == 'temperature':
                    try:
                        db.set_setting(chat_id, 'temperature', float(v))
                        changed.append('temperature')
                    except:
                        pass
                elif k == 'image_size':
                    db.set_setting(chat_id, 'image_size', v)
                    changed.append('image_size')
                elif k == 'history_length':
                    try:
                        db.set_setting(chat_id, 'history_length', int(v))
                        changed.append('history_length')
                    except:
                        pass
            await message.reply('Параметры обновлены: ' + (', '.join(changed) if changed else 'ничего не изменено'))
            return

    # If bot is disabled in this chat, ignore non-command messages
    enabled = db.get_setting(chat_id, 'enabled')
    if not enabled:
        # allow commands like /on and /settings
        if message.text and message.text.startswith('/'):
            pass
        else:
            return

    # If message has photo -> OCR
    if message.photo and settings.OCR_ENABLED:
        await message.reply('Получил фото — запускаю OCR...')
        file = await bot.get_file(message.photo[-1].file_id)
        bio = BytesIO()
        await file.download_to(bio)
        bio.seek(0)
        text = ocr_image(bio)
        await message.reply(f'Распознанный текст:\n{text}')
        return

    # Chat flow
    if message.text:
        db.add_message(chat_id, 'user', message.text)
        prompt = db.get_prompt(chat_id) or settings.BOT_DEFAULT_PROMPT
        history = db.get_history(chat_id)
        # read per-chat model/temperature
        model = db.get_setting(chat_id, 'model') or settings.GEMINI_MODEL_CHAT
        temperature = db.get_setting(chat_id, 'temperature') or settings.DEFAULT_TEMPERATURE
        reply = ai.chat_reply(system_prompt=prompt, history=history, model=model, temperature=temperature)
        if reply:
            db.add_message(chat_id, 'assistant', reply)
            await message.reply(reply)
        else:
            await message.reply('Ошибка от AI. Попробуй позже.')


def run_bot():
    from aiogram import executor
    executor.start_polling(dp, bot=bot)
