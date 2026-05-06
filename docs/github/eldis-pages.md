# GitHub Pages для Eldis-дашборда

Workflow `.github/workflows/eldis-dashboard.yml` собирает дашборд в GitHub Actions каждые 30 минут и публикует его через GitHub Pages.

## Secrets

В репозитории GitHub откройте `Settings -> Secrets and variables -> Actions -> Secrets` и добавьте:

```text
ELDIS_ACCOUNT_1_KEY
ELDIS_ACCOUNT_1_LOGIN
ELDIS_ACCOUNT_1_PASSWORD
ELDIS_ACCOUNT_2_KEY
ELDIS_ACCOUNT_2_LOGIN
ELDIS_ACCOUNT_2_PASSWORD
```

Если второй аккаунт не нужен, можно не задавать `ELDIS_ACCOUNT_2_*`.

## Variables

В `Settings -> Secrets and variables -> Actions -> Variables` можно добавить:

```text
ELDIS_ACCOUNT_1_NAME=account_1
ELDIS_ACCOUNT_2_NAME=account_2
ELDIS_LOOKBACK_MONTHS=2
ELDIS_API_BASE=https://api.eldis24.ru/api/v1
```

Все эти variables необязательные, есть значения по умолчанию.

## GitHub Pages

Откройте `Settings -> Pages` и выберите:

```text
Source: GitHub Actions
```

После первого успешного запуска GitHub покажет URL сайта.

## Первый запуск

Откройте `Actions -> Eldis dashboard -> Run workflow`.

Дальше workflow будет запускаться автоматически по расписанию:

```text
*/30 * * * *
```

Это один запуск каждые 30 минут.

## Важно

Если GitHub Pages включен для публичного доступа, адреса объектов и показания будут доступны по ссылке в браузере. Для закрытого доступа нужен приватный режим Pages, если он доступен в вашем тарифе/организации, либо отдельный защищенный хостинг.
