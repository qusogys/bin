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

# Текстовая модель по умолчанию
DEFAULT_TEXT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-lite"
)

# Модель генерации изображений по умолчанию
DEFAULT_IMAGE_MODEL = os.getenv(
    "GEMINI_IMAGE_MODEL",
    "gemini-3.1-flash-image"
)

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

    # Отдельная модель для текста
    "text_model": DEFAULT_TEXT_MODEL,

    # Отдельная модель для картинок
    "image_model": DEFAULT_IMAGE_MODEL,

    "prompt": DEFAULT_PROMPT,

    "enabled": DEFAULT_ENABLED,

    "history_limit": DEFAULT_HISTORY_LIMIT,
}


# ============================================================
# AVAILABLE MODELS
# ============================================================

# ВАЖНО:
# Здесь модели разделены.
#
# text_models -> только текстовые
# image_models -> только генерация изображений

TEXT_MODELS = {
    "gemini-3.1-flash-lite":
        "⚡ Gemini 3.1 Flash-Lite",

    "gemini-3.6-flash":
        "🚀 Gemini 3.6 Flash",
}


IMAGE_MODELS = {
    "gemini-3.1-flash-image":
        "🖼 Gemini 3.1 Flash Image",

    "gemini-3-pro-image":
        "🎨 Gemini 3 Pro Image",
}


# ============================================================
# BOT
# ============================================================

bot = Bot(BOT_TOKEN)

dp = Dispatcher()


# ============================================================
# STATES
# ============================================================

class SettingsState(StatesGroup):

    waiting_api_key = State()

    waiting_prompt = State()

    waiting_history_limit = State()


# ============================================================
# HISTORY
# ============================================================

# История отдельно для каждого чата.
#
# БД НЕ используется.
#
# После перезапуска Railway история очищается.

chat_histories: dict[
    int,
    list[dict[str, str]]
] = {}


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
                    text=(
                        "🤖 Текстовая модель\n"
                        f"{settings['text_model']}"
                    ),
                    callback_data="settings_text_model",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        "🖼 Модель изображений\n"
                        f"{settings['image_model']}"
                    ),
                    callback_data="settings_image_model",
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
                    text="🔄 Обновить",
                    callback_data="settings_refresh",
                )
            ],
        ]
    )


# ============================================================
# TEXT MODEL KEYBOARD
# ============================================================

def text_model_keyboard():

    buttons = []

    for model_id, model_name in TEXT_MODELS.items():

        selected = (
            " ✅"
            if model_id == settings["text_model"]
            else ""
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=model_name + selected,
                    callback_data=f"text_model:{model_id}",
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
# IMAGE MODEL KEYBOARD
# ============================================================

def image_model_keyboard():

    buttons = []

    for model_id, model_name in IMAGE_MODELS.items():

        selected = (
            " ✅"
            if model_id == settings["image_model"]
            else ""
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=model_name + selected,
                    callback_data=f"image_model:{model_id}",
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
            "с символом <code>%</code> в начале."
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
        "⚙️ <b>Глобальные настройки</b>\n\n"

        f"👑 Владелец: <code>{OWNER_ID}</code>\n\n"

        f"🔑 API key: {api_status}\n\n"

        "🤖 <b>Текстовая модель:</b>\n"
        f"<code>{settings['text_model']}</code>\n\n"

        "🖼 <b>Модель изображений:</b>\n"
        f"<code>{settings['image_model']}</code>\n\n"

        f"📡 <b>Режим:</b>\n{mode}\n\n"

        f"🧠 <b>История:</b> {history_text}\n\n"

        "🎭 <b>Роль:</b>\n"
        f"<blockquote>{escape_html(prompt)}</blockquote>\n\n"

        "🌍 Все настройки глобальные и "
        "распространяются на все чаты."
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
        "📷 анализировать фото\n"
        "🎤 анализировать аудио\n"
        "🎥 анализировать видео\n"
        "🖼 генерировать изображения\n"
        "🧠 помнить историю чата\n\n"

        "Для генерации изображения:\n"
        "<code>%нарисуй кота в космосе</code>\n\n"

        "Настройки владельца:\n"
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

        "API key будет использоваться "
        "во всех чатах.\n\n"

        "⚠️ Не отправляй ключ никому.\n\n"

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
                    config={
                        "page_size": 1
                    }
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
        "🎭 <b>Роль / system prompt</b>\n\n"

        "Отправь новый промт.\n\n"

        "Пример:\n"

        "<code>"
        "Ты профессиональный Python-разработчик. "
        "Отвечай подробно, но понятно."
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
            "❌ Промт должен быть текстом."
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
# TEXT MODEL MENU
# ============================================================

@dp.callback_query(
    F.data == "settings_text_model"
)
async def settings_text_model(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🤖 <b>Текстовая модель</b>\n\n"
        "Эта модель используется для обычных "
        "ответов и анализа фото/аудио/видео.\n\n"
        "Выбери модель:",

        reply_markup=text_model_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("text_model:")
)
async def select_text_model(
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

    if model_id not in TEXT_MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["text_model"] = model_id

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Текстовая модель изменена."
    )


# ============================================================
# IMAGE MODEL MENU
# ============================================================

@dp.callback_query(
    F.data == "settings_image_model"
)
async def settings_image_model(
    callback: CallbackQuery,
):

    if not is_owner(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🖼 <b>Модель изображений</b>\n\n"

        "Эта модель используется только "
        "для генерации картинок.\n\n"

        "Выбери модель:",

        reply_markup=image_model_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("image_model:")
)
async def select_image_model(
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

    if model_id not in IMAGE_MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["image_model"] = model_id

    await callback.message.edit_text(
        settings_text(),

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Модель изображений изменена."
    )


# ============================================================
# HISTORY SETTINGS
# ============================================================

@dp.callback_query(
    F.data == "settings_history"
)
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
        "5 — помнить ограниченное количество\n"
        "20 — больше контекста\n\n"

        f"Сейчас: <b>{settings['history_limit']}</b>\n\n"

        "Для отмены:\n"
        "/cancel",

        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_history_limit
)
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
            "❌ Нужно отправить целое число."
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
        f"✅ Лимит истории установлен: "
        f"<b>{limit}</b>",
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

    settings["enabled"] = not settings["enabled"]

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

        return (
            temp_dir,
            path,
            "image/jpeg",
        )

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

        return (
            temp_dir,
            path,
            "audio/ogg",
        )

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

        return (
            temp_dir,
            path,
            "video/mp4",
        )

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
# HISTORY → GEMINI CONTENTS
# ============================================================

def build_history_contents(
    chat_id: int,
    current_prompt: str,
):

    contents = []

    limit = settings["history_limit"]

    history = get_chat_history(chat_id)

    # 1 = полностью без памяти

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

    # 0 = вся история

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

    # --------------------------------------------------------
    # MEDIA
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TEXT MODEL
    # --------------------------------------------------------

    response = await asyncio.to_thread(
        client.models.generate_content,

        model=settings["text_model"],

        contents=contents,

        config=types.GenerateContentConfig(
            system_instruction=settings["prompt"]
        ),
    )

    return response.text or (
        "Gemini не вернул текстовый ответ."
    )


# ============================================================
# IMAGE REQUEST DETECTION
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

        "нарисовать",

        "сгенерировать картинку",

        "сгенерировать изображение",

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

        "нарисовать",

        "сгенерировать картинку",

        "сгенерировать изображение",

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


# ============================================================
# IMAGE GENERATION
# ============================================================

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

    # Используется ТЕКУЩАЯ выбранная
    # image-модель, а не text_model.

    response = await asyncio.to_thread(
        client.models.generate_content,

        model=settings["image_model"],

        contents=prompt,

        config=types.GenerateContentConfig(

            response_modalities=[
                "IMAGE"
            ],

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

            return (
                temp_dir,
                image_path,
            )

    raise RuntimeError(
        "Модель не вернула изображение."
    )


# ============================================================
# SHOULD ANSWER
# ============================================================

def should_answer(message: Message):

    # ВКЛ:
    # реагируем на всё.

    if settings["enabled"]:

        return True

    # ВЫКЛ:
    # только если начинается с %

    text = (
        message.text
        or message.caption
        or ""
    )

    return text.startswith("%")


# ============================================================
# GET USER PROMPT
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
# TRIM HISTORY
# ============================================================

def trim_history(chat_id: int):

    limit = settings["history_limit"]

    if limit == 0:

        return

    history = get_chat_history(chat_id)

    max_stored = max(
        limit - 1,
        0,
    )

    if max_stored == 0:

        history.clear()

    elif len(history) > max_stored:

        del history[
            :len(history) - max_stored
        ]


# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================

@dp.message()
async def all_messages(message: Message):

    # Команды не обрабатываем.

    if (
        message.text
        and message.text.startswith("/")
    ):

        return

    if (
        message.caption
        and message.caption.startswith("/")
    ):

        return

    # Проверяем режим.

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

        if (
            user_prompt
            and is_image_request(user_prompt)
        ):

            image_prompt = clean_image_prompt(
                user_prompt
            )

            if not image_prompt:

                await message.reply(
                    "🖼 Напиши, что нарисовать.\n\n"

                    "<code>"
                    "%нарисуй кота в космосе"
                    "</code>",

                    parse_mode="HTML",
                )

                return

            await bot.send_chat_action(
                chat_id=message.chat.id,
                action="upload_photo",
            )

            (
                generated_image_dir,
                image_path,
            ) = await generate_image(
                image_prompt
            )

            # Записываем запрос в историю.

            add_to_history(
                chat_id=message.chat.id,

                user_text=user_prompt,

                assistant_text=(
                    "[Сгенерировано изображение]"
                ),
            )

            trim_history(
                message.chat.id
            )

            await message.reply_photo(
                photo=FSInputFile(
                    image_path
                ),

                caption="🖼 Готово!",
            )

            return

        # ====================================================
        # NORMAL MESSAGE / MEDIA
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

        trim_history(
            message.chat.id
        )

        # ====================================================
        # SEND TEXT
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

            f"<code>"
            f"{escape_html(str(error)[:2500])}"
            f"</code>",

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
        "======================================"
    )

    print(
        "Gemini Telegram Bot started"
    )

    print(
        f"Owner ID: {OWNER_ID}"
    )

    print(
        f"Text model: "
        f"{settings['text_model']}"
    )

    print(
        f"Image model: "
        f"{settings['image_model']}"
    )

    print(
        f"Mode: "
        f"{settings['enabled']}"
    )

    print(
        f"History limit: "
        f"{settings['history_limit']}"
    )

    print(
        "======================================"
    )

    await dp.start_polling(
        bot,

        allowed_updates=(
            dp.resolve_used_update_types()
        ),
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())
