import asyncio
import logging
import os
import tempfile
from collections import defaultdict, deque
from pathlib import Path

from google import genai
from google.genai import types

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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-3.6-flash"

SYSTEM_PROMPT = (
    "Ты полезный AI-ассистент Telegram-бота. "
    "Отвечай на русском языке, если пользователь "
    "не просит другой язык. "
    "Отвечай понятно, точно и по делу."
)

# 1 = память выключена
# 10 = последние 10 сообщений
# 20 = последние 20 сообщений
# 0 = без ограничения
HISTORY_LIMIT = 20

# True — отвечает на любой текст
# False — только на текст, начинающийся с %
REPLY_ALL = True


# ============================================================
# MEMORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id, role, text):
    if HISTORY_LIMIT == 1:
        return

    if HISTORY_LIMIT == 0:
        histories[chat_id].append({
            "role": role,
            "text": text,
        })
        return

    histories[chat_id] = deque(
        histories[chat_id],
        maxlen=HISTORY_LIMIT,
    )

    histories[chat_id].append({
        "role": role,
        "text": text,
    })


def get_history(chat_id):
    return list(histories[chat_id])


def clear_history(chat_id):
    histories.pop(chat_id, None)


def history_text(chat_id):
    history = get_history(chat_id)

    if not history:
        return ""

    result = []

    for item in history:
        role = (
            "Пользователь"
            if item["role"] == "user"
            else "Ассистент"
        )

        result.append(
            f"{role}: {item['text']}"
        )

    return "\n".join(result)


# ============================================================
# GEMINI
# ============================================================

def get_client():
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "Gemini API ключ не установлен.\n"
            "Открой /settings → 🔑 Gemini API."
        )

    return genai.Client(
        api_key=GEMINI_API_KEY
    )


async def ask_text(chat_id, prompt):

    client = get_client()

    old_history = history_text(chat_id)

    if old_history:
        contents = (
            "История диалога:\n\n"
            f"{old_history}\n\n"
            "Новое сообщение:\n"
            f"{prompt}"
        )
    else:
        contents = prompt

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    answer = response.text or "Gemini не вернул ответ."

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
# GEMINI FILES
# ============================================================

async def analyze_file(
    local_path,
    prompt,
    wait_for_video=False,
):

    client = get_client()

    uploaded = await asyncio.to_thread(
        client.files.upload,
        file=local_path,
    )

    # --------------------------------------------------------
    # VIDEO
    # --------------------------------------------------------

    if wait_for_video:

        waited = 0
        max_wait = 600

        while True:

            state = getattr(
                uploaded,
                "state",
                None,
            )

            state_name = getattr(
                state,
                "name",
                None,
            )

            if state_name == "ACTIVE":
                break

            if state_name == "FAILED":
                raise RuntimeError(
                    "Gemini не смог обработать видео."
                )

            if waited >= max_wait:
                raise TimeoutError(
                    "Gemini слишком долго "
                    "обрабатывает видео."
                )

            await asyncio.sleep(5)

            waited += 5

            uploaded = await asyncio.to_thread(
                client.files.get,
                name=uploaded.name,
            )

    # --------------------------------------------------------
    # ANALYZE
    # --------------------------------------------------------

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=[
            uploaded,
            prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    answer = response.text or "Gemini не вернул ответ."

    try:
        await asyncio.to_thread(
            client.files.delete,
            name=uploaded.name,
        )
    except Exception:
        pass

    return answer


# ============================================================
# SETTINGS UI
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🤖 Модель",
                callback_data="set_model",
            ),
            InlineKeyboardButton(
                "🧠 Память",
                callback_data="set_memory",
            ),
        ],

        [
            InlineKeyboardButton(
                "💬 Режим",
                callback_data="set_mode",
            ),
            InlineKeyboardButton(
                "📝 System Prompt",
                callback_data="set_system",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔑 Gemini API",
                callback_data="api_menu",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔄 Проверить API",
                callback_data="check_api",
            ),
        ],

        [
            InlineKeyboardButton(
                "🗑 Очистить память",
                callback_data="clear_memory",
            ),
        ],

        [
            InlineKeyboardButton(
                "❌ Закрыть",
                callback_data="close_settings",
            ),
        ],
    ])


def settings_text():

    api_status = (
        "✅ установлен"
        if GEMINI_API_KEY
        else "❌ не установлен"
    )

    mode = (
        "Все сообщения"
        if REPLY_ALL
        else "Только %"
    )

    if HISTORY_LIMIT == 0:
        memory = "Без ограничения"
    elif HISTORY_LIMIT == 1:
        memory = "Выключена"
    else:
        memory = f"{HISTORY_LIMIT} сообщений"

    return (
        "⚙️ <b>Настройки бота</b>\n\n"
        f"🤖 Модель: <code>{GEMINI_MODEL}</code>\n"
        f"🧠 Память: {memory}\n"
        f"💬 Режим: {mode}\n"
        f"🔑 Gemini API: {api_status}\n"
    )


# ============================================================
# /START
# ============================================================

async def start_command(update, context):

    await update.message.reply_text(
        "🤖 <b>Gemini AI бот</b>\n\n"
        "Я умею:\n"
        "📝 понимать текст\n"
        "🖼 анализировать фото\n"
        "🎬 анализировать видео\n"
        "🎵 анализировать аудио\n"
        "📄 анализировать документы\n\n"
        "Открой /settings для настройки.",
        parse_mode="HTML",
    )


# ============================================================
# /SETTINGS
# ============================================================

async def settings_command(update, context):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text(
            "❌ Нет доступа."
        )
        return

    await update.message.reply_text(
        settings_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )


# ============================================================
# API MENU
# ============================================================

def api_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ Изменить ключ",
                callback_data="api_change",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔍 Проверить ключ",
                callback_data="check_api",
            ),
        ],

        [
            InlineKeyboardButton(
                "🗑 Удалить ключ",
                callback_data="api_delete",
            ),
        ],

        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="settings_main",
            ),
        ],
    ])


async def show_api_menu(query):

    status = (
        "✅ установлен"
        if GEMINI_API_KEY
        else "❌ отсутствует"
    )

    await query.edit_message_text(
        "🔑 <b>Gemini API</b>\n\n"
        f"Статус: {status}\n\n"
        "Ключ хранится только в памяти "
        "запущенного бота.",
        parse_mode="HTML",
        reply_markup=api_keyboard(),
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(update, context):

    global GEMINI_MODEL
    global HISTORY_LIMIT
    global REPLY_ALL
    global SYSTEM_PROMPT
    global GEMINI_API_KEY

    query = update.callback_query

    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text(
            "❌ Нет доступа."
        )
        return

    data = query.data

    # --------------------------------------------------------
    # MAIN SETTINGS
    # --------------------------------------------------------

    if data == "settings_main":

        await query.edit_message_text(
            settings_text(),
            parse_mode="HTML",
            reply_markup=settings_keyboard(),
        )
        return

    # --------------------------------------------------------
    # MODEL
    # --------------------------------------------------------

    if data == "set_model":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "⚡ Gemini Flash",
                    callback_data="model_flash",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🧠 Gemini Pro",
                    callback_data="model_pro",
                ),
            ],

            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="settings_main",
                ),
            ],
        ])

        await query.edit_message_text(
            "🤖 <b>Выбор модели</b>\n\n"
            f"Сейчас: <code>{GEMINI_MODEL}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data == "model_flash":

        GEMINI_MODEL = "gemini-3.6-flash"

        await query.edit_message_text(
            "✅ Выбрана Flash-модель.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    if data == "model_pro":

        GEMINI_MODEL = "gemini-3.6-pro"

        await query.edit_message_text(
            "✅ Выбрана Pro-модель.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    if data == "set_memory":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "🚫 Выкл",
                    callback_data="memory_1",
                ),
                InlineKeyboardButton(
                    "5",
                    callback_data="memory_5",
                ),
                InlineKeyboardButton(
                    "10",
                    callback_data="memory_10",
                ),
            ],

            [
                InlineKeyboardButton(
                    "20",
                    callback_data="memory_20",
                ),
                InlineKeyboardButton(
                    "50",
                    callback_data="memory_50",
                ),
                InlineKeyboardButton(
                    "♾️",
                    callback_data="memory_0",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🗑 Очистить",
                    callback_data="clear_memory",
                ),
            ],

            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="settings_main",
                ),
            ],
        ])

        await query.edit_message_text(
            "🧠 <b>Память</b>\n\n"
            "Выбери размер памяти диалога.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data.startswith("memory_"):

        value = int(
            data.split("_")[1]
        )

        HISTORY_LIMIT = value

        if value == 1:
            histories.clear()

        await query.edit_message_text(
            f"✅ Память изменена: "
            f"{'выключена' if value == 1 else 'без ограничения' if value == 0 else str(value) + ' сообщений'}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

    if data == "set_mode":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "💬 Все сообщения",
                    callback_data="mode_all",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔤 Только %",
                    callback_data="mode_percent",
                ),
            ],

            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="settings_main",
                ),
            ],
        ])

        await query.edit_message_text(
            "💬 <b>Режим сообщений</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if data == "mode_all":

        REPLY_ALL = True

        await query.edit_message_text(
            "✅ Бот теперь отвечает на все сообщения.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    if data == "mode_percent":

        REPLY_ALL = False

        await query.edit_message_text(
            "✅ Теперь бот отвечает только на сообщения, "
            "начинающиеся с %.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

    if data == "set_system":

        context.user_data["waiting"] = "system"

        await query.edit_message_text(
            "📝 <b>System Prompt</b>\n\n"
            "Теперь отправь новый System Prompt.\n\n"
            "После отправки он сохранится.\n\n"
            "/cancel",
            parse_mode="HTML",
        )
        return

    # --------------------------------------------------------
    # API MENU
    # --------------------------------------------------------

    if data == "api_menu":

        await show_api_menu(query)
        return

    # --------------------------------------------------------
    # CHANGE API
    # --------------------------------------------------------

    if data == "api_change":

        context.user_data["waiting"] = "api"

        await query.edit_message_text(
            "🔑 <b>Новый Gemini API ключ</b>\n\n"
            "Отправь сюда только API ключ.\n\n"
            "Например:\n"
            "<code>AIzaSy...</code>\n\n"
            "/cancel",
            parse_mode="HTML",
        )
        return

    # --------------------------------------------------------
    # DELETE API
    # --------------------------------------------------------

    if data == "api_delete":

        GEMINI_API_KEY = ""

        await query.edit_message_text(
            "🗑 Gemini API ключ удалён.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # CHECK API
    # --------------------------------------------------------

    if data == "check_api":

        if not GEMINI_API_KEY:

            await query.edit_message_text(
                "❌ API ключ не установлен.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔑 Добавить ключ",
                            callback_data="api_change",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data="settings_main",
                        )
                    ],
                ]),
            )
            return

        try:

            client = get_client()

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents="Ответь одним словом: OK",
            )

            answer = response.text or ""

            await query.edit_message_text(
                "✅ <b>Gemini API работает!</b>\n\n"
                f"Модель: <code>{GEMINI_MODEL}</code>\n"
                f"Ответ: {answer}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data="settings_main",
                        )
                    ]
                ]),
            )

        except Exception as e:

            await query.edit_message_text(
                "❌ <b>Gemini API не работает.</b>\n\n"
                f"<code>{str(e)[:3000]}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔑 Изменить ключ",
                            callback_data="api_change",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data="settings_main",
                        )
                    ],
                ]),
            )

        return

    # --------------------------------------------------------
    # CLEAR MEMORY
    # --------------------------------------------------------

    if data == "clear_memory":

        histories.clear()

        await query.edit_message_text(
            "🗑 Память всех диалогов очищена.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings_main",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    if data == "close_settings":

        await query.edit_message_text(
            "✅ Настройки закрыты."
        )
        return


# ============================================================
# CANCEL
# ============================================================

async def cancel_command(update, context):

    context.user_data.pop(
        "waiting",
        None,
    )

    await update.message.reply_text(
        "❌ Отменено."
    )


# ============================================================
# SETTINGS TEXT INPUT
# ============================================================

async def process_settings_input(
    update,
    context,
):

    global GEMINI_API_KEY
    global SYSTEM_PROMPT

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return False

    text = update.message.text.strip()

    if waiting == "api":

        GEMINI_API_KEY = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Gemini API ключ сохранён.\n\n"
            "Теперь можно нажать:\n"
            "/settings → 🔄 Проверить API"
        )

        return True

    if waiting == "system":

        SYSTEM_PROMPT = text

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ System Prompt сохранён."
        )

        return True

    return False


# ============================================================
# TEXT
# ============================================================

async def text_handler(update, context):

    if update.effective_user.id == OWNER_ID:

        if await process_settings_input(
            update,
            context,
        ):
            return

    text = update.message.text.strip()

    if not REPLY_ALL:

        if not text.startswith("%"):
            return

        text = text[1:].strip()

    if not text:
        return

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        answer = await ask_text(
            update.effective_chat.id,
            text,
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
# PHOTO
# ============================================================

async def photo_handler(update, context):

    prompt = (
        update.message.caption
        or "Подробно опиши это изображение."
    )

    if prompt.startswith("%"):
        prompt = prompt[1:].strip()

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False,
        ) as tmp:

            path = tmp.name

        await file.download_to_drive(path)

        try:

            answer = await analyze_file(
                path,
                prompt,
            )

        finally:

            try:
                os.remove(path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "PHOTO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа фото:\n\n"
            + str(e)
        )


# ============================================================
# VIDEO
# ============================================================

async def video_handler(update, context):

    prompt = (
        update.message.caption
        or "Подробно проанализируй это видео."
    )

    if prompt.startswith("%"):
        prompt = prompt[1:].strip()

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        video = update.message.video

        file = await context.bot.get_file(
            video.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False,
        ) as tmp:

            path = tmp.name

        await file.download_to_drive(path)

        try:

            await update.message.reply_text(
                "🎬 Видео получено.\n"
                "⏳ Gemini обрабатывает его..."
            )

            answer = await analyze_file(
                path,
                prompt,
                wait_for_video=True,
            )

        finally:

            try:
                os.remove(path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "VIDEO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа видео:\n\n"
            + str(e)
        )


# ============================================================
# AUDIO
# ============================================================

async def audio_handler(update, context):

    prompt = (
        update.message.caption
        or "Прослушай аудио и подробно опиши его содержание."
    )

    if prompt.startswith("%"):
        prompt = prompt[1:].strip()

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        if update.message.voice:

            file_id = update.message.voice.file_id
            suffix = ".ogg"

        else:

            file_id = update.message.audio.file_id
            suffix = ".mp3"

        file = await context.bot.get_file(
            file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as tmp:

            path = tmp.name

        await file.download_to_drive(path)

        try:

            answer = await analyze_file(
                path,
                prompt,
            )

        finally:

            try:
                os.remove(path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "AUDIO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа аудио:\n\n"
            + str(e)
        )


# ============================================================
# DOCUMENT
# ============================================================

async def document_handler(update, context):

    prompt = (
        update.message.caption
        or "Проанализируй этот документ и выдели главное."
    )

    if prompt.startswith("%"):
        prompt = prompt[1:].strip()

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        document = update.message.document

        file = await context.bot.get_file(
            document.file_id
        )

        filename = (
            document.file_name
            or "document"
        )

        suffix = Path(
            filename
        ).suffix

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as tmp:

            path = tmp.name

        await file.download_to_drive(path)

        try:

            answer = await analyze_file(
                path,
                prompt,
            )

        finally:

            try:
                os.remove(path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        logging.exception(
            "DOCUMENT ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа документа:\n\n"
            + str(e)
        )


# ============================================================
# ERROR
# ============================================================

async def error_handler(update, context):

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
            "BOT_TOKEN не задан."
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
            "cancel",
            cancel_command,
        )
    )

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Media
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VOICE,
            audio_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            audio_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document_handler,
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "🤖 Gemini Telegram Bot запущен."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
