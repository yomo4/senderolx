# OLX.ro Sender Bot

Telegram-бот для отправки сообщений продавцам на OLX.ro через куки браузерной сессии и опциональный HTTP/SOCKS5 прокси.

---

## Возможности

- Авторизация через куки (без хранения пароля)
- Поддержка HTTP, SOCKS4, SOCKS5 прокси
- У каждого пользователя бота свои куки и прокси
- Деплой одной командой через Docker
- Ограничение доступа по Telegram ID (опционально)

---

## Быстрый старт (Docker — рекомендуется для VDS)

```bash
# 1. Скопируйте файлы на VDS
scp -r . user@your-server:/opt/olx-bot/

# 2. На сервере
cd /opt/olx-bot

# 3. Создайте .env
cp .env.example .env
nano .env          # укажите TELEGRAM_BOT_TOKEN

# 4. Запустите
docker compose up -d --build

# Логи
docker compose logs -f
```

---

## Ручная установка (Python 3.11+)

```bash
# Клонируйте / скопируйте проект
cd senderolx

# Виртуальное окружение (рекомендуется)
python -m venv venv
# Linux/macOS:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# Зависимости
pip install -r requirements.txt

# Конфиг
cp .env.example .env
# Откройте .env и заполните TELEGRAM_BOT_TOKEN

# Запуск
python bot.py
```

---

## Настройка `.env`

| Переменная | Описание | Обязательна |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather | ✅ |
| `ALLOWED_USERS` | Telegram ID через запятую (ограничение доступа) | ❌ |
| `STORAGE_FILE` | Путь к файлу хранилища (default: `data/users.json`) | ❌ |

---

## Как получить токен бота

1. Напишите [@BotFather](https://t.me/BotFather)
2. `/newbot` → введите имя и username
3. Скопируйте токен в `.env`

---

## Как получить куки OLX.ro

**Chrome/Firefox:**
1. Войдите на [olx.ro](https://www.olx.ro)
2. Нажмите **F12** → вкладка **Application** (Chrome) / **Storage** (Firefox)
3. Слева: **Cookies** → `https://www.olx.ro`
4. Скопируйте все куки в одну строку формата: `name1=value1; name2=value2`

**Через расширение (проще):**
- [Cookie-Editor](https://chrome.google.com/webstore/detail/cookie-editor/) → Export → **Header String**

**В боте:**
```
/setcookie sessionid=abc123; access_token=eyJ...; csrftoken=xyz
```

---

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие и меню |
| `/setcookie` | Установить куки OLX.ro |
| `/setproxy` | Настроить прокси |
| `/delproxy` | Удалить прокси |
| `/send [url]` | Отправить сообщение по ссылке |
| `/checkauth` | Проверить авторизацию |
| `/status` | Текущие настройки |
| `/cancel` | Отменить текущий диалог |
| `/help` | Подробная справка |

---

## Форматы прокси

```
http://host:port
http://user:pass@host:port
socks5://host:port
socks5://user:pass@host:port
```

---

## Структура проекта

```
senderolx/
├── bot.py              # Telegram bot (ConversationHandler)
├── olx_client.py       # OLX.ro HTTP client (3 стратегии отправки)
├── storage.py          # Хранилище пользователей (JSON)
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Поддержание работы на VDS (systemd)

```ini
# /etc/systemd/system/olx-bot.service
[Unit]
Description=OLX Sender Bot
After=network.target

[Service]
WorkingDirectory=/opt/olx-bot
ExecStart=/opt/olx-bot/venv/bin/python bot.py
Restart=always
RestartSec=10
EnvironmentFile=/opt/olx-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now olx-bot
journalctl -u olx-bot -f
```

---

## Примечания по API OLX.ro

Бот пробует три метода отправки по очереди:
1. **REST API** — `POST /api/v2/offers/{id}/messages/` и смежные endpoints
2. **GraphQL** — мутация `sendMessage`
3. **Контактная форма** — POST на `/d/kontakt/{id}/`

Если OLX изменит API, достаточно обновить `olx_client.py` — структура бота остаётся без изменений.

> **Важно:** OLX.ro может заблокировать сессию при нарушении их правил использования. Используйте бота ответственно.
