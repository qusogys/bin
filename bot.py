import asyncio
import io
import logging
import os
import random
import tempfile
from collections import defaultdict, deque
from pathlib import Path

import aiohttp
import google.generativeai as genai
from gradio_client import Client, handle_file
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

# Только этот Telegram ID может менять глобальные настройки
OWNER_ID = 8904429775

# ------------------------------------------------------------
# Gemini
# ------------------------------------------------------------

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# ------------------------------------------------------------
# Image
# ------------------------------------------------------------

DEFAULT_IMAGE_MODEL = "flux-schnell"

PIXAZO_URL = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData"

# ------------------------------------------------------------
# Video
# ------------------------------------------------------------

VIDEO_SPACE = "Lightricks/LTX-2-3"

# ------------------------------------------------------------
# Global settings
# ------------------------------------------------------------

settings = {
    "gemini_model": DEFAULT_GEMINI_MODEL,

    # Модель для картинок
    "image_model": DEFAULT_IMAGE_MODEL,

    # Промпт / роль
    "system_prompt": (
        "Ты полезный Telegram AI-ассистент. "
        "Отвечай понятно, точно и на языке пользователя."
    ),

    # True = отвечает на все сообщения
    # False = только если сообщение начинается с %
    "reply_all": False,

    # 0 = без лимита
    # 1 = фактически ничего не помнить
    "history_limit": 20,

    # API ключи вводятся владельцем через /settings
    "gemini_api_key": "",
    "pixazo_api_key": "",
    "hf_token": "",
}


# ============================================================
# HISTORY
# ============================================================

# История отдельно для каждого чата
histories = defaultdict(deque)


def get_history(chat_id: int):
    limit = settings["history_limit"]

    if limit <= 0:
        return histories[chat_id]

    # При limit = 1 модель получает только текущее сообщение.
    return deque(histories[chat_id], maxlen=max(0, limit - 1))


def add_history(chat_id: int, role: str, text: str):
    limit = settings["history_limit"]

    if limit == 1:
        return

    if limit <= 0:
        # Без ограничения
        histories[chat_id].append({
            "role": role,
            "text": text,
        })
    else:
        histories[chat_id] = deque(
            histories[chat_id],
            maxlen=limit
        )

        histories[chat_id].append({
            "role": role,
            "text": text,
        })


def clear_history(chat_id: int):
    histories[chat_id].clear()


# ============================================================
# GEMINI
# ============================================================

def configure_gemini():
    key = settings["gemini_api_key"]

    if not key:
        raise RuntimeError(
            "Gemini API ключ не установлен. "
            "Владелец должен открыть /settings."
        )

    genai.configure(api_key=key)


async def gemini_text(
    chat_id: int,
    user_text: str,
):
    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=settings["system_prompt"],
    )

    history = get_history(chat_id)

    messages = []

    for item in history:
        role = "user" if item["role"] == "user" else "model"

        messages.append({
            "role": role,
            "parts": [item["text"]],
        })

    messages.append({
        "role": "user",
        "parts": [user_text],
    })

    response = await asyncio.to_thread(
        model.generate_content,
        messages,
    )

    answer = response.text

    add_history(chat_id, "user", user_text)
    add_history(chat_id, "assistant", answer)

    return answer


# ============================================================
# GEMINI IMAGE / MEDIA UNDERSTANDING
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

    text_parts = []

    for item in history:
        text_parts.append(
            f"{item['role']}: {item['text']}"
        )

    context = "\n".join(text_parts)

    full_prompt = f"""
Предыдущая история:
{context}

Пользователь отправил медиа.

Запрос пользователя:
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

    add_history(chat_id, "user", prompt)
    add_history(chat_id, "assistant", answer)

    return answer


# ============================================================
# PIXAZO IMAGE
# ============================================================

async def generate_image(prompt: str):
    api_key = settings["pixazo_api_key"]

    if not api_key:
        raise RuntimeError(
            "Pixazo API ключ не установлен."
        )

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": api_key,
    }

    data = {
        "prompt": prompt,
        "num_steps": 4,
        "height": 1024,
        "width": 1024,
        "seed": random.randint(1, 2_000_000_000),
    }

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            PIXAZO_URL,
            headers=headers,
            json=data,
        ) as response:

            text = await response.text()

            if response.status != 200:
                raise RuntimeError(
                    f"Pixazo HTTP {response.status}: {text}"
                )

            try:
                result = await response.json()
            except Exception:
                raise RuntimeError(
                    f"Pixazo вернул некорректный ответ: {text}"
                )

    image_url = result.get("output")

    if not image_url:
        raise RuntimeError(
            f"Pixazo не вернул картинку: {result}"
        )

    return image_url


# ============================================================
# LTX VIDEO
# ============================================================

def generate_video_sync(prompt: str):
    """
    Генерация через официальный Hugging Face Space
    Lightricks/LTX-2-3.

    Мы специально используем:
    - 2 секунды
    - low resolution
    - без лишней нагрузки

    Это повышает шанс пройти бесплатный ZeroGPU.
    """

    client_kwargs = {}

    hf_token = settings.get("hf_token")

    if hf_token:
        client_kwargs["hf_token"] = hf_token

    client = Client(
        VIDEO_SPACE,
        **client_kwargs,
    )

    # У Lightricks Space интерфейс содержит:
    #
    # input_image
    # prompt
    # duration
    # enhance_prompt
    # high_res
    # seed
    # randomize_seed
    # width
    # height
    #
    # Для text-to-video image = None.

    seed = random.randint(1, 2_000_000_000)

    result = client.predict(
        None,       # input_image
        prompt,     # prompt
        2.0,        # duration
        False,      # enhance_prompt
        False,      # high_res
        seed,       # seed
        True,       # randomize_seed
        768,        # width
        512,        # height
        api_name="/generate_video",
    )

    if isinstance(result, (list, tuple)):
        video_path = result[0]
    else:
        video_path = result

    if not video_path:
        raise RuntimeError(
            "LTX Space не вернул видео."
        )

    return video_path


async def generate_video(prompt: str):
    return await asyncio.to_thread(
        generate_video_sync,
        prompt,
    )


# ============================================================
# SETTINGS UI
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤖 Gemini",
                callback_data="set_gemini"
            ),
            InlineKeyboardButton(
                "🖼 Фото",
                callback_data="set_image"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎬 Видео",
                callback_data="set_video"
            ),
        ],
        [
            InlineKeyboardButton(
                "📝 Роль / промпт",
                callback_data="set_prompt"
            ),
        ],
        [
            InlineKeyboardButton(
                "🧠 История",
                callback_data="history_menu"
            ),
            InlineKeyboardButton(
                "🗑 Очистить историю",
                callback_data="clear_history_all"
            ),
        ],
        [
            InlineKeyboardButton(
                "⚡ Реагировать на все",
                callback_data="reply_all_on"
            ),
            InlineKeyboardButton(
                "％ Только %",
                callback_data="reply_all_off"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 API ключи",
                callback_data="api_menu"
            ),
        ],
    ])


async def settings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text(
            "❌ У тебя нет доступа к настройкам."
        )
        return

    await update.message.reply_text(
        "⚙️ Глобальные настройки бота\n\n"
        f"🤖 Gemini: `{settings['gemini_model']}`\n"
        f"🖼 Фото: `{settings['image_model']}`\n"
        f"🎬 Видео: `{VIDEO_SPACE}`\n\n"
        f"⚡ Реакция на все: "
        f"{'ВКЛ' if settings['reply_all'] else 'ВЫКЛ'}\n"
        f"🧠 Лимит истории: "
        f"{settings['history_limit']}\n\n"
        "Все эти настройки распространяются на все чаты.",
        reply_markup=settings_keyboard(),
        parse_mode="Markdown",
    )


# ============================================================
# SETTINGS CALLBACKS
# ============================================================

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text(
            "❌ Только владелец может менять настройки."
        )
        return

    data = query.data

    if data == "reply_all_on":

        settings["reply_all"] = True

        await query.edit_message_text(
            "✅ Реакция на все сообщения ВКЛ.\n\n"
            "Теперь бот отвечает на обычные сообщения "
            "во всех чатах."
        )

    elif data == "reply_all_off":

        settings["reply_all"] = False

        await query.edit_message_text(
            "✅ Режим `%` ВКЛ.\n\n"
            "Теперь бот отвечает только на сообщения, "
            "начинающиеся с `%`."
        )

    elif data == "clear_history_all":

        histories.clear()

        await query.edit_message_text(
            "🗑 История всех чатов очищена."
        )

    elif data == "set_gemini":

        context.user_data["waiting"] = "gemini_model"

        await query.edit_message_text(
            "🤖 Отправь название Gemini-модели.\n\n"
            "Например:\n"
            "`gemini-3.5-flash`\n\n"
            "Для отмены: /cancel",
            parse_mode="Markdown",
        )

    elif data == "set_image":

        await query.edit_message_text(
            "🖼 Модель фото:\n\n"
            "Текущая: `Flux Schnell`\n\n"
            "Она используется через бесплатный "
            "Pixazo API.\n\n"
            "Модель фиксирована в этой версии.",
            parse_mode="Markdown",
        )

    elif data == "set_video":

        await query.edit_message_text(
            "🎬 Видео:\n\n"
            "Lightricks LTX-2.3\n"
            "Hugging Face ZeroGPU\n\n"
            "Короткое видео генерируется через "
            "бесплатный Space.\n\n"
            "Модель: LTX-2.3 Distilled"
        )

    elif data == "set_prompt":

        context.user_data["waiting"] = "system_prompt"

        await query.edit_message_text(
            "📝 Отправь новую роль / системный промпт.\n\n"
            "Например:\n"
            "`Ты эксперт по программированию...`\n\n"
            "Для отмены: /cancel",
            parse_mode="Markdown",
        )

    elif data == "history_menu":

        context.user_data["waiting"] = "history_limit"

        await query.edit_message_text(
            "🧠 Отправь число сообщений для памяти.\n\n"
            "`0` — без лимита\n"
            "`1` — ничего не помнить\n"
            "`10` — помнить последние 10\n"
            "`20` — помнить последние 20\n\n"
            "Для отмены: /cancel",
            parse_mode="Markdown",
        )

    elif data == "api_menu":

        context.user_data["waiting"] = "api_menu"

        await query.edit_message_text(
            "🔑 API ключи\n\n"
            "Отправь одну из команд:\n\n"
            "`gemini: ТВОЙ_КЛЮЧ`\n"
            "`pixazo: ТВОЙ_КЛЮЧ`\n"
            "`hf: ТВОЙ_ТОКЕН`\n\n"
            "Для отмены: /cancel",
            parse_mode="Markdown",
        )


# ============================================================
# SETTINGS INPUT
# ============================================================

async def settings_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:
        return

    waiting = context.user_data.get("waiting")

    if not waiting:
        return

    text = update.message.text.strip()

    if waiting == "gemini_model":

        settings["gemini_model"] = text

        context.user_data.pop("waiting", None)

        await update.message.reply_text(
            f"✅ Gemini модель изменена:\n`{text}`",
            parse_mode="Markdown",
        )

    elif waiting == "system_prompt":

        settings["system_prompt"] = text

        context.user_data.pop("waiting", None)

        await update.message.reply_text(
            "✅ Роль / системный промпт изменён."
        )

    elif waiting == "history_limit":

        try:
            value = int(text)

            if value < 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Нужно отправить целое число."
            )
            return

        settings["history_limit"] = value

        # Пересоздаём deque
        for chat_id in list(histories.keys()):

            if value > 1:
                histories[chat_id] = deque(
                    histories[chat_id],
                    maxlen=value
                )
            elif value == 1:
                histories[chat_id].clear()

        context.user_data.pop("waiting", None)

        await update.message.reply_text(
            f"✅ Лимит истории: `{value}`",
            parse_mode="Markdown",
        )

    elif waiting == "api_menu":

        if ":" not in text:

            await update.message.reply_text(
                "❌ Формат:\n"
                "`gemini: ключ`\n"
                "`pixazo: ключ`\n"
                "`hf: токен`",
                parse_mode="Markdown",
            )
            return

        name, value = text.split(":", 1)

        name = name.strip().lower()
        value = value.strip()

        if not value:

            await update.message.reply_text(
                "❌ Ключ пустой."
            )
            return

        if name == "gemini":

            settings["gemini_api_key"] = value
            message = "🤖 Gemini API ключ сохранён."

        elif name == "pixazo":

            settings["pixazo_api_key"] = value
            message = "🖼 Pixazo API ключ сохранён."

        elif name == "hf":

            settings["hf_token"] = value
            message = "🎬 Hugging Face токен сохранён."

        else:

            await update.message.reply_text(
                "❌ Неизвестный тип ключа."
            )
            return

        context.user_data.pop("waiting", None)

        await update.message.reply_text(
            f"✅ {message}"
        )


# ============================================================
# CANCEL
# ============================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.pop("waiting", None)

    await update.message.reply_text(
        "❌ Отменено."
    )


# ============================================================
# PHOTO GENERATION COMMAND
# ============================================================

async def image_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "`/image кот в космосе`",
            parse_mode="Markdown",
        )
        return

    prompt = " ".join(context.args)

    await update.message.chat.send_action(
        ChatAction.UPLOAD_PHOTO
    )

    try:

        image_url = await generate_image(prompt)

        await update.message.reply_photo(
            photo=image_url,
            caption=f"🖼 {prompt}"
        )

    except Exception as e:

        logging.exception("IMAGE ERROR")

        await update.message.reply_text(
            f"❌ Ошибка генерации изображения:\n\n{e}"
        )


# ============================================================
# VIDEO COMMAND
# ============================================================

async def video_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "`/video кот идёт по улице ночью`",
            parse_mode="Markdown",
        )
        return

    prompt = " ".join(context.args)

    status = await update.message.reply_text(
        "🎬 Генерирую видео...\n\n"
        "⏳ Используется бесплатный Hugging Face "
        "ZeroGPU Space.\n"
        "Это может занять некоторое время."
    )

    try:

        video_path = await generate_video(prompt)

        await status.delete()

        await update.message.chat.send_action(
            ChatAction.UPLOAD_VIDEO
        )

        await update.message.reply_video(
            video=video_path,
            caption="🎬 Готово!"
        )

        # Удаляем временный файл
        try:
            Path(video_path).unlink(missing_ok=True)
        except Exception:
            pass

    except Exception as e:

        logging.exception("VIDEO ERROR")

        await status.edit_text(
            f"❌ Ошибка генерации видео:\n\n{e}"
        )


# ============================================================
# TEXT MESSAGE
# ============================================================

def should_answer(text: str):

    if settings["reply_all"]:
        return True

    return text.startswith("%")


async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    # Если владелец сейчас вводит настройки
    if update.effective_user.id == OWNER_ID:
        if context.user_data.get("waiting"):
            await settings_input(update, context)
            return

    if not should_answer(text):
        return

    prompt = text

    if not settings["reply_all"]:

        prompt = text[1:].strip()

        if not prompt:
            return

    # --------------------------------------------------------
    # Генерация изображения
    # --------------------------------------------------------

    image_commands = [
        "нарисуй",
        "сгенерируй картинку",
        "создай картинку",
        "создай изображение",
        "нарисуй фото",
    ]

    lower = prompt.lower()

    if any(
        lower.startswith(x)
        for x in image_commands
    ):

        image_prompt = prompt

        for prefix in image_commands:

            if lower.startswith(prefix):

                image_prompt = prompt[
                    len(prefix):
                ].strip()

                break

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

            logging.exception("IMAGE ERROR")

            await update.message.reply_text(
                f"❌ Ошибка:\n\n{e}"
            )

        return

    # --------------------------------------------------------
    # Генерация видео
    # --------------------------------------------------------

    video_commands = [
        "сгенерируй видео",
        "создай видео",
        "сделай видео",
        "видео",
    ]

    if any(
        lower.startswith(x)
        for x in video_commands
    ):

        video_prompt = prompt

        for prefix in video_commands:

            if lower.startswith(prefix):

                video_prompt = prompt[
                    len(prefix):
                ].strip()

                break

        status = await update.message.reply_text(
            "🎬 Генерирую видео..."
        )

        try:

            video_path = await generate_video(
                video_prompt
            )

            await status.delete()

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

            logging.exception("VIDEO ERROR")

            await status.edit_text(
                f"❌ Ошибка:\n\n{e}"
            )

        return

    # --------------------------------------------------------
    # Обычный Gemini
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

        logging.exception("GEMINI ERROR")

        await update.message.reply_text(
            f"❌ Ошибка:\n\n{e}"
        )


# ============================================================
# PHOTO MESSAGE
# ============================================================

async def photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not settings["reply_all"]:

        caption = update.message.caption or ""

        if not caption.startswith("%"):
            return

    caption = update.message.caption or ""

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:
        caption = (
            "Опиши подробно, что изображено "
            "на этой фотографии."
        )

    await update.message.chat.send_action(
        ChatAction.TYPING
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

        result = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            "image/jpeg",
        )

        await update.message.reply_text(
            result
        )

    except Exception as e:

        logging.exception("PHOTO ERROR")

        await update.message.reply_text(
            f"❌ Ошибка анализа фото:\n\n{e}"
        )


# ============================================================
# AUDIO
# ============================================================

async def audio_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not settings["reply_all"]:

        caption = update.message.caption or ""

        if not caption.startswith("%"):
            return

    caption = update.message.caption or ""

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:
        caption = "Проанализируй это аудио."

    try:

        if update.message.audio:

            media = update.message.audio

        elif update.message.voice:

            media = update.message.voice

        else:
            return

        file = await context.bot.get_file(
            media.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        mime = (
            media.mime_type
            if getattr(media, "mime_type", None)
            else "audio/ogg"
        )

        result = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            mime,
        )

        await update.message.reply_text(
            result
        )

    except Exception as e:

        logging.exception("AUDIO ERROR")

        await update.message.reply_text(
            f"❌ Ошибка анализа аудио:\n\n{e}"
        )


# ============================================================
# VIDEO UNDERSTANDING
# ============================================================

async def video_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not settings["reply_all"]:

        caption = update.message.caption or ""

        if not caption.startswith("%"):
            return

    caption = update.message.caption or ""

    if caption.startswith("%"):
        caption = caption[1:].strip()

    if not caption:
        caption = "Проанализируй это видео."

    try:

        media = update.message.video

        file = await context.bot.get_file(
            media.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        result = await gemini_media(
            update.effective_chat.id,
            caption,
            buffer.getvalue(),
            media.mime_type or "video/mp4",
        )

        await update.message.reply_text(
            result
        )

    except Exception as e:

        logging.exception("VIDEO ANALYSIS ERROR")

        await update.message.reply_text(
            f"❌ Ошибка анализа видео:\n\n{e}"
        )


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 AI Telegram Bot\n\n"
        "Я умею:\n"
        "• отвечать через Gemini\n"
        "• анализировать фото\n"
        "• анализировать аудио\n"
        "• анализировать видео\n"
        "• 🖼 генерировать изображения\n"
        "• 🎬 генерировать видео\n\n"
        "Команды:\n"
        "/image <описание>\n"
        "/video <описание>\n"
        "/settings\n\n"
        "В обычном режиме запрос начинается с `%`."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logging.exception(
        "Unhandled exception:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "Не установлена переменная BOT_TOKEN."
        )

    logging.basicConfig(
        format=(
            "%(asctime)s - "
            "%(name)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
        level=logging.INFO,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "settings",
            settings_command
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    application.add_handler(
        CommandHandler(
            "image",
            image_command
        )
    )

    application.add_handler(
        CommandHandler(
            "video",
            video_command
        )
    )

    # Settings buttons
    application.add_handler(
        CallbackQueryHandler(
            settings_callback
        )
    )

    # Photo
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message
        )
    )

    # Audio / voice
    application.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VOICE,
            audio_message
        )
    )

    # Video
    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_message
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_message
        )
    )

    application.add_error_handler(
        error_handler
    )

    print("Bot started.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
