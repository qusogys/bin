import asyncio
import io
import logging
import os
import random
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

OWNER_ID = 8904429775


# ============================================================
# GLOBAL SETTINGS
# ============================================================

settings = {
    # TEXT
    "gemini_model": "gemini-3.5-flash",

    # IMAGE
    "image_model": "flux-schnell",

    # VIDEO
    "video_model": "wan2.2-fast",

    # ROLE
    "system_prompt": (
        "Ты полезный AI-ассистент в Telegram. "
        "Отвечай понятно, точно и на языке пользователя."
    ),

    # True:
    # отвечает на все сообщения
    #
    # False:
    # отвечает только если сообщение начинается с %
    "reply_all": False,

    # HISTORY
    #
    # 0 = без лимита
    # 1 = ничего не помнить
    # N = помнить последние N сообщений
    "history_limit": 20,

    # API
    "gemini_api_key": "",
    "pixazo_api_key": "",
    "hf_token": "",
}


# ============================================================
# VIDEO SPACES
# ============================================================

VIDEO_SPACES = [
    {
        "name": "Wan2.2 14B Fast",
        "space": "zerogpu-aoti/wan2-2-fp8da-aoti-faster",
        "kind": "wan_i2v",
    },
    {
        "name": "Wan2.2 14B Fast Preview",
        "space": "r3gm/wan2-2-fp8da-aoti-preview-2c",
        "kind": "wan_i2v",
    },
]


# ============================================================
# HISTORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id, role, text):

    limit = settings["history_limit"]

    # 1 = вообще ничего не помнить
    if limit == 1:
        return

    # 0 = без лимита
    if limit == 0:
        histories[chat_id].append({
            "role": role,
            "text": text,
        })
        return

    # N
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
            "Открой /settings → 🔑 API ключи."
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
# PIXAZO
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
# GRADIO HELPERS
# ============================================================

def make_gradio_client(space):

    token = settings.get("hf_token", "").strip()

    if token:

        try:
            return Client(
                space,
                token=token,
            )
        except TypeError:
            return Client(space)

    return Client(space)


def get_api_info(client):

    """
    Получает реальную информацию об API Space.

    Это важно:
    мы НЕ угадываем api_name.
    """

    try:

        return client.view_api(
            all_endpoints=True,
            return_format="dict",
        )

    except TypeError:

        # Для старых версий gradio_client
        return client.view_api(
            all_endpoints=True,
        )


def normalize_endpoint_name(name):

    if not name:
        return ""

    name = str(name)

    if not name.startswith("/"):
        name = "/" + name

    return name


def find_video_endpoint(api_info):

    """
    Автоматически ищет endpoint,
    связанный с video generation.
    """

    if not api_info:
        return None, None

    endpoints = {}

    if isinstance(api_info, dict):

        # Новый формат может выглядеть:
        #
        # {
        #   "named_endpoints": {
        #       "/generate": {...}
        #   }
        # }

        named = api_info.get(
            "named_endpoints"
        )

        if isinstance(named, dict):
            endpoints.update(named)

        # Иногда endpoint-ы находятся
        # непосредственно в словаре.

        for key, value in api_info.items():

            if isinstance(value, dict):

                if (
                    "parameters" in value
                    or "inputs" in value
                    or "input" in value
                ):
                    endpoints[str(key)] = value

    # Если ничего не нашли
    if not endpoints:
        return None, None

    best = None
    best_score = -999

    for endpoint_name, endpoint_data in endpoints.items():

        endpoint_text = (
            str(endpoint_name)
            + " "
            + str(endpoint_data)
        ).lower()

        score = 0

        # Сильные признаки генерации
        if "video" in endpoint_text:
            score += 10

        if "generate" in endpoint_text:
            score += 8

        if "predict" in endpoint_text:
            score += 2

        if "image" in endpoint_text:
            score += 3

        if "prompt" in endpoint_text:
            score += 4

        if "duration" in endpoint_text:
            score += 3

        if "seed" in endpoint_text:
            score += 2

        if "negative_prompt" in endpoint_text:
            score += 1

        if score > best_score:

            best_score = score

            best = (
                normalize_endpoint_name(
                    endpoint_name
                ),
                endpoint_data,
            )

    return best


def endpoint_parameters(endpoint_data):

    """
    Достаёт список параметров endpoint.
    """

    if not isinstance(
        endpoint_data,
        dict,
    ):
        return []

    params = (
        endpoint_data.get("parameters")
        or endpoint_data.get("inputs")
        or []
    )

    if isinstance(params, dict):
        return list(params.items())

    return params


def parameter_name(param, index):

    if isinstance(param, dict):

        return (
            param.get("parameter_name")
            or param.get("name")
            or param.get("label")
            or f"arg_{index}"
        )

    return f"arg_{index}"


def parameter_default(param):

    if not isinstance(
        param,
        dict,
    ):
        return None

    return (
        param.get("parameter_default")
        if "parameter_default" in param
        else param.get("default")
    )


def parameter_optional(param):

    if not isinstance(
        param,
        dict,
    ):
        return False

    return bool(
        param.get(
            "parameter_has_default",
            False,
        )
        or param.get(
            "optional",
            False,
        )
    )


# ============================================================
# WAN2.2 AUTO CALL
# ============================================================

def build_wan_arguments(
    endpoint_data,
    prompt,
):
    """
    Автоматически строит аргументы
    по названиям параметров Gradio API.

    Нам НЕ нужно знать точный порядок.
    """

    params = endpoint_parameters(
        endpoint_data
    )

    kwargs = {}

    seed = random.randint(
        1,
        2_000_000_000,
    )

    for index, param in enumerate(params):

        name = parameter_name(
            param,
            index,
        )

        lname = name.lower()

        # ----------------------------------------------------
        # IMAGE
        # ----------------------------------------------------

        if (
            "input_image" in lname
            or lname in {
                "image",
                "img",
                "init_image",
                "start_image",
            }
        ):

            # Wan2.2 Fast требует изображение.
            #
            # Здесь создаём минимальный
            # placeholder через PIL.
            #
            # Пользовательский prompt всё равно
            # передаётся отдельно.

            from PIL import Image

            placeholder = Image.new(
                "RGB",
                (512, 512),
                "gray",
            )

            kwargs[name] = placeholder

        # ----------------------------------------------------
        # PROMPT
        # ----------------------------------------------------

        elif (
            "prompt" in lname
            and "negative" not in lname
        ):

            kwargs[name] = prompt

        # ----------------------------------------------------
        # NEGATIVE PROMPT
        # ----------------------------------------------------

        elif "negative_prompt" in lname:

            kwargs[name] = (
                "blurry, low quality, "
                "distorted, watermark"
            )

        # ----------------------------------------------------
        # DURATION
        # ----------------------------------------------------

        elif (
            "duration" in lname
            or "seconds" in lname
        ):

            kwargs[name] = 2.0

        # ----------------------------------------------------
        # SEED
        # ----------------------------------------------------

        elif lname == "seed":

            kwargs[name] = seed

        elif (
            "randomize_seed" in lname
            or "random_seed" in lname
        ):

            kwargs[name] = True

        # ----------------------------------------------------
        # STEPS
        # ----------------------------------------------------

        elif (
            "steps" in lname
            or "inference_steps" in lname
        ):

            # Fast Wan обычно работает
            # на небольшом количестве steps.
            kwargs[name] = 4

        # ----------------------------------------------------
        # GUIDANCE
        # ----------------------------------------------------

        elif (
            "guidance_scale" in lname
            or lname == "cfg"
        ):

            default = parameter_default(
                param
            )

            if default is not None:
                kwargs[name] = default
            else:
                kwargs[name] = 5.0

        # ----------------------------------------------------
        # HEIGHT
        # ----------------------------------------------------

        elif lname == "height":

            kwargs[name] = 512

        # ----------------------------------------------------
        # WIDTH
        # ----------------------------------------------------

        elif lname == "width":

            kwargs[name] = 512

        # ----------------------------------------------------
        # FPS
        # ----------------------------------------------------

        elif "fps" in lname:

            default = parameter_default(
                param
            )

            if default is not None:
                kwargs[name] = default

        # ----------------------------------------------------
        # OTHER BOOLEAN OPTIONS
        # ----------------------------------------------------

        elif (
            lname.startswith("enhance")
            or lname.startswith("random")
        ):

            default = parameter_default(
                param
            )

            if default is not None:
                kwargs[name] = default

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------

        else:

            default = parameter_default(
                param
            )

            if default is not None:
                kwargs[name] = default

    return kwargs


def extract_video(result):

    """
    Извлекает путь к видео
    из результата Gradio.
    """

    if result is None:
        return None

    if isinstance(
        result,
        (str, Path),
    ):

        value = str(result)

        if (
            value.startswith("http://")
            or value.startswith("https://")
            or os.path.exists(value)
            or ".mp4" in value.lower()
            or ".webm" in value.lower()
            or ".mov" in value.lower()
        ):
            return value

    if isinstance(result, dict):

        # Gradio FileData

        for key in (
            "path",
            "url",
            "video",
            "file",
        ):

            value = result.get(key)

            if value:
                return value

        # nested
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

        for item in result:

            found = extract_video(
                item
            )

            if found:
                return found

    return None


# ============================================================
# VIDEO
# ============================================================

def generate_video_sync(prompt):

    errors = []

    for item in VIDEO_SPACES:

        space_name = item["name"]
        space_id = item["space"]

        try:

            logging.info(
                "Connecting to video Space: %s",
                space_id,
            )

            client = make_gradio_client(
                space_id
            )

            # ------------------------------------------------
            # GET REAL API
            # ------------------------------------------------

            api_info = get_api_info(
                client
            )

            logging.info(
                "API info for %s: %s",
                space_id,
                api_info,
            )

            endpoint_name, endpoint_data = (
                find_video_endpoint(
                    api_info
                )
            )

            if not endpoint_name:

                raise RuntimeError(
                    "Не найден video endpoint. "
                    "API Space не предоставил "
                    "подходящий endpoint."
                )

            logging.info(
                "Selected endpoint: %s",
                endpoint_name,
            )

            # ------------------------------------------------
            # BUILD ARGUMENTS
            # ------------------------------------------------

            kwargs = build_wan_arguments(
                endpoint_data,
                prompt,
            )

            logging.info(
                "Calling %s with kwargs: %s",
                endpoint_name,
                list(kwargs.keys()),
            )

            # ------------------------------------------------
            # SUBMIT
            # ------------------------------------------------

            job = client.submit(
                api_name=endpoint_name,
                **kwargs,
            )

            # ------------------------------------------------
            # WAIT
            # ------------------------------------------------

            result = job.result()

            video = extract_video(
                result
            )

            if not video:

                raise RuntimeError(
                    "Space завершил запрос, "
                    "но видеофайл не найден "
                    f"в результате: {result}"
                )

            logging.info(
                "Video result: %s",
                video,
            )

            return video

        except Exception as e:

            logging.exception(
                "Video Space failed: %s",
                space_id,
            )

            errors.append(
                f"• {space_name}: {e}"
            )

    raise RuntimeError(
        "Все бесплатные video Space "
        "недоступны.\n\n"
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

    if (
        update.effective_user.id
        != OWNER_ID
    ):

        await update.message.reply_text(
            "❌ Только владелец бота "
            "может менять настройки."
        )

        return

    await update.message.reply_text(

        "⚙️ Глобальные настройки\n\n"

        f"🤖 Gemini: "
        f"{settings['gemini_model']}\n"

        f"🖼 Фото: "
        f"{settings['image_model']}\n"

        "🎬 Видео: Wan2.2 14B Fast\n\n"

        "⚡ Реагировать на всё: "
        f"{'ВКЛ' if settings['reply_all'] else 'ВЫКЛ'}\n"

        f"🧠 История: "
        f"{settings['history_limit']}\n\n"

        "Настройки общие для всех чатов.",

        reply_markup=settings_keyboard(),
    )


# ============================================================
# SETTINGS CALLBACK
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

    # --------------------------------------------------------
    # REPLY ON
    # --------------------------------------------------------

    if data == "reply_on":

        settings["reply_all"] = True

        await query.edit_message_text(
            "✅ Бот теперь реагирует "
            "на все сообщения."
        )

    # --------------------------------------------------------
    # REPLY OFF
    # --------------------------------------------------------

    elif data == "reply_off":

        settings["reply_all"] = False

        await query.edit_message_text(
            "✅ Бот теперь реагирует "
            "только на сообщения с `%`."
        )

    # --------------------------------------------------------
    # CLEAR HISTORY
    # --------------------------------------------------------

    elif data == "clear_history":

        clear_all_history()

        await query.edit_message_text(
            "🗑 История всех чатов очищена."
        )

    # --------------------------------------------------------
    # GEMINI MODEL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # IMAGE MODEL
    # --------------------------------------------------------

    elif data == "image_model":

        await query.edit_message_text(
            "🖼 Генерация фото\n\n"
            "Сейчас:\n"
            "Flux Schnell через Pixazo."
        )

    # --------------------------------------------------------
    # VIDEO MODEL
    # --------------------------------------------------------

    elif data == "video_model":

        await query.edit_message_text(
            "🎬 Генерация видео\n\n"

            "Основная:\n"
            "Wan2.2 14B Fast\n\n"

            "Fallback:\n"
            "Wan2.2 14B Fast Preview\n\n"

            "API Gradio определяется "
            "автоматически."
        )

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # API KEYS
    # --------------------------------------------------------

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

            "HF Token необязателен.\n\n"

            "Отмена: /cancel"
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

    # --------------------------------------------------------
    # GEMINI MODEL
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
    # ROLE
    # --------------------------------------------------------

    elif waiting == "system_prompt":

        settings["system_prompt"] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Роль установлена."
        )

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    elif waiting == "history":

        try:

            value = int(text)

            if value < 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Нужно отправить "
                "целое число."
            )

            return

        settings["history_limit"] = value

        if value == 1:

            clear_all_history()

        elif value > 1:

            for chat_id in list(
                histories
            ):

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

    # --------------------------------------------------------
    # API KEYS
    # --------------------------------------------------------

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

            settings[
                "gemini_api_key"
            ] = value

            answer = (
                "🤖 Gemini API ключ сохранён."
            )

        elif name == "pixazo":

            settings[
                "pixazo_api_key"
            ] = value

            answer = (
                "🖼 Pixazo API ключ сохранён."
            )

        elif name == "hf":

            settings["hf_token"] = value

            answer = (
                "🎬 Hugging Face Token "
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
        "🤖 AI Telegram Bot\n\n"

        "🤖 Gemini — текст\n"
        "🖼 Pixazo — фото\n"
        "🎬 Wan2.2 — видео\n"
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
# /IMAGE
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
# /VIDEO
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
        "⏳ Бесплатный ZeroGPU "
        "может поставить запрос "
        "в очередь."
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

            if isinstance(
                video,
                str
            ):

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
            "❌ Не удалось создать видео.\n\n"
            f"{e}"
        )


# ============================================================
# TEXT MESSAGE
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

    # --------------------------------------------------------
    # SETTINGS INPUT
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # REPLY MODE
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
                "кинематографичный пейзаж"
            )

        status = await update.message.reply_text(
            "🎬 Генерирую видео...\n\n"
            "⏳ Проверяю бесплатные "
            "ZeroGPU Spaces."
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

                if isinstance(
                    video,
                    str
                ):

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
                "❌ Видео не удалось создать.\n\n"
                f"{e}"
            )

        return

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

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

    # COMMANDS

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

    # SETTINGS

    app.add_handler(
        CallbackQueryHandler(
            settings_callback
        )
    )

    # MEDIA

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

    # TEXT

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
