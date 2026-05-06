# MVP контроля распечаток МОЭК

Проект разбирает PDF-распечатки теплосчетчиков из текущей папки и собирает цветной HTML-дашборд по состоянию приборов.

Отчеты группируются по заводскому номеру прибора, точке измерения/системе и ресурсу. Ошибки показываются на уровне теплосчетчика, а не отдельного PDF.

## Запуск

```bash
python3 -m pip install -r requirements.txt
python3 build_dashboard.py
```

После запуска появятся:

- `parsed_reports.json` — разобранные показания и результаты проверок;
- `dashboard.html` — интерактивный дашборд, который можно открыть в браузере.

## Сбор из Mail.ru

Параметры уже подготовлены в `.env`:

- ящик: `promstroyproekt@mail.ru`;
- папка: `Распечатки`;
- период: последние 2 месяца;
- после обработки письма остаются как есть.

Нужно только вписать в `.env` пароль для внешнего приложения:

```env
MAILRU_APP_PASSWORD=...
```

После этого запустите:

```bash
python3 mail_collector.py
python3 build_dashboard.py --pdf-dir mailru_pdfs
```

PDF сохраняются в `mailru_pdfs`, повторные вложения не скачиваются второй раз благодаря `mailru_downloads.json`.

Удобный вариант одной командой из любой папки:

```bash
cd /Users/vitaliigudelev/Documents/Moek_dash
./run_pipeline.sh
```

Автоматическая проверка Eldis каждые 30 минут:

```bash
cd /Users/vitaliigudelev/Documents/Moek_dash
./run_scheduler.sh
```

Планировщик больше не обращается к Mail.ru: каждые 30 минут он собирает данные через Eldis API и пересобирает `dashboard.html`. Исторические копии отчетов складываются в `reports_history`.

## Сбор из Eldis24

Eldis24 можно использовать вместо PDF-распечаток. Сборщик сам получает доступные ТУ по двум кабинетам, забирает часовой архив (`typeDataCode=30003`) через `data/normalized` и приводит данные к тому же формату, который использует дашборд.

Добавьте ключи API в `.env`:

```env
ELDIS_API_BASE=https://api.eldis24.ru/api/v1
ELDIS_ACCOUNT_1_NAME=moek_1
ELDIS_ACCOUNT_1_KEY=...
ELDIS_ACCOUNT_1_LOGIN=...
ELDIS_ACCOUNT_1_PASSWORD=...
ELDIS_ACCOUNT_1_ACCESS_TOKEN=
ELDIS_ACCOUNT_2_NAME=moek_2
ELDIS_ACCOUNT_2_KEY=...
ELDIS_ACCOUNT_2_LOGIN=...
ELDIS_ACCOUNT_2_PASSWORD=...
ELDIS_ACCOUNT_2_ACCESS_TOKEN=
ELDIS_LOOKBACK_MONTHS=2
```

`ELDIS_ACCOUNT_*_ACCESS_TOKEN` обычно можно оставить пустым. Если заданы `LOGIN` и `PASSWORD`, сборщик сам вызовет `users/login` и получит `access_token` из Cookie.

Проверить список доступных теплосчетчиков без загрузки архивов:

```bash
python3 eldis_collector.py --discover
```

Одна команда для полного обновления:

```bash
cd /Users/vitaliigudelev/Documents/Moek_dash
./run_eldis_pipeline.sh
```

Автоматический режим каждые 30 минут:

```bash
./run_eldis_scheduler.sh
```

Фоновый запуск через macOS `launchd`, чтобы терминал можно было закрыть:

```bash
./install_eldis_launch_agent.sh
```

Остановить и удалить фоновую задачу:

```bash
./uninstall_eldis_launch_agent.sh
```

Логи фонового запуска пишутся в `logs/eldis_scheduler.out.log` и `logs/eldis_scheduler.err.log`.

## Публикация через GitHub Pages

Чтобы компьютер мог быть выключен, используйте GitHub Actions. Workflow `.github/workflows/eldis-dashboard.yml` каждые 30 минут собирает Eldis-данные, формирует `dashboard.html` как `index.html` и публикует его через GitHub Pages.

Кратко:

```text
Settings -> Secrets and variables -> Actions -> Secrets
```

Добавьте секреты `ELDIS_ACCOUNT_1_KEY`, `ELDIS_ACCOUNT_1_LOGIN`, `ELDIS_ACCOUNT_1_PASSWORD` и такие же для `ELDIS_ACCOUNT_2`, если нужен второй аккаунт.

Затем:

```text
Settings -> Pages -> Source: GitHub Actions
Actions -> Eldis dashboard -> Run workflow
```

Подробная инструкция: `docs/github/eldis-pages.md`.

После запуска появятся `eldis_points.csv` и `eldis_reports.json`, затем пересоберется обычный `dashboard.html`. Для ручного периода можно использовать:

```bash
python3 eldis_collector.py --collect --start 2026-03-01 --end 2026-05-01
python3 build_dashboard.py --reports-json eldis_reports.json
```

## Telegram-бот

Первая функция бота: по кнопке создает графическую часовую распечатку за последние 36 часов. Сценарий:

- `/start`;
- кнопка `Создать часовую распечатку (36ч)`;
- выбор системы (`ГВС`, `ТЭ/ТС`);
- выбор теплосчетчика;
- бот присылает PNG с колонками: дата/время, `M1`, `M2`, разница `M`, `T1`, `T2`, `dT`, `Q`, наработка, наружная температура.

Токен берется из `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=
```

`TELEGRAM_ALLOWED_CHAT_IDS` можно оставить пустым, тогда бот отвечает всем, кто ему написал. Для ограничения доступа впишите один или несколько ID через запятую, например `123456789,987654321`.

Чтобы узнать свой Telegram ID, запустите бота с пустым `TELEGRAM_ALLOWED_CHAT_IDS`, напишите ему `/start`, затем при необходимости впишите этот ID в `.env` и перезапустите бота.

Запуск:

```bash
cd /Users/vitaliigudelev/Documents/Moek_dash
./run_telegram_bot.sh
```

Бот читает уже собранный `parsed_reports.json`, поэтому перед запуском нужно хотя бы один раз выполнить `./run_pipeline.sh`, `./run_eldis_pipeline.sh` или `python3 build_dashboard.py --pdf-dir mailru_pdfs`.

Проверка генератора без Telegram:

```bash
python3 telegram_report.py --list
python3 telegram_report.py --sample 0
```

## Публикация на GitHub

Приватные и тяжелые файлы исключены через `.gitignore`: `.env`, PDF, скачанные вложения, JSON-выгрузки, HTML-отчеты и временные PNG для Telegram.

Команды для первого размещения:

```bash
cd /Users/vitaliigudelev/Documents/Moek_dash
git init
git add .
git commit -m "Add MOEK dashboard and Telegram bot"
git branch -M main
git remote add origin https://github.com/USER/REPO.git
git push -u origin main
```

## Что проверяет карточка теплосчетчика

- по умолчанию показывает ошибки за последние 5 дней;
- позволяет смотреть все ошибки по дням;
- красный статус включается только при критических ошибках за последние 7 дней;
- критично: наработка меньше 23 ч за последние полные сутки;
- критично: `M1/M2 > 3%` в двух и более часах за сутки;
- критично: нет данных больше 20 часов;
- `t2` относительно профиля из `moek_temperature_graph.csv` допускается в пределах `+3 °C`, остальные отклонения показываются в подробностях;
- `t1` проверяется по погоде Москвы из `weather_moscow_hourly.json`, который обновляется через Open-Meteo;
- наработка меньше 1 часа в часовой строке показывается в подробностях;
- пропуски `---` показываются справочно и не делают статус критичным.

По умолчанию используется профиль `rts_kts_150_70`. Для отдельного теплосчетчика профиль можно назначить в `meter_graph_profiles.csv` по заводскому номеру, точке/системе и ресурсу.

## Следующий слой

- автоматический IMAP-сбор PDF из папки mail.ru;
- база данных PostgreSQL;
- роли пользователей и доступ через интернет;
- настройка порогов по объектам и типам приборов;
- история замечаний и комментарии инженера.
