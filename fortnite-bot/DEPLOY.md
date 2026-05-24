# Деплой на VPS

Гайд для VPS на Ubuntu 22.04+ с **2 GB RAM / 1 CPU / 40 GB диск**.

---

## 1. Подготовка сервера (один раз)

Подключись по SSH к VPS и выполни:

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker (официальный скрипт)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Перелогинься, чтобы группа подцепилась
exit
```

Снова заходи по SSH и проверь:

```bash
docker --version
docker compose version
```

### Swap 2 GB (важно для 2 GB RAM)

Без swap при пиках Playwright VPS может зависнуть. Добавляем 2 GB swap-файл:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Проверка
free -h
```

Должна появиться строчка `Swap: 2.0Gi`.

---

## 2. Перенос проекта

С локальной машины (Windows PowerShell):

```powershell
# Создаём архив без лишнего
cd c:\Users\S6lev\Desktop\BAG1-News
tar -czf fortnite-bot.tar.gz `
    --exclude='fortnite-bot/images/*' `
    --exclude='fortnite-bot/__pycache__' `
    --exclude='fortnite-bot/**/__pycache__' `
    fortnite-bot

# Заливаем на сервер (замени user@your-vps-ip)
scp fortnite-bot.tar.gz user@your-vps-ip:~/
```

На сервере:

```bash
cd ~
tar -xzf fortnite-bot.tar.gz
cd fortnite-bot
```

---

## 3. Конфигурация

Файл `.env` уже содержит все секреты, проверь:

```bash
cat .env | grep -v '^#' | grep -v '^$'
```

Убедись что есть:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID=@FortnitebucksShop`
- `TELEGRAM_ADMIN_USER_ID=7424891771`
- `CLOSEROUTER_API_KEY`
- `REQUIRE_ADMIN_APPROVAL=true` (рекомендуется на старте)

---

## 4. Запуск

```bash
# Билд + старт с production-настройками (лимиты памяти, restart=always)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Подождать минуту пока всё стартанёт
sleep 60

# Проверить что все 5 контейнеров живые
docker compose ps

# Проверить health
curl http://localhost:8000/health
```

Должен вернуть `{"status":"ok",...}`.

---

## 5. Заполнение источников (один раз после первого запуска)

```bash
docker compose exec app python scripts/seed_sources.py
```

---

## 6. Проверка работы

В Telegram:
1. Напиши `/start` боту `@Bag1News_bot`
2. Должен ответить
3. `/status` → покажет лимит постов на день и состояние очереди
4. Через 10–15 минут должен прийти первый пост на модерацию (когда worker соберёт что-то с высоким score)

---

## 7. Мониторинг

```bash
# Логи всех сервисов
docker compose logs -f --tail=100

# Только worker
docker compose logs -f worker

# Использование памяти/CPU
docker stats --no-stream

# Размер дисков
df -h
docker system df
```

---

## 8. Обновление кода (когда что-то поменяешь)

```bash
# С локалки переливаешь архив (как в шаге 2)
# На сервере:
cd ~/fortnite-bot
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

---

## 9. Бэкап БД (раз в неделю)

```bash
# Дамп
docker compose exec -T postgres pg_dump -U fortnite fortnite_bot > backup-$(date +%Y%m%d).sql

# Восстановление (если что)
cat backup-20260101.sql | docker compose exec -T postgres psql -U fortnite fortnite_bot
```

---

## 10. Очистка старых картинок (раз в месяц)

```bash
# Удалить картинки старше 30 дней
docker compose exec app find /app/images -type f -mtime +30 -delete

# Очистить старые образы
docker image prune -af
```

---

## 11. Если что-то пошло не так

```bash
# Полная остановка
docker compose down

# Удалить всё (включая БД!) — только если хочешь начать с нуля
docker compose down -v

# Перезапустить только один сервис
docker compose restart app

# Зайти внутрь контейнера
docker compose exec app bash
```

---

## Тонкие моменты

### Память впритык

При мониторинге через `docker stats` нормальные значения:
- `postgres` — 80–200 MB
- `redis` — 30–60 MB
- `app` — 150–250 MB
- `worker` — 200–500 MB (пик 600 при Playwright)
- `beat` — 80–120 MB

**Итого ~700 MB idle, до 1.3 GB при сборе fortnite.gg.** Swap 2 GB страхует пики.

Если хочешь больше запаса:
- Закомментируй задачу `collect-fortnite-gg` в `app/celery_app.py` — это самая прожорливая
- Подними VPS до 4 GB RAM

### Production-режим без approval

Когда увидишь что бот стабильно генерит хорошие посты (~1-2 недели):

```bash
# В .env заменить
REQUIRE_ADMIN_APPROVAL=false

# Применить
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate
```

Бот начнёт публиковать сам, до 20 постов в день.
