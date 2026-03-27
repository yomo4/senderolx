"""
OLX.ro Sender Bot
Telegram bot that sends messages to OLX.ro listings using cookies + proxy.
"""

import logging
import os
import re
from html import escape

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from olx_client import OLXClient, OLXError
from storage import UserStorage

load_dotenv()
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────────────
IDLE, AWAITING_COOKIE, AWAITING_PROXY, AWAITING_URL, AWAITING_MESSAGE = range(5)

# ── Global storage ───────────────────────────────────────────────────────────
storage = UserStorage(storage_file=os.getenv("STORAGE_FILE", "data/users.json"))

# Optional whitelist: comma-separated Telegram user IDs in env ALLOWED_USERS
_raw_allowed = os.getenv("ALLOWED_USERS", "")


def _load_allowed_users(raw_allowed: str) -> set[int]:
    allowed_users: set[int] = set()
    for raw_user_id in raw_allowed.split(","):
        raw_user_id = raw_user_id.strip()
        if not raw_user_id:
            continue
        try:
            allowed_users.add(int(raw_user_id))
        except ValueError:
            logger.warning("Ignoring invalid Telegram ID in ALLOWED_USERS: %s", raw_user_id)
    return allowed_users


ALLOWED_USERS = _load_allowed_users(_raw_allowed)


# ── Helpers ──────────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📨 Отправить сообщение", "⚙️ Статус"],
            ["🍪 Установить Cookie", "🔌 Настроить прокси"],
        ],
        resize_keyboard=True,
    )


async def check_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    uid = update.effective_user.id
    if uid not in ALLOWED_USERS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return False
    return True


async def require_cookie(update: Update, user_id: int) -> bool:
    if not storage.get_cookie(user_id):
        await update.message.reply_text(
            "⚠️ Сначала установите куки OLX.ro — команда /setcookie"
        )
        return False
    return True


def _validate_proxy(proxy: str) -> bool:
    return bool(
        re.match(
            r"^(http|https|socks4|socks5)://([^@\s]+@)?[a-zA-Z0-9.\-]+:\d+$",
            proxy.strip(),
        )
    )


def _truncate(value: str, limit: int) -> str:
    return value[:limit] + ("..." if len(value) > limit else "")


def _html_code(value: str) -> str:
    return f"<code>{escape(value)}</code>"


def _normalize_cookie_string(cookie: str) -> str:
    parts: list[str] = []
    for line in cookie.replace("\ufeff", "").splitlines():
        line = line.strip().strip(";")
        if not line:
            continue
        parts.extend(part.strip() for part in line.split(";") if part.strip())
    return "; ".join(parts)


def _decode_text_payload(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


# ── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, {escape(name)}!\n\n"
        "🤖 <b>OLX.ro Sender Bot</b>\n\n"
        "Бот отправляет сообщения продавцам на OLX.ro через ваши куки и прокси.\n\n"
        "📋 <b>Команды:</b>\n"
        "• /setcookie — Установить куки сессии OLX.ro\n"
        "• /setproxy — Установить прокси (HTTP/SOCKS5)\n"
        "• /delproxy — Удалить прокси\n"
        "• /send — Отправить сообщение по ссылке объявления\n"
        "• /status — Текущие настройки\n"
        "• /checkauth — Проверить авторизацию на OLX\n"
        "• /help — Подробная справка\n\n"
        "🚀 Начните с /setcookie",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    return IDLE


# ── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📖 *Справка*\n\n"
        "*Как получить куки OLX.ro:*\n"
        "1. Войдите на olx.ro в Chrome/Firefox\n"
        "2. Нажмите F12 → Application → Cookies → www.olx.ro\n"
        "3. Скопируйте всё в формате `name=value; name2=value2`\n"
        "   Или используйте расширение *Cookie-Editor* / *EditThisCookie*\n"
        "4. Отправьте строку боту через /setcookie\n"
        "   Или отправьте `.txt`-файл с cookies\n\n"
        "*Форматы прокси:*\n"
        "`http://host:port`\n"
        "`http://user:pass@host:port`\n"
        "`socks5://host:port`\n"
        "`socks5://user:pass@host:port`\n\n"
        "*Отправка сообщения:*\n"
        "1. /send\n"
        "2. Вставьте ссылку на объявление OLX.ro\n"
        "3. Введите текст сообщения\n\n"
        "*Массовая отправка:*\n"
        "Просто используйте /send несколько раз подряд.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard(),
    )
    return IDLE


# ── /status ──────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE
    uid = update.effective_user.id
    cookie = storage.get_cookie(uid)
    proxy = storage.get_proxy(uid)

    cookie_line = (
        f"✅ Установлены ({_html_code(_truncate(cookie, 35))})"
        if cookie
        else "❌ Не установлены"
    )
    proxy_line = (
        f"✅ {_html_code(proxy)}"
        if proxy
        else "❌ Не установлен (прямое соединение)"
    )

    await update.message.reply_text(
        f"⚙️ <b>Настройки</b>\n\n"
        f"🍪 Куки: {cookie_line}\n"
        f"🔌 Прокси: {proxy_line}",
        parse_mode=ParseMode.HTML,
    )
    return IDLE


# ── /setcookie ───────────────────────────────────────────────────────────────

async def cmd_setcookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE

    if context.args:
        if await _handle_cookie(update, " ".join(context.args)):
            return IDLE
        return AWAITING_COOKIE

    await update.message.reply_text(
        "🍪 *Установка кук OLX.ro*\n\n"
        "Отправьте строку кук в формате:\n"
        "`name1=value1; name2=value2; ...`\n\n"
        "Или отправьте `.txt`-файл с этой строкой\n\n"
        "Как получить:\n"
        "• F12 → Application → Cookies → www.olx.ro\n"
        "• Или расширение *Cookie-Editor* → Export as Header String\n\n"
        "Отправьте строку, `.txt`-файл или /cancel для отмены:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return AWAITING_COOKIE


async def _handle_cookie(update: Update, cookie: str) -> bool:
    cookie = _normalize_cookie_string(cookie)
    if not cookie or "=" not in cookie:
        await update.message.reply_text(
            "⚠️ Некорректный формат. Пример:\n"
            "`sessionid=abc123; csrftoken=xyz`\n\nПопробуйте ещё раз:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return False
    storage.set_cookie(update.effective_user.id, cookie)
    await update.message.reply_text(
        "✅ Куки сохранены!\n\nПроверьте авторизацию: /checkauth",
        reply_markup=main_keyboard(),
    )
    return True


async def received_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if await _handle_cookie(update, text):
        return IDLE
    return AWAITING_COOKIE


async def received_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    filename = (document.file_name or "").lower()
    mime_type = document.mime_type or ""

    if not (filename.endswith(".txt") or mime_type == "text/plain"):
        await update.message.reply_text(
            "⚠️ Поддерживаются только TXT-файлы с cookies. "
            "Или вставьте строку вручную."
        )
        return AWAITING_COOKIE

    if document.file_size and document.file_size > 64 * 1024:
        await update.message.reply_text(
            "⚠️ TXT-файл слишком большой. Пришлите небольшой файл со строкой cookies."
        )
        return AWAITING_COOKIE

    try:
        telegram_file = await document.get_file()
        payload = bytes(await telegram_file.download_as_bytearray())
    except Exception:
        logger.exception("Failed to download cookie TXT")
        await update.message.reply_text(
            "❌ Не удалось скачать файл из Telegram. Попробуйте ещё раз."
        )
        return AWAITING_COOKIE

    text = _decode_text_payload(payload)
    if text is None:
        await update.message.reply_text(
            "⚠️ Не удалось прочитать TXT-файл. Сохраните его в UTF-8 или отправьте строку вручную."
        )
        return AWAITING_COOKIE

    if await _handle_cookie(update, text):
        return IDLE
    return AWAITING_COOKIE


# ── /setproxy ────────────────────────────────────────────────────────────────

async def cmd_setproxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE

    if context.args:
        await _handle_proxy(update, context.args[0])
        return IDLE

    await update.message.reply_text(
        "🔌 *Настройка прокси*\n\n"
        "Отправьте прокси в формате:\n"
        "`http://host:port`\n"
        "`http://user:pass@host:port`\n"
        "`socks5://host:port`\n"
        "`socks5://user:pass@host:port`\n\n"
        "Отправьте строку или /cancel для отмены:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return AWAITING_PROXY


async def _handle_proxy(update: Update, proxy: str) -> bool:
    proxy = proxy.strip()
    if not _validate_proxy(proxy):
        await update.message.reply_text(
            "⚠️ Некорректный формат прокси.\n"
            "Пример: `socks5://user:pass@1.2.3.4:1080`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return False
    storage.set_proxy(update.effective_user.id, proxy)
    await update.message.reply_text(
        f"✅ Прокси сохранён: {_html_code(proxy)}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    return True


async def received_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _handle_proxy(update, update.message.text):
        return IDLE
    return AWAITING_PROXY


# ── /delproxy ────────────────────────────────────────────────────────────────

async def cmd_delproxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE
    storage.set_proxy(update.effective_user.id, None)
    await update.message.reply_text("✅ Прокси удалён. Используется прямое соединение.")
    return IDLE


# ── /checkauth ───────────────────────────────────────────────────────────────

async def cmd_checkauth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE
    uid = update.effective_user.id
    if not await require_cookie(update, uid):
        return IDLE

    msg = await update.message.reply_text("🔄 Проверяю авторизацию на OLX.ro...")
    client = OLXClient(storage.get_cookie(uid), storage.get_proxy(uid))
    try:
        ok, info = await client.check_auth()
        icon = "✅" if ok else "❌"
        await msg.edit_text(f"{icon} {info}")
    except Exception as exc:
        await msg.edit_text(f"❌ Ошибка: {exc}")
    finally:
        await client.close()
    return IDLE


# ── /send ────────────────────────────────────────────────────────────────────

async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_allowed(update):
        return IDLE
    uid = update.effective_user.id
    if not await require_cookie(update, uid):
        return IDLE

    if context.args:
        url = context.args[0]
        if "olx.ro" in url:
            context.user_data["target_url"] = url
            await update.message.reply_text(
                f"✅ Ссылка принята!\n\nВведите текст сообщения или /cancel:",
                reply_markup=ReplyKeyboardRemove(),
            )
            return AWAITING_MESSAGE

    await update.message.reply_text(
        "📎 Отправьте ссылку на объявление OLX.ro:\n\n"
        "Пример:\n`https://www.olx.ro/d/oferta/title-IDxxxxxxxxxx.html`\n\n"
        "/cancel — отмена",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return AWAITING_URL


async def received_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()

    # Keyboard buttons
    buttons = {
        "📨 Отправить сообщение": cmd_send,
        "⚙️ Статус": cmd_status,
        "🍪 Установить Cookie": cmd_setcookie,
        "🔌 Настроить прокси": cmd_setproxy,
    }
    if url in buttons:
        return await buttons[url](update, context)

    if "olx.ro" not in url:
        await update.message.reply_text(
            "⚠️ Нужна ссылка на объявление OLX.ro.\n"
            "Пример: `https://www.olx.ro/d/oferta/...-IDxxxxxxxx.html`\n\n/cancel — отмена",
            parse_mode=ParseMode.MARKDOWN,
        )
        return AWAITING_URL

    context.user_data["target_url"] = url
    await update.message.reply_text(
        "✅ Ссылка принята!\n\nВведите текст сообщения или /cancel:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AWAITING_MESSAGE


async def received_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid = update.effective_user.id

    if not text:
        await update.message.reply_text("⚠️ Сообщение не может быть пустым:")
        return AWAITING_MESSAGE

    url = context.user_data.pop("target_url", None)
    if not url:
        await update.message.reply_text("⚠️ Ссылка потеряна. Начните заново: /send")
        return IDLE

    cookie = storage.get_cookie(uid)
    proxy = storage.get_proxy(uid)
    proxy_icon = "✅" if proxy else "❌"

    status_msg = await update.message.reply_text(
        f"📤 Отправляю...\n"
        f"🔗 {_html_code(_truncate(url, 70))}\n"
        f"🔌 Прокси: {proxy_icon}",
        parse_mode=ParseMode.HTML,
    )

    client = OLXClient(cookie, proxy)
    try:
        result = await client.send_message(url, text)
        preview = _truncate(text, 80)
        await status_msg.edit_text(
            f"✅ <b>Сообщение отправлено!</b>\n\n"
            f"🔗 {_html_code(_truncate(url, 70))}\n"
            f"💬 {_html_code(preview)}\n\n"
            f"ℹ️ {escape(str(result.get('info', '')))}",
            parse_mode=ParseMode.HTML,
        )
    except OLXError as exc:
        await status_msg.edit_text(
            f"❌ <b>Ошибка отправки:</b>\n{_html_code(_truncate(str(exc), 400))}\n\n"
            f"Проверьте куки (/checkauth) и корректность ссылки.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.exception("Unexpected error in received_message")
        await status_msg.edit_text(
            f"❌ Неожиданная ошибка:\n{_html_code(_truncate(str(exc), 300))}",
            parse_mode=ParseMode.HTML,
        )
    finally:
        await client.close()

    await update.message.reply_text(
        "Отправить ещё? Используйте /send",
        reply_markup=main_keyboard(),
    )
    return IDLE


# ── /cancel ──────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Операция отменена.", reply_markup=main_keyboard())
    return IDLE


# ── Idle text handler (keyboard buttons) ─────────────────────────────────────

async def handle_idle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    mapping = {
        "📨 Отправить сообщение": cmd_send,
        "⚙️ Статус": cmd_status,
        "🍪 Установить Cookie": cmd_setcookie,
        "🔌 Настроить прокси": cmd_setproxy,
    }
    handler = mapping.get(update.message.text)
    if handler:
        return await handler(update, context)
    await update.message.reply_text(
        "Используйте кнопки меню или команды: /send /setcookie /setproxy /status",
        reply_markup=main_keyboard(),
    )
    return IDLE


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    os.makedirs("data", exist_ok=True)

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    app = Application.builder().token(token).build()

    # All commands available in every state for convenience
    shared_cmds = [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CommandHandler("status", cmd_status),
        CommandHandler("setcookie", cmd_setcookie),
        CommandHandler("setproxy", cmd_setproxy),
        CommandHandler("delproxy", cmd_delproxy),
        CommandHandler("checkauth", cmd_checkauth),
        CommandHandler("send", cmd_send),
        CommandHandler("cancel", cmd_cancel),
    ]

    conv = ConversationHandler(
        entry_points=shared_cmds
        + [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_idle_text)],
        states={
            IDLE: shared_cmds
            + [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_idle_text)],
            AWAITING_COOKIE: [
                MessageHandler(filters.Document.ALL, received_cookie_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_cookie),
                CommandHandler("cancel", cmd_cancel),
            ],
            AWAITING_PROXY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_proxy),
                CommandHandler("cancel", cmd_cancel),
            ],
            AWAITING_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_url),
                CommandHandler("cancel", cmd_cancel),
            ],
            AWAITING_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_message),
                CommandHandler("cancel", cmd_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel), CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Bot started, polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
