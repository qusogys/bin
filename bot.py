import asyncio
import json
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

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
MODELS_FILE = DATA_DIR / "models.json"

DEFAULT_SYSTEM_PROMPT = (
    "Ты полезный AI-ассистент Telegram-бота. "
    "Отвечай на русском языке, если пользователь "
    "не просит другой язык. "
    "Отвечай понятно, точно и по делу."
)


# ============================================================
# DEFAULT DATA
# ============================================================

DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "current_model": "",
    "history_limit": 20,
    "reply_all": True,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
}

DEFAULT_MODELS = [
    "gemini-3.6-flash",
]


# ============================================================
# JSON
# ============================================================

def load_json(path, default):

    if not path.exists():
        save_json(path, default)
        return default.copy() if isinstance(default, dict) else list(default)

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default.copy() if isinstance(default, dict) else list(default)


def save_json(path, data):

    temp = path.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    temp.replace(path)


settings = load_json(
    SETTINGS_FILE,
    DEFAULT_SETTINGS,
)

models = load_json(
    MODELS_FILE,
    DEFAULT_MODELS,
)


# ============================================================
# NORMALIZE
# ============================================================

if not isinstance(models, list):
    models = DEFAULT_MODELS.copy()

models = list(dict.fromkeys(
    str(x).strip()
    for x in models
    if str(x).strip()
))

if not models:
    models = DEFAULT_MODELS.copy()

if not settings.get("current_model"):
    settings["current_model"] = models[0]

save_json(MODELS_FILE, models)
save_json(SETTINGS_FILE, settings)


# ============================================================
# MEMORY
# ============================================================

histories = defaultdict(deque)


def add_history(chat_id, role, text):

    limit = int(
        settings.get(
            "history_limit",
            20,
        )
    )

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


def history_text(chat_id):

    if not histories[chat_id]:
        return ""

    result = []

    for item in histories[chat_id]:

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

def get_api_key():

    key = settings.get(
        "gemini_api_key",
        "",
    )

    if not key:

        raise RuntimeError(
            "Gemini API ключ не установлен.\n"
            "Открой /settings → 🔑 Gemini API."
        )

    return key


def get_client():

    return genai.Client(
        api_key=get_api_key()
    )


def get_current_model():

    model = settings.get(
        "current_model",
        "",
    )

    if not model:
        raise RuntimeError(
            "Модель не выбрана."
        )

    return model


# ============================================================
# TEXT
# ============================================================

async def ask_text(
    chat_id,
    prompt,
):

    client = get_client()

    old_history = history_text(
        chat_id
    )

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
        model=get_current_model(),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=settings.get(
                "system_prompt",
                DEFAULT_SYSTEM_PROMPT,
            ),
        ),
    )

    answer = response.text or (
        "Модель не вернула текст."
    )

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
# FILE ANALYSIS
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

    if wait_for_video:

        waited = 0
        timeout = 600

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

            if waited >= timeout:

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

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=get_current_model(),
        contents=[
            uploaded,
            prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=settings.get(
                "system_prompt",
                DEFAULT_SYSTEM_PROMPT,
            ),
        ),
    )

    answer = response.text or (
        "Модель не вернула ответ."
    )

    try:

        await asyncio.to_thread(
            client.files.delete,
            name=uploaded.name,
        )

    except Exception:
        pass

    return answer


# ============================================================
# SETTINGS MAIN
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🤖 Модели",
                callback_data="models_menu",
            ),

            InlineKeyboardButton(
                "🧠 Память",
                callback_data="memory_menu",
            ),
        ],

        [
            InlineKeyboardButton(
                "💬 Режим",
                callback_data="mode_menu",
            ),

            InlineKeyboardButton(
                "📝 System Prompt",
                callback_data="system_menu",
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
                "🗑 Очистить память",
                callback_data="clear_memory",
            ),
        ],

        [
            InlineKeyboardButton(
                "❌ Закрыть",
                callback_data="close",
            ),
        ],
    ])


def settings_text():

    api = (
        "✅ установлен"
        if settings.get("gemini_api_key")
        else "❌ не установлен"
    )

    model = settings.get(
        "current_model",
        "не выбрана",
    )

    limit = settings.get(
        "history_limit",
        20,
    )

    if limit == 0:
        memory = "♾️ без ограничения"
    elif limit == 1:
        memory = "🚫 выключена"
    else:
        memory = f"{limit} сообщений"

    mode = (
        "💬 все сообщения"
        if settings.get("reply_all", True)
        else "🔤 только %"
    )

    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"🤖 Модель: <code>{model}</code>\n"
        f"🧠 Память: {memory}\n"
        f"💬 Режим: {mode}\n"
        f"🔑 Gemini API: {api}\n\n"
        f"📦 Сохранено моделей: {len(models)}"
    )


# ============================================================
# MODELS MENU
# ============================================================

def models_keyboard():

    rows = []

    current = settings.get(
        "current_model",
        "",
    )

    for index, model in enumerate(models):

        prefix = (
            "✅ "
            if model == current
            else "○ "
        )

        rows.append([
            InlineKeyboardButton(
                prefix + model,
                callback_data=f"model:{index}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "➕ Добавить свою модель",
            callback_data="model_add",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🔄 Загрузить модели Gemini",
            callback_data="model_sync",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🗑 Управление моделями",
            callback_data="model_manage",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "◀️ Назад",
            callback_data="settings",
        )
    ])

    return InlineKeyboardMarkup(rows)


async def show_models_menu(query):

    current = settings.get(
        "current_model",
        "",
    )

    await query.edit_message_text(
        "🤖 <b>Мои модели</b>\n\n"
        f"Текущая:\n"
        f"<code>{current}</code>\n\n"
        "Нажми на модель, чтобы выбрать её.",
        parse_mode="HTML",
        reply_markup=models_keyboard(),
    )


# ============================================================
# ADD MODEL
# ============================================================

async def start_add_model(
    query,
    context,
):

    context.user_data["waiting"] = (
        "model_add"
    )

    await query.edit_message_text(
        "➕ <b>Добавление модели</b>\n\n"
        "Отправь ID модели.\n\n"
        "Например:\n"
        "<code>gemini-3.6-flash</code>\n\n"
        "После добавления модель появится "
        "в меню выбора.\n\n"
        "/cancel",
        parse_mode="HTML",
    )


# ============================================================
# MODEL MANAGEMENT
# ============================================================

def model_management_keyboard():

    rows = []

    for index, model in enumerate(models):

        rows.append([
            InlineKeyboardButton(
                f"🗑 {model}",
                callback_data=f"delete_model:{index}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "◀️ Назад",
            callback_data="models_menu",
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# SYNC GEMINI MODELS
# ============================================================

async def sync_gemini_models():

    client = get_client()

    found = []

    pager = await asyncio.to_thread(
        client.models.list
    )

    for model in pager:

        supported = getattr(
            model,
            "supported_actions",
            None,
        )

        if not supported:
            continue

        if "generateContent" not in supported:
            continue

        name = getattr(
            model,
            "name",
            "",
        )

        if name.startswith("models/"):
            name = name[
                len("models/"):
            ]

        if name:
            found.append(name)

    return found


# ============================================================
# API MENU
# ============================================================

def api_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ Изменить ключ",
                callback_data="api_change",
            )
        ],

        [
            InlineKeyboardButton(
                "🔍 Проверить API",
                callback_data="api_check",
            )
        ],

        [
            InlineKeyboardButton(
                "🗑 Удалить ключ",
                callback_data="api_delete",
            )
        ],

        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="settings",
            )
        ],
    ])


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
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

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    if data == "settings":

        await query.edit_message_text(
            settings_text(),
            parse_mode="HTML",
            reply_markup=settings_keyboard(),
        )

        return

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    if data == "close":

        await query.edit_message_text(
            "✅ Настройки закрыты."
        )

        return

    # --------------------------------------------------------
    # MODELS
    # --------------------------------------------------------

    if data == "models_menu":

        await show_models_menu(
            query
        )

        return

    # --------------------------------------------------------
    # SELECT MODEL
    # --------------------------------------------------------

    if data.startswith("model:"):

        index = int(
            data.split(":")[1]
        )

        if index < 0 or index >= len(models):

            await query.answer(
                "Модель не найдена.",
                show_alert=True,
            )

            return

        settings["current_model"] = (
            models[index]
        )

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.answer(
            f"Выбрана: {models[index]}"
        )

        await show_models_menu(
            query
        )

        return

    # --------------------------------------------------------
    # ADD MODEL
    # --------------------------------------------------------

    if data == "model_add":

        await start_add_model(
            query,
            context,
        )

        return

    # --------------------------------------------------------
    # MANAGE MODELS
    # --------------------------------------------------------

    if data == "model_manage":

        await query.edit_message_text(
            "🗑 <b>Удаление моделей</b>\n\n"
            "Нажми на модель, которую "
            "хочешь удалить.",
            parse_mode="HTML",
            reply_markup=model_management_keyboard(),
        )

        return

    # --------------------------------------------------------
    # DELETE MODEL
    # --------------------------------------------------------

    if data.startswith(
        "delete_model:"
    ):

        index = int(
            data.split(":")[1]
        )

        if index < 0 or index >= len(models):
            return

        model = models[index]

        if len(models) <= 1:

            await query.answer(
                "Нельзя удалить последнюю модель.",
                show_alert=True,
            )

            return

        models.pop(index)

        if settings.get(
            "current_model"
        ) == model:

            settings["current_model"] = (
                models[0]
            )

        save_json(
            MODELS_FILE,
            models,
        )

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.answer(
            "Модель удалена."
        )

        await query.edit_message_text(
            "🗑 <b>Удаление моделей</b>\n\n"
            "Модель удалена.",
            parse_mode="HTML",
            reply_markup=model_management_keyboard(),
        )

        return

    # --------------------------------------------------------
    # SYNC
    # --------------------------------------------------------

    if data == "model_sync":

        if not settings.get(
            "gemini_api_key"
        ):

            await query.answer(
                "Сначала добавь Gemini API ключ.",
                show_alert=True,
            )

            return

        await query.edit_message_text(
            "🔄 Получаю список моделей Gemini..."
        )

        try:

            found = await sync_gemini_models()

            added = 0

            for model in found:

                if model not in models:

                    models.append(model)
                    added += 1

            save_json(
                MODELS_FILE,
                models,
            )

            await query.edit_message_text(
                "✅ <b>Список обновлён</b>\n\n"
                f"Найдено поддерживаемых моделей: "
                f"{len(found)}\n"
                f"Новых добавлено: {added}\n\n"
                "Модели с поддержкой "
                "<code>generateContent</code> "
                "добавляются автоматически.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🤖 Открыть модели",
                            callback_data="models_menu",
                        )
                    ]
                ]),
            )

        except Exception as e:

            await query.edit_message_text(
                "❌ Не удалось получить модели Gemini.\n\n"
                f"<code>{str(e)[:3000]}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data="models_menu",
                        )
                    ]
                ]),
            )

        return

    # --------------------------------------------------------
    # MEMORY MENU
    # --------------------------------------------------------

    if data == "memory_menu":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "🚫 Выкл",
                    callback_data="memory:1",
                ),
                InlineKeyboardButton(
                    "5",
                    callback_data="memory:5",
                ),
                InlineKeyboardButton(
                    "10",
                    callback_data="memory:10",
                ),
            ],

            [
                InlineKeyboardButton(
                    "20",
                    callback_data="memory:20",
                ),
                InlineKeyboardButton(
                    "50",
                    callback_data="memory:50",
                ),
                InlineKeyboardButton(
                    "♾️",
                    callback_data="memory:0",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🗑 Очистить",
                    callback_data="clear_memory",
                )
            ],

            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="settings",
                )
            ],
        ])

        await query.edit_message_text(
            "🧠 <b>Память</b>\n\n"
            "Выбери размер истории.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    # --------------------------------------------------------
    # MEMORY SET
    # --------------------------------------------------------

    if data.startswith("memory:"):

        value = int(
            data.split(":")[1]
        )

        settings["history_limit"] = value

        save_json(
            SETTINGS_FILE,
            settings,
        )

        if value == 1:
            histories.clear()

        text = (
            "выключена"
            if value == 1
            else "без ограничения"
            if value == 0
            else f"{value} сообщений"
        )

        await query.edit_message_text(
            f"✅ Память: {text}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # MODE MENU
    # --------------------------------------------------------

    if data == "mode_menu":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "💬 Все сообщения",
                    callback_data="mode:all",
                )
            ],

            [
                InlineKeyboardButton(
                    "🔤 Только %",
                    callback_data="mode:percent",
                )
            ],

            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="settings",
                )
            ],
        ])

        await query.edit_message_text(
            "💬 <b>Режим</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    # --------------------------------------------------------
    # MODE SET
    # --------------------------------------------------------

    if data.startswith("mode:"):

        value = data.split(":")[1]

        settings["reply_all"] = (
            value == "all"
        )

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.edit_message_text(
            "✅ Режим изменён.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

    if data == "system_menu":

        context.user_data["waiting"] = (
            "system"
        )

        await query.edit_message_text(
            "📝 <b>System Prompt</b>\n\n"
            "Отправь новый System Prompt.\n\n"
            "/cancel",
            parse_mode="HTML",
        )

        return

    # --------------------------------------------------------
    # API MENU
    # --------------------------------------------------------

    if data == "api_menu":

        status = (
            "✅ установлен"
            if settings.get(
                "gemini_api_key"
            )
            else "❌ отсутствует"
        )

        await query.edit_message_text(
            "🔑 <b>Gemini API</b>\n\n"
            f"Статус: {status}",
            parse_mode="HTML",
            reply_markup=api_keyboard(),
        )

        return

    # --------------------------------------------------------
    # API CHANGE
    # --------------------------------------------------------

    if data == "api_change":

        context.user_data["waiting"] = (
            "api"
        )

        await query.edit_message_text(
            "🔑 <b>Gemini API ключ</b>\n\n"
            "Отправь только API ключ.\n\n"
            "Например:\n"
            "<code>AIza...</code>\n\n"
            "/cancel",
            parse_mode="HTML",
        )

        return

    # --------------------------------------------------------
    # API DELETE
    # --------------------------------------------------------

    if data == "api_delete":

        settings["gemini_api_key"] = ""

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.edit_message_text(
            "🗑 API ключ удалён.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # API CHECK
    # --------------------------------------------------------

    if data == "api_check":

        try:

            client = get_client()

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=get_current_model(),
                contents="Ответь одним словом: OK",
            )

            await query.edit_message_text(
                "✅ <b>API работает</b>\n\n"
                f"Модель:\n"
                f"<code>{get_current_model()}</code>\n\n"
                f"Ответ: {response.text}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data="api_menu",
                        )
                    ]
                ]),
            )

        except Exception as e:

            await query.edit_message_text(
                "❌ <b>API не работает</b>\n\n"
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
                            callback_data="api_menu",
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
            "🗑 Память очищена.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="settings",
                    )
                ]
            ]),
        )

        return


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
# TEXT INPUT FOR SETTINGS
# ============================================================

async def process_settings_input(
    update,
    context,
):

    waiting = context.user_data.get(
        "waiting"
    )

    if not waiting:
        return False

    text = update.message.text.strip()

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    if waiting == "api":

        settings["gemini_api_key"] = text

        save_json(
            SETTINGS_FILE,
            settings,
        )

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ Gemini API ключ сохранён.\n\n"
            "Теперь можешь открыть "
            "/settings → 🔑 Gemini API → "
            "🔍 Проверить API."
        )

        return True

    # --------------------------------------------------------
    # SYSTEM
    # --------------------------------------------------------

    if waiting == "system":

        settings["system_prompt"] = text

        save_json(
            SETTINGS_FILE,
            settings,
        )

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ System Prompt сохранён."
        )

        return True

    # --------------------------------------------------------
    # MODEL
    # --------------------------------------------------------

    if waiting == "model_add":

        model = text.strip()

        if model.startswith("models/"):

            model = model[
                len("models/"):
            ]

        if model not in models:

            models.append(model)

            save_json(
                MODELS_FILE,
                models,
            )

        settings["current_model"] = model

        save_json(
            SETTINGS_FILE,
            settings,
        )

        context.user_data.pop(
            "waiting",
            None,
        )

        await update.message.reply_text(
            "✅ <b>Модель добавлена</b>\n\n"
            f"<code>{model}</code>\n\n"
            "Она автоматически выбрана.",
            parse_mode="HTML",
        )

        return True

    return False


# ============================================================
# START
# ============================================================

async def start_command(
    update,
    context,
):

    await update.message.reply_text(
        "🤖 <b>Gemini AI Bot</b>\n\n"
        "Поддерживается:\n"
        "📝 текст\n"
        "🖼 фото\n"
        "🎬 видео\n"
        "🎵 аудио\n"
        "📄 документы\n\n"
        "⚙️ /settings — настройки",
        parse_mode="HTML",
    )


# ============================================================
# TEXT
# ============================================================

async def text_handler(
    update,
    context,
):

    if update.effective_user.id == OWNER_ID:

        if await process_settings_input(
            update,
            context,
        ):
            return

    text = update.message.text.strip()

    if not settings.get(
        "reply_all",
        True,
    ):

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

async def photo_handler(
    update,
    context,
):

    prompt = (
        update.message.caption
        or "Подробно опиши это изображение."
    )

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        photo = update.message.photo[-1]

        tg_file = await context.bot.get_file(
            photo.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False,
        ) as tmp:

            path = tmp.name

        await tg_file.download_to_drive(
            path
        )

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

async def video_handler(
    update,
    context,
):

    prompt = (
        update.message.caption
        or "Подробно проанализируй это видео."
    )

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        video = update.message.video

        tg_file = await context.bot.get_file(
            video.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False,
        ) as tmp:

            path = tmp.name

        await tg_file.download_to_drive(
            path
        )

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

async def audio_handler(
    update,
    context,
):

    prompt = (
        update.message.caption
        or "Прослушай аудио и подробно опиши его содержание."
    )

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

        tg_file = await context.bot.get_file(
            file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as tmp:

            path = tmp.name

        await tg_file.download_to_drive(
            path
        )

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

async def document_handler(
    update,
    context,
):

    prompt = (
        update.message.caption
        or "Проанализируй этот документ и выдели главное."
    )

    try:

        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        document = update.message.document

        tg_file = await context.bot.get_file(
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

        await tg_file.download_to_drive(
            path
        )

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
            "Не задан BOT_TOKEN."
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

    # Buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Photo
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    # Video
    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_handler,
        )
    )

    # Voice
    application.add_handler(
        MessageHandler(
            filters.VOICE,
            audio_handler,
        )
    )

    # Audio
    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            audio_handler,
        )
    )

    # Documents
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
