import asyncio
import os
import shutil
import tempfile
import html
from pathlib import Path

import aiohttp

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

from huggingface_hub import InferenceClient


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = 8904429775

DEFAULT_TEXT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-lite"
)

DEFAULT_IMAGE_MODEL = os.getenv(
    "IMAGE_MODEL",
    "flux"
)

DEFAULT_VIDEO_MODEL = os.getenv(
    "VIDEO_MODEL",
    "Wan-AI/Wan2.2-TI2V-5B"
)

DEFAULT_PROMPT = os.getenv(
    "GEMINI_PROMPT",
    "Ты полезный ИИ-ассистент. "
    "Отвечай на языке пользователя. "
    "Отвечай точно и понятно."
)

DEFAULT_HISTORY_LIMIT = int(
    os.getenv("HISTORY_LIMIT", "10")
)

DEFAULT_ENABLED = (
    os.getenv("BOT_ENABLED", "false").lower()
    in ("true", "1", "yes", "on")
)

DEFAULT_GEMINI_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)

DEFAULT_HF_TOKEN = os.getenv(
    "HF_TOKEN",
    ""
)

DEFAULT_POLLINATIONS_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
)

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не установлен."
    )


# ============================================================
# GLOBAL SETTINGS
# ============================================================

settings = {
    "gemini_api_key": DEFAULT_GEMINI_KEY,

    "hf_token": DEFAULT_HF_TOKEN,

    "pollinations_api_key":
        DEFAULT_POLLINATIONS_KEY,

    "text_model": DEFAULT_TEXT_MODEL,

    "image_model": DEFAULT_IMAGE_MODEL,

    "video_model": DEFAULT_VIDEO_MODEL,

    "prompt": DEFAULT_PROMPT,

    "enabled": DEFAULT_ENABLED,

    "history_limit": DEFAULT_HISTORY_LIMIT,
}


# ============================================================
# MODELS
# ============================================================

TEXT_MODELS = {
    "gemini-3.1-flash-lite":
        "⚡ Gemini 3.1 Flash-Lite",

    "gemini-3.6-flash":
        "🚀 Gemini 3.6 Flash",
}


IMAGE_MODELS = {
    "flux":
        "🎨 Flux",

    "nanobanana-2":
        "🍌 Nano Banana 2",

    "gptimage":
        "🖼 GPT Image",

    "seedream5":
        "✨ Seedream 5",

    "zimage":
        "⚡ Z-Image",
}


VIDEO_MODELS = {
    "Wan-AI/Wan2.2-TI2V-5B":
        "🎥 Wan 2.2 TI2V 5B",

    "tencent/HunyuanVideo":
        "🎥 HunyuanVideo",

    "Lightricks/LTX-Video-0.9.8-13B-distilled":
        "⚡ LTX-Video",
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

    waiting_gemini_key = State()

    waiting_hf_token = State()

    waiting_pollinations_key = State()

    waiting_prompt = State()

    waiting_history = State()


# ============================================================
# HISTORY
# ============================================================

# БД НЕ используется.
#
# После перезапуска Railway история очищается.

chat_histories = {}


def get_history(chat_id: int):

    if chat_id not in chat_histories:

        chat_histories[chat_id] = []

    return chat_histories[chat_id]


def clear_history(chat_id: int):

    chat_histories[chat_id] = []


def save_history(
    chat_id: int,
    user_text: str,
    answer: str,
):

    history = get_history(chat_id)

    history.append(
        {
            "role": "user",
            "text": user_text,
        }
    )

    history.append(
        {
            "role": "model",
            "text": answer,
        }
    )


def trim_history(chat_id: int):

    limit = settings["history_limit"]

    if limit == 0:
        return

    history = get_history(chat_id)

    if limit == 1:

        history.clear()

        return

    max_items = limit * 2

    if len(history) > max_items:

        del history[:-max_items]


# ============================================================
# OWNER
# ============================================================

def is_owner(user_id: int):

    return user_id == OWNER_ID


# ============================================================
# SETTINGS KEYBOARD
# ============================================================

def settings_keyboard():

    gemini_status = (
        "✅"
        if settings["gemini_api_key"]
        else "❌"
    )

    hf_status = (
        "✅"
        if settings["hf_token"]
        else "❌"
    )

    pollinations_status = (
        "✅"
        if settings["pollinations_api_key"]
        else "❌"
    )

    mode = (
        "🟢 ВКЛ"
        if settings["enabled"]
        else "🔴 ВЫКЛ"
    )

    limit = settings["history_limit"]

    history = (
        "♾"
        if limit == 0
        else str(limit)
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=f"🔑 Gemini API: {gemini_status}",
                    callback_data="set_gemini_key",
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"🎬 Hugging Face API: {hf_status}",
                    callback_data="set_hf_token",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        f"🖼 Image API: "
                        f"{pollinations_status}"
                    ),
                    callback_data="set_pollinations_key",
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎭 Роль / промт",
                    callback_data="set_prompt",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        "🤖 Текстовая модель\n"
                        f"{settings['text_model']}"
                    ),
                    callback_data="text_models",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        "🖼 Модель изображений\n"
                        f"{settings['image_model']}"
                    ),
                    callback_data="image_models",
                )
            ],

            [
                InlineKeyboardButton(
                    text=(
                        "🎬 Модель видео\n"
                        f"{settings['video_model']}"
                    ),
                    callback_data="video_models",
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"{mode} — режим",
                    callback_data="toggle_mode",
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"🧠 История: {history}",
                    callback_data="history_limit",
                )
            ],

            [
                InlineKeyboardButton(
                    text="🗑 Очистить историю",
                    callback_data="clear_history",
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="refresh_settings",
                )
            ],
        ]
    )


# ============================================================
# SETTINGS TEXT
# ============================================================

def settings_text():

    gemini_status = (
        "✅ установлен"
        if settings["gemini_api_key"]
        else "❌ не установлен"
    )

    hf_status = (
        "✅ установлен"
        if settings["hf_token"]
        else "❌ не установлен"
    )

    image_status = (
        "✅ установлен"
        if settings["pollinations_api_key"]
        else "❌ не установлен"
    )

    if settings["enabled"]:

        mode = (
            "🟢 <b>ВКЛ</b>\n"
            "Реагирует на все сообщения."
        )

    else:

        mode = (
            "🔴 <b>ВЫКЛ</b>\n"
            "Реагирует только на сообщения "
            "с <code>%</code> в начале."
        )

    limit = settings["history_limit"]

    if limit == 0:
        history = "♾ без лимита"

    elif limit == 1:
        history = "1 — ничего не помнить"

    else:
        history = f"{limit} сообщений"

    prompt = settings["prompt"]

    if len(prompt) > 800:
        prompt = prompt[:800] + "..."

    return (
        "⚙️ <b>Настройки бота</b>\n\n"

        f"👑 Владелец: <code>{OWNER_ID}</code>\n\n"

        f"🔑 Gemini API: {gemini_status}\n"
        f"🎬 Hugging Face API: {hf_status}\n"
        f"🖼 Image API: {image_status}\n\n"

        "🤖 <b>Текст:</b>\n"
        f"<code>{esc(settings['text_model'])}</code>\n\n"

        "🖼 <b>Изображения:</b>\n"
        f"<code>{esc(settings['image_model'])}</code>\n\n"

        "🎬 <b>Видео:</b>\n"
        f"<code>{esc(settings['video_model'])}</code>\n\n"

        f"📡 <b>Режим:</b>\n{mode}\n\n"

        f"🧠 <b>История:</b> {history}\n\n"

        "🎭 <b>Роль:</b>\n"
        f"<blockquote>{esc(prompt)}</blockquote>\n\n"

        "🌍 Настройки глобальные и действуют "
        "во всех чатах."
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start(message: Message):

    await message.answer(
        "👋 <b>Gemini AI Bot</b>\n\n"

        "💬 Текст\n"
        "📷 Анализ фото\n"
        "🎤 Анализ аудио\n"
        "🎥 Анализ видео\n"
        "🖼 Генерация изображений\n"
        "🎬 Генерация видео\n"
        "🧠 История\n\n"

        "Пример:\n"
        "<code>%сколько будет 25 × 25?</code>\n\n"

        "Картинка:\n"
        "<code>%нарисуй кота в космосе</code>\n\n"

        "Видео:\n"
        "<code>%видео кот идёт по Алматы ночью</code>\n\n"

        "Настройки:\n"
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

    if not is_owner(
        message.from_user.id
    ):

        await message.answer(
            "⛔ Настройки доступны "
            "только владельцу."
        )

        return

    await state.clear()

    await message.answer(
        settings_text(),
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# GEMINI KEY
# ============================================================

@dp.callback_query(
    F.data == "set_gemini_key"
)
async def set_gemini_key(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_gemini_key
    )

    await callback.message.answer(
        "🔑 Отправь Gemini API key.\n\n"
        "Для отмены: /cancel"
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_gemini_key
)
async def receive_gemini_key(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):

        await state.clear()

        return

    key = (
        message.text or ""
    ).strip()

    if len(key) < 10:

        await message.answer(
            "❌ Ключ выглядит неправильно."
        )

        return

    settings["gemini_api_key"] = key

    await state.clear()

    await message.answer(
        "✅ Gemini API key сохранён.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# HF TOKEN
# ============================================================

@dp.callback_query(
    F.data == "set_hf_token"
)
async def set_hf_token(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_hf_token
    )

    await callback.message.answer(
        "🎬 Отправь Hugging Face Token.\n\n"
        "Нужен токен с разрешением "
        "<b>Inference Providers</b>.\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_hf_token
)
async def receive_hf_token(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):

        await state.clear()

        return

    token = (
        message.text or ""
    ).strip()

    if not token.startswith("hf_"):

        await message.answer(
            "❌ Hugging Face Token "
            "обычно начинается с hf_."
        )

        return

    settings["hf_token"] = token

    await state.clear()

    await message.answer(
        "✅ Hugging Face token сохранён.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# POLLINATIONS KEY
# ============================================================

@dp.callback_query(
    F.data == "set_pollinations_key"
)
async def set_pollinations_key(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_pollinations_key
    )

    await callback.message.answer(
        "🖼 Отправь Pollinations API key.\n\n"

        "Для генерации через API нужен ключ.\n\n"

        "Официальная страница ключей:\n"
        "https://enter.pollinations.ai\n\n"

        "Для отмены: /cancel",
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_pollinations_key
)
async def receive_pollinations_key(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):

        await state.clear()

        return

    key = (
        message.text or ""
    ).strip()

    if not (
        key.startswith("sk_")
        or key.startswith("pk_")
    ):

        await message.answer(
            "❌ Pollinations key должен "
            "начинаться с sk_ или pk_."
        )

        return

    settings[
        "pollinations_api_key"
    ] = key

    await state.clear()

    await message.answer(
        "✅ Pollinations API key сохранён.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# PROMPT
# ============================================================

@dp.callback_query(
    F.data == "set_prompt"
)
async def set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_prompt
    )

    await callback.message.answer(
        "🎭 Отправь новую роль / system prompt.\n\n"
        "Например:\n"
        "<code>"
        "Ты профессиональный программист. "
        "Отвечай кратко и по делу."
        "</code>\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_prompt
)
async def receive_prompt(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):

        await state.clear()

        return

    if not message.text:

        await message.answer(
            "❌ Отправь текстовый промт."
        )

        return

    settings["prompt"] = (
        message.text.strip()
    )

    await state.clear()

    await message.answer(
        "✅ Роль изменена.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# TEXT MODELS
# ============================================================

def text_models_keyboard():

    rows = []

    for model, name in TEXT_MODELS.items():

        mark = (
            " ✅"
            if model == settings["text_model"]
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=name + mark,
                    callback_data=f"tm:{model}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_settings",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


@dp.callback_query(
    F.data == "text_models"
)
async def text_models(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🤖 <b>Текстовая модель</b>\n\n"
        "Выбери модель:",

        reply_markup=text_models_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("tm:")
)
async def choose_text_model(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    model = callback.data[3:]

    if model not in TEXT_MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["text_model"] = model

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Текстовая модель изменена."
    )


# ============================================================
# IMAGE MODELS
# ============================================================

def image_models_keyboard():

    rows = []

    for model, name in IMAGE_MODELS.items():

        mark = (
            " ✅"
            if model == settings["image_model"]
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=name + mark,
                    callback_data=f"im:{model}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_settings",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


@dp.callback_query(
    F.data == "image_models"
)
async def image_models(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🖼 <b>Модель изображений</b>\n\n"
        "Выбери модель:",

        reply_markup=image_models_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("im:")
)
async def choose_image_model(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    model = callback.data[3:]

    if model not in IMAGE_MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["image_model"] = model

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Image-модель изменена."
    )


# ============================================================
# VIDEO MODELS
# ============================================================

def video_models_keyboard():

    rows = []

    for model, name in VIDEO_MODELS.items():

        mark = (
            " ✅"
            if model == settings["video_model"]
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=name + mark,
                    callback_data=f"vm:{model}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_settings",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


@dp.callback_query(
    F.data == "video_models"
)
async def video_models(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.edit_text(
        "🎬 <b>Модель видео</b>\n\n"
        "Выбери модель:",

        reply_markup=video_models_keyboard(),

        parse_mode="HTML",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("vm:")
)
async def choose_video_model(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    model = callback.data[3:]

    if model not in VIDEO_MODELS:

        await callback.answer(
            "❌ Неизвестная модель.",
            show_alert=True,
        )

        return

    settings["video_model"] = model

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
        parse_mode="HTML",
    )

    await callback.answer(
        "✅ Video-модель изменена."
    )


# ============================================================
# TOGGLE
# ============================================================

@dp.callback_query(
    F.data == "toggle_mode"
)
async def toggle_mode(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

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
# HISTORY
# ============================================================

@dp.callback_query(
    F.data == "history_limit"
)
async def history_limit(
    callback: CallbackQuery,
    state: FSMContext,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    await state.set_state(
        SettingsState.waiting_history
    )

    await callback.message.answer(
        "🧠 <b>Лимит истории</b>\n\n"

        "0 — без лимита\n"
        "1 — ничего не помнить\n"
        "5 — примерно 5 сообщений\n"
        "20 — примерно 20 сообщений\n\n"

        "Для отмены: /cancel",

        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(
    SettingsState.waiting_history
)
async def receive_history_limit(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):

        await state.clear()

        return

    try:

        value = int(
            message.text.strip()
        )

    except Exception:

        await message.answer(
            "❌ Отправь целое число."
        )

        return

    if value < 0:

        await message.answer(
            "❌ Значение не может быть "
            "меньше 0."
        )

        return

    settings["history_limit"] = value

    await state.clear()

    await message.answer(
        f"✅ Лимит истории: "
        f"<b>{value}</b>",

        reply_markup=settings_keyboard(),

        parse_mode="HTML",
    )


# ============================================================
# CLEAR HISTORY
# ============================================================

@dp.callback_query(
    F.data == "clear_history"
)
async def clear_history_callback(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )

        return

    clear_history(
        callback.message.chat.id
    )

    await callback.answer(
        "🗑 История текущего чата очищена.",
        show_alert=True,
    )


# ============================================================
# REFRESH / BACK
# ============================================================

@dp.callback_query(
    F.data == "refresh_settings"
)
async def refresh_settings(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):
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


@dp.callback_query(
    F.data == "back_settings"
)
async def back_settings(
    callback: CallbackQuery,
):

    if not is_owner(
        callback.from_user.id
    ):
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
# CANCEL
# ============================================================

@dp.message(Command("cancel"))
async def cancel(
    message: Message,
    state: FSMContext,
):

    if not is_owner(
        message.from_user.id
    ):
        return

    await state.clear()

    await message.answer(
        "❌ Отменено."
    )


# ============================================================
# TELEGRAM MEDIA DOWNLOAD
# ============================================================

async def download_media(
    message: Message,
):

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="tg_gemini_"
        )
    )

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

    if message.document:

        file = await bot.get_file(
            message.document.file_id
        )

        filename = (
            message.document.file_name
            or "file"
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

    shutil.rmtree(
        temp_dir,
        ignore_errors=True,
    )

    return None, None, None


# ============================================================
# GEMINI
# ============================================================

async def ask_gemini(
    chat_id: int,
    prompt: str,
    media_path=None,
    mime_type=None,
):

    if not settings["gemini_api_key"]:

        raise RuntimeError(
            "Gemini API key не установлен.\n"
            "Открой /settings."
        )

    client = genai.Client(
        api_key=settings["gemini_api_key"]
    )

    contents = []

    history = get_history(chat_id)

    limit = settings["history_limit"]

    if limit == 0:

        selected = history

    elif limit == 1:

        selected = []

    else:

        selected = history[
            -(limit * 2):
        ]

    for item in selected:

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

    current_parts = [
        types.Part.from_text(
            text=prompt
        )
    ]

    if media_path:

        file_size = media_path.stat().st_size

        if file_size <= 20 * 1024 * 1024:

            data = await asyncio.to_thread(
                media_path.read_bytes
            )

            current_parts.append(
                types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type,
                )
            )

        else:

            uploaded = await asyncio.to_thread(
                lambda: client.files.upload(
                    file=str(media_path),
                    config=types.UploadFileConfig(
                        mime_type=mime_type
                    ),
                )
            )

            current_parts.append(
                uploaded
            )

    contents.append(
        types.Content(
            role="user",
            parts=current_parts,
        )
    )

    response = await asyncio.to_thread(
        client.models.generate_content,

        model=settings["text_model"],

        contents=contents,

        config=types.GenerateContentConfig(
            system_instruction=settings["prompt"]
        ),
    )

    return response.text or (
        "❌ Gemini не вернул текст."
    )


# ============================================================
# IMAGE REQUEST
# ============================================================

def is_image_request(
    text: str
):

    text = text.lower().strip()

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
        "draw",
        "generate image",
        "create image",
    ]

    return any(
        text.startswith(prefix)
        for prefix in prefixes
    )


def clean_image_prompt(
    text: str
):

    text = text.strip()

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
        "draw",
        "generate image",
        "create image",
    ]

    lower = text.lower()

    for prefix in prefixes:

        if lower.startswith(prefix):

            return text[
                len(prefix):
            ].strip()

    return text


# ============================================================
# POLLINATIONS IMAGE
# ============================================================

async def generate_image(
    prompt: str
):

    api_key = settings[
        "pollinations_api_key"
    ]

    if not api_key:

        raise RuntimeError(
            "Pollinations API key "
            "не установлен.\n\n"
            "Открой /settings → "
            "🖼 Image API."
        )

    model = settings[
        "image_model"
    ]

    encoded_prompt = (
        prompt
    )

    url = (
        "https://gen.pollinations.ai/image/"
        + aiohttp.helpers.quote(
            encoded_prompt,
            safe=""
        )
    )

    params = {
        "model": model,

        "width": "1024",

        "height": "1024",

        "nologo": "true",
    }

    headers = {
        "Authorization":
            f"Bearer {api_key}",
    }

    timeout = aiohttp.ClientTimeout(
        total=300
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="generated_image_"
        )
    )

    image_path = (
        temp_dir / "image.jpg"
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                url,
                params=params,
                headers=headers,
            ) as response:

                data = await response.read()

                if response.status != 200:

                    error_text = (
                        data.decode(
                            "utf-8",
                            errors="ignore"
                        )
                    )

                    raise RuntimeError(
                        f"Pollinations HTTP "
                        f"{response.status}: "
                        f"{error_text[:2000]}"
                    )

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        ""
                    )
                )

                if not content_type.startswith(
                    "image/"
                ):

                    text = data.decode(
                        "utf-8",
                        errors="ignore"
                    )

                    raise RuntimeError(
                        "Pollinations вернул "
                        "не изображение:\n"
                        + text[:2000]
                    )

                image_path.write_bytes(
                    data
                )

        return (
            temp_dir,
            image_path,
        )

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )

        raise


# ============================================================
# VIDEO
# ============================================================

def is_video_request(
    text: str
):

    text = text.lower().strip()

    prefixes = [
        "видео",
        "сгенерируй видео",
        "создай видео",
        "сделай видео",
        "generate video",
        "create video",
        "video",
    ]

    return any(
        text.startswith(prefix)
        for prefix in prefixes
    )


def clean_video_prompt(
    text: str
):

    text = text.strip()

    prefixes = [
        "видео",
        "сгенерируй видео",
        "создай видео",
        "сделай видео",
        "generate video",
        "create video",
        "video",
    ]

    lower = text.lower()

    for prefix in prefixes:

        if lower.startswith(prefix):

            return text[
                len(prefix):
            ].strip()

    return text


async def generate_video(
    prompt: str
):

    if not settings["hf_token"]:

        raise RuntimeError(
            "Hugging Face token "
            "не установлен."
        )

    # ВИДЕО НЕ МЕНЯЕМ.
    # Оставляем текущую реализацию.

    client = InferenceClient(
        provider="fal-ai",
        api_key=settings["hf_token"],
    )

    video_bytes = await asyncio.to_thread(
        lambda: client.text_to_video(
            prompt,
            model=settings["video_model"],
        )
    )

    if not video_bytes:

        raise RuntimeError(
            "Video API не вернуло видео."
        )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="generated_video_"
        )
    )

    video_path = (
        temp_dir / "video.mp4"
    )

    if hasattr(
        video_bytes,
        "read"
    ):

        data = video_bytes.read()

    else:

        data = bytes(video_bytes)

    video_path.write_bytes(
        data
    )

    return (
        temp_dir,
        video_path,
    )


# ============================================================
# SHOULD ANSWER
# ============================================================

def should_answer(
    message: Message
):

    if settings["enabled"]:

        return True

    text = (
        message.text
        or message.caption
        or ""
    )

    return text.startswith("%")


def get_user_text(
    message: Message
):

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
# MAIN HANDLER
# ============================================================

@dp.message()
async def messages(
    message: Message
):

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

    if not should_answer(message):

        return

    media_dir = None

    generated_dir = None

    try:

        user_text = get_user_text(
            message
        )

        # ====================================================
        # VIDEO
        # ====================================================

        if (
            user_text
            and is_video_request(user_text)
        ):

            prompt = clean_video_prompt(
                user_text
            )

            if not prompt:

                await message.reply(
                    "🎬 Напиши описание видео."
                )

                return

            await message.reply(
                "🎬 Генерирую видео...\n\n"
                "⏳ Это может занять "
                "некоторое время."
            )

            generated_dir, video_path = (
                await generate_video(
                    prompt
                )
            )

            save_history(
                message.chat.id,
                user_text,
                "[Видео сгенерировано]",
            )

            trim_history(
                message.chat.id
            )

            await message.answer_video(
                video=FSInputFile(
                    video_path
                ),
                caption="🎬 Готово!",
            )

            return

        # ====================================================
        # IMAGE
        # ====================================================

        if (
            user_text
            and is_image_request(user_text)
        ):

            prompt = clean_image_prompt(
                user_text
            )

            if not prompt:

                await message.reply(
                    "🖼 Напиши, что нарисовать."
                )

                return

            await message.reply(
                "🖼 Генерирую изображение..."
            )

            generated_dir, image_path = (
                await generate_image(
                    prompt
                )
            )

            save_history(
                message.chat.id,
                user_text,
                "[Изображение сгенерировано]",
            )

            trim_history(
                message.chat.id
            )

            await message.answer_photo(
                photo=FSInputFile(
                    image_path
                ),
                caption="🖼 Готово!",
            )

            return

        # ====================================================
        # GEMINI
        # ====================================================

        (
            media_dir,
            media_path,
            mime_type,
        ) = await download_media(
            message
        )

        if not user_text:

            user_text = (
                "Проанализируй этот "
                "медиафайл и ответь "
                "пользователю."
            )

        await bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
        )

        answer = await ask_gemini(
            chat_id=message.chat.id,
            prompt=user_text,
            media_path=media_path,
            mime_type=mime_type,
        )

        save_history(
            message.chat.id,
            user_text,
            answer,
        )

        trim_history(
            message.chat.id
        )

        for i in range(
            0,
            len(answer),
            4096,
        ):

            await message.reply(
                answer[
                    i:i + 4096
                ]
            )

    except Exception as error:

        print(
            f"[ERROR] "
            f"{message.chat.id}: "
            f"{repr(error)}"
        )

        await message.reply(
            "❌ Ошибка:\n\n"
            f"<code>"
            f"{esc(str(error)[:3000])}"
            f"</code>",
            parse_mode="HTML",
        )

    finally:

        if media_dir:

            shutil.rmtree(
                media_dir,
                ignore_errors=True,
            )

        if generated_dir:

            shutil.rmtree(
                generated_dir,
                ignore_errors=True,
            )


# ============================================================
# HTML
# ============================================================

def esc(
    text: str
):

    return html.escape(
        str(text)
    )


# ============================================================
# RUN
# ============================================================

async def main():

    print(
        "================================"
    )

    print(
        "Gemini Telegram Bot"
    )

    print(
        f"Owner: {OWNER_ID}"
    )

    print(
        f"Text: "
        f"{settings['text_model']}"
    )

    print(
        f"Image: "
        f"{settings['image_model']}"
    )

    print(
        f"Video: "
        f"{settings['video_model']}"
    )

    print(
        f"Enabled: "
        f"{settings['enabled']}"
    )

    print(
        f"History: "
        f"{settings['history_limit']}"
    )

    print(
        "================================"
    )

    await dp.start_polling(
        bot,
        allowed_updates=(
            dp.resolve_used_update_types()
        ),
    )


if __name__ == "__main__":

    asyncio.run(main())
