import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, ChatPermissions, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Render URL, например:
# https://bromelo-moderator.onrender.com
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = "/telegram/webhook"

OWNER_ID = int(os.getenv("OWNER_ID", "8668633782"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "devBroMelo").lstrip("@")

# 0 = работает во всех группах.
# -100... = работает только в указанной группе.
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

AUTO_DELETE_SECONDS = int(os.getenv("AUTO_DELETE_SECONDS", "20"))
AUTO_MUTE_WARN_COUNT = int(os.getenv("AUTO_MUTE_WARN_COUNT", "3"))
AUTO_MUTE_MINUTES = int(os.getenv("AUTO_MUTE_MINUTES", "5"))
FAMILY_MUTE_MINUTES = int(os.getenv("FAMILY_MUTE_MINUTES", "120"))

ALLOWED_LINK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}

ROLE_LEVELS = {
    "MEMBER": 0,
    "HELPER": 10,
    "MODERATOR": 20,
    "ADMIN": 30,
    "DEPUTY": 40,
    "OWNER": 100,
}

ROLE_LABELS = {
    "MEMBER": "👤 MEMBER",
    "HELPER": "🔰 HELPER",
    "MODERATOR": "⚔️ MODERATOR",
    "ADMIN": "🛡 ADMIN",
    "DEPUTY": "🔱 DEPUTY",
    "OWNER": "👑 OWNER",
}

ROLE_ALIASES = {
    "member": "MEMBER",
    "участник": "MEMBER",
    "helper": "HELPER",
    "хелпер": "HELPER",
    "moderator": "MODERATOR",
    "mod": "MODERATOR",
    "модер": "MODERATOR",
    "модератор": "MODERATOR",
    "admin": "ADMIN",
    "админ": "ADMIN",
    "deputy": "DEPUTY",
    "зам": "DEPUTY",
    "заместитель": "DEPUTY",
}

RULES_TEXT = f"""📜 <b>Правила группы</b>

1. 🚫 Оскорбления участников запрещены.
2. 🚫 Мат и агрессивная ругань запрещены.
3. ⛔ Оскорбления родителей/семьи — более строгое наказание.
4. 🔗 Реклама и сторонние ссылки запрещены.
5. ✅ TikTok-ссылки разрешены.
6. 🚯 Спам и флуд запрещены.
7. 👮 Требования администрации нужно соблюдать.

После <b>{AUTO_MUTE_WARN_COUNT}</b> предупреждений — мут на <b>{AUTO_MUTE_MINUTES} минут</b>.
Оскорбления семьи могут привести к муту сразу.
"""

LEET_TABLE = str.maketrans({
    "0": "о", "1": "и", "3": "з", "4": "ч", "6": "б",
    "@": "а", "$": "с",
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
    "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "b": "в",
})

PROFANITY_PATTERNS = [
    r"\bбл[яа][дт]",
    r"\bсук[аи]",
    r"\bх[ую][йеёяи]",
    r"\bп[ие]зд",
    r"\bеб[а-яё]*",
    r"\bёб[а-яё]*",
    r"\bдолбо[её]б",
    r"\bмудак",
    r"\bмраз",
]

INSULT_PATTERNS = [
    r"\bдебил",
    r"\bидиот",
    r"\bтуп(ой|ая|ое|ые)",
    r"\bдаун\b",
    r"\bурод",
    r"\bчмо\b",
]

FAMILY_PATTERNS = [
    r"\b(тво[яйе]|твою|твоего|твоей)\s+(мам|мать|матер|пап|отц)",
    r"\b(мам|мать|матер|пап|отц)[а-яё]*\s+(тво[яйе]|твою|твоего|твоей)",
]

URL_RE = re.compile(
    r"(?i)(?:https?://|www\.|t\.me/|telegram\.me/|discord\.gg/|"
    r"(?:[a-z0-9-]+\.)+(?:com|net|org|ru|рф|io|gg|me|xyz|site|online|shop|app|dev|co)(?:/|\b))\S*"
)

DURATION_RE = re.compile(r"^(\d+)([mhdw])$", re.I)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bromelo")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row


# =========================================================
# DATABASE
# =========================================================

def init_db():
    db.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            activated_at INTEGER NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT NOT NULL,
            assigned_by INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()


def activate_chat(chat_id: int, title: str | None, timestamp: int | None = None):
    ts = timestamp or int(datetime.now(timezone.utc).timestamp())
    db.execute("""
        INSERT INTO chats(chat_id,title,activated_at)
        VALUES(?,?,?)
        ON CONFLICT(chat_id) DO NOTHING
    """, (chat_id, title or "", ts))
    db.commit()


def get_activation(chat_id: int) -> int | None:
    row = db.execute("SELECT activated_at FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    return int(row["activated_at"]) if row else None


def set_role(chat_id: int, user, role: str, assigned_by: int):
    db.execute("""
        INSERT INTO roles(chat_id,user_id,username,full_name,role,assigned_by,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name,
            role=excluded.role,
            assigned_by=excluded.assigned_by,
            updated_at=excluded.updated_at
    """, (
        chat_id,
        user.id,
        user.username or "",
        user.full_name or "",
        role,
        assigned_by,
        datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()


def remove_role(chat_id: int, user_id: int):
    db.execute("DELETE FROM roles WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    db.commit()


def stored_role(chat_id: int, user_id: int) -> str:
    if user_id == OWNER_ID:
        return "OWNER"
    row = db.execute(
        "SELECT role FROM roles WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return row["role"] if row else "MEMBER"


def add_warn(chat_id: int, user_id: int) -> int:
    row = db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    count = (row["count"] if row else 0) + 1
    db.execute("""
        INSERT INTO warns(chat_id,user_id,count)
        VALUES(?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count
    """, (chat_id, user_id, count))
    db.commit()
    return count


def get_warns(chat_id: int, user_id: int) -> int:
    row = db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return int(row["count"]) if row else 0


def clear_warns(chat_id: int, user_id: int):
    db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    db.commit()


def log_action(chat_id: int, user_id: int, moderator_id: int | None, action: str, reason: str):
    db.execute("""
        INSERT INTO actions(chat_id,user_id,moderator_id,action,reason,created_at)
        VALUES(?,?,?,?,?,?)
    """, (
        chat_id, user_id, moderator_id, action, reason,
        datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()


# =========================================================
# HELPERS
# =========================================================

def display_user(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.full_name)}</a>'


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def is_allowed_chat(chat_id: int) -> bool:
    return ALLOWED_CHAT_ID == 0 or chat_id == ALLOWED_CHAT_ID


async def telegram_is_admin(chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception:
        return False


async def effective_role(chat_id: int, user_id: int) -> str:
    if user_id == OWNER_ID:
        return "OWNER"
    role = stored_role(chat_id, user_id)
    if role != "MEMBER":
        return role
    if await telegram_is_admin(chat_id, user_id):
        return "ADMIN"
    return "MEMBER"


async def has_role(chat_id: int, user_id: int, minimum: str) -> bool:
    role = await effective_role(chat_id, user_id)
    return ROLE_LEVELS[role] >= ROLE_LEVELS[minimum]


async def can_act_on(chat_id: int, actor_id: int, target_id: int) -> bool:
    if target_id == OWNER_ID:
        return False
    actor = await effective_role(chat_id, actor_id)
    target = await effective_role(chat_id, target_id)
    return ROLE_LEVELS[actor] > ROLE_LEVELS[target]


async def safe_delete(message: Message):
    with suppress(Exception):
        await message.delete()


async def delete_after(message: Message, seconds: int = AUTO_DELETE_SECONDS):
    await asyncio.sleep(seconds)
    await safe_delete(message)


async def cleanup_pair(command: Message, response: Message | None):
    async def worker():
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        await safe_delete(command)
        if response:
            await safe_delete(response)
    asyncio.create_task(worker())


async def ensure_current_group_message(message: Message) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        return True

    if not is_allowed_chat(message.chat.id):
        return False

    activated = get_activation(message.chat.id)
    if activated is None:
        activate_chat(message.chat.id, message.chat.title, int(message.date.timestamp()))
        # Первое увиденное сообщение только фиксирует момент запуска.
        return False

    return int(message.date.timestamp()) >= activated


async def target_from_reply(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        sent = await message.answer("↩️ Используй команду ответом на сообщение пользователя.")
        await cleanup_pair(message, sent)
        return None
    return message.reply_to_message.from_user


async def require_role(message: Message, minimum: str) -> bool:
    if not message.from_user:
        return False
    if not await has_role(message.chat.id, message.from_user.id, minimum):
        sent = await message.answer(
            f"⛔ Нужна роль не ниже <b>{role_label(minimum)}</b>."
        )
        await cleanup_pair(message, sent)
        return False
    return True


async def require_hierarchy(message: Message, target) -> bool:
    if not message.from_user:
        return False
    if not await can_act_on(message.chat.id, message.from_user.id, target.id):
        sent = await message.answer(
            "⛔ Нельзя наказать пользователя с равной или более высокой ролью."
        )
        await cleanup_pair(message, sent)
        return False
    return True


def normalize_text(text: str) -> str:
    text = (text or "").lower().translate(LEET_TABLE)
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    text = re.sub(r"[^а-яёa-z0-9\s:/._-]", " ", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return re.sub(r"\s+", " ", text).strip()


def matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def host_from_url(raw: str) -> str:
    raw = raw.strip("()[]{}<>.,!?;:'\"")
    if raw.lower().startswith(("t.me/", "telegram.me/", "discord.gg/", "www.")):
        raw = "https://" + raw
    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw
    try:
        return (urlparse(raw).hostname or "").lower()
    except Exception:
        return ""


def contains_forbidden_link(text: str) -> bool:
    urls = extract_urls(text)
    return bool(urls) and any(host_from_url(u) not in ALLOWED_LINK_HOSTS for u in urls)


def detect_violation(text: str) -> str | None:
    norm = normalize_text(text)
    if matches_any(norm, FAMILY_PATTERNS):
        return "FAMILY_INSULT"
    if matches_any(norm, PROFANITY_PATTERNS):
        return "PROFANITY"
    if matches_any(norm, INSULT_PATTERNS):
        return "INSULT"
    return None


def parse_duration(value: str | None, default_minutes: int = 5) -> timedelta:
    if not value:
        return timedelta(minutes=default_minutes)
    m = DURATION_RE.match(value)
    if not m:
        return timedelta(minutes=default_minutes)
    amount = int(m.group(1))
    unit = m.group(2).lower()
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "w": timedelta(weeks=amount),
    }[unit]


def human_duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds % 604800 == 0:
        return f"{seconds // 604800} нед."
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    return f"{max(1, seconds // 60)} мин."


async def mute_user(
    message: Message,
    user,
    duration: timedelta,
    reason: str,
    moderator_text: str,
    moderator_id: int | None,
):
    try:
        await bot.restrict_chat_member(
            message.chat.id,
            user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + duration,
        )
    except Exception as e:
        log.exception("Mute failed")
        return await message.answer(
            "❌ Не удалось выдать мут. Проверь право бота «ограничивать участников»."
        )

    log_action(message.chat.id, user.id, moderator_id, "mute", reason)

    return await message.answer(
        f"🔇 <b>Мут выдан</b>\n\n"
        f"👤 Пользователь: {display_user(user)}\n"
        f"⏱ Срок: <b>{human_duration(duration)}</b>\n"
        f"📝 Причина: <b>{html.escape(reason)}</b>\n"
        f"👮 Выдал: {moderator_text}"
    )


# =========================================================
# BASIC COMMANDS
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):
    # В личке всегда отвечает — удобно для проверки.
    if message.chat.type == "private":
        await message.answer(
            "✅ <b>BroMelo Moderator работает.</b>\n\n"
            "Добавь меня администратором в группу.\n"
            "Для проверки группы используй /chatid."
        )
        return

    if not await ensure_current_group_message(message):
        return

    await message.answer(
        "🛡 <b>BroMelo Moderator активен.</b>\n"
        "Команды: /chatid /role /rules /staff /warns /help"
    )


@dp.message(Command("ping"))
async def ping(message: Message):
    await message.answer("🏓 Pong — бот получает сообщения.")


@dp.message(Command("chatid"))
async def chatid(message: Message):
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Добавь меня в группу и отправь /chatid там.")
        return

    await message.answer(
        f"🆔 <b>ID группы:</b>\n<code>{message.chat.id}</code>\n\n"
        f"Для Render:\n<code>ALLOWED_CHAT_ID={message.chat.id}</code>"
    )


@dp.message(Command("rules"))
async def rules(message: Message):
    if not await ensure_current_group_message(message):
        return
    await message.answer(RULES_TEXT)


@dp.message(Command("role"))
async def role(message: Message):
    if not await ensure_current_group_message(message):
        return

    target = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else message.from_user
    )
    if not target:
        return

    r = await effective_role(message.chat.id, target.id)
    await message.answer(
        f"🎖 {display_user(target)}\nРоль: <b>{role_label(r)}</b>"
    )


@dp.message(Command("warns"))
async def warns(message: Message):
    if not await ensure_current_group_message(message):
        return

    target = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else message.from_user
    )
    if not target:
        return

    count = get_warns(message.chat.id, target.id)
    await message.answer(
        f"⚠️ {display_user(target)}: <b>{count}/{AUTO_MUTE_WARN_COUNT}</b> предупреждений."
    )


@dp.message(Command("staff"))
async def staff(message: Message):
    if not await ensure_current_group_message(message):
        return

    rows = db.execute("""
        SELECT user_id,username,full_name,role
        FROM roles
        WHERE chat_id=? AND role!='MEMBER'
        ORDER BY
            CASE role
                WHEN 'DEPUTY' THEN 40
                WHEN 'ADMIN' THEN 30
                WHEN 'MODERATOR' THEN 20
                WHEN 'HELPER' THEN 10
                ELSE 0
            END DESC
    """, (message.chat.id,)).fetchall()

    sections = {
        "OWNER": [f"@{OWNER_USERNAME}"],
        "DEPUTY": [],
        "ADMIN": [],
        "MODERATOR": [],
        "HELPER": [],
    }

    for row in rows:
        if row["user_id"] == OWNER_ID:
            continue
        name = f"@{row['username']}" if row["username"] else html.escape(row["full_name"] or str(row["user_id"]))
        sections[row["role"]].append(name)

    out = ["👥 <b>Состав администрации</b>", ""]
    for r in ("OWNER", "DEPUTY", "ADMIN", "MODERATOR", "HELPER"):
        if sections[r]:
            out.append(f"<b>{role_label(r)}</b>")
            out.extend(f"• {x}" for x in sections[r])
            out.append("")

    await message.answer("\n".join(out).rstrip())


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if message.chat.type != "private" and not await ensure_current_group_message(message):
        return

    await message.answer(
        "🛡 <b>Команды</b>\n\n"
        "/ping — проверить работу\n"
        "/chatid — ID группы\n"
        "/rules — правила\n"
        "/role — роль\n"
        "/staff — состав\n"
        "/warns — варны\n\n"
        "<b>HELPER+</b>\n"
        "/warn причина\n\n"
        "<b>MODERATOR+</b>\n"
        "/mute — 5 минут\n"
        "/mute 30m причина\n"
        "/unmute\n\n"
        "<b>ADMIN+</b>\n"
        "/ban причина\n"
        "/unban ID\n"
        "/clearwarns\n\n"
        "<b>DEPUTY+</b>\n"
        "/setrole helper|moderator|admin\n"
        "/demote\n\n"
        "<b>OWNER</b>\n"
        "/setrole deputy"
    )


# =========================================================
# ROLE MANAGEMENT
# =========================================================

@dp.message(Command("setrole"))
async def setrole(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "DEPUTY"):
        return

    target = await target_from_reply(message)
    if not target:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        sent = await message.answer("Использование: <code>/setrole moderator</code>")
        await cleanup_pair(message, sent)
        return

    requested = ROLE_ALIASES.get(parts[1].strip().lower())
    if not requested:
        sent = await message.answer("Роли: helper, moderator, admin, deputy")
        await cleanup_pair(message, sent)
        return

    actor_role = await effective_role(message.chat.id, message.from_user.id)

    if requested == "DEPUTY" and actor_role != "OWNER":
        sent = await message.answer("⛔ DEPUTY может выдавать только OWNER.")
        await cleanup_pair(message, sent)
        return

    if ROLE_LEVELS[requested] >= ROLE_LEVELS[actor_role]:
        sent = await message.answer("⛔ Нельзя выдать роль равную или выше своей.")
        await cleanup_pair(message, sent)
        return

    if not await require_hierarchy(message, target):
        return

    set_role(message.chat.id, target, requested, message.from_user.id)
    log_action(message.chat.id, target.id, message.from_user.id, "setrole", requested)

    sent = await message.answer(
        f"⬆️ <b>Пользователь повышен</b>\n\n"
        f"👤 {display_user(target)}\n"
        f"🎖 Новая роль: <b>{role_label(requested)}</b>\n"
        f"👑 Повысил: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


@dp.message(Command("demote"))
async def demote(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "DEPUTY"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    remove_role(message.chat.id, target.id)
    log_action(message.chat.id, target.id, message.from_user.id, "demote", "MEMBER")

    sent = await message.answer(
        f"⬇️ <b>Роль снята</b>\n"
        f"👤 {display_user(target)}\n"
        f"🎖 Новая роль: <b>{role_label('MEMBER')}</b>\n"
        f"👮 Понизил: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


# =========================================================
# MODERATION COMMANDS
# =========================================================

@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "HELPER"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    parts = (message.text or "").split(maxsplit=1)
    reason = parts[1] if len(parts) > 1 else "нарушение правил"

    count = add_warn(message.chat.id, target.id)
    log_action(message.chat.id, target.id, message.from_user.id, "warn", reason)

    sent = await message.answer(
        f"⚠️ <b>Предупреждение</b>\n\n"
        f"👤 {display_user(target)}\n"
        f"📝 Причина: <b>{html.escape(reason)}</b>\n"
        f"📊 Варны: <b>{count}/{AUTO_MUTE_WARN_COUNT}</b>\n"
        f"👮 Выдал: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)

    if count >= AUTO_MUTE_WARN_COUNT:
        clear_warns(message.chat.id, target.id)
        mute_notice = await mute_user(
            message, target,
            timedelta(minutes=AUTO_MUTE_MINUTES),
            f"{AUTO_MUTE_WARN_COUNT} предупреждения",
            display_user(message.from_user),
            message.from_user.id,
        )
        asyncio.create_task(delete_after(mute_notice))


@dp.message(Command("mute"))
async def mute_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "MODERATOR"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    parts = (message.text or "").split(maxsplit=2)
    duration = parse_duration(parts[1] if len(parts) > 1 else None, 5)
    reason = parts[2] if len(parts) > 2 else "нарушение правил"

    sent = await mute_user(
        message, target, duration, reason,
        display_user(message.from_user),
        message.from_user.id,
    )
    await cleanup_pair(message, sent)


@dp.message(Command("unmute"))
async def unmute_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "MODERATOR"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    try:
        await bot.restrict_chat_member(
            message.chat.id,
            target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
            ),
        )
    except Exception:
        sent = await message.answer("❌ Не удалось снять мут.")
        await cleanup_pair(message, sent)
        return

    log_action(message.chat.id, target.id, message.from_user.id, "unmute", "unmute")
    sent = await message.answer(
        f"🔊 Мут снят с {display_user(target)}.\n"
        f"👮 Снял: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "ADMIN"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    parts = (message.text or "").split(maxsplit=1)
    reason = parts[1] if len(parts) > 1 else "нарушение правил"

    try:
        await bot.ban_chat_member(message.chat.id, target.id)
    except Exception:
        sent = await message.answer("❌ Не удалось забанить. Проверь права бота.")
        await cleanup_pair(message, sent)
        return

    log_action(message.chat.id, target.id, message.from_user.id, "ban", reason)

    sent = await message.answer(
        f"🔨 <b>Пользователь забанен</b>\n\n"
        f"👤 {display_user(target)}\n"
        f"📝 Причина: <b>{html.escape(reason)}</b>\n"
        f"👮 Выдал: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "ADMIN"):
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        sent = await message.answer("Использование: <code>/unban 123456789</code>")
        await cleanup_pair(message, sent)
        return

    user_id = int(parts[1])
    try:
        await bot.unban_chat_member(message.chat.id, user_id, only_if_banned=True)
    except Exception:
        sent = await message.answer("❌ Не удалось разбанить.")
        await cleanup_pair(message, sent)
        return

    log_action(message.chat.id, user_id, message.from_user.id, "unban", "unban")
    sent = await message.answer(
        f"✅ Пользователь <code>{user_id}</code> разбанен.\n"
        f"👮 Выполнил: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


@dp.message(Command("clearwarns"))
async def clearwarns_cmd(message: Message):
    if not await ensure_current_group_message(message):
        return
    if not await require_role(message, "ADMIN"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    clear_warns(message.chat.id, target.id)
    log_action(message.chat.id, target.id, message.from_user.id, "clearwarns", "clear")

    sent = await message.answer(
        f"✅ Варны {display_user(target)} очищены.\n"
        f"👮 Выполнил: {display_user(message.from_user)}"
    )
    await cleanup_pair(message, sent)


# =========================================================
# AUTOMOD
# =========================================================

@dp.message(F.text)
async def automod(message: Message):
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not is_allowed_chat(message.chat.id):
        return
    if not await ensure_current_group_message(message):
        return
    if not message.from_user or message.from_user.is_bot:
        return

    role = await effective_role(message.chat.id, message.from_user.id)
    if ROLE_LEVELS[role] >= ROLE_LEVELS["HELPER"]:
        return

    text = message.text or ""

    # Ссылки: только удаление, БЕЗ варна/мута.
    if contains_forbidden_link(text):
        await safe_delete(message)
        sent = await bot.send_message(
            message.chat.id,
            f"🔗 {display_user(message.from_user)}, сторонние ссылки и реклама запрещены.\n"
            f"✅ TikTok разрешён.\n"
            f"Наказание не выдано."
        )
        asyncio.create_task(delete_after(sent))
        return

    violation = detect_violation(text)
    if not violation:
        return

    await safe_delete(message)

    if violation == "FAMILY_INSULT":
        sent = await mute_user(
            message,
            message.from_user,
            timedelta(minutes=FAMILY_MUTE_MINUTES),
            "оскорбление родителей/семьи",
            "🤖 AutoMod",
            None,
        )
        asyncio.create_task(delete_after(sent))

        rules_notice = await bot.send_message(message.chat.id, RULES_TEXT)
        asyncio.create_task(delete_after(rules_notice))
        return

    reason = "мат" if violation == "PROFANITY" else "оскорбление участника"

    count = add_warn(message.chat.id, message.from_user.id)
    log_action(message.chat.id, message.from_user.id, None, "auto_warn", reason)

    if count >= AUTO_MUTE_WARN_COUNT:
        clear_warns(message.chat.id, message.from_user.id)

        sent = await mute_user(
            message,
            message.from_user,
            timedelta(minutes=AUTO_MUTE_MINUTES),
            f"{reason}; достигнут лимит предупреждений",
            "🤖 AutoMod",
            None,
        )
        asyncio.create_task(delete_after(sent))
    else:
        sent = await bot.send_message(
            message.chat.id,
            f"⚠️ {display_user(message.from_user)}, сообщение удалено.\n"
            f"📝 Причина: <b>{html.escape(reason)}</b>\n"
            f"📊 Варны: <b>{count}/{AUTO_MUTE_WARN_COUNT}</b>\n\n"
            f"{RULES_TEXT}"
        )
        asyncio.create_task(delete_after(sent))


# =========================================================
# BOT JOIN EVENT
# =========================================================

@dp.my_chat_member()
async def bot_join(event: ChatMemberUpdated):
    if event.chat.type not in {"group", "supergroup"}:
        return
    if not is_allowed_chat(event.chat.id):
        return

    old = event.old_chat_member.status
    new = event.new_chat_member.status

    if old in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} and new in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    }:
        activate_chat(event.chat.id, event.chat.title, int(event.date.timestamp()))
        with suppress(Exception):
            sent = await bot.send_message(
                event.chat.id,
                "🛡 <b>BroMelo Moderator подключён.</b>\n"
                "Проверяю только новые сообщения после подключения.\n"
                f"👑 OWNER: @{OWNER_USERNAME}"
            )
            asyncio.create_task(delete_after(sent))


# =========================================================
# WEBHOOK / RENDER
# =========================================================

async def health(_request):
    me = await bot.get_me()
    return web.json_response({
        "status": "ok",
        "bot": me.username,
        "mode": "webhook",
        "allowed_chat_id": ALLOWED_CHAT_ID,
    })


async def on_startup(bot: Bot):
    if not WEBHOOK_URL:
        raise RuntimeError(
            "WEBHOOK_URL не задан. В Render Environment добавь "
            "WEBHOOK_URL=https://ТВОЙ-СЕРВИС.onrender.com"
        )

    # Удаляем старый polling/webhook и очередь старых updates.
    await bot.delete_webhook(drop_pending_updates=True)

    webhook = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(
        url=webhook,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=True,
    )

    info = await bot.get_webhook_info()
    log.info("Webhook set: %s", info.url)
    log.info("Bot started in WEBHOOK mode")


async def on_shutdown(bot: Bot):
    # Webhook специально НЕ удаляем, чтобы при коротком рестарте Render
    # Telegram не переключался на polling.
    await bot.session.close()


def main():
    init_db()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
