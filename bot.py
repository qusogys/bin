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

from PIL import Image

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

OWNER_ID = 8904429775


# ============================================================
# SETTINGS
# ============================================================

settings = {
    # TEXT
    "gemini_model": "gemini-3.5-flash",

    # IMAGE
    "image_model": "flux-schnell",

    # VIDEO
    "video_space": "zerogpu-aoti/wan2-2-fp8da-aoti-faster",

    # API
    "gemini_api_key": "",
    "pixazo_api_key": "",
    "hf_token": "",

    # BOT
    "reply_all": False,

    # HISTORY
    #
    # 0 = без лимита
    # 1 = ничего не помнить
    # N = последние N сообщений
    "history_limit": 20,

    # SYSTEM
    "system_prompt": (
        "Ты полезный AI-ассистент Telegram-бота. "
        "Отвечай понятно, кратко и по делу."
    ),
}


# ============================================================
# HISTORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id, role, text):

    limit = settings["history_limit"]

    # 1 = ничего не сохраняем
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

    key = settings["gemini_api_key"].strip()

    if not key:

        raise RuntimeError(
            "Gemini API ключ не установлен.\n"
            "Открой /settings → 🔑 API ключи."
        )

    genai.configure(
        api_key=key
    )


async def gemini_text(
    chat_id,
    prompt,
):

    configure_gemini()

    model = genai.GenerativeModel(
        settings["gemini_model"],
        system_instruction=(
            settings["system_prompt"]
        ),
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
        system_instruction=(
            settings["system_prompt"]
        ),
    )

    response = await asyncio.to_thread(
        model.generate_content,
        [
            prompt,
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

PIXAZO_IMAGE_URL = (
    "https://gateway.pixazo.ai/"
    "flux-1-schnell/v1/getData"
)


async def generate_image(prompt):

    key = settings[
        "pixazo_api_key"
    ].strip()

    if not key:

        raise RuntimeError(
            "Pixazo API ключ не установлен."
        )

    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": key,
    }

    payload = {
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
            PIXAZO_IMAGE_URL,
            headers=headers,
            json=payload,
        ) as response:

            text = await response.text()

            if response.status != 200:

                raise RuntimeError(
                    f"Pixazo HTTP "
                    f"{response.status}\n{text}"
                )

            try:
                data = await response.json()

            except Exception:

                raise RuntimeError(
                    f"Pixazo вернул:\n{text}"
                )

    image_url = (
        data.get("output")
        or data.get("image")
        or data.get("url")
        or data.get("output_url")
    )

    if not image_url:

        raise RuntimeError(
            "Pixazo не вернул URL картинки:\n"
            + str(data)
        )

    return image_url


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

async def download_file(
    url,
    output_path,
):

    timeout = aiohttp.ClientTimeout(
        total=180
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(url) as response:

            if response.status != 200:

                raise RuntimeError(
                    f"Не удалось скачать файл: "
                    f"HTTP {response.status}"
                )

            data = await response.read()

    with open(
        output_path,
        "wb",
    ) as f:

        f.write(data)


# ============================================================
# GRADIO
# ============================================================

def make_gradio_client():

    space = settings[
        "video_space"
    ]

    token = settings[
        "hf_token"
    ].strip()

    if token:

        try:

            return Client(
                space,
                token=token,
            )

        except TypeError:

            return Client(
                space,
                hf_token=token,
            )

    return Client(space)


def get_api_info(client):

    try:

        return client.view_api(
            all_endpoints=True,
            return_format="dict",
        )

    except TypeError:

        return client.view_api(
            all_endpoints=True,
        )


# ============================================================
# FIND REAL VIDEO ENDPOINT
# ============================================================

def find_video_endpoint(
    api_info,
):

    if not api_info:
        return None, None

    endpoints = {}

    if isinstance(
        api_info,
        dict,
    ):

        named = api_info.get(
            "named_endpoints"
        )

        if isinstance(
            named,
            dict,
        ):

            endpoints.update(named)

        for key, value in api_info.items():

            if isinstance(
                value,
                dict,
            ):

                if (
                    "parameters" in value
                    or "inputs" in value
                ):

                    endpoints[str(key)] = value

    if not endpoints:

        return None, None

    # Сначала ищем именно generate_video
    for name, data in endpoints.items():

        clean = str(name).lower()

        if (
            "generate_video"
            in clean
        ):

            return (
                normalize_endpoint(name),
                data,
            )

    # Потом любой endpoint с video
    for name, data in endpoints.items():

        text = (
            str(name)
            + " "
            + str(data)
        ).lower()

        if "video" in text:

            return (
                normalize_endpoint(name),
                data,
            )

    return None, None


def normalize_endpoint(name):

    name = str(name)

    if not name.startswith("/"):
        name = "/" + name

    return name


# ============================================================
# PARAMETERS
# ============================================================

def get_parameters(
    endpoint_data,
):

    if not isinstance(
        endpoint_data,
        dict,
    ):

        return []

    return (
        endpoint_data.get(
            "parameters"
        )
        or endpoint_data.get(
            "inputs"
        )
        or []
    )


def get_parameter_name(
    parameter,
    index,
):

    if isinstance(
        parameter,
        dict,
    ):

        return (
            parameter.get(
                "parameter_name"
            )
            or parameter.get(
                "name"
            )
            or parameter.get(
                "label"
            )
            or f"arg_{index}"
        )

    return f"arg_{index}"


def get_default(
    parameter,
):

    if not isinstance(
        parameter,
        dict,
    ):

        return None

    return (
        parameter.get(
            "parameter_default"
        )
        if "parameter_default"
        in parameter
        else parameter.get(
            "default"
        )
    )


# ============================================================
# BUILD WAN ARGUMENTS
# ============================================================

def build_wan_arguments(
    endpoint_data,
    prompt,
    image_path,
):

    parameters = get_parameters(
        endpoint_data
    )

    kwargs = {}

    seed = random.randint(
        0,
        2_147_483_647,
    )

    for index, parameter in enumerate(
        parameters
    ):

        name = get_parameter_name(
            parameter,
            index,
        )

        lname = name.lower()

        # INPUT IMAGE
        if (
            "input_image"
            in lname
            or lname in {
                "image",
                "img",
                "init_image",
            }
        ):

            kwargs[name] = handle_file(
                image_path
            )

        # PROMPT
        elif (
            "prompt" in lname
            and "negative"
            not in lname
        ):

            kwargs[name] = prompt

        # NEGATIVE
        elif (
            "negative_prompt"
            in lname
        ):

            kwargs[name] = (
                "blurry, low quality, "
                "distorted, deformed, "
                "watermark"
            )

        # STEPS
        elif (
            "steps" in lname
            or "inference_steps"
            in lname
        ):

            kwargs[name] = 4

        # DURATION
        elif (
            "duration"
            in lname
        ):

            kwargs[name] = 3.5

        # GUIDANCE
        elif (
            "guidance_scale"
            in lname
        ):

            kwargs[name] = 1.0

        # SECOND GUIDANCE
        elif (
            "guidance_scale_2"
            in lname
        ):

            kwargs[name] = 1.0

        # SEED
        elif lname == "seed":

            kwargs[name] = seed

        # RANDOM SEED
        elif (
            "randomize_seed"
            in lname
        ):

            kwargs[name] = True

        # DEFAULT
        else:

            default = get_default(
                parameter
            )

            if default is not None:

                kwargs[name] = default

    return kwargs


# ============================================================
# EXTRACT VIDEO
# ============================================================

def extract_video(result):

    if result is None:
        return None

    if isinstance(
        result,
        str,
    ):

        if (
            result.startswith(
                "http://"
            )
            or result.startswith(
                "https://"
            )
            or os.path.exists(result)
            or result.lower().endswith(
                (
                    ".mp4",
                    ".webm",
                    ".mov",
                )
            )
        ):

            return result

    if isinstance(
        result,
        dict,
    ):

        for key in (
            "path",
            "url",
            "video",
            "file",
        ):

            if result.get(key):

                return result[key]

        for value in result.values():

            found = extract_video(
                value
            )

            if found:
                return found

    if isinstance(
        result,
        (list, tuple),
    ):

        for value in result:

            found = extract_video(
                value
            )

            if found:
                return found

    return None


# ============================================================
# TEXT → IMAGE → VIDEO
# ============================================================

async def generate_video(
    prompt,
):

    image_url = None
    image_path = None

    try:

        # ----------------------------------------------------
        # STEP 1 — PIXAZO IMAGE
        # ----------------------------------------------------

        image_url = await generate_image(
            prompt
        )

        logging.info(
            "Generated image: %s",
            image_url
        )

        # ----------------------------------------------------
        # STEP 2 — DOWNLOAD
        # ----------------------------------------------------

        fd, image_path = tempfile.mkstemp(
            suffix=".png"
        )

        os.close(fd)

        await download_file(
            image_url,
            image_path,
        )

        # Проверяем, что это действительно картинка
        Image.open(
            image_path
        ).verify()

        # ----------------------------------------------------
        # STEP 3 — WAN
        # ----------------------------------------------------

        client = make_gradio_client()

        api_info = get_api_info(
            client
        )

        logging.info(
            "Wan API: %s",
            api_info
        )

        endpoint_name, endpoint_data = (
            find_video_endpoint(
                api_info
            )
        )

        if not endpoint_name:

            raise RuntimeError(
                "Не найден endpoint "
                "generate_video в Wan2.2."
            )

        logging.info(
            "Using endpoint: %s",
            endpoint_name
        )

        kwargs = build_wan_arguments(
            endpoint_data,
            prompt,
            image_path,
        )

        logging.info(
            "Wan arguments: %s",
            list(kwargs.keys())
        )

        job = client.submit(
            api_name=endpoint_name,
            **kwargs,
        )

        result = job.result()

        logging.info(
            "Wan result: %r",
            result,
        )

        video = extract_video(
            result
        )

        if not video:

            raise RuntimeError(
                "Wan завершил генерацию, "
                "но видео не найдено:\n"
                + str(result)
            )

        return video

    finally:

        if image_path:

            try:
                os.remove(
                    image_path
                )

            except Exception:
                pass


# ============================================================
# SETTINGS KEYBOARD
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🤖 Модель текста",
                callback_data="text_model",
            ),
        ],

        [
            InlineKeyboardButton(
                "🖼 Модель фото",
                callback_data="image_model",
            ),
        ],

        [
            InlineKeyboardButton(
                "🎬 Модель видео",
                callback_data="video_model",
            ),
        ],

        [
            InlineKeyboardButton(
                "🧠 Память",
                callback_data="history",
            ),

            InlineKeyboardButton(
                "🗑 Очистить",
                callback_data="clear_history",
            ),
        ],

        [
            InlineKeyboardButton(
                "📝 Роль",
                callback_data="system_prompt",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔑 API ключи",
                callback_data="api_keys",
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
    ])


# ============================================================
# /SETTINGS
# ============================================================

async def settings_command(
    update,
    context,
):

    if (
        update.effective_user.id
        != OWNER_ID
    ):

        await update.message.reply_text(
            "❌ Нет доступа."
        )

        return

    await update.message.reply_text(

        "⚙️ Настройки\n\n"

        f"🤖 Текст: "
        f"{settings['gemini_model']}\n"

        f"🖼 Фото: "
        f"{settings['image_model']}\n"

        f"🎬 Видео: "
        f"{settings['video_space']}\n\n"

        f"🧠 Память: "
        f"{settings['history_limit']}\n"

        f"💬 Ответ на всё: "
        f"{'ДА' if settings['reply_all'] else 'НЕТ'}",

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

    if (
        query.from_user.id
        != OWNER_ID
    ):

        await query.edit_message_text(
            "❌ Нет доступа."
        )

        return

    data = query.data

    # TEXT MODEL

    if data == "text_model":

        context.user_data[
            "waiting"
        ] = "text_model"

        await query.edit_message_text(
            "🤖 Отправь модель текста.\n\n"
            "Например:\n"
            "gemini-3.5-flash\n\n"
            "/cancel"
        )

    # IMAGE MODEL

    elif data == "image_model":

        context.user_data[
            "waiting"
        ] = "image_model"

        await query.edit_message_text(
            "🖼 Отправь название модели фото.\n\n"
            "Например:\n"
            "flux-schnell\n\n"
            "/cancel"
        )

    # VIDEO MODEL

    elif data == "video_model":

        await query.edit_message_text(

            "🎬 Видео\n\n"

            "Сейчас используется:\n"
            "Wan2.2 14B Fast\n\n"

            "Схема:\n"
            "текст → Pixazo → картинка "
            "→ Wan2.2 → видео\n\n"

            "Space:\n"
            "zerogpu-aoti/"
            "wan2-2-fp8da-aoti-faster"
        )

    # HISTORY

    elif data == "history":

        context.user_data[
            "waiting"
        ] = "history"

        await query.edit_message_text(

            "🧠 Лимит памяти:\n\n"

            "0 — без лимита\n"
            "1 — ничего не помнить\n"
            "10 — последние 10\n"
            "20 — последние 20\n\n"

            "Отправь число."
        )

    # CLEAR

    elif data == "clear_history":

        clear_all_history()

        await query.edit_message_text(
            "🗑 История всех чатов очищена."
        )

    # ROLE

    elif data == "system_prompt":

        context.user_data[
            "waiting"
        ] = "system_prompt"

        await query.edit_message_text(

            "📝 Отправь новую роль.\n\n"

            "Например:\n"
            "Ты эксперт по Python.\n\n"

            "/cancel"
        )

    # API

    elif data == "api_keys":

        context.user_data[
            "waiting"
        ] = "api_keys"

        await query.edit_message_text(

            "🔑 API ключи\n\n"

            "Можно отправить по одному:\n\n"

            "gemini: ключ\n"
            "pixazo: ключ\n"
            "hf: hf_ключ\n\n"

            "Для видео HF Token "
            "не обязателен.\n\n"

            "/cancel"
        )

    # REPLY ON

    elif data == "reply_on":

        settings["reply_all"] = True

        await query.edit_message_text(
            "✅ Теперь бот отвечает "
            "на все сообщения."
        )

    # REPLY OFF

    elif data == "reply_off":

        settings["reply_all"] = False

        await query.edit_message_text(
            "✅ Теперь бот отвечает "
            "только на сообщения с %."
        )


# ============================================================
# SETTINGS INPUT
# ============================================================

async def settings_input(
    update,
    context,
):

    if (
        update.effective_user.id
        != OWNER_ID
    ):
        return

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return

    text = update.message.text.strip()

    # TEXT MODEL

    if waiting == "text_model":

        settings[
            "gemini_model"
        ] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Модель текста:\n"
            + text
        )

    # IMAGE MODEL

    elif waiting == "image_model":

        settings[
            "image_model"
        ] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Модель фото:\n"
            + text
        )

    # HISTORY

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

        settings[
            "history_limit"
        ] = value

        if value == 1:

            clear_all_history()

        elif value > 1:

            for chat_id in histories:

                histories[
                    chat_id
                ] = deque(
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

    # ROLE

    elif waiting == "system_prompt":

        settings[
            "system_prompt"
        ] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Роль изменена."
        )

    # API

    elif waiting == "api_keys":

        if ":" not in text:

            await update.message.reply_text(
                "❌ Формат:\n\n"
                "gemini: ключ\n"
                "pixazo: ключ\n"
                "hf: ключ"
            )

            return

        name, value = text.split(
            ":",
            1,
        )

        name = name.strip().lower()
        value = value.strip()

        if name == "gemini":

            settings[
                "gemini_api_key"
            ] = value

            answer = (
                "🤖 Gemini ключ сохранён."
            )

        elif name == "pixazo":

            settings[
                "pixazo_api_key"
            ] = value

            answer = (
                "🖼 Pixazo ключ сохранён."
            )

        elif name == "hf":

            settings[
                "hf_token"
            ] = value

            answer = (
                "🤗 Hugging Face Token "
                "сохранён."
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

        "🤖 AI BOT\n\n"

        "📝 Текст — Gemini\n"
        "🖼 Фото — Pixazo\n"
        "🎬 Видео — Pixazo + Wan2.2\n"
        "🧠 Память чата\n\n"

        "Примеры:\n\n"

        "%привет\n"
        "%сгенерируй фото кота\n"
        "%сгенерируй видео кот идёт по улице\n\n"

        "/settings"
    )


# ============================================================
# IMAGE COMMAND
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

        url = await generate_image(
            prompt
        )

        await update.message.reply_photo(
            photo=url,
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
# VIDEO COMMAND
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
        "1️⃣ Создаю стартовый кадр\n"
        "2️⃣ Передаю его Wan2.2\n"
        "3️⃣ Жду видео"
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

    except Exception as e:

        logging.exception(
            "VIDEO ERROR"
        )

        await status.edit_text(
            "❌ Ошибка видео:\n\n"
            + str(e)
        )


# ============================================================
# TEXT
# ============================================================

IMAGE_WORDS = [
    "сгенерируй фото",
    "сгенерируй картинку",
    "сгенерируй изображение",
    "создай фото",
    "создай картинку",
    "создай изображение",
    "сделай фото",
    "сделай картинку",
]

VIDEO_WORDS = [
    "сгенерируй видео",
    "создай видео",
    "сделай видео",
]


async def text_message(
    update,
    context,
):

    text = update.message.text

    if (
        update.effective_user.id
        == OWNER_ID
        and context.user_data.get(
            "waiting"
        )
    ):

        await settings_input(
            update,
            context,
        )

        return

    if settings["reply_all"]:

        prompt = text.strip()

    else:

        if not text.startswith("%"):
            return

        prompt = text[1:].strip()

    lower = prompt.lower()

    # IMAGE

    for word in IMAGE_WORDS:

        if lower.startswith(word):

            image_prompt = prompt[
                len(word):
            ].strip()

            if not image_prompt:
                image_prompt = (
                    "реалистичная фотография"
                )

            try:

                await update.message.chat.send_action(
                    ChatAction.UPLOAD_PHOTO
                )

                url = await generate_image(
                    image_prompt
                )

                await update.message.reply_photo(
                    photo=url,
                    caption="🖼 Готово!"
                )

            except Exception as e:

                await update.message.reply_text(
                    "❌ Ошибка фото:\n"
                    + str(e)
                )

            return

    # VIDEO

    for word in VIDEO_WORDS:

        if lower.startswith(word):

            video_prompt = prompt[
                len(word):
            ].strip()

            if not video_prompt:
                video_prompt = (
                    "кинематографичный пейзаж"
                )

            status = (
                await update.message.reply_text(
                    "🎬 Генерирую видео..."
                )
            )

            try:

                video = await generate_video(
                    video_prompt
                )

                await status.delete()

                await update.message.reply_video(
                    video=video,
                    caption="🎬 Готово!"
                )

            except Exception as e:

                await status.edit_text(
                    "❌ Ошибка видео:\n"
                    + str(e)
                )

            return

    # TEXT

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

        await update.message.reply_text(
            "❌ Ошибка Gemini:\n"
            + str(e)
        )


# ============================================================
# PHOTO ANALYSIS
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
            "Подробно опиши изображение."
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

        await update.message.reply_text(
            "❌ Ошибка анализа:\n"
            + str(e)
        )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context,
):

    logging.error(
        "Unhandled exception",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "Не установлен BOT_TOKEN."
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

    app.add_handler(
        CallbackQueryHandler(
            settings_callback
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message,
        )
    )

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

    print("🤖 Bot started")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
