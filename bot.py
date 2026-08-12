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
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
MODELS_FILE = DATA_DIR / "models.json"


# ============================================================
# DEFAULTS
# ============================================================

DEFAULT_SYSTEM_PROMPT = (
    "Ты полезный AI-ассистент в Telegram. "
    "Отвечай на языке пользователя. "
    "Отвечай понятно, точно и по делу."
)

DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "current_model": "gemini-3.6-flash",
    "history_limit": 20,
    "reply_all": True,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
}

DEFAULT_MODELS = [
    "gemini-3.6-flash",
]


# ============================================================
# JSON STORAGE
# ============================================================

def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")

    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    tmp.replace(path)


def load_json(path: Path, default):
    if not path.exists():
        save_json(path, default)
        return default.copy() if isinstance(default, dict) else list(default)

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data

    except Exception:
        return default.copy() if isinstance(default, dict) else list(default)


settings = load_json(
    SETTINGS_FILE,
    DEFAULT_SETTINGS,
)

models = load_json(
    MODELS_FILE,
    DEFAULT_MODELS,
)


# ============================================================
# NORMALIZE STORAGE
# ============================================================

if not isinstance(settings, dict):
    settings = DEFAULT_SETTINGS.copy()

if not isinstance(models, list):
    models = DEFAULT_MODELS.copy()

models = list(
    dict.fromkeys(
        str(model).strip()
        for model in models
        if str(model).strip()
    )
)

if not models:
    models = DEFAULT_MODELS.copy()

if not settings.get("current_model"):
    settings["current_model"] = models[0]

if settings["current_model"] not in models:
    models.insert(0, settings["current_model"])

save_json(SETTINGS_FILE, settings)
save_json(MODELS_FILE, models)


# ============================================================
# MEMORY
# ============================================================

histories = defaultdict(deque)


def get_history_limit():
    try:
        return int(settings.get("history_limit", 20))
    except Exception:
        return 20


def add_history(chat_id: int, role: str, text: str):
    limit = get_history_limit()

    # 1 = ничего не помнить
    if limit == 1:
        return

    # 0 = без лимита
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


def get_history(chat_id: int):
    return list(histories[chat_id])


def get_history_text(chat_id: int):
    history = get_history(chat_id)

    if not history:
        return ""

    lines = []

    for item in history:
        role = (
            "Пользователь"
            if item["role"] == "user"
            else "Ассистент"
        )

        lines.append(
            f"{role}: {item['text']}"
        )

    return "\n".join(lines)


def clear_history(chat_id: int):
    histories.pop(chat_id, None)


def clear_all_history():
    histories.clear()


def trim_all_histories():
    limit = get_history_limit()

    if limit == 0:
        return

    if limit == 1:
        histories.clear()
        return

    for chat_id in list(histories.keys()):
        histories[chat_id] = deque(
            list(histories[chat_id])[-limit:],
            maxlen=limit,
        )


# ============================================================
# OWNER
# ============================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ============================================================
# GEMINI CLIENT
# ============================================================

def get_api_key() -> str:
    key = str(
        settings.get("gemini_api_key", "")
    ).strip()

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


def get_current_model() -> str:
    model = str(
        settings.get("current_model", "")
    ).strip()

    if not model:
        raise RuntimeError(
            "Модель Gemini не выбрана."
        )

    return model


# ============================================================
# GEMINI TEXT
# ============================================================

async def ask_text(
    chat_id: int,
    prompt: str,
):
    client = get_client()

    history = get_history_text(chat_id)

    if history:
        contents = (
            "Предыдущая история диалога:\n\n"
            f"{history}\n\n"
            "Новое сообщение пользователя:\n"
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
# GEMINI FILE
# ============================================================

async def analyze_file(
    local_path: str,
    prompt: str,
    wait_for_video: bool = False,
):
    client = get_client()

    uploaded = await asyncio.to_thread(
        client.files.upload,
        file=local_path,
    )

    # --------------------------------------------------------
    # VIDEO PROCESSING
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
    # GENERATE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DELETE REMOTE FILE
    # --------------------------------------------------------

    try:
        await asyncio.to_thread(
            client.files.delete,
            name=uploaded.name,
        )
    except Exception:
        pass

    return answer


# ============================================================
# SETTINGS TEXT
# ============================================================

def settings_text():
    api_status = (
        "✅ установлен"
        if settings.get("gemini_api_key")
        else "❌ не установлен"
    )

    model = settings.get(
        "current_model",
        "не выбрана",
    )

    limit = get_history_limit()

    if limit == 0:
        memory = "♾️ без лимита"
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
        "⚙️ <b>Настройки бота</b>\n\n"
        f"🤖 Модель:\n"
        f"<code>{model}</code>\n\n"
        f"🧠 Память: {memory}\n"
        f"💬 Режим: {mode}\n"
        f"🔑 Gemini API: {api_status}\n"
        f"📦 Своих моделей: {len(models)}"
    )


# ============================================================
# MAIN SETTINGS KEYBOARD
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
                callback_data="close_settings",
            ),
        ],
    ])


# ============================================================
# SETTINGS COMMAND
# ============================================================

async def settings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "❌ Настройки доступны только владельцу."
        )
        return

    await update.message.reply_text(
        settings_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
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
                callback_data=f"select_model:{index}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "➕ Добавить свою модель",
            callback_data="add_model",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🔄 Загрузить модели Gemini",
            callback_data="sync_models",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🗑 Удалить модель",
            callback_data="manage_models",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "◀️ Назад",
            callback_data="settings_main",
        )
    ])

    return InlineKeyboardMarkup(rows)


async def show_models_menu(query):
    await query.edit_message_text(
        "🤖 <b>Модели Gemini</b>\n\n"
        "Нажми на модель, чтобы выбрать её.\n\n"
        "Можно добавить собственный ID модели "
        "или автоматически загрузить доступные "
        "модели Gemini.",
        parse_mode="HTML",
        reply_markup=models_keyboard(),
    )


# ============================================================
# MODEL MANAGEMENT
# ============================================================

def manage_models_keyboard():
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
# GEMINI MODEL SYNC
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

        supported = list(supported)

        if "generateContent" not in supported:
            continue

        name = getattr(
            model,
            "name",
            "",
        )

        if not name:
            continue

        if name.startswith("models/"):
            name = name[7:]

        if name:
            found.append(name)

    return found


# ============================================================
# MEMORY MENU
# ============================================================

def memory_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚫 Выкл",
                callback_data="set_memory:1",
            ),
            InlineKeyboardButton(
                "5",
                callback_data="set_memory:5",
            ),
            InlineKeyboardButton(
                "10",
                callback_data="set_memory:10",
            ),
        ],
        [
            InlineKeyboardButton(
                "20",
                callback_data="set_memory:20",
            ),
            InlineKeyboardButton(
                "50",
                callback_data="set_memory:50",
            ),
            InlineKeyboardButton(
                "♾️",
                callback_data="set_memory:0",
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


# ============================================================
# MODE MENU
# ============================================================

def mode_keyboard():
    current_all = settings.get(
        "reply_all",
        True,
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                ("✅ " if current_all else "")
                + "💬 Все сообщения",
                callback_data="set_mode:all",
            ),
        ],
        [
            InlineKeyboardButton(
                ("✅ " if not current_all else "")
                + "🔤 Только %",
                callback_data="set_mode:percent",
            ),
        ],
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="settings_main",
            ),
        ],
    ])


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
                "🔍 Проверить API",
                callback_data="api_check",
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


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    global models

    query = update.callback_query

    await query.answer()

    if not is_owner(query.from_user.id):
        await query.edit_message_text(
            "❌ Нет доступа."
        )
        return

    data = query.data

    # --------------------------------------------------------
    # MAIN SETTINGS
    # --------------------------------------------------------

    if data in {
        "settings_main",
        "settings",
    }:
        await query.edit_message_text(
            settings_text(),
            parse_mode="HTML",
            reply_markup=settings_keyboard(),
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

    # --------------------------------------------------------
    # MODELS
    # --------------------------------------------------------

    if data == "models_menu":
        await show_models_menu(query)
        return

    # --------------------------------------------------------
    # SELECT MODEL
    # --------------------------------------------------------

    if data.startswith("select_model:"):
        try:
            index = int(
                data.split(":", 1)[1]
            )
        except Exception:
            await query.answer(
                "Некорректная модель.",
                show_alert=True,
            )
            return

        if index < 0 or index >= len(models):
            await query.answer(
                "Модель не найдена.",
                show_alert=True,
            )
            return

        selected = models[index]

        settings["current_model"] = selected

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.answer(
            f"Выбрана: {selected}"
        )

        await show_models_menu(query)
        return

    # --------------------------------------------------------
    # ADD MODEL
    # --------------------------------------------------------

    if data == "add_model":
        context.user_data["waiting"] = "add_model"

        await query.edit_message_text(
            "➕ <b>Добавление своей модели</b>\n\n"
            "Отправь ID модели.\n\n"
            "Например:\n"
            "<code>gemini-3.6-flash</code>\n\n"
            "Если ID начинается с "
            "<code>models/</code>, его можно "
            "прислать вместе с этим префиксом.\n\n"
            "/cancel",
            parse_mode="HTML",
        )
        return

    # --------------------------------------------------------
    # MANAGE MODELS
    # --------------------------------------------------------

    if data == "manage_models":
        await query.edit_message_text(
            "🗑 <b>Удаление моделей</b>\n\n"
            "Нажми на модель, которую хочешь удалить.",
            parse_mode="HTML",
            reply_markup=manage_models_keyboard(),
        )
        return

    # --------------------------------------------------------
    # DELETE MODEL
    # --------------------------------------------------------

    if data.startswith("delete_model:"):
        try:
            index = int(
                data.split(":", 1)[1]
            )
        except Exception:
            return

        if index < 0 or index >= len(models):
            return

        if len(models) == 1:
            await query.answer(
                "Нельзя удалить последнюю модель.",
                show_alert=True,
            )
            return

        deleted = models.pop(index)

        if settings.get("current_model") == deleted:
            settings["current_model"] = models[0]

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
            f"Удалена: <code>{deleted}</code>",
            parse_mode="HTML",
            reply_markup=manage_models_keyboard(),
        )
        return

    # --------------------------------------------------------
    # SYNC MODELS
    # --------------------------------------------------------

    if data == "sync_models":
        if not settings.get("gemini_api_key"):
            await query.answer(
                "Сначала добавь Gemini API ключ.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            "🔄 Загружаю доступные модели Gemini..."
        )

        try:
            found = await sync_gemini_models()

            added = 0

            for model_name in found:
                if model_name not in models:
                    models.append(model_name)
                    added += 1

            save_json(
                MODELS_FILE,
                models,
            )

            await query.edit_message_text(
                "✅ <b>Список моделей обновлён</b>\n\n"
                f"Доступно через API: {len(found)}\n"
                f"Новых добавлено: {added}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🤖 К моделям",
                            callback_data="models_menu",
                        )
                    ]
                ]),
            )

        except Exception as exc:
            await query.edit_message_text(
                "❌ Не удалось получить список моделей.\n\n"
                f"<code>{str(exc)[:3000]}</code>",
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
        await query.edit_message_text(
            "🧠 <b>Память</b>\n\n"
            "Выбери объём истории.",
            parse_mode="HTML",
            reply_markup=memory_keyboard(),
        )
        return

    # --------------------------------------------------------
    # SET MEMORY
    # --------------------------------------------------------

    if data.startswith("set_memory:"):
        try:
            value = int(
                data.split(":", 1)[1]
            )
        except Exception:
            return

        settings["history_limit"] = value

        save_json(
            SETTINGS_FILE,
            settings,
        )

        if value == 1:
            histories.clear()
        else:
            trim_all_histories()

        if value == 0:
            description = "♾️ без лимита"
        elif value == 1:
            description = "🚫 выключена"
        else:
            description = f"{value} сообщений"

        await query.edit_message_text(
            f"✅ Память: {description}",
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
    # MODE MENU
    # --------------------------------------------------------

    if data == "mode_menu":
        await query.edit_message_text(
            "💬 <b>Режим сообщений</b>\n\n"
            "Выбери, когда бот должен отвечать.",
            parse_mode="HTML",
            reply_markup=mode_keyboard(),
        )
        return

    # --------------------------------------------------------
    # SET MODE
    # --------------------------------------------------------

    if data.startswith("set_mode:"):
        mode = data.split(":", 1)[1]

        settings["reply_all"] = (
            mode == "all"
        )

        save_json(
            SETTINGS_FILE,
            settings,
        )

        await query.edit_message_text(
            "✅ Режим изменён.",
            reply_markup=mode_keyboard(),
        )
        return

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

    if data == "system_menu":
        context.user_data["waiting"] = "system"

        await query.edit_message_text(
            "📝 <b>System Prompt</b>\n\n"
            "Отправь новый промт следующим сообщением.\n\n"
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
            if settings.get("gemini_api_key")
            else "❌ отсутствует"
        )

        await query.edit_message_text(
            "🔑 <b>Gemini API</b>\n\n"
            f"Статус: {status}\n\n"
            "Ключ хранится в data/settings.json.",
            parse_mode="HTML",
            reply_markup=api_keyboard(),
        )
        return

    # --------------------------------------------------------
    # API CHANGE
    # --------------------------------------------------------

    if data == "api_change":
        context.user_data["waiting"] = "api"

        await query.edit_message_text(
            "🔑 <b>Новый Gemini API ключ</b>\n\n"
            "Отправь следующим сообщением "
            "только сам ключ.\n\n"
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
            "🗑 Gemini API ключ удалён.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="api_menu",
                    )
                ]
            ]),
        )
        return

    # --------------------------------------------------------
    # API CHECK
    # --------------------------------------------------------

    if data == "api_check":
        if not settings.get("gemini_api_key"):
            await query.edit_message_text(
                "❌ Gemini API ключ не установлен.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "➕ Добавить ключ",
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

        await query.edit_message_text(
            "🔄 Проверяю Gemini API..."
        )

        try:
            client = get_client()

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=get_current_model(),
                contents="Ответь одним словом: OK",
            )

            answer = response.text or "OK"

            await query.edit_message_text(
                "✅ <b>Gemini API работает</b>\n\n"
                f"Модель:\n"
                f"<code>{get_current_model()}</code>\n\n"
                f"Ответ: {answer}",
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

        except Exception as exc:
            await query.edit_message_text(
                "❌ <b>Gemini API не работает</b>\n\n"
                f"<code>{str(exc)[:3000]}</code>",
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
        clear_all_history()

        await query.edit_message_text(
            "🗑 Память всех чатов очищена.",
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


# ============================================================
# SETTINGS TEXT INPUT
# ============================================================

async def process_settings_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update.effective_user.id):
        return False

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
        if not text:
            await update.message.reply_text(
                "❌ Ключ пустой."
            )
            return True

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
            "Проверь его через:\n"
            "/settings → 🔑 Gemini API → 🔍 Проверить API"
        )

        return True

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

    if waiting == "system":
        if not text:
            await update.message.reply_text(
                "❌ System Prompt не может быть пустым."
            )
            return True

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
    # ADD MODEL
    # --------------------------------------------------------

    if waiting == "add_model":
        model = text.strip()

        if model.startswith("models/"):
            model = model[7:]

        if not model:
            await update.message.reply_text(
                "❌ ID модели пустой."
            )
            return True

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
            "✅ <b>Модель добавлена и выбрана</b>\n\n"
            f"<code>{model}</code>",
            parse_mode="HTML",
        )

        return True

    return False


# ============================================================
# /CANCEL
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
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🤖 <b>Gemini AI бот</b>\n\n"
        "Поддерживает:\n"
        "📝 текст\n"
        "🖼 фото\n"
        "🎬 видео\n"
        "🎵 аудио\n"
        "📄 документы\n\n"
        "/settings — настройки",
        parse_mode="HTML",
    )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    # Ввод настроек
    if is_owner(update.effective_user.id):
        handled = await process_settings_input(
            update,
            context,
        )

        if handled:
            return

    text = (
        update.message.text
        or ""
    ).strip()

    if not text:
        return

    # Режим %
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

        # Telegram limit
        if len(answer) <= 4096:
            await update.message.reply_text(
                answer
            )
            return

        for start in range(
            0,
            len(answer),
            4096,
        ):
            await update.message.reply_text(
                answer[start:start + 4096]
            )

    except Exception as exc:
        logging.exception(
            "TEXT ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка Gemini:\n\n"
            + str(exc)
        )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    caption = (
        update.message.caption
        or "Подробно опиши это изображение."
    )

    if not settings.get(
        "reply_all",
        True,
    ):
        if not caption.startswith("%"):
            return

        caption = caption[1:].strip()

        if not caption:
            caption = (
                "Подробно опиши это изображение."
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
            local_path = tmp.name

        try:
            await tg_file.download_to_drive(
                local_path
            )

            answer = await analyze_file(
                local_path,
                caption,
            )

        finally:
            try:
                os.remove(local_path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as exc:
        logging.exception(
            "PHOTO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа фото:\n\n"
            + str(exc)
        )


# ============================================================
# VIDEO HANDLER
# ============================================================

async def video_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    caption = (
        update.message.caption
        or "Подробно проанализируй это видео."
    )

    if not settings.get(
        "reply_all",
        True,
    ):
        if not caption.startswith("%"):
            return

        caption = caption[1:].strip()

    try:
        await update.message.chat.send_action(
            ChatAction.TYPING
        )

        await update.message.reply_text(
            "🎬 Видео получено.\n"
            "⏳ Загружаю в Gemini и жду обработки..."
        )

        video = update.message.video

        tg_file = await context.bot.get_file(
            video.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False,
        ) as tmp:
            local_path = tmp.name

        try:
            await tg_file.download_to_drive(
                local_path
            )

            answer = await analyze_file(
                local_path,
                caption,
                wait_for_video=True,
            )

        finally:
            try:
                os.remove(local_path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as exc:
        logging.exception(
            "VIDEO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа видео:\n\n"
            + str(exc)
        )


# ============================================================
# AUDIO HANDLER
# ============================================================

async def audio_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    caption = (
        update.message.caption
        or "Прослушай аудио и подробно опиши его содержание."
    )

    if not settings.get(
        "reply_all",
        True,
    ):
        if not caption.startswith("%"):
            return

        caption = caption[1:].strip()

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
            local_path = tmp.name

        try:
            await tg_file.download_to_drive(
                local_path
            )

            answer = await analyze_file(
                local_path,
                caption,
            )

        finally:
            try:
                os.remove(local_path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as exc:
        logging.exception(
            "AUDIO ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа аудио:\n\n"
            + str(exc)
        )


# ============================================================
# DOCUMENT HANDLER
# ============================================================

async def document_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    caption = (
        update.message.caption
        or "Проанализируй этот документ и выдели главное."
    )

    if not settings.get(
        "reply_all",
        True,
    ):
        if not caption.startswith("%"):
            return

        caption = caption[1:].strip()

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

        suffix = Path(filename).suffix

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as tmp:
            local_path = tmp.name

        try:
            await tg_file.download_to_drive(
                local_path
            )

            answer = await analyze_file(
                local_path,
                caption,
            )

        finally:
            try:
                os.remove(local_path)
            except OSError:
                pass

        await update.message.reply_text(
            answer
        )

    except Exception as exc:
        logging.exception(
            "DOCUMENT ERROR"
        )

        await update.message.reply_text(
            "❌ Ошибка анализа документа:\n\n"
            + str(exc)
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
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

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # CALLBACKS
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    # --------------------------------------------------------
    # VIDEO
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_handler,
        )
    )

    # --------------------------------------------------------
    # VOICE
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.VOICE,
            audio_handler,
        )
    )

    # --------------------------------------------------------
    # AUDIO
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            audio_handler,
        )
    )

    # --------------------------------------------------------
    # DOCUMENT
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document_handler,
        )
    )

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

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
        "🤖 Gemini Telegram Bot started"
    )

    print(
        f"Owner ID: {OWNER_ID}"
    )

    print(
        f"Current model: "
        f"{settings.get('current_model')}"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
