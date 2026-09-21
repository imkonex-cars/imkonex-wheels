# Render: полный проект WORKSPACE-1

Для архива IMKONEX_WHEELS_WORKSPACE_v0.3.0.zip. Исходники расположены обычными папками, главный HTML — frontend/index.html. В корне GitHub должны находиться BUILD_RENDER.mjs, package.json, render.yaml и папки frontend/, data/, scripts/. Полный список — FILES_MANIFEST.txt.

Создайте **Static Site** из нужного GitHub-репозитория и ветки. Для существующего сервиса проверьте Repository и Branch.

| Настройка | Значение |
|---|---|
| Root Directory | Пусто |
| Build Command | node BUILD_RENDER.mjs |
| Publish Directory | dist |
| NODE_VERSION | 22 |
| CATALOG_MODE | snapshot |

Штатный BUILD_RENDER.mjs фиксирует snapshot и запускает scripts/build.mjs. Старое значение CATALOG_MODE=demo не меняет опубликованную выборку. npm run build вызывает тот же вход. Публикуется только dist; Python, база данных и пароль поставщика для статической выборки не нужны.

При изменении существующего сервиса сохраните настройки и выберите Manual Deploy → Clear build cache & deploy. Это пересоберёт связанную ветку с очищенным кешем. Если создаёте новый сервис, используйте адрес, выданный этому сервису; прежний адрес не переносится автоматически. [Документация Render](https://render.com/docs/deploys), [Static Sites](https://render.com/docs/static-sites).

Ожидаемый журнал:

```text
BUILD_OK IMKONEX-WHEELS-0.3.0-WORKSPACE-1 | snapshot | 6 products
```

На сайте: «Реальная выборка · 6 товаров». В /build-info.json: release=IMKONEX-WHEELS-0.3.0-WORKSPACE-1, products=6, mode=snapshot. На прежнем сервисе URL был https://imkonex-wheels-demo.onrender.com/ ; для нового сервиса используйте выданный ему адрес.

## Заголовки и фотографии

Обновление PHOTOS-1 добавляет шесть оригинальных фотографий поставщика в frontend/assets/products/. Сборка копирует их в dist/assets/products/; браузер загружает фото с домена самого каталога. Для исправления фотографий настройки Render менять не требуется. В журнале появится Photos: 6 local, 0 remote, 0 unavailable., а в /build-info.json — photos.local: 6.

Ссылки img_big_my из исходного отчёта при проверке 21.09.2026 возвращали HTTP 200 с пустым телом. Публичные ссылки img_big_pish из того же отчёта вернули шесть изображений 400×400. Они сохранены без изменения байтов. Источники и SHA256: PRODUCT_PHOTO_SOURCES.json.

В render.yaml уже указана политика, разрешающая собственные ресурсы и внешние фото поставщика. Для вручную настроенного Static Site простая загрузка YAML не применяет заголовки автоматически. Если настраиваете Content-Security-Policy отдельно, используйте один заголовок для пути /*:

```text
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://api-b2b.pwrs.ru https://www.4tochki.ru; connect-src 'self' https://*.onrender.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
```

При ошибке загрузки фото используется нейтральная заглушка. [Заголовки статических сайтов](https://render.com/docs/static-site-headers).

## Что хранить в репозитории

Загрузите файлы из полного архива, сохранив пути. Не помещайте в GitHub dist, node_modules, .venv, .check-venv, .env, приватные API-отчёты, бэкапы или .git. Файл .env.example — безопасный шаблон, он входит в архив.

.github/workflows/ci.yml запускает дополнительные проверки. Для самой команды Render он не обязателен. backend/, Dockerfile, compose.yaml и render-api.yaml сохранены для серверного этапа; текущий Static Site их не запускает.

Публикация в аккаунтах GitHub и Render из этой сессии не выполнялась. В пакет включён снимок 6 товаров на 17.09.2026; автообновление и оформление заказа требуют следующего этапа интеграции.
