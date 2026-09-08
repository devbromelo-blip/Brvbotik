# BroMelo Moderator v5 — Render Webhook

Эта версия работает через Telegram **webhook**, а не polling.
Поэтому ошибка `TelegramConflictError: other getUpdates request` больше не должна появляться.

## 1. Файлы на GitHub

Загрузи в корень репозитория:

- `bot.py`
- `requirements.txt`
- `render.yaml`
- `.python-version`
- `.gitignore`

## 2. Render

Создай/обнови Web Service.

Build Command:

`pip install -r requirements.txt`

Start Command:

`python bot.py`

## 3. Environment Variables

Обязательно:

`BOT_TOKEN` = токен из BotFather

`WEBHOOK_URL` = полный адрес Render без слеша в конце.

Пример:

`https://bromelo-moderator.onrender.com`

Также:

`OWNER_ID=8668633782`

`OWNER_USERNAME=devBroMelo`

`ALLOWED_CHAT_ID=0`

`AUTO_DELETE_SECONDS=20`

`AUTO_MUTE_WARN_COUNT=3`

`AUTO_MUTE_MINUTES=5`

`FAMILY_MUTE_MINUTES=120`

Сначала оставь `ALLOWED_CHAT_ID=0`.

После запуска добавь бота в группу и напиши:

`/chatid`

Он покажет ID группы вида:

`-1001234567890`

После этого можешь поставить:

`ALLOWED_CHAT_ID=-1001234567890`

и сделать redeploy.

## 4. BotFather

В BotFather:

`/setprivacy`

Выбери бота -> `Disable`.

Это обязательно для автоматической проверки обычных сообщений.

## 5. Права в группе

Добавь бота администратором и разреши:

- удалять сообщения;
- блокировать пользователей;
- ограничивать участников.

## 6. Быстрая проверка

В личке бота:

`/start`

Должен ответить.

В группе:

`/ping`

Должен ответить:

`🏓 Pong — бот получает сообщения.`

Затем:

`/chatid`

и:

`/role`

OWNER ID `8668633782` всегда получает роль OWNER.

## Что умеет

- мат -> удаление + варн;
- оскорбление -> удаление + варн;
- 3 варна -> мут 5 минут;
- оскорбление родителей/семьи -> мут 2 часа;
- любые ссылки кроме TikTok -> удаление БЕЗ мута/варна;
- `/mute` без времени -> 5 минут;
- `/mute 30m причина`, `/mute 2h причина`;
- `/warn`, `/unmute`, `/ban`, `/unban`, `/clearwarns`;
- роли OWNER / DEPUTY / ADMIN / MODERATOR / HELPER / MEMBER;
- `/setrole`, `/demote`, `/staff`, `/role`;
- команды админа и ответы бота удаляются через 20 секунд;
- сообщения AutoMod тоже очищаются через 20 секунд;
- старые pending updates при запуске удаляются;
- модерация начинается только с момента подключения/первого нового сообщения.

## Важно про SQLite

`bot.db` хранит роли и варны локально.
На бесплатном Render локальный файл может исчезнуть после пересоздания сервиса.
Позже лучше подключить PostgreSQL, если хочешь постоянное хранение.
