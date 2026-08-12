import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Единственный человек, который может менять настройки
OWNER_ID = 8904429775

# Модель по умолчанию
DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)

# API key можно сразу задать в Railway Variables.
# После этого его также можно поменять через /settings.
DEFAULT_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)

DEFAULT_PROMPT = os.getenv(
    "GEMINI_PROMPT",
    "Ты полезный ИИ-ассистент. "
    "Отвечай на языке пользователя. "
    "Будь полезным, точным и понятным."
)

# false = отвечать только на %
# true = отвечать на все сообщения
DEFAULT_ENABLED = (
    os.getenv("BOT_ENABLED", "false").lower()
    in ("true", "1", "yes", "on")
)

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN в Railway Variables."
    )


# ============================================================
# GLOBAL SETTINGS
# ============================================================

# Всё хранится в памяти.
#
# ВАЖНО:
# после перезапуска Railway значения снова возьмутся
# из Railway Variables.

settings = {
    "api_key": DEFAULT_API_KEY,
    "model": DEFAULT_MODEL,
    "prompt": DEFAULT_PROMPT,
    "enabled": DEFAULT_ENABLED,
}


# ============================================================
# AVAILABLE MODELS
# ============================================================

MODELS = {
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
}


# ============================================================
# BOT
# ============================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# FSM
# ============================================================

class SettingsState(StatesGroup):
    waiting_api_key = State()
    waiting_prompt = State()


# ============================================================
# ACCESS
# ============================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ============================================================
# SETTINGS KEYBOARD
# ============================================================

def settings_keyboard():

    api_status = (
        "✅ установлен"
        if settings["api_key"]
        else "❌ не установлен"
    )

    mode = (
        "🟢 ВКЛ"
        if settings["enabled"]
        else "🔴 ВЫКЛ"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔑 API key: {api_status}",
                    callback_data="settings_api",
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎭 Роль / промт",
                    callback_data="settings_prompt",
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"🤖 Модель: {settings['model']}",
                    callback_data="settings_model",
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"{mode} — режим ответа",
                    callback_data="settings_toggle",
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="settings_refresh",
                ),
            ],
        ]
    )


# ============================================================
# MODEL KEYBOARD
# ============================================================

def model_keyboard():

    buttons = []

    for model_id, model_name in MODELS.items():

        buttons.append(
            [
                InlineKeyboardButton(
                    text=model_name,
                    callback_data=f"model:{model_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="settings_back",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# SETTINGS TEXT
# ============================================================

def settings_text():

    api_status = (
        "✅ установлен"
        if settings["api_key"]
        else "❌ не установлен"
    )

    mode = (
        "🟢 <b>ВКЛ</b>\n"
        "Бот реагирует на все сообщения."
        if settings["enabled"]
        else
        "🔴 <b>ВЫКЛ</b>\n"
        "Бот реагирует только на сообщения, "
        "начинающиеся с <code>%</code>."
    )

    prompt = settings["prompt"]

    if len(prompt) > 1000:
        prompt = prompt[:1000] + "..."

    return (
        "⚙️ <b>Глобальные настройки Gemini</b>\n\n"

        f"👑 Владелец: <code>{OWNER_ID}</code>\n\n"

        f"🔑 API key: {api_status}\n"

        f"🤖 Модель: "
        f"<code>{settings['model']}</code>\n\n"

        f"📡 Режим:\n{mode}\n\n"

        "🎭 <b>Роль / system prompt:</b>\n"
        f"<blockquote>{prompt}</blockquote>\n\n"

        "🌍 Настройки общие для всех чатов."
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start(message: Message):

    await message.answer(
        "👋 <b>Gemini Telegram Bot</b>\n\n"

        "Я умею работать с:\n"
        "💬 текстом\n"
        "📷 фотографиями\n"
        "🎤 голосовыми\n"
        "🎵 аудио\n"
        "🎥 видео\n"
        "📄 файлами\n\n"

        "Для владельца доступна команда "
        "/settings.",

        parse_mode="HTML",
    )


# ============================================================
# SETTINGS
# ============================================================

@dp.message(Command("settings"))
async def settings_command(
    message: Message,
    state: FSMContext,
):

    if not is_owner(message.from_user.id):

        await message.answer(
            "⛔ У тебя нет доступа к настройкам."
        )

        return

    await state.clear()

    await message.answer(
        settings_text(),
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CANCEL
# ============================================================

@dp.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext,
):

    if not is_owner(message.from_user.id):
        return

    await state.clear()

    await message.answer(
        "❌ Действие отменено."
    )


# ============================================================
# API KEY
# ============================================================

@dp.callback_query(F.data == "settings_api")
async def settings_api(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_api_key
    )

    await callback.message.answer(
        "🔑 <b>Отправь Gemini API key.</b>\n\n"

        "Этот ключ будет использоваться "
        "во всех чатах.\n\n"

        "Для отмены используй:\n"
        "/cancel",

        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(SettingsState.waiting_api_key)
async def save_api_key(
    message: Message,
    state: FSMContext,
):

    if not is_owner(message.from_user.id):

        await state.clear()

        return

    if not message.text:

        await message.answer(
            "❌ Отправь API key текстом."
        )

        return

    api_key = message.text.strip()

    if len(api_key) < 10:

        await message.answer(
            "❌ API key выглядит неправильно."
        )

        return

    await message.answer(
        "⏳ Проверяю API key..."
    )

    try:

        client = genai.Client(
            api_key=api_key
        )

        # Проверяем, что ключ работает.
        await asyncio.to_thread(
            lambda: list(
                client.models.list(
                    config={
                        "page_size": 1
                    }
                )
            )
        )

    except Exception as error:

        await message.answer(
            "❌ API key не прошёл проверку.\n\n"
            f"<code>{str(error)[:2000]}</code>",

            parse_mode="HTML",
        )

        return

    settings["api_key"] = api_key

    await state.clear()

    await message.answer(
        "✅ Gemini API key установлен.\n\n"
        "Теперь он используется во всех чатах.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# PROMPT
# ============================================================

@dp.callback_query(F.data == "settings_prompt")
async def settings_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_prompt
    )

    await callback.message.answer(
        "🎭 <b>Настройка роли</b>\n\n"

        "Отправь новый system prompt.\n\n"

        "Например:\n\n"

        "<code>"
        "Ты профессиональный программист Python. "
        "Отвечай подробно, показывай рабочий код "
        "и объясняй ошибки."
        "</code>\n\n"

        "Для отмены:\n"
        "/cancel",

        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(SettingsState.waiting_prompt)
async def save_prompt(
    message: Message,
    state: FSMContext,
):

    if not is_owner(message.from_user.id):

        await state.clear()

        return

    if not message.text:

        await message.answer(
            "❌ Роль должна быть текстом."
        )

        return

    prompt = message.text.strip()

    if not prompt:

        await message.answer(
            "❌ Промт не может быть пустым."
        )

        return

    settings["prompt"] = prompt

    await state.clear()

    await message.answer(
        "✅ Роль успешно изменена.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# MODEL
# ============================================================

@dp.callback_query(F.data == "settings_model")
async def settings_model(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🤖 <b>Выбери модель:</b>",

        reply_markup=model_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("model:"))
async def select_model(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    model_id = callback.data.split(
        ":",
        1
    )[1]

    if model_id not in MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["model"] = model_id

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Модель изменена."
    )


# ============================================================
# TOGGLE
# ============================================================

@dp.callback_query(
    F.data == "settings_toggle"
)
async def settings_toggle(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    settings["enabled"] = (
        not settings["enabled"]
    )

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Режим изменён."
    )


# ============================================================
# REFRESH
# ============================================================

@dp.callback_query(
    F.data == "settings_refresh"
)
async def settings_refresh(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


# ============================================================
# BACK
# ============================================================

@dp.callback_query(
    F.data == "settings_back"
)
async def settings_back(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


# ============================================================
# DOWNLOAD TELEGRAM MEDIA
# ============================================================

async def download_media(message: Message):

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="gemini_bot_"
        )
    )

    # -------------------------
    # PHOTO
    # -------------------------

    if message.photo:

        file = await bot.get_file(
            message.photo[-1].file_id
        )

        path = temp_dir / "photo.jpg"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return (
            temp_dir,
            path,
            "image/jpeg",
        )

    # -------------------------
    # VOICE
    # -------------------------

    if message.voice:

        file = await bot.get_file(
            message.voice.file_id
        )

        path = temp_dir / "voice.ogg"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return (
            temp_dir,
            path,
            "audio/ogg",
        )

    # -------------------------
    # AUDIO
    # -------------------------

    if message.audio:

        file = await bot.get_file(
            message.audio.file_id
        )

        filename = (
            message.audio.file_name
            or "audio.mp3"
        )

        path = temp_dir / filename

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        mime_type = (
            message.audio.mime_type
            or "audio/mpeg"
        )

        return (
            temp_dir,
            path,
            mime_type,
        )

    # -------------------------
    # VIDEO
    # -------------------------

    if message.video:

        file = await bot.get_file(
            message.video.file_id
        )

        path = temp_dir / "video.mp4"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        mime_type = (
            message.video.mime_type
            or "video/mp4"
        )

        return (
            temp_dir,
            path,
            mime_type,
        )

    # -------------------------
    # VIDEO NOTE
    # -------------------------

    if message.video_note:

        file = await bot.get_file(
            message.video_note.file_id
        )

        path = temp_dir / "video_note.mp4"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return (
            temp_dir,
            path,
            "video/mp4",
        )

    # -------------------------
    # DOCUMENT
    # -------------------------

    if message.document:

        file = await bot.get_file(
            message.document.file_id
        )

        filename = (
            message.document.file_name
            or "document"
        )

        path = temp_dir / filename

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        mime_type = (
            message.document.mime_type
            or "application/octet-stream"
        )

        return (
            temp_dir,
            path,
            mime_type,
        )

    return (
        None,
        None,
        None,
    )


# ============================================================
# GEMINI
# ============================================================

async def ask_gemini(
    prompt: str,
    media_path=None,
    mime_type=None,
):

    if not settings["api_key"]:

        raise RuntimeError(
            "Gemini API key не установлен. "
            "Владелец должен открыть /settings."
        )

    client = genai.Client(
        api_key=settings["api_key"]
    )

    contents = []

    # Текст пользователя

    if prompt:
        contents.append(prompt)

    # Если текста нет,
    # но есть файл

    if not prompt and media_path:

        contents.append(
            "Проанализируй предоставленный файл."
        )

    # -------------------------
    # MEDIA
    # -------------------------

    if media_path:

        file_size = media_path.stat().st_size

        # Небольшие файлы
        # отправляем непосредственно.

        if file_size <= 20 * 1024 * 1024:

            data = await asyncio.to_thread(
                media_path.read_bytes
            )

            contents.append(
                types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type,
                )
            )

        # Большие файлы
        # загружаем через Gemini Files API.

        else:

            uploaded_file = await asyncio.to_thread(
                lambda: client.files.upload(
                    file=str(media_path),
                    config=types.UploadFileConfig(
                        mime_type=mime_type
                    ),
                )
            )

            contents.append(
                uploaded_file
            )

    if not contents:

        contents.append(
            "Ответь на запрос пользователя."
        )

    response = await asyncio.to_thread(
        client.models.generate_content,

        model=settings["model"],

        contents=contents,

        config=types.GenerateContentConfig(
            system_instruction=settings["prompt"]
        ),
    )

    return response.text or (
        "Gemini не вернул текстовый ответ."
    )


# ============================================================
# SHOULD BOT ANSWER?
# ============================================================

def should_answer(message: Message):

    # Глобальный режим ВКЛ:
    # отвечаем на всё.

    if settings["enabled"]:
        return True

    # Глобальный режим ВЫКЛ:
    # только %

    text = (
        message.text
        or message.caption
        or ""
    )

    return text.startswith("%")


# ============================================================
# REMOVE %
# ============================================================

def get_user_prompt(message: Message):

    text = (
        message.text
        or message.caption
        or ""
    )

    text = text.strip()

    if text.startswith("%"):

        text = text[1:].strip()

    return text


# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================

@dp.message()
async def all_messages(message: Message):

    # Не обрабатываем команды
    # как обычные запросы.

    if message.text and message.text.startswith("/"):
        return

    if (
        message.caption
        and message.caption.startswith("/")
    ):
        return

    # Проверяем глобальный режим.

    if not should_answer(message):
        return

    temp_dir = None

    try:

        user_prompt = get_user_prompt(
            message
        )

        # Скачиваем медиа,
        # если оно есть.

        (
            temp_dir,
            media_path,
            mime_type,
        ) = await download_media(
            message
        )

        # Если пользователь отправил
        # только медиа.

        if not user_prompt:

            user_prompt = (
                "Проанализируй предоставленный "
                "медиафайл. Опиши подробно, "
                "что на нём или в нём находится."
            )

        # Показываем typing.

        await bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
        )

        # Запрос Gemini.

        answer = await ask_gemini(
            prompt=user_prompt,
            media_path=media_path,
            mime_type=mime_type,
        )

        # Telegram позволяет максимум
        # 4096 символов в одном сообщении.

        for position in range(
            0,
            len(answer),
            4096,
        ):

            await message.reply(
                answer[
                    position:
                    position + 4096
                ]
            )

    except Exception as error:

        print(
            f"[ERROR] "
            f"chat={message.chat.id} "
            f"user={message.from_user.id}: "
            f"{error}"
        )

        await message.reply(
            "❌ Произошла ошибка:\n\n"
            f"<code>{escape_html(str(error)[:2500])}</code>",
            parse_mode="HTML",
        )

    finally:

        # Удаляем временные файлы.

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )


# ============================================================
# HTML ESCAPE
# ============================================================

def escape_html(text: str) -> str:

    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ============================================================
# START
# ============================================================

async def main():

    print(
        "================================="
    )

    print(
        "Gemini Telegram Bot started"
    )

    print(
        f"Owner ID: {OWNER_ID}"
    )

    print(
        f"Model: {settings['model']}"
    )

    print(
        f"Global mode: {settings['enabled']}"
    )

    print(
        "================================="
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
