import asyncio
import io
import logging
import os
import random
from collections import defaultdict, deque
from pathlib import Path

import aiohttp
import google.generativeai as genai
from gradio_client import Client

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Только этот Telegram ID может менять настройки
OWNER_ID = 8904429775

# ============================================================
# DEFAULT SETTINGS
# ============================================================

settings = {
    # Gemini
    "gemini_model": "gemini-3.5-flash",

    # Image
    "image_model": "flux-schnell",

    # System prompt
    "system_prompt": (
        "Ты полезный AI-ассистент в Telegram. "
        "Отвечай понятно, точно и на языке пользователя."
    ),

    # True = отвечать на все сообщения
    # False = только сообщения с %
    "reply_all": False,

    # 0 = без лимита
    # 1 = не помнить предыдущие сообщения
    "history_limit": 20,

    # API keys
    "gemini_api_key": "",
    "pixazo_api_key": "",
}

# ============================================================
# CHAT HISTORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id: int, role: str, text: str):
    limit = settings["history_limit"]

    if limit == 1:
        return

    if limit <= 0:
        histories[chat_id].append({
            "role": role,
            "text": text,
        })
        return

    histories[chat_id] = deque(
        histories[chat_id],
        maxlen=limit,
    )

    histories[chat_id].append({
        "role": role,
        "text": text,
    })


def get_history(chat_id: int):
    limit = settings["history_limit"]

    if limit == 1:
        return []

    if limit <= 0:
        return list(histories[chat_id])

    return list(histories[chat_id])[-limit:]


def clear_history(chat_id: int):
    histories.pop(chat_id, None)


def clear_all_history():
    histories.clear()


# ============================================================
# GEMINI
# ============================================================

def configure_gemini():

    api_key = settings["gemini_api_key"]

    if not api_key:
        raise RuntimeError(
            "Gemini API ключ не установлен.\n"
            "Открой /settings и добавь Gemini API."
        )

    genai.configure(api_key=api_key)


async def gemini_text(
    chat_id: int,
    prompt: str,
):

    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=settings["system_prompt"],
    )

    history = get_history(chat_id)

    contents = []

    for item in history:

        role = (
            "user"
            if item["role"] == "user"
            else "model"
        )

        contents.append({
            "role": role,
            "parts": [
                {
                    "text": item["text"]
                }
            ],
        })

    contents.append({
        "role": "user",
        "parts": [
            {
                "text": prompt
            }
        ],
    })

    response = await asyncio.to_thread(
        model.generate_content,
        contents,
    )

    answer = response.text

    add_history(
        chat_id,
        "user",
        prompt,
    )

    add_history(
        chat_id,
        "assistant",
        answer,
    )

    return answer


# ============================================================
# GEMINI MEDIA
# ============================================================

async def gemini_media(
    chat_id: int,
    prompt: str,
    media_bytes: bytes,
    mime_type: str,
):

    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=settings["system_prompt"],
    )

    history = get_history(chat_id)

    history_text = "\n".join(
        f"{item['role']}: {item['text']}"
        for item in history
    )

    full_prompt = f"""
История разговора:

{history_text}

Пользователь отправил медиа.

Запрос:
{prompt}

Проанализируй медиа и ответь пользователю.
"""

    response = await asyncio.to_thread(
        model.generate_content,
        [
            full_prompt,
            {
                "mime_type": mime_type,
                "data": media_bytes,
            },
        ],
    )

    answer = response.text

    add_history(
        chat_id,
        "user",
        prompt,
    )

    add_history(
        chat_id,
        "assistant",
        answer,
    )

    return answer


# ============================================================
# PIXAZO IMAGE
# ============================================================

PIXAZO_URL = (
    "https://gateway.pixazo.ai/"
    "flux-1-schnell/v1/getData"
)


async def generate_image(prompt: str):

    api_key = settings["pixazo_api_key"]

    if not api_key:
        raise RuntimeError(
            "Pixazo API ключ не установлен.\n"
            "Открой /settings → 🔑 API ключи."
        )

    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": api_key,
    }

    data = {
        "prompt": prompt,
        "num_steps": 4,
        "height": 1024,
        "width": 1024,
        "seed": random.randint(
            1,
            2_000_000_000,
        ),
    }

    timeout = aiohttp.ClientTimeout(
        total=180
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            PIXAZO_URL,
            headers=headers,
            json=data,
        ) as response:

            text = await response.text()

            if response.status != 200:
                raise RuntimeError(
                    f"Pixazo HTTP {response.status}\n"
                    f"{text}"
                )

            try:
                result = await response.json()
            except Exception:
                raise RuntimeError(
                    f"Pixazo вернул некорректный ответ:\n"
                    f"{text}"
                )

    image_url = (
        result.get("output")
        or result.get("image")
        or result.get("url")
    )

    if not image_url:
        raise RuntimeError(
            "Pixazo не вернул URL изображения:\n"
            f"{result}"
        )

    return image_url


# ============================================================
# LTX VIDEO
# ============================================================

VIDEO_SPACE = "Lightricks/LTX-2-3"


def generate_video_sync(prompt: str):

    client = Client(
        VIDEO_SPACE
    )

    seed = random.randint(
        1,
        2_000_000_000,
    )

    result = client.predict(
        None,       # input_image
        prompt,     # prompt
        2.0,        # duration
        False,      # enhance_prompt
        seed,       # seed
        True,       # randomize_seed
        512,        # height
        768,        # width
        api_name="/generate_video",
    )

    if isinstance(result, (list, tuple)):

        if not result:
            raise RuntimeError(
                "LTX не вернул результат."
            )

        video_path = result[0]

    else:

        video_path = result

    if not video_path:
        raise RuntimeError(
            "LTX не вернул видео."
        )

    return video_path


async def generate_video(prompt: str):

    return await asyncio.to_thread(
        generate_video_sync,
        prompt,
    )


# ============================================================
# SETTINGS KEYBOARD
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤖 Gemini",
                callback_data="gemini_model",
            ),
            InlineKeyboardButton(
                "🖼 Фото",
                callback_data="image_model",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎬 Видео",
                callback_data="video_info",
            ),
        ],
        [
            InlineKeyboardButton(
                "📝 Роль",
                callback_data="system_prompt",
            ),
            InlineKeyboardButton(
                "🧠 История",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Очистить историю",
                callback_data="clear_history",
            ),
        ],
        [
            InlineKeyboardButton(
                "✅ Реагировать на всё",
                callback_data="reply_on",
            ),
            InlineKeyboardButton(
                "％ Только %",
                callback_data="reply_off",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 API ключи",
                callback_data="api_keys",
            ),
        ],
    ])


# ============================================================
# /settings
# ============================================================

async def settings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "❌ У тебя нет доступа к настройкам."
        )

        return

    await update.message.reply_text(
        "⚙️ Глобальные настройки\n\n"
        f"🤖 Gemini: {settings['gemini_model']}\n"
        f"🖼 Фото: Flux Schnell\n"
        f"🎬 Видео: LTX-2.3\n\n"
        f"⚡ Реакция на всё: "
        f"{'ВКЛ' if settings['reply_all'] else 'ВЫКЛ'}\n\n"
        f"🧠 История: "
        f"{settings['history_limit']}\n\n"
        "Эти настройки распространяются "
        "на все чаты.",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# SETTINGS CALLBACK
# ============================================================

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != OWNER_ID:

        await query.edit_message_text(
            "❌ Только владелец может менять настройки."
        )

        return

    data = query.data

    # --------------------------------------------------------
    # Reply ON
    # --------------------------------------------------------

    if data == "reply_on":

        settings["reply_all"] = True

        await query.edit_message_text(
            "✅ Включено.\n\n"
            "Теперь бот реагирует на все сообщения.",
        )

    # --------------------------------------------------------
    # Reply OFF
    # --------------------------------------------------------

    elif data == "reply_off":

        settings["reply_all"] = False

        await query.edit_message_text(
            "✅ Выключено.\n\n"
            "Теперь бот отвечает только на сообщения "
            "начинающиеся с `%`.",
        )

    # --------------------------------------------------------
    # Clear history
    # --------------------------------------------------------

    elif data == "clear_history":

        clear_all_history()

        await query.edit_message_text(
            "🗑 История всех чатов очищена."
        )

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    elif data == "gemini_model":

        context.user_data["waiting"] = (
            "gemini_model"
        )

        await query.edit_message_text(
            "🤖 Отправь название Gemini-модели.\n\n"
            "Например:\n"
            "gemini-3.5-flash\n\n"
            "Для отмены: /cancel"
        )

    # --------------------------------------------------------
    # Image
    # --------------------------------------------------------

    elif data == "image_model":

        await query.edit_message_text(
            "🖼 Генерация изображений\n\n"
            "Модель: Flux Schnell\n"
            "Провайдер: Pixazo\n\n"
            "Для изменения модели потребуется "
            "изменить код."
        )

    # --------------------------------------------------------
    # Video
    # --------------------------------------------------------

    elif data == "video_info":

        await query.edit_message_text(
            "🎬 Генерация видео\n\n"
            "Модель: LTX-2.3\n"
            "Space: Lightricks/LTX-2-3\n"
            "Режим: бесплатный ZeroGPU\n\n"
            "Видео короткое, чтобы уменьшить "
            "нагрузку на бесплатный GPU."
        )

    # --------------------------------------------------------
    # System prompt
    # --------------------------------------------------------

    elif data == "system_prompt":

        context.user_data["waiting"] = (
            "system_prompt"
        )

        await query.edit_message_text(
            "📝 Отправь новую роль / промпт.\n\n"
            "Например:\n"
            "Ты эксперт по программированию.\n\n"
            "Для отмены: /cancel"
        )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    elif data == "history":

        context.user_data["waiting"] = (
            "history"
        )

        await query.edit_message_text(
            "🧠 Сколько последних сообщений помнить?\n\n"
            "0 — без лимита\n"
            "1 — ничего не помнить\n"
            "10 — последние 10\n"
            "20 — последние 20\n\n"
            "Для отмены: /cancel"
        )

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    elif data == "api_keys":

        context.user_data["waiting"] = (
            "api_keys"
        )

        await query.edit_message_text(
            "🔑 Отправь ключ в формате:\n\n"
            "gemini: ТВОЙ_КЛЮЧ\n\n"
            "или\n\n"
            "pixazo: ТВОЙ_КЛЮЧ\n\n"
            "Для отмены: /cancel\n\n"
            "⚠️ Не отправляй ключ в группы."
        )


# ============================================================
# SETTINGS INPUT
# ============================================================

async def settings_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id != OWNER_ID:
        return

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return

    text = update.message.text.strip()

    # --------------------------------------------------------
    # Gemini model
    # --------------------------------------------------------

    if waiting == "gemini_model":

        settings["gemini_model"] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            f"✅ Gemini модель:\n{text}"
        )

    # --------------------------------------------------------
    # Prompt
    # --------------------------------------------------------

    elif waiting == "system_prompt":

        settings["system_prompt"] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Роль изменена."
        )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    elif waiting == "history":

        try:

            value = int(text)

            if value < 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Отправь целое число."
            )

            return

        settings["history_limit"] = value

        if value == 1:
            clear_all_history()

        elif value > 1:

            for chat_id in list(histories):

                histories[chat_id] = deque(
                    histories[chat_id],
                    maxlen=value,
                )

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            f"✅ Лимит истории: {value}"
        )

    # --------------------------------------------------------
    # API keys
    # --------------------------------------------------------

    elif waiting == "api_keys":

        if ":" not in text:

            await update.message.reply_text(
                "❌ Формат:\n\n"
                "gemini: ключ\n"
                "pixazo: ключ"
            )

            return

        name, value = text.split(
            ":",
            1,
        )

        name = name.strip().lower()
        value = value.strip()

        if not value:

            await update.message.reply_text(
                "❌ Ключ пустой."
            )

            return

        if name == "gemini":

            settings["gemini_api_key"] = value

            message = (
                "🤖 Gemini API ключ сохранён."
            )

        elif name == "pixazo":

            settings["pixazo_api_key"] = value

            message = (
                "🖼 Pixazo API ключ сохранён."
            )

        else:

            await update.message.reply_text(
                "❌ Можно добавить только:\n"
                "gemini\n"
                "pixazo"
            )

            return

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            f"✅ {message}"
        )


# ============================================================
# /cancel
# ============================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data.pop(
        "waiting",
        None,
    )

    await update.message.reply_text(
        "❌ Отменено."
    )


# ============================================================
# /start
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🤖 AI Telegram Bot\n\n"
        "Я умею:\n"
        "🤖 Gemini — текст\n"
        "🖼 Pixazo — изображения\n"
        "🎬 LTX — видео\n"
        "👁 анализ фото\n"
        "🎧 анализ аудио\n"
        "🎥 анализ видео\n\n"
        "Примеры:\n\n"
        "%сколько будет 25*25\n"
        "%сгенерируй фото кота\n"
        "%сгенерируй видео кота\n\n"
        "Настройки: /settings"
    )


# ============================================================
# IMAGE COMMAND
# ============================================================

async def image_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/image кот в космосе"
        )

        return

    prompt = " ".join(
        context.args
    )

    await update.message.chat.send_action(
        ChatAction.UPLOAD_PHOTO
    )

    try:

        image_url = await generate_image(
            prompt
        )

        await update.message.reply_photo(
            photo=image_url,
            caption="🖼 Готово!"
        )

    except Exception as e:

        logging.exception(
            "IMAGE ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка:\n\n{e}"
        )


# ============================================================
# VIDEO COMMAND
# ============================================================

async def video_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/video кот идёт по улице"
        )

        return

    prompt = " ".join(
        context.args
    )

    status = await update.message.reply_text(
        "🎬 Генерирую видео...\n\n"
        "⏳ Подожди, бесплатный GPU может "
        "поставить запрос в очередь."
    )

    try:

        video_path = await generate_video(
            prompt
        )

        await status.delete()

        await update.message.chat.send_action(
            ChatAction.UPLOAD_VIDEO
        )

        await update.message.reply_video(
            video=video_path,
            caption="🎬 Готово!"
        )

        try:

            Path(video_path).unlink(
                missing_ok=True
            )

        except Exception:
            pass

    except Exception as e:

        logging.exception(
            "VIDEO ERROR"
        )

        await status.edit_text(
            f"❌ Ошибка:\n\n{e}"
        )


# ============================================================
# TEXT
# ============================================================

IMAGE_WORDS = [
    "нарисуй",
    "нарисуй фото",
    "нарисуй картинку",
    "сгенерируй фото",
    "сгенерируй картинку",
    "сгенерируй изображение",
    "создай фото",
    "создай картинку",
    "создай изображение",
    "сделай фото",
    "сделай картинку",
    "сделай изображение",
]

VIDEO_WORDS = [
    "сгенерируй видео",
    "создай видео",
    "сделай видео",
    "генерируй видео",
    "видео",
]


async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    # Если владелец вводит настройки
    if (
        update.effective_user.id
        == OWNER_ID
        and context.user_data.get("waiting")
    ):

        await settings_input(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # Проверяем %
    # --------------------------------------------------------

    if settings["reply_all"]:

        prompt = text.strip()

    else:

        if not text.startswith("%"):
            return

        prompt = text[1:].strip()

        if not prompt:
            return

    lower = prompt.lower()

    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    image_prefix = None

    for word in IMAGE_WORDS:

        if lower.startswith(word):

            image_prefix = word
            break

    if image_prefix:

        image_prompt = prompt[
            len(image_prefix):
        ].strip()

        if not image_prompt:
            image_prompt = (
                "реалистичная фотография"
            )

        await update.message.chat.send_action(
            ChatAction.UPLOAD_PHOTO
        )

        try:

            image_url = await generate_image(
                image_prompt
            )

            await update.message.reply_photo(
                photo=image_url,
                caption="🖼 Готово!"
            )

        except Exception as e:

            logging.exception(
                "IMAGE ERROR"
            )

            await update.message.reply_text(
                f"❌ Ошибка генерации фото:\n\n{e}"
            )

        return

    # --------------------------------------------------------
    # VIDEO
    # --------------------------------------------------------

    video_prefix = None

    for word in VIDEO_WORDS:

        if lower.startswith(word):

            video_prefix = word
            break

    if video_prefix:

        video_prompt = prompt[
            len(video_prefix):
        ].strip()

        if not video_prompt:

            video_prompt = (
                "красивый кинематографичный "
                "пейзаж"
            )

        status = await update.message.reply_text(
            "🎬 Генерирую видео...\n\n"
            "⏳ Бесплатный LTX Space может "
            "работать через очередь."
        )

        try:

            video_path = await generate_video(
                video_prompt
            )

            await status.delete()

            await update.message.chat.send_action(
                ChatAction.UPLOAD_VIDEO
            )

            await update.message.reply_video(
                video=video_path,
                caption="🎬 Готово!"
            )

            try:

                Path(video_path).unlink(
                    missing_ok=True
                )

            except Exception:
                pass

        except Exception as e:

            logging.exception(
                "VIDEO ERROR"
            )

            await status.edit_text(
                f"❌ Ошибка генерации видео:\n\n{e}"
            )

        return

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    await update.message.chat.send_action(
        ChatAction.TYPING
    )

    try:

        answer = await gemini_text(
            update.effective_chat.id,
            prompt,
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "GEMINI ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка Gemini:\n\n{e}"
        )


# ============================================================
# PHOTO ANALYSIS
# ============================================================

async def photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    caption = (
        update.message.caption
        or ""
    )

    if not settings["reply_all"]:

        if not caption.startswith("%"):
            return

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:

        caption = (
            "Подробно опиши, что изображено "
            "на этой фотографии."
        )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        answer = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            "image/jpeg",
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "PHOTO ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка анализа фото:\n\n{e}"
        )


# ============================================================
# AUDIO / VOICE
# ============================================================

async def audio_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    caption = (
        update.message.caption
        or ""
    )

    if not settings["reply_all"]:

        if not caption.startswith("%"):
            return

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:

        caption = (
            "Проанализируй это аудио."
        )

    try:

        if update.message.audio:

            media = update.message.audio

        else:

            media = update.message.voice

        file = await context.bot.get_file(
            media.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        mime_type = (
            getattr(
                media,
                "mime_type",
                None,
            )
            or "audio/ogg"
        )

        answer = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            mime_type,
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "AUDIO ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка анализа аудио:\n\n{e}"
        )


# ============================================================
# VIDEO ANALYSIS
# ============================================================

async def video_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    caption = (
        update.message.caption
        or ""
    )

    if not settings["reply_all"]:

        if not caption.startswith("%"):
            return

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:

        caption = (
            "Проанализируй это видео."
        )

    try:

        media = update.message.video

        file = await context.bot.get_file(
            media.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        answer = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            media.mime_type
            or "video/mp4",
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "VIDEO ANALYSIS ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка анализа видео:\n\n{e}"
        )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context,
):

    logging.error(
        "Unhandled exception:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не установлен."
        )

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands

    app.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "settings",
            settings_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "cancel",
            cancel_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "image",
            image_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "video",
            video_command,
        )
    )

    # Settings

    app.add_handler(
        CallbackQueryHandler(
            settings_callback
        )
    )

    # Media

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.AUDIO
            | filters.VOICE,
            audio_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_message,
        )
    )

    # Text

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_message,
        )
    )

    app.add_error_handler(
        error_handler
    )

    print(
        "🤖 Bot started successfully"
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
