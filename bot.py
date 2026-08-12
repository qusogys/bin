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
    FSInputFile,
)

from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = 8904429775

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)

IMAGE_MODEL = "gemini-3.1-flash-image"

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

DEFAULT_ENABLED = (
    os.getenv("BOT_ENABLED", "false").lower()
    in ("true", "1", "yes", "on")
)

DEFAULT_HISTORY_LIMIT = int(
    os.getenv("HISTORY_LIMIT", "10")
)

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN в Railway Variables."
    )


# ============================================================
# GLOBAL SETTINGS
# ============================================================

settings = {
    "api_key": DEFAULT_API_KEY,
    "model": DEFAULT_MODEL,
    "prompt": DEFAULT_PROMPT,
    "enabled": DEFAULT_ENABLED,
    "history_limit": DEFAULT_HISTORY_LIMIT,
}


# ============================================================
# CHAT HISTORY
# ============================================================

chat_histories: dict[int, list[dict[str, str]]] = {}


def get_chat_history(chat_id: int):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    return chat_histories[chat_id]


def clear_chat_history(chat_id: int):
    chat_histories[chat_id] = []


def add_to_history(
    chat_id: int,
    user_text: str,
    assistant_text: str,
):
    history = get_chat_history(chat_id)

    history.append(
        {
            "role": "user",
            "text": user_text,
        }
    )

    history.append(
        {
            "role": "model",
            "text": assistant_text,
        }
    )


# ============================================================
# MODELS
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
    waiting_history_limit = State()


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

    history_limit = settings["history_limit"]

    if history_limit == 0:
        history_text = "♾ без лимита"
    elif history_limit == 1:
        history_text = "1 — без памяти"
    else:
        history_text = str(history_limit)

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
                    text=f"🧠 История: {history_text}",
                    callback_data="settings_history",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Очистить историю",
                    callback_data="settings_clear_history",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Генерация фото: ВКЛ",
                    callback_data="image_info",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="settings_refresh",
                )
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

    if settings["enabled"]:
        mode = (
            "🟢 <b>ВКЛ</b>\n"
            "Бот реагирует на все сообщения."
        )
    else:
        mode = (
            "🔴 <b>ВЫКЛ</b>\n"
            "Бот реагирует только на сообщения "
            "с <code>%</code>."
        )

    history_limit = settings["history_limit"]

    if history_limit == 0:
        history_text = "♾ <b>Без лимита</b>"
    elif history_limit == 1:
        history_text = "1 — <b>без памяти</b>"
    else:
        history_text = (
            f"<b>{history_limit}</b> сообщений"
        )

    prompt = settings["prompt"]

    if len(prompt) > 1000:
        prompt = prompt[:1000] + "..."

    return (
        "⚙️ <b>Глобальные настройки Gemini</b>\n\n"

        f"👑 Владелец: <code>{OWNER_ID}</code>\n\n"

        f"🔑 API key: {api_status}\n"

        f"🤖 Модель: <code>{settings['model']}</code>\n\n"

        f"📡 Режим:\n{mode}\n\n"

        f"🧠 История: {history_text}\n\n"

        "🖼 <b>Генерация изображений:</b> "
        "доступна\n"
        f"🎨 Image model: <code>{IMAGE_MODEL}</code>\n\n"

        "🎭 <b>Роль:</b>\n"
        f"<blockquote>{prompt}</blockquote>\n\n"

        "🌍 Настройки общие для всех чатов."
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_command(message: Message):

    await message.answer(
        "👋 <b>Gemini Telegram Bot</b>\n\n"

        "Я умею:\n"
        "💬 отвечать на текст\n"
        "📷 анализировать фотографии\n"
        "🎤 анализировать аудио\n"
        "🎥 анализировать видео\n"
        "🖼 генерировать изображения\n"
        "🧠 помнить историю чата\n\n"

        "Генерация изображения:\n"
        "<code>%нарисуй кота в космосе</code>\n\n"

        "Для владельца:\n"
        "<code>/settings</code>",

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
        "Ключ используется во всех чатах.\n\n"
        "Для отмены:\n"
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

        await asyncio.to_thread(
            lambda: list(
                client.models.list(
                    config={"page_size": 1}
                )
            )
        )

    except Exception as error:

        await message.answer(
            "❌ API key не прошёл проверку.\n\n"
            f"<code>{escape_html(str(error)[:2000])}</code>",
            parse_mode="HTML",
        )

        return

    settings["api_key"] = api_key

    await state.clear()

    await message.answer(
        "✅ Gemini API key установлен.",
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
        "🎭 <b>Новая роль / system prompt</b>\n\n"

        "Отправь текст роли.\n\n"

        "Например:\n"
        "<code>"
        "Ты профессиональный программист Python. "
        "Отвечай понятно и подробно."
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
        "✅ Роль изменена.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# HISTORY LIMIT
# ============================================================

@dp.callback_query(F.data == "settings_history")
async def settings_history(
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
        SettingsState.waiting_history_limit
    )

    await callback.message.answer(
        "🧠 <b>Лимит истории</b>\n\n"

        "Отправь число:\n\n"

        "0 — без лимита\n"
        "1 — ничего не помнить\n"
        "5 — ограниченная память\n"
        "20 — большая память\n\n"

        f"Сейчас: <b>{settings['history_limit']}</b>\n\n"

        "Для отмены:\n"
        "/cancel",

        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(SettingsState.waiting_history_limit)
async def save_history_limit(
    message: Message,
    state: FSMContext,
):

    if not is_owner(message.from_user.id):

        await state.clear()
        return

    try:

        limit = int(
            message.text.strip()
        )

    except (ValueError, AttributeError):

        await message.answer(
            "❌ Отправь целое число."
        )

        return

    if limit < 0:

        await message.answer(
            "❌ Минимальное значение — 0."
        )

        return

    settings["history_limit"] = limit

    await state.clear()

    await message.answer(
        f"✅ Лимит истории: <b>{limit}</b>",
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CLEAR HISTORY
# ============================================================

@dp.callback_query(
    F.data == "settings_clear_history"
)
async def settings_clear_history(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    clear_chat_history(
        callback.message.chat.id
    )

    await callback.answer(
        "🗑 История этого чата очищена.",
        show_alert=True,
    )


# ============================================================
# IMAGE INFO
# ============================================================

@dp.callback_query(F.data == "image_info")
async def image_info(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.answer(
        "🖼 Генерация включена. "
        "Используй %нарисуй ...",
        show_alert=True,
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
        "🤖 <b>Выбери модель для обычных ответов:</b>",
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

@dp.callback_query(F.data == "settings_toggle")
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

@dp.callback_query(F.data == "settings_refresh")
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

@dp.callback_query(F.data == "settings_back")
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
# MEDIA DOWNLOAD
# ============================================================

async def download_media(message: Message):

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="gemini_bot_"
        )
    )

    # PHOTO

    if message.photo:

        file = await bot.get_file(
            message.photo[-1].file_id
        )

        path = temp_dir / "photo.jpg"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return temp_dir, path, "image/jpeg"

    # VOICE

    if message.voice:

        file = await bot.get_file(
            message.voice.file_id
        )

        path = temp_dir / "voice.ogg"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return temp_dir, path, "audio/ogg"

    # AUDIO

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

        return (
            temp_dir,
            path,
            message.audio.mime_type
            or "audio/mpeg",
        )

    # VIDEO

    if message.video:

        file = await bot.get_file(
            message.video.file_id
        )

        path = temp_dir / "video.mp4"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return (
            temp_dir,
            path,
            message.video.mime_type
            or "video/mp4",
        )

    # VIDEO NOTE

    if message.video_note:

        file = await bot.get_file(
            message.video_note.file_id
        )

        path = temp_dir / "video_note.mp4"

        await bot.download_file(
            file.file_path,
            destination=path,
        )

        return temp_dir, path, "video/mp4"

    # DOCUMENT

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

        return (
            temp_dir,
            path,
            message.document.mime_type
            or "application/octet-stream",
        )

    return None, None, None


# ============================================================
# HISTORY FOR GEMINI
# ============================================================

def build_history_contents(
    chat_id: int,
    current_prompt: str,
):

    contents = []

    limit = settings["history_limit"]

    history = get_chat_history(chat_id)

    if limit == 1:

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=current_prompt
                    )
                ],
            )
        )

        return contents

    if limit == 0:

        selected_history = history

    else:

        old_messages_count = max(
            limit - 1,
            0,
        )

        if old_messages_count:
            selected_history = history[
                -old_messages_count:
            ]
        else:
            selected_history = []

    for item in selected_history:

        contents.append(
            types.Content(
                role=item["role"],
                parts=[
                    types.Part.from_text(
                        text=item["text"]
                    )
                ],
            )
        )

    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=current_prompt
                )
            ],
        )
    )

    return contents


# ============================================================
# NORMAL GEMINI
# ============================================================

async def ask_gemini(
    chat_id: int,
    prompt: str,
    media_path=None,
    mime_type=None,
):

    if not settings["api_key"]:

        raise RuntimeError(
            "Gemini API key не установлен.\n\n"
            "Открой /settings → 🔑 API key."
        )

    client = genai.Client(
        api_key=settings["api_key"]
    )

    contents = build_history_contents(
        chat_id,
        prompt,
    )

    if media_path:

        file_size = media_path.stat().st_size

        if file_size <= 20 * 1024 * 1024:

            data = await asyncio.to_thread(
                media_path.read_bytes
            )

            contents[-1].parts.append(
                types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type,
                )
            )

        else:

            uploaded_file = await asyncio.to_thread(
                lambda: client.files.upload(
                    file=str(media_path),
                    config=types.UploadFileConfig(
                        mime_type=mime_type
                    ),
                )
            )

            contents[-1].parts.append(
                uploaded_file
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
# IMAGE GENERATION
# ============================================================

def is_image_request(text: str) -> bool:

    text = text.lower().strip()

    image_words = [
        "нарисуй",
        "сгенерируй фото",
        "сгенерируй картинку",
        "сгенерируй изображение",
        "создай фото",
        "создай картинку",
        "создай изображение",
        "сделай фото",
        "сделай картинку",
        "сделай изображение",
        "изобрази",
        "покажи картинкой",
        "generate image",
        "generate a picture",
        "create image",
        "create a picture",
        "draw",
    ]

    return any(
        text.startswith(word)
        for word in image_words
    )


def clean_image_prompt(text: str) -> str:

    prefixes = [
        "нарисуй",
        "сгенерируй фото",
        "сгенерируй картинку",
        "сгенерируй изображение",
        "создай фото",
        "создай картинку",
        "создай изображение",
        "сделай фото",
        "сделай картинку",
        "сделай изображение",
        "изобрази",
        "покажи картинкой",
        "generate image",
        "generate a picture",
        "create image",
        "create a picture",
        "draw",
    ]

    result = text.strip()

    lower = result.lower()

    for prefix in prefixes:

        if lower.startswith(prefix):

            result = result[
                len(prefix):
            ].strip()

            break

    return result


async def generate_image(
    prompt: str,
):

    if not settings["api_key"]:

        raise RuntimeError(
            "Gemini API key не установлен.\n\n"
            "Открой /settings → 🔑 API key."
        )

    client = genai.Client(
        api_key=settings["api_key"]
    )

    response = await asyncio.to_thread(
        client.models.generate_content,

        model=IMAGE_MODEL,

        contents=prompt,

        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            response_format={
                "image": {
                    "aspect_ratio": "1:1",
                    "image_size": "1K",
                }
            },
        ),
    )

    for part in response.parts:

        if part.inline_data is not None:

            image = part.as_image()

            temp_dir = Path(
                tempfile.mkdtemp(
                    prefix="gemini_image_"
                )
            )

            image_path = (
                temp_dir / "generated.png"
            )

            image.save(image_path)

            return temp_dir, image_path

    raise RuntimeError(
        "Gemini не вернул изображение."
    )


# ============================================================
# SHOULD ANSWER
# ============================================================

def should_answer(message: Message):

    if settings["enabled"]:
        return True

    text = (
        message.text
        or message.caption
        or ""
    )

    return text.startswith("%")


# ============================================================
# USER PROMPT
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

    # Не обрабатываем команды.

    if message.text and message.text.startswith("/"):
        return

    if (
        message.caption
        and message.caption.startswith("/")
    ):
        return

    if not should_answer(message):
        return

    temp_dir = None
    generated_image_dir = None

    try:

        user_prompt = get_user_prompt(
            message
        )

        # ====================================================
        # IMAGE GENERATION
        # ====================================================

        if user_prompt and is_image_request(
            user_prompt
        ):

            image_prompt = clean_image_prompt(
                user_prompt
            )

            if not image_prompt:

                await message.reply(
                    "🖼 Напиши, что именно нарисовать.\n\n"
                    "Например:\n"
                    "<code>%нарисуй кота в космосе</code>",
                    parse_mode="HTML",
                )

                return

            await bot.send_chat_action(
                chat_id=message.chat.id,
                action="upload_photo",
            )

            generated_image_dir, image_path = (
                await generate_image(
                    image_prompt
                )
            )

            # Сохраняем факт запроса
            # в историю.

            add_to_history(
                chat_id=message.chat.id,
                user_text=user_prompt,
                assistant_text="[Сгенерировано изображение]",
            )

            # Ограничиваем историю.

            limit = settings["history_limit"]

            if limit > 0:

                max_stored = max(
                    limit - 1,
                    0,
                )

                history = get_chat_history(
                    message.chat.id
                )

                if max_stored == 0:

                    history.clear()

                elif len(history) > max_stored:

                    del history[
                        :len(history) - max_stored
                    ]

            await message.reply_photo(
                photo=FSInputFile(
                    image_path
                ),
                caption="🖼 Готово!",
            )

            return

        # ====================================================
        # NORMAL MEDIA
        # ====================================================

        (
            temp_dir,
            media_path,
            mime_type,
        ) = await download_media(
            message
        )

        if not user_prompt:

            user_prompt = (
                "Проанализируй предоставленный "
                "медиафайл и подробно опиши "
                "результат."
            )

        await bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
        )

        answer = await ask_gemini(
            chat_id=message.chat.id,
            prompt=user_prompt,
            media_path=media_path,
            mime_type=mime_type,
        )

        # ====================================================
        # SAVE HISTORY
        # ====================================================

        add_to_history(
            chat_id=message.chat.id,
            user_text=user_prompt,
            assistant_text=answer,
        )

        limit = settings["history_limit"]

        if limit > 0:

            max_stored = max(
                limit - 1,
                0,
            )

            history = get_chat_history(
                message.chat.id
            )

            if max_stored == 0:

                history.clear()

            elif len(history) > max_stored:

                del history[
                    :len(history) - max_stored
                ]

        # ====================================================
        # SEND RESPONSE
        # ====================================================

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

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

        if generated_image_dir:

            shutil.rmtree(
                generated_image_dir,
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
# MAIN
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
        f"Text model: {settings['model']}"
    )

    print(
        f"Image model: {IMAGE_MODEL}"
    )

    print(
        f"Global mode: {settings['enabled']}"
    )

    print(
        f"History limit: "
        f"{settings['history_limit']}"
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
