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

OWNER_ID = 8904429775

# ============================================================
# GLOBAL SETTINGS
# ============================================================

settings = {
    "gemini_model": "gemini-3.5-flash",

    "image_model": "flux-schnell",

    "system_prompt": (
        "Ты полезный AI-ассистент в Telegram. "
        "Отвечай понятно, точно и на языке пользователя."
    ),

    # True = отвечать на все сообщения
    # False = только %
    "reply_all": False,

    # 0 = без лимита
    # 1 = ничего не помнить
    "history_limit": 20,

    "gemini_api_key": "",
    "pixazo_api_key": "",

    # HF token необязателен
    "hf_token": "",
}

# ============================================================
# HISTORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id, role, text):

    limit = settings["history_limit"]

    if limit == 1:
        return

    if limit == 0:
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


def get_history(chat_id):

    limit = settings["history_limit"]

    if limit == 1:
        return []

    if limit == 0:
        return list(histories[chat_id])

    return list(histories[chat_id])[-limit:]


def clear_history(chat_id):
    histories.pop(chat_id, None)


def clear_all_history():
    histories.clear()


# ============================================================
# GEMINI
# ============================================================

def configure_gemini():

    key = settings["gemini_api_key"]

    if not key:
        raise RuntimeError(
            "Gemini API ключ не установлен.\n"
            "Открой /settings → API ключи."
        )

    genai.configure(api_key=key)


async def gemini_text(chat_id, prompt):

    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=settings["system_prompt"],
    )

    contents = []

    for item in get_history(chat_id):

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
    chat_id,
    prompt,
    media_bytes,
    mime_type,
):

    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=settings["system_prompt"],
    )

    history = get_history(chat_id)

    history_text = "\n".join(
        f"{x['role']}: {x['text']}"
        for x in history
    )

    full_prompt = f"""
История разговора:

{history_text}

Пользователь отправил медиа.

Запрос пользователя:
{prompt}

Проанализируй медиа и ответь.
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


async def generate_image(prompt):

    key = settings["pixazo_api_key"]

    if not key:
        raise RuntimeError(
            "Pixazo API ключ не установлен."
        )

    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": key,
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

            body = await response.text()

            if response.status != 200:
                raise RuntimeError(
                    f"Pixazo HTTP {response.status}\n"
                    f"{body}"
                )

            try:
                result = await response.json()
            except Exception:
                raise RuntimeError(
                    f"Pixazo вернул:\n{body}"
                )

    image_url = (
        result.get("output")
        or result.get("image")
        or result.get("url")
    )

    if not image_url:
        raise RuntimeError(
            f"Pixazo не вернул изображение:\n{result}"
        )

    return image_url


# ============================================================
# VIDEO
# ============================================================

VIDEO_SPACES = [
    "Lightricks/ltx-2-distilled",
    "Lightricks/ltx-2",
]


def _client(space):

    token = settings.get("hf_token")

    if token:
        try:
            return Client(
                space,
                token=token,
            )
        except TypeError:
            return Client(space)

    return Client(space)


def video_from_result(result):

    if isinstance(result, (list, tuple)):

        for item in result:

            if isinstance(item, str):

                if (
                    item.endswith(".mp4")
                    or item.endswith(".webm")
                    or item.endswith(".mov")
                ):
                    return item

            if isinstance(item, dict):

                path = (
                    item.get("path")
                    or item.get("video")
                    or item.get("url")
                )

                if path:
                    return path

        if result:
            return result[0]

    if isinstance(result, dict):

        return (
            result.get("path")
            or result.get("video")
            or result.get("url")
        )

    return result


def generate_video_sync(prompt):

    errors = []

    for space in VIDEO_SPACES:

        try:

            client = _client(space)

            # Получаем информацию об API Space.
            # Это позволяет не полагаться на старый
            # жёстко заданный endpoint.

            try:
                api_info = client.view_api(
                    all_endpoints=True
                )
            except Exception:
                api_info = None

            logging.info(
                "Trying video Space: %s",
                space,
            )

            # ------------------------------------------------
            # LTX-2 distilled
            # ------------------------------------------------

            if space == "Lightricks/ltx-2-distilled":

                seed = random.randint(
                    1,
                    2_000_000_000,
                )

                # Актуальная функция Space:
                #
                # input_image
                # prompt
                # duration
                # seed
                # randomize_seed
                # height
                #
                # Используем именованные аргументы,
                # чтобы порядок не ломался.

                try:

                    result = client.predict(
                        input_image=None,
                        prompt=prompt,
                        duration=3.0,
                        seed=seed,
                        randomize_seed=True,
                        height=576,
                        api_name="/generate_video",
                    )

                except Exception as first_error:

                    logging.warning(
                        "Named call failed: %s",
                        first_error,
                    )

                    # Некоторые версии Space
                    # публикуют endpoint без slash.

                    result = client.predict(
                        None,
                        prompt,
                        3.0,
                        seed,
                        True,
                        576,
                        api_name="generate_video",
                    )

                video = video_from_result(
                    result
                )

                if video:
                    return video

            # ------------------------------------------------
            # Fallback
            # ------------------------------------------------

            elif space == "Lightricks/ltx-2":

                seed = random.randint(
                    1,
                    2_000_000_000,
                )

                result = client.predict(
                    input_image=None,
                    prompt=prompt,
                    duration=3.0,
                    seed=seed,
                    randomize_seed=True,
                    height=576,
                    api_name="/generate_video",
                )

                video = video_from_result(
                    result
                )

                if video:
                    return video

        except Exception as e:

            logging.exception(
                "Video Space failed: %s",
                space,
            )

            errors.append(
                f"{space}: {e}"
            )

    raise RuntimeError(
        "Все бесплатные video Space недоступны.\n\n"
        + "\n\n".join(errors)
    )


async def generate_video(prompt):

    return await asyncio.to_thread(
        generate_video_sync,
        prompt,
    )


# ============================================================
# SETTINGS
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
                callback_data="video_model",
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


async def settings_command(
    update,
    context,
):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "❌ Только владелец бота может "
            "менять настройки."
        )

        return

    await update.message.reply_text(

        "⚙️ Глобальные настройки\n\n"

        f"🤖 Gemini: "
        f"{settings['gemini_model']}\n"

        f"🖼 Фото: "
        f"{settings['image_model']}\n"

        "🎬 Видео: "
        "LTX-2 Distilled\n\n"

        "⚡ Реагировать на всё: "
        f"{'ВКЛ' if settings['reply_all'] else 'ВЫКЛ'}\n"

        f"🧠 История: "
        f"{settings['history_limit']}\n\n"

        "Настройки общие для всех чатов.",

        reply_markup=settings_keyboard(),
    )


# ============================================================
# CALLBACK
# ============================================================

async def settings_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if query.from_user.id != OWNER_ID:

        await query.edit_message_text(
            "❌ Нет доступа."
        )

        return

    data = query.data

    if data == "reply_on":

        settings["reply_all"] = True

        await query.edit_message_text(
            "✅ Теперь бот отвечает на все сообщения."
        )

    elif data == "reply_off":

        settings["reply_all"] = False

        await query.edit_message_text(
            "✅ Теперь бот отвечает только "
            "на сообщения с `%`."
        )

    elif data == "clear_history":

        clear_all_history()

        await query.edit_message_text(
            "🗑 История всех чатов очищена."
        )

    elif data == "gemini_model":

        context.user_data["waiting"] = (
            "gemini_model"
        )

        await query.edit_message_text(
            "🤖 Отправь Gemini модель.\n\n"
            "Например:\n"
            "gemini-3.5-flash\n\n"
            "Отмена: /cancel"
        )

    elif data == "image_model":

        await query.edit_message_text(
            "🖼 Фото\n\n"
            "Сейчас используется:\n"
            "Flux Schnell через Pixazo."
        )

    elif data == "video_model":

        await query.edit_message_text(
            "🎬 Видео\n\n"
            "Основная модель:\n"
            "LTX-2 Distilled\n\n"
            "Если Space недоступен, бот "
            "попробует fallback Space.\n\n"
            "Генерация выполняется через "
            "бесплатный Hugging Face ZeroGPU."
        )

    elif data == "system_prompt":

        context.user_data["waiting"] = (
            "system_prompt"
        )

        await query.edit_message_text(
            "📝 Отправь новую роль.\n\n"
            "Например:\n"
            "Ты эксперт по Python.\n\n"
            "Отмена: /cancel"
        )

    elif data == "history":

        context.user_data["waiting"] = (
            "history"
        )

        await query.edit_message_text(
            "🧠 Лимит памяти:\n\n"
            "0 — без лимита\n"
            "1 — ничего не помнить\n"
            "10 — последние 10\n"
            "20 — последние 20\n\n"
            "Отправь число."
        )

    elif data == "api_keys":

        context.user_data["waiting"] = (
            "api_keys"
        )

        await query.edit_message_text(
            "🔑 API ключи\n\n"

            "Gemini:\n"
            "gemini: ТВОЙ_КЛЮЧ\n\n"

            "Pixazo:\n"
            "pixazo: ТВОЙ_КЛЮЧ\n\n"

            "Hugging Face:\n"
            "hf: hf_ТВОЙ_КЛЮЧ\n\n"

            "HF Token необязателен.\n"
            "Он может повысить приоритет "
            "в ZeroGPU.\n\n"

            "Отмена: /cancel"
        )


# ============================================================
# SETTINGS INPUT
# ============================================================

async def settings_input(
    update,
    context,
):

    if update.effective_user.id != OWNER_ID:
        return

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return

    text = update.message.text.strip()

    if waiting == "gemini_model":

        settings["gemini_model"] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            f"✅ Gemini модель установлена:\n{text}"
        )

    elif waiting == "system_prompt":

        settings["system_prompt"] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Роль установлена."
        )

    elif waiting == "history":

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
            f"✅ Лимит памяти: {value}"
        )

    elif waiting == "api_keys":

        if ":" not in text:

            await update.message.reply_text(
                "❌ Формат:\n\n"
                "gemini: ключ\n"
                "pixazo: ключ\n"
                "hf: hf_ключ"
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

            answer = (
                "🤖 Gemini API ключ сохранён."
            )

        elif name == "pixazo":

            settings["pixazo_api_key"] = value

            answer = (
                "🖼 Pixazo API ключ сохранён."
            )

        elif name == "hf":

            settings["hf_token"] = value

            answer = (
                "🎬 Hugging Face Token сохранён."
            )

        else:

            await update.message.reply_text(
                "❌ Используй:\n"
                "gemini:\n"
                "pixazo:\n"
                "hf:"
            )

            return

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ " + answer
        )


# ============================================================
# CANCEL
# ============================================================

async def cancel_command(
    update,
    context,
):

    context.user_data.pop(
        "waiting",
        None,
    )

    await update.message.reply_text(
        "❌ Отменено."
    )


# ============================================================
# START
# ============================================================

async def start_command(
    update,
    context,
):

    await update.message.reply_text(
        "🤖 AI Telegram Bot\n\n"

        "🤖 Gemini — текст\n"
        "🖼 Pixazo — фото\n"
        "🎬 LTX — видео\n"
        "👁 Анализ фото\n"
        "🎧 Анализ аудио\n"
        "🎥 Анализ видео\n\n"

        "Примеры:\n\n"

        "%сколько будет 25*25\n"
        "%сгенерируй фото кота\n"
        "%сгенерируй видео кот идёт\n\n"

        "/settings"
    )


# ============================================================
# /image
# ============================================================

async def image_command(
    update,
    context,
):

    if not context.args:

        await update.message.reply_text(
            "/image кот в космосе"
        )

        return

    prompt = " ".join(
        context.args
    )

    try:

        await update.message.chat.send_action(
            ChatAction.UPLOAD_PHOTO
        )

        image = await generate_image(
            prompt
        )

        await update.message.reply_photo(
            photo=image,
            caption="🖼 Готово!"
        )

    except Exception as e:

        logging.exception(
            "IMAGE ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка:\n{e}"
        )


# ============================================================
# /video
# ============================================================

async def video_command(
    update,
    context,
):

    if not context.args:

        await update.message.reply_text(
            "/video кот идёт по улице"
        )

        return

    prompt = " ".join(
        context.args
    )

    status = await update.message.reply_text(
        "🎬 Генерирую видео...\n\n"
        "⏳ Бесплатный GPU может поставить "
        "запрос в очередь."
    )

    try:

        video = await generate_video(
            prompt
        )

        await status.delete()

        await update.message.chat.send_action(
            ChatAction.UPLOAD_VIDEO
        )

        await update.message.reply_video(
            video=video,
            caption="🎬 Готово!"
        )

        try:

            Path(video).unlink(
                missing_ok=True
            )

        except Exception:
            pass

    except Exception as e:

        logging.exception(
            "VIDEO ERROR"
        )

        await status.edit_text(
            "❌ Видео сейчас не удалось создать.\n\n"
            f"{e}"
        )


# ============================================================
# TEXT
# ============================================================

IMAGE_WORDS = [
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
]

VIDEO_WORDS = [
    "сгенерируй видео",
    "создай видео",
    "сделай видео",
    "генерируй видео",
]


async def text_message(
    update,
    context,
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    # Settings input
    if (
        update.effective_user.id == OWNER_ID
        and context.user_data.get("waiting")
    ):

        await settings_input(
            update,
            context,
        )

        return

    # Reply mode
    if settings["reply_all"]:

        prompt = text.strip()

    else:

        if not text.startswith("%"):
            return

        prompt = text[1:].strip()

        if not prompt:
            return

    lower = prompt.lower()

    # ========================================================
    # IMAGE
    # ========================================================

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

        try:

            await update.message.chat.send_action(
                ChatAction.UPLOAD_PHOTO
            )

            image = await generate_image(
                image_prompt
            )

            await update.message.reply_photo(
                photo=image,
                caption="🖼 Готово!"
            )

        except Exception as e:

            logging.exception(
                "IMAGE ERROR"
            )

            await update.message.reply_text(
                f"❌ Ошибка фото:\n{e}"
            )

        return

    # ========================================================
    # VIDEO
    # ========================================================

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
                "кинематографичный пейзаж"
            )

        status = await update.message.reply_text(
            "🎬 Генерирую видео...\n\n"
            "⏳ Ищу свободный бесплатный GPU."
        )

        try:

            video = await generate_video(
                video_prompt
            )

            await status.delete()

            await update.message.chat.send_action(
                ChatAction.UPLOAD_VIDEO
            )

            await update.message.reply_video(
                video=video,
                caption="🎬 Готово!"
            )

            try:

                Path(video).unlink(
                    missing_ok=True
                )

            except Exception:
                pass

        except Exception as e:

            logging.exception(
                "VIDEO ERROR"
            )

            await status.edit_text(
                "❌ Не удалось получить бесплатный GPU.\n\n"
                f"{e}"
            )

        return

    # ========================================================
    # GEMINI
    # ========================================================

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

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
            f"❌ Ошибка Gemini:\n{e}"
        )


# ============================================================
# PHOTO
# ============================================================

async def photo_message(
    update,
    context,
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
            "Подробно опиши это изображение."
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
            f"❌ Ошибка:\n{e}"
        )


# ============================================================
# AUDIO
# ============================================================

async def audio_message(
    update,
    context,
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

        media = (
            update.message.audio
            or update.message.voice
        )

        file = await context.bot.get_file(
            media.file_id
        )

        buffer = io.BytesIO()

        await file.download_to_memory(
            out=buffer
        )

        mime = (
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
            mime,
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "AUDIO ERROR"
        )

        await update.message.reply_text(
            f"❌ Ошибка:\n{e}"
        )


# ============================================================
# VIDEO ANALYSIS
# ============================================================

async def video_message(
    update,
    context,
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
            f"❌ Ошибка:\n{e}"
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
            "%(asctime)s "
            "%(levelname)s "
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
        "🤖 Bot started"
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
