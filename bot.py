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
from aiogram.types import (
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.getenv("OWNER_ID", "8668633782"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "devBroMelo").lstrip("@")
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

AUTO_DELETE_SECONDS = int(os.getenv("AUTO_DELETE_SECONDS", "20"))
AUTO_MUTE_WARN_COUNT = int(os.getenv("AUTO_MUTE_WARN_COUNT", "3"))
AUTO_MUTE_MINUTES = int(os.getenv("AUTO_MUTE_MINUTES", "30"))
FAMILY_MUTE_MINUTES = int(os.getenv("FAMILY_MUTE_MINUTES", "120"))

AI_MODERATION = os.getenv("AI_MODERATION", "false").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.6-luna")

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

Обычные нарушения: предупреждения.
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
log = logging.getLogger("moderator")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

ai_client = None
if AI_MODERATION and OPENAI_API_KEY and AsyncOpenAI:
    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# =========================
# DATABASE
# =========================

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
        INSERT INTO chats(chat_id, title, activated_at)
        VALUES(?,?,?)
        ON CONFLICT(chat_id) DO NOTHING
    """, (chat_id, title or "", ts))
    db.commit()


def get_chat_activation(chat_id: int) -> int | None:
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


def db_role(chat_id: int, user_id: int) -> str:
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
        INSERT INTO warns(chat_id,user_id,count) VALUES(?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count
    """, (chat_id, user_id, count))
    db.commit()
    return count


def get_warns(chat_id: int, user_id: int) -> int:
    row = db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return row["count"] if row else 0


def clear_warns(chat_id: int, user_id: int):
    db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    db.commit()


def log_action(chat_id: int, user_id: int, moderator_id: int | None, action: str, reason: str):
    db.execute("""
        INSERT INTO actions(chat_id,user_id,moderator_id,action,reason,created_at)
        VALUES(?,?,?,?,?,?)
    """, (
        chat_id,
        user_id,
        moderator_id,
        action,
        reason,
        datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()


# =========================
# HELPERS
# =========================

def display_user(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.full_name)}</a>'


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


async def telegram_is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception:
        return False


async def effective_role(chat_id: int, user_id: int) -> str:
    if user_id == OWNER_ID:
        return "OWNER"
    role = db_role(chat_id, user_id)
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


async def send_temporary(message: Message, text: str, seconds: int = AUTO_DELETE_SECONDS):
    sent = await message.answer(text)
    asyncio.create_task(delete_after(sent, seconds))
    return sent


async def cleanup_admin_command(message: Message, bot_reply: Message | None = None):
    async def _cleanup():
        await asyncio.sleep(AUTO_DELETE_SECONDS)
        await safe_delete(message)
        if bot_reply:
            await safe_delete(bot_reply)
    asyncio.create_task(_cleanup())


def is_allowed_chat(chat_id: int) -> bool:
    return ALLOWED_CHAT_ID == 0 or chat_id == ALLOWED_CHAT_ID


async def ensure_active_message(message: Message) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        return True

    if not is_allowed_chat(message.chat.id):
        return False

    activated = get_chat_activation(message.chat.id)
    if activated is None:
        # Если бот уже находился в группе до обновления/первого запуска,
        # начинаем работу "сейчас" и не трогаем сообщение, которое создало запись.
        activate_chat(message.chat.id, message.chat.title)
        return False

    # Telegram Message.date — время создания сообщения.
    # Старые сообщения, появившиеся до подключения бота, игнорируем.
    return int(message.date.timestamp()) >= activated


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
    if not urls:
        return False
    return any(host_from_url(url) not in ALLOWED_LINK_HOSTS for url in urls)


def parse_duration(value: str | None, default_minutes: int = 30) -> timedelta:
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


async def target_from_reply(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        sent = await message.answer("↩️ Используй команду ответом на сообщение пользователя.")
        await cleanup_admin_command(message, sent)
        return None
    return message.reply_to_message.from_user


async def require_role(message: Message, minimum: str) -> bool:
    if not message.from_user:
        return False
    if not await has_role(message.chat.id, message.from_user.id, minimum):
        sent = await message.answer(
            f"⛔ Для этой команды нужна роль не ниже <b>{role_label(minimum)}</b>."
        )
        await cleanup_admin_command(message, sent)
        return False
    return True


async def require_hierarchy(message: Message, target) -> bool:
    if not message.from_user:
        return False
    if not await can_act_on(message.chat.id, message.from_user.id, target.id):
        sent = await message.answer(
            "⛔ Нельзя применить это действие к пользователю с равной или более высокой ролью."
        )
        await cleanup_admin_command(message, sent)
        return False
    return True


# =========================
# AI MODERATION
# =========================

async def ai_classify(text: str) -> str:
    if not ai_client or not text or len(text) < 4:
        return "ALLOW"

    prompt = f"""
Ты модератор русскоязычной Telegram-группы.
Верни только одну метку:
ALLOW
PROFANITY
INSULT
FAMILY_INSULT

PROFANITY — мат/грубая ругань.
INSULT — прямое оскорбление человека.
FAMILY_INSULT — оскорбление матери, отца, родителей или семьи.
Не наказывай за нейтральное обсуждение правил или цитирование слова.

Сообщение:
{text[:1200]}
"""
    try:
        response = await ai_client.responses.create(
            model=AI_MODEL,
            input=prompt,
            max_output_tokens=12,
        )
        value = (response.output_text or "").strip().upper()
        for label in ("FAMILY_INSULT", "INSULT", "PROFANITY", "ALLOW"):
            if label in value:
                return label
    except Exception as e:
        log.warning("AI moderation error: %s", e)
    return "ALLOW"


async def detect_violation(text: str) -> str | None:
    norm = normalize_text(text)

    if matches_any(norm, FAMILY_PATTERNS):
        return "FAMILY_INSULT"
    if matches_any(norm, PROFANITY_PATTERNS):
        return "PROFANITY"
    if matches_any(norm, INSULT_PATTERNS):
        return "INSULT"

    if ai_client:
        label = await ai_classify(text)
        return None if label == "ALLOW" else label

    return None


# =========================
# PUNISHMENTS
# =========================

async def mute_user(message: Message, user, duration: timedelta, reason: str, moderator_text: str, moderator_id=None, auto_cleanup=False):
    until = datetime.now(timezone.utc) + duration
    try:
        await bot.restrict_chat_member(
            message.chat.id,
            user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception:
        sent = await message.answer(
            "❌ Не удалось выдать мут. Проверь права бота на ограничение участников."
        )
        if auto_cleanup:
            await cleanup_admin_command(message, sent)
        return None

    log_action(message.chat.id, user.id, moderator_id, "mute", reason)

    sent = await message.answer(
        f"🔇 <b>Мут выдан</b>\n\n"
        f"👤 Пользователь: {display_user(user)}\n"
        f"⏱ Срок: <b>{human_duration(duration)}</b>\n"
        f"📝 Причина: <b>{html.escape(reason)}</b>\n"
        f"👮 Выдал: {moderator_text}"
    )

    if auto_cleanup:
        await cleanup_admin_command(message, sent)

    return sent


# =========================
# JOIN / ACTIVATION
# =========================

@dp.my_chat_member()
async def bot_membership_update(event: ChatMemberUpdated):
    if event.chat.type not in {"group", "supergroup"}:
        return

    if not is_allowed_chat(event.chat.id):
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    was_out = old_status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}
    now_in = new_status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    }

    if was_out and now_in:
        activate_chat(
            event.chat.id,
            event.chat.title,
            int(event.date.timestamp()),
        )
        with suppress(Exception):
            sent = await bot.send_message(
                event.chat.id,
                "🛡 <b>Модерация активирована.</b>\n"
                "Я буду проверять только новые сообщения, отправленные после моего подключения.\n"
                f"👑 Владелец системы: @{OWNER_USERNAME}"
            )
            asyncio.create_task(delete_after(sent, AUTO_DELETE_SECONDS))


# =========================
# BASIC COMMANDS
# =========================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not await ensure_active_message(message):
        return
    await message.answer(
        "🛡 <b>Advanced Moderator</b>\n\n"
        "Команды: /rules, /help, /chatid, /role, /staff, /warns\n"
        "Админские: /warn, /mute, /unmute, /ban, /unban, /clearwarns\n"
        "Управление ролями: /setrole, /demote"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not await ensure_active_message(message):
        return
    await message.answer(
        "🛡 <b>Команды</b>\n\n"
        "Для всех:\n"
        "/rules — правила\n"
        "/role — своя роль\n"
        "/staff — состав администрации\n"
        "/warns — свои предупреждения\n\n"
        "HELPER+:\n"
        "/warn причина — ответом на сообщение\n\n"
        "MODERATOR+:\n"
        "/mute 30m причина\n"
        "/unmute\n\n"
        "ADMIN+:\n"
        "/ban причина\n"
        "/unban ID\n"
        "/clearwarns\n\n"
        "DEPUTY+:\n"
        "/setrole helper|moderator|admin\n"
        "/demote\n\n"
        "OWNER:\n"
        "/setrole deputy\n\n"
        "Форматы времени: 10m, 2h, 1d, 1w"
    )


@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    if not await ensure_active_message(message):
        return
    await message.answer(RULES_TEXT)


@dp.message(Command("chatid"))
async def cmd_chatid(message: Message):
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Эту команду нужно отправить в группе.")
        return

    await message.answer(
        f"🆔 ID этой группы:\n<code>{message.chat.id}</code>\n\n"
        f"Добавь в Render → Environment:\n"
        f"<code>ALLOWED_CHAT_ID={message.chat.id}</code>"
    )


@dp.message(Command("role"))
async def cmd_role(message: Message):
    if not await ensure_active_message(message):
        return
    target = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else message.from_user
    )
    if not target:
        return
    role = await effective_role(message.chat.id, target.id)
    await message.answer(
        f"🎖 {display_user(target)}\nРоль: <b>{role_label(role)}</b>"
    )


@dp.message(Command("staff"))
async def cmd_staff(message: Message):
    if not await ensure_active_message(message):
        return

    rows = db.execute("""
        SELECT user_id, username, full_name, role
        FROM roles
        WHERE chat_id=? AND role!='MEMBER'
        ORDER BY
            CASE role
                WHEN 'DEPUTY' THEN 40
                WHEN 'ADMIN' THEN 30
                WHEN 'MODERATOR' THEN 20
                WHEN 'HELPER' THEN 10
                ELSE 0
            END DESC,
            full_name ASC
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

    text = ["👥 <b>Состав администрации</b>", ""]
    for role in ("OWNER", "DEPUTY", "ADMIN", "MODERATOR", "HELPER"):
        members = sections[role]
        if members:
            text.append(f"<b>{role_label(role)}</b>")
            text.extend(f"• {m}" for m in members)
            text.append("")

    await message.answer("\n".join(text).rstrip())


@dp.message(Command("warns"))
async def cmd_warns(message: Message):
    if not await ensure_active_message(message):
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


# =========================
# ROLE MANAGEMENT
# =========================

@dp.message(Command("setrole"))
async def cmd_setrole(message: Message):
    if not await ensure_active_message(message):
        return
    if not await require_role(message, "DEPUTY"):
        return

    target = await target_from_reply(message)
    if not target:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        sent = await message.answer("Использование: <code>/setrole moderator</code>")
        await cleanup_admin_command(message, sent)
        return

    requested = ROLE_ALIASES.get(parts[1].strip().lower())
    if not requested:
        sent = await message.answer("Роли: helper, moderator, admin, deputy")
        await cleanup_admin_command(message, sent)
        return

    actor_role = await effective_role(message.chat.id, message.from_user.id)

    if requested == "DEPUTY" and actor_role != "OWNER":
        sent = await message.answer("⛔ Роль DEPUTY может выдавать только OWNER.")
        await cleanup_admin_command(message, sent)
        return

    if ROLE_LEVELS[requested] >= ROLE_LEVELS[actor_role]:
        sent = await message.answer("⛔ Нельзя выдать роль равную или выше своей.")
        await cleanup_admin_command(message, sent)
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
    await cleanup_admin_command(message, sent)


@dp.message(Command("demote"))
async def cmd_demote(message: Message):
    if not await ensure_active_message(message):
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
        f"⬇️ <b>Роль снята</b>\n\n"
        f"👤 {display_user(target)}\n"
        f"🎖 Новая роль: <b>{role_label('MEMBER')}</b>\n"
        f"👮 Понизил: {display_user(message.from_user)}"
    )
    await cleanup_admin_command(message, sent)


# =========================
# MODERATION COMMANDS
# =========================

@dp.message(Command("warn"))
async def cmd_warn(message: Message):
    if not await ensure_active_message(message):
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

    # Команда админа + ответ бота удалятся через 20 секунд.
    await cleanup_admin_command(message, sent)

    if count >= AUTO_MUTE_WARN_COUNT:
        clear_warns(message.chat.id, target.id)
        mute_notice = await mute_user(
            message,
            target,
            timedelta(minutes=AUTO_MUTE_MINUTES),
            f"{AUTO_MUTE_WARN_COUNT} предупреждения",
            display_user(message.from_user),
            message.from_user.id,
            auto_cleanup=False,
        )
        if mute_notice:
            asyncio.create_task(delete_after(mute_notice, AUTO_DELETE_SECONDS))


@dp.message(Command("clearwarns"))
async def cmd_clearwarns(message: Message):
    if not await ensure_active_message(message):
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
    await cleanup_admin_command(message, sent)


@dp.message(Command("mute"))
async def cmd_mute(message: Message):
    if not await ensure_active_message(message):
        return
    if not await require_role(message, "MODERATOR"):
        return

    target = await target_from_reply(message)
    if not target:
        return
    if not await require_hierarchy(message, target):
        return

    parts = (message.text or "").split(maxsplit=2)
    duration = parse_duration(parts[1] if len(parts) > 1 else None, 30)
    reason = parts[2] if len(parts) > 2 else "нарушение правил"

    await mute_user(
        message,
        target,
        duration,
        reason,
        display_user(message.from_user),
        message.from_user.id,
        auto_cleanup=True,
    )


@dp.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not await ensure_active_message(message):
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
        await cleanup_admin_command(message, sent)
        return

    log_action(message.chat.id, target.id, message.from_user.id, "unmute", "unmute")
    sent = await message.answer(
        f"🔊 Мут снят с {display_user(target)}.\n"
        f"👮 Снял: {display_user(message.from_user)}"
    )
    await cleanup_admin_command(message, sent)


@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not await ensure_active_message(message):
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
        sent = await message.answer("❌ Не удалось забанить пользователя.")
        await cleanup_admin_command(message, sent)
        return

    log_action(message.chat.id, target.id, message.from_user.id, "ban", reason)
    sent = await message.answer(
        f"🔨 <b>Пользователь забанен</b>\n\n"
        f"👤 {display_user(target)}\n"
        f"📝 Причина: <b>{html.escape(reason)}</b>\n"
        f"👮 Выдал: {display_user(message.from_user)}"
    )
    await cleanup_admin_command(message, sent)


@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await ensure_active_message(message):
        return
    if not await require_role(message, "ADMIN"):
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        sent = await message.answer("Использование: <code>/unban 123456789</code>")
        await cleanup_admin_command(message, sent)
        return

    user_id = int(parts[1])
    if user_id == OWNER_ID:
        sent = await message.answer("⛔ OWNER нельзя блокировать или разблокировать через обычную модерацию.")
        await cleanup_admin_command(message, sent)
        return

    try:
        await bot.unban_chat_member(message.chat.id, user_id, only_if_banned=True)
    except Exception:
        sent = await message.answer("❌ Не удалось разбанить пользователя.")
        await cleanup_admin_command(message, sent)
        return

    log_action(message.chat.id, user_id, message.from_user.id, "unban", "unban")
    sent = await message.answer(
        f"✅ Пользователь <code>{user_id}</code> разбанен.\n"
        f"👮 Выполнил: {display_user(message.from_user)}"
    )
    await cleanup_admin_command(message, sent)


# =========================
# AUTOMOD
# =========================

@dp.message(F.text)
async def automod(message: Message):
    if message.chat.type not in {"group", "supergroup"}:
        return
    if not await ensure_active_message(message):
        return
    if not message.from_user or message.from_user.is_bot:
        return

    role = await effective_role(message.chat.id, message.from_user.id)

    # Персонал не попадает под автоматический фильтр.
    if ROLE_LEVELS[role] >= ROLE_LEVELS["HELPER"]:
        return

    text = message.text or ""

    # Любая сторонняя ссылка: удаляем, но НЕ мутим и НЕ варним.
    if contains_forbidden_link(text):
        await safe_delete(message)
        sent = await bot.send_message(
            message.chat.id,
            f"🔗 {display_user(message.from_user)}, сторонние ссылки и реклама запрещены.\n"
            f"✅ TikTok разрешён.\n"
            f"Наказание за ссылку не выдано."
        )
        asyncio.create_task(delete_after(sent, AUTO_DELETE_SECONDS))
        return

    violation = await detect_violation(text)
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
            auto_cleanup=False,
        )
        if sent:
            asyncio.create_task(delete_after(sent, AUTO_DELETE_SECONDS))

        rules = await bot.send_message(message.chat.id, RULES_TEXT)
        asyncio.create_task(delete_after(rules, AUTO_DELETE_SECONDS))
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
            auto_cleanup=False,
        )
        if sent:
            asyncio.create_task(delete_after(sent, AUTO_DELETE_SECONDS))
    else:
        sent = await bot.send_message(
            message.chat.id,
            f"⚠️ {display_user(message.from_user)}, сообщение удалено.\n"
            f"📝 Причина: <b>{html.escape(reason)}</b>\n"
            f"📊 Предупреждения: <b>{count}/{AUTO_MUTE_WARN_COUNT}</b>\n\n"
            f"{RULES_TEXT}"
        )
        asyncio.create_task(delete_after(sent, AUTO_DELETE_SECONDS))


# =========================
# HEALTH / STARTUP
# =========================

async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "advanced-telegram-moderator",
    })


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()


async def main():
    init_db()
    await start_health_server()

    # Критично: не обрабатываем накопившиеся старые updates после офлайна/деплоя.
    await bot.delete_webhook(drop_pending_updates=True)

    log.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
