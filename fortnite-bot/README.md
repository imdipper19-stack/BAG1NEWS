# 🎮 Fortnite AI Telegram Bot

Полностью автоматизированный AI-ассистент для Telegram-канала [@FortnitebucksShop](https://t.me/FortnitebucksShop).

Бот собирает новости Fortnite из множества источников, пишет короткие русскоязычные Telegram-посты через Closerouter, генерирует 16:9 медиа-карточки и публикует их с кнопкой перехода в магазин.

---

## Что делает бот

- **Сбор новостей** — мониторит Fortnite-API, fortnite.com/news, YouTube RSS, утечки от HYPEX/ShiinaBR/FireMonkey, r/FortniteLeaks и fortnite.gg
- **Дедупликация** — не публикует одну и ту же новость дважды (Redis + PostgreSQL)
- **Оценка релевантности** — скоринг 0–100 по 5 критериям, автофильтр скучного контента
- **Проверка утечек** — корректно маркирует официальные новости, данные из API и неподтверждённые утечки
- **Написание постов** — переписывает новости на русском через Closerouter (`openai/gpt-5.5`)
- **Генерация изображений** — создаёт 16:9 медиа-карточки через Closerouter (`openai/gpt-image-2`)
- **Публикация** — отправляет фото + текст в канал; ссылка на магазин остается в caption
- **Расписание** — 3–6 постов в день в обычном режиме, до 20 постов в день при запуске сезона

---

## Требования

- [Docker](https://docs.docker.com/get-docker/) 24+
- [Docker Compose](https://docs.docker.com/compose/install/) v2+

Больше ничего устанавливать не нужно — всё запускается внутри контейнеров.

---

## Быстрый старт

### 1. Скопировать проект

```bash
git clone <repo-url> fortnite-bot
cd fortnite-bot
```

Или просто скопируйте папку проекта на сервер.

### 2. Настроить переменные окружения

```bash
cp .env.example .env
```

Откройте `.env` и заполните обязательные переменные (см. раздел [Конфигурация](#конфигурация) ниже).

### 3. Запустить

```bash
docker-compose up -d
```

Docker соберёт образ и запустит все сервисы: PostgreSQL, Redis, приложение, Celery worker и Celery beat.

### 4. Проверить работоспособность

```bash
curl http://localhost:8000/health
```

Ожидаемый ответ: `{"status": "ok"}`

---

## Конфигурация

Все настройки хранятся в файле `.env`. Ниже описание каждой переменной.

### Telegram

| Переменная | Описание | Пример |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) | `1234567890:AAF...` |
| `TELEGRAM_CHANNEL_ID` | Username или числовой ID канала для публикации | `@FortnitebucksShop` |

### AI provider (Closerouter)

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `CLOSEROUTER_BASE_URL` | Базовый URL API (с `/v1` или без) | `https://api.closerouter.dev` |
| `CLOSEROUTER_API_KEY` | API-ключ для текста и изображений | — |
| `CLOSEROUTER_TEXT_MODEL` | Модель для генерации текста | `openai/gpt-5.5` |
| `CLOSEROUTER_IMAGE_MODEL` | Модель для генерации изображений | `openai/gpt-image-2` |
| `IMAGE_SIZE` | Размер 16:9 изображения | `1536x864` |

### База данных (PostgreSQL)

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `DATABASE_URL` | Полная строка подключения (asyncpg) | `postgresql+asyncpg://fortnite:password@postgres:5432/fortnite_bot` |
| `POSTGRES_USER` | Имя пользователя PostgreSQL | `fortnite` |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL | `password` |
| `POSTGRES_DB` | Имя базы данных | `fortnite_bot` |

### Redis

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `REDIS_URL` | URL подключения к Redis | `redis://redis:6379/0` |

### Магазин

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `SHOP_URL` | URL магазина V-Bucks для CTA в постах | `https://bag1v-bucks.shop/` |

### Reddit (опционально)

Нужен только для сбора постов из r/FortniteLeaks через официальный API. Без этих переменных бот будет использовать публичный JSON-эндпоинт Reddit.

| Переменная | Описание |
|---|---|
| `REDDIT_CLIENT_ID` | Client ID приложения Reddit |
| `REDDIT_CLIENT_SECRET` | Client Secret приложения Reddit |
| `REDDIT_USER_AGENT` | User-Agent строка для запросов |

Создать приложение Reddit можно на [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps).

### Лимиты публикаций

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `MAX_POSTS_PER_DAY` | Максимум постов в сутки | `20` |
| `MIN_SCORE_TO_PUBLISH` | Минимальный скор (0–100) для публикации | `70` |

---

## Архитектура пайплайна

Каждый пост проходит через 9 этапов:

1. **Сбор** — данные из выбранного источника (Fortnite-API, YouTube RSS, nitter, Reddit, fortnite.gg)
2. **Нормализация** — приведение к единой схеме `RawItem`
3. **Дедупликация** — проверка хешей URL и заголовка в Redis (TTL 7 дней) + проверка в PostgreSQL
4. **Скоринг** — оценка 0–100 по 5 параметрам: relevance, freshness, source_trust, audience_interest, monetization_fit
5. **Верификация** — установка флагов `is_official` и `is_leak` по правилам из источника
6. **Написание** — LLM (`openai/gpt-5.5` через Closerouter) пишет короткий Telegram-caption
7. **Генерация изображения** — Closerouter (`openai/gpt-image-2`) создаёт 16:9 медиа-карточку
8. **Публикация** — отправка фото + текста в канал @FortnitebucksShop
9. **Сохранение в БД** — запись в `posts` и `published_posts`

Лимит публикаций (по умолчанию 20 в сутки) хранится как счётчик в Redis с TTL 48 часов.

---

## Запуск тестового прогона

Чтобы проверить полный пайплайн без реальной публикации в Telegram:

```bash
docker-compose exec app python scripts/test_pipeline.py
```

Скрипт запустит один полный цикл (сбор → нормализация → дедупликация → скоринг → верификация → написание поста → генерация изображения) с источником Fortnite-API и выведет результат в консоль. Публикация в Telegram не выполняется (`dry_run=True`).

---

## Заполнение базы источниками

При первом запуске нужно добавить все источники новостей в базу данных:

```bash
docker-compose exec app python scripts/seed_sources.py
```

Скрипт вставит все 13 источников из спецификации в таблицу `sources`.

---

## Структура проекта

```
fortnite-bot/
├── app/
│   ├── bot/                  # Telegram-бот (aiogram)
│   ├── collectors/           # Сборщики новостей по источникам
│   ├── db/                   # Модели SQLAlchemy, сессия, инициализация БД
│   ├── prompts/              # Промпты для LLM и генерации изображений
│   ├── services/             # Бизнес-логика (скоринг, верификация, LLM, изображения)
│   ├── celery_app.py         # Celery-приложение
│   ├── config.py             # Настройки через pydantic-settings
│   ├── main.py               # FastAPI-приложение с /health эндпоинтом
│   ├── pipeline.py           # Полный пайплайн обработки новости
│   └── tasks.py              # Celery-задачи с расписанием
├── scripts/
│   ├── seed_sources.py       # Заполнение таблицы sources
│   └── test_pipeline.py      # Тестовый прогон пайплайна
├── images/                   # Сгенерированные баннеры (монтируется в контейнер)
├── .env.example              # Шаблон переменных окружения
├── docker-compose.yml        # Оркестрация сервисов
├── Dockerfile                # Образ приложения
└── requirements.txt          # Python-зависимости
```

---

## Расписание публикаций

| Источник | Интервал |
|---|---|
| Fortnite-API (магазин) | Раз в сутки в 00:05 UTC |
| Fortnite-API (косметика, новости) | Каждые 30 минут |
| fortnite.com/news | Каждые 30 минут |
| YouTube RSS | Каждые 15 минут |
| Утечки (HYPEX, ShiinaBR, FireMonkey) | Каждые 10 минут |
| r/FortniteLeaks | Каждые 30 минут |
| fortnite.gg | Каждые 60 минут |
| Обработка очереди | Каждые 5 минут |

В обычном режиме публикуется 3–6 постов в день. При запуске нового сезона лимит поднимается до 20 постов в день.

---

## Как остановить бота

```bash
docker-compose down
```

Чтобы также удалить все данные (базу данных и кэш Redis):

```bash
docker-compose down -v
```

> ⚠️ Флаг `-v` удаляет Docker volumes с данными PostgreSQL и Redis. Используйте с осторожностью.

---

## Полезные команды

```bash
# Посмотреть логи всех сервисов
docker-compose logs -f

# Логи только приложения
docker-compose logs -f app

# Логи Celery worker
docker-compose logs -f worker

# Перезапустить только приложение после изменений кода
docker-compose up -d --build app

# Открыть shell внутри контейнера
docker-compose exec app bash
```
