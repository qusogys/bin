import logging
import os
from collections import defaultdict, deque
from urllib.parse import quote

import aiohttp
import google.generativeai as genai

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

# Твой Telegram ID
OWNER_ID = 8904429775


# ============================================================
# SETTINGS
# ============================================================

settings = {

    # ---------------- TEXT ----------------

    "gemini_model": "gemini-3.5-flash",

    "gemini_api_key": "",


    # ---------------- IMAGE ----------------

    # Основная модель
    "image_model": "seedream5",

    "pollinations_api_key": "",


    # ---------------- BOT ----------------

    # False:
    # бот отвечает только на сообщения с %
    #
    # True:
    # бот отвечает на обычные сообщения тоже

    "reply_all": False,


    # ---------------- MEMORY ----------------

    # 0 = без лимита
    # 1 = вообще не помнить
    # 10 = последние 10
    # 20 = последние 20

    "history_limit": 20,


    # ---------------- SYSTEM PROMPT ----------------

    "system_prompt": (
        "Ты полезный AI-ассистент Telegram-бота. "
        "Отвечай понятно, грамотно и по делу."
    ),
}


# ============================================================
# AVAILABLE IMAGE MODELS
# ============================================================

IMAGE_MODELS = {

    "seedream5": "⭐ Seedream 5",

    "nanobanana-2": "🍌 Nano Banana 2",

    "nanobanana-pro": "🍌 Nano Banana Pro",

    "ideogram-v4-quality":
        "🎨 Ideogram V4 Quality",

    "gpt-image-2":
        "🧠 GPT Image 2",

    "gptimage-large":
        "🧠 GPT Image Large",

    "flux":
        "⚡ FLUX",

    "grok-imagine-pro":
        "🌌 Grok Imagine Pro",

    "qwen-image":
        "🔮 Qwen Image",

    "wan-image-pro":
        "🔥 Wan Image Pro",

    "zimage":
        "✨ Z-Image",
}


# ============================================================
# HISTORY
# ============================================================

histories = defaultdict(deque)


def add_history(
    chat_id,
    role,
    text,
):

    limit = settings["history_limit"]

    # 1 = ничего не сохранять
    if limit == 1:
        return

    # 0 = без ограничения
    if limit == 0:

        histories[chat_id].append({
            "role": role,
            "text": text,
        })

        return

    # Ограниченная память

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

    return list(
        histories[chat_id]
    )[-limit:]


def clear_history(chat_id):

    histories.pop(
        chat_id,
        None,
    )


def clear_all_history():

    histories.clear()


# ============================================================
# GEMINI
# ============================================================

def configure_gemini():

    key = settings[
        "gemini_api_key"
    ].strip()

    if not key:

        raise RuntimeError(
            "Gemini API ключ не установлен.\n\n"
            "Открой /settings → 🔑 API ключи"
        )

    genai.configure(
        api_key=key
    )


async def generate_text(
    chat_id,
    prompt,
):

    configure_gemini()

    model = genai.GenerativeModel(

        settings[
            "gemini_model"
        ],

        system_instruction=(
            settings[
                "system_prompt"
            ]
        ),
    )

    contents = []

    for item in get_history(
        chat_id
    ):

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

    response = await __import__(
        "asyncio"
    ).to_thread(

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
# POLLINATIONS IMAGE
# ============================================================

async def generate_image(
    prompt,
):

    api_key = settings[
        "pollinations_api_key"
    ].strip()

    if not api_key:

        raise RuntimeError(
            "Pollinations API ключ "
            "не установлен.\n\n"
            "Открой /settings → "
            "🔑 API ключи"
        )

    model = settings[
        "image_model"
    ]

    encoded_prompt = quote(
        prompt,
        safe="",
    )

    url = (
        "https://gen.pollinations.ai/image/"
        + encoded_prompt
    )

    params = {

        "model": model,

        "width": 1024,

        "height": 1024,

        "n": 1,
    }

    headers = {
        "Authorization":
            f"Bearer {api_key}",
    }

    timeout = aiohttp.ClientTimeout(
        total=180
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            url,
            params=params,
            headers=headers,
        ) as response:

            if response.status != 200:

                text = await response.text()

                raise RuntimeError(
                    f"Pollinations HTTP "
                    f"{response.status}\n\n"
                    f"{text[:2000]}"
                )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                )
            )

            data = await response.read()

    if not data:

        raise RuntimeError(
            "Pollinations вернул "
            "пустой ответ."
        )

    return data, content_type


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
                callback_data="image_models",
            ),
        ],

        [
            InlineKeyboardButton(
                "🧠 Память",
                callback_data="history",
            ),
        ],

        [
            InlineKeyboardButton(
                "🗑 Очистить память",
                callback_data="clear_history",
            ),
        ],

        [
            InlineKeyboardButton(
                "📝 Системная роль",
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
                "💬 Ответ на всё",
                callback_data="reply_mode",
            ),
        ],
    ])


# ============================================================
# IMAGE MODEL KEYBOARD
# ============================================================

def image_models_keyboard():

    buttons = []

    for model, name in IMAGE_MODELS.items():

        selected = (
            " ✅"
            if model
            == settings["image_model"]
            else ""
        )

        buttons.append([
            InlineKeyboardButton(
                name + selected,
                callback_data=(
                    "set_image:"
                    + model
                ),
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="settings",
        )
    ])

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update,
    context,
):

    await update.message.reply_text(

        "🤖 AI BOT\n\n"

        "📝 Gemini — текст\n"
        "🖼 Pollinations — фото\n\n"

        "Примеры:\n\n"

        "%привет\n"
        "%объясни квантовую физику\n"
        "%сгенерируй фото "
        "рыжего кота\n\n"

        "Команды:\n"
        "/settings\n"
        "/image\n"
        "/cancel"
    )


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

    model_name = IMAGE_MODELS.get(
        settings["image_model"],
        settings["image_model"],
    )

    await update.message.reply_text(

        "⚙️ НАСТРОЙКИ\n\n"

        f"🤖 Текст:\n"
        f"{settings['gemini_model']}\n\n"

        f"🖼 Фото:\n"
        f"{model_name}\n\n"

        f"🧠 Память:\n"
        f"{settings['history_limit']}\n\n"

        f"💬 Ответ на всё:\n"
        f"{'ДА' if settings['reply_all'] else 'НЕТ'}",

        reply_markup=settings_keyboard(),
    )


# ============================================================
# CALLBACK SETTINGS
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


    # ---------------- SETTINGS ----------------

    if data == "settings":

        await query.edit_message_text(

            "⚙️ НАСТРОЙКИ\n\n"

            f"🤖 Текст: "
            f"{settings['gemini_model']}\n"

            f"🖼 Фото: "
            f"{settings['image_model']}\n"

            f"🧠 Память: "
            f"{settings['history_limit']}",

            reply_markup=settings_keyboard(),
        )

        return


    # ---------------- IMAGE MODELS ----------------

    if data == "image_models":

        await query.edit_message_text(

            "🖼 ВЫБОР МОДЕЛИ ФОТО\n\n"

            "Выбери модель:",

            reply_markup=(
                image_models_keyboard()
            ),
        )

        return


    # ---------------- SET IMAGE MODEL ----------------

    if data.startswith(
        "set_image:"
    ):

        model = data.split(
            ":",
            1,
        )[1]

        if model not in IMAGE_MODELS:

            await query.edit_message_text(
                "❌ Неизвестная модель."
            )

            return

        settings[
            "image_model"
        ] = model

        await query.edit_message_text(

            "✅ Модель фото изменена:\n\n"
            + IMAGE_MODELS[model],

            reply_markup=(
                image_models_keyboard()
            ),
        )

        return


    # ---------------- TEXT MODEL ----------------

    if data == "text_model":

        context.user_data[
            "waiting"
        ] = "text_model"

        await query.edit_message_text(

            "🤖 Отправь название "
            "модели Gemini.\n\n"

            "Например:\n"
            "gemini-3.5-flash\n\n"

            "/cancel"
        )

        return


    # ---------------- HISTORY ----------------

    if data == "history":

        context.user_data[
            "waiting"
        ] = "history"

        await query.edit_message_text(

            "🧠 ПАМЯТЬ\n\n"

            "0 — без лимита\n"
            "1 — ничего не помнить\n"
            "10 — последние 10\n"
            "20 — последние 20\n\n"

            "Отправь число."
        )

        return


    # ---------------- CLEAR ----------------

    if data == "clear_history":

        clear_all_history()

        await query.edit_message_text(
            "🗑 Вся память очищена."
        )

        return


    # ---------------- SYSTEM PROMPT ----------------

    if data == "system_prompt":

        context.user_data[
            "waiting"
        ] = "system_prompt"

        await query.edit_message_text(

            "📝 Отправь системную роль.\n\n"

            "Например:\n\n"

            "Ты эксперт по программированию. "
            "Отвечай кратко и понятно.\n\n"

            "/cancel"
        )

        return


    # ---------------- API KEYS ----------------

    if data == "api_keys":

        context.user_data[
            "waiting"
        ] = "api_keys"

        await query.edit_message_text(

            "🔑 API КЛЮЧИ\n\n"

            "Отправляй по одному:\n\n"

            "gemini: ключ\n"
            "pollinations: ключ\n\n"

            "Например:\n"
            "pollinations: sk_xxxxx\n\n"

            "/cancel"
        )

        return


    # ---------------- REPLY MODE ----------------

    if data == "reply_mode":

        settings[
            "reply_all"
        ] = not settings[
            "reply_all"
        ]

        mode = (
            "все сообщения"
            if settings["reply_all"]
            else "только сообщения с %"
        )

        await query.edit_message_text(

            "💬 Режим изменён.\n\n"
            f"Теперь бот отвечает на: "
            f"{mode}",

            reply_markup=settings_keyboard(),
        )

        return


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
        return False

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return False

    text = update.message.text.strip()


    # ---------------- TEXT MODEL ----------------

    if waiting == "text_model":

        settings[
            "gemini_model"
        ] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Модель текста изменена:\n"
            + text
        )

        return True


    # ---------------- HISTORY ----------------

    if waiting == "history":

        try:

            value = int(text)

            if value < 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Нужно отправить "
                "целое число."
            )

            return True

        settings[
            "history_limit"
        ] = value

        if value == 1:

            clear_all_history()

        elif value > 1:

            for chat_id in list(
                histories.keys()
            ):

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
            f"✅ Память: {value}"
        )

        return True


    # ---------------- SYSTEM PROMPT ----------------

    if waiting == "system_prompt":

        settings[
            "system_prompt"
        ] = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Системная роль изменена."
        )

        return True


    # ---------------- API KEYS ----------------

    if waiting == "api_keys":

        if ":" not in text:

            await update.message.reply_text(

                "❌ Неверный формат.\n\n"

                "Используй:\n"
                "gemini: ключ\n"
                "pollinations: ключ"
            )

            return True

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

            return True


        if name == "gemini":

            settings[
                "gemini_api_key"
            ] = value

            answer = (
                "🤖 Gemini API ключ сохранён."
            )


        elif name == "pollinations":

            settings[
                "pollinations_api_key"
            ] = value

            answer = (
                "🖼 Pollinations API "
                "ключ сохранён."
            )


        else:

            await update.message.reply_text(
                "❌ Доступны только:\n\n"
                "gemini:\n"
                "pollinations:"
            )

            return True


        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ " + answer
        )

        return True


    return False


# ============================================================
# /CANCEL
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
# /IMAGE
# ============================================================

async def image_command(
    update,
    context,
):

    if not context.args:

        await update.message.reply_text(

            "Использование:\n\n"

            "/image "
            "реалистичный рыжий кот "
            "на диване"
        )

        return

    prompt = " ".join(
        context.args
    )

    try:

        await update.message.chat.send_action(
            ChatAction.UPLOAD_PHOTO
        )

        data, content_type = (
            await generate_image(
                prompt
            )
        )

        # Telegram принимает bytes
        await update.message.reply_photo(
            photo=data,
            caption=(
                "🖼 Готово!\n"
                f"Модель: "
                f"{settings['image_model']}"
            ),
        )

    except Exception as e:

        logging.exception(
            "IMAGE ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка генерации фото:\n\n"
            + str(e)
        )


# ============================================================
# TEXT MESSAGE
# ============================================================

async def text_message(
    update,
    context,
):

    text = update.message.text

    # Сначала проверяем ввод настроек

    if (
        update.effective_user.id
        == OWNER_ID
    ):

        handled = await settings_input(
            update,
            context,
        )

        if handled:
            return


    # ---------------- MODE ----------------

    if settings["reply_all"]:

        prompt = text.strip()

    else:

        if not text.startswith("%"):
            return

        prompt = text[
            1:
        ].strip()


    if not prompt:
        return


    lower = prompt.lower()


    # ========================================================
    # IMAGE
    # ========================================================

    image_triggers = [

        "сгенерируй фото",

        "сгенерируй картинку",

        "сгенерируй изображение",

        "создай фото",

        "создай картинку",

        "создай изображение",

        "сделай фото",

        "сделай картинку",

        "нарисуй",
    ]


    image_trigger = None

    for trigger in image_triggers:

        if lower.startswith(
            trigger
        ):

            image_trigger = trigger

            break


    if image_trigger:

        image_prompt = prompt[
            len(image_trigger):
        ].strip()


        if not image_prompt:

            image_prompt = (
                "красивая реалистичная "
                "фотография"
            )


        try:

            await update.message.chat.send_action(
                ChatAction.UPLOAD_PHOTO
            )

            data, content_type = (
                await generate_image(
                    image_prompt
                )
            )

            await update.message.reply_photo(

                photo=data,

                caption=(
                    "🖼 Готово!\n"
                    f"Модель: "
                    f"{settings['image_model']}"
                ),
            )

        except Exception as e:

            logging.exception(
                "IMAGE ERROR"
            )

            await update.message.reply_text(

                "❌ Ошибка генерации фото:\n\n"
                + str(e)
            )

        return


    # ========================================================
    # TEXT
    # ========================================================

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        answer = await generate_text(

            update.effective_chat.id,

            prompt,
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "TEXT ERROR"
        )

        await update.message.reply_text(

            "❌ Ошибка Gemini:\n\n"
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

        caption = caption[
            1:
        ].strip()

    if not caption:

        caption = (
            "Опиши подробно "
            "что изображено на фото."
        )

    try:

        photo = (
            update.message.photo[-1]
        )

        file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = (
            await file.download_as_bytearray()
        )

        configure_gemini()

        model = genai.GenerativeModel(
            settings["gemini_model"]
        )

        response = await __import__(
            "asyncio"
        ).to_thread(

            model.generate_content,

            [
                caption,

                {
                    "mime_type":
                        "image/jpeg",

                    "data":
                        bytes(image_bytes),
                },
            ],
        )

        await update.message.reply_text(
            response.text
        )

    except Exception as e:

        logging.exception(
            "VISION ERROR"
        )

        await update.message.reply_text(

            "❌ Ошибка анализа фото:\n\n"
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
            "Установи BOT_TOKEN."
        )


    logging.basicConfig(

        level=logging.INFO,

        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(message)s"
        ),
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
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "settings",
            settings_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "image",
            image_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command,
        )
    )


    # Settings

    application.add_handler(
        CallbackQueryHandler(
            settings_callback
        )
    )


    # Photo analysis

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message,
        )
    )


    # Text

    application.add_handler(
        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            text_message,
        )
    )


    application.add_error_handler(
        error_handler
    )


    print(
        "🤖 AI BOT запущен"
    )


    application.run_polling(
        allowed_updates=(
            Update.ALL_TYPES
        )
    )


if __name__ == "__main__":

    main()
