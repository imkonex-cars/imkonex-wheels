# Render: рабочая область 0.4.0

Используется существующий Static Site imkonex-wheels-demo, репозиторий imkonex-cars/imkonex-wheels, ветка main. Главный HTML исходников — frontend/index.html; публикуемый — dist/index.html.

| Поле | Значение |
|---|---|
| Root Directory | пусто |
| Build Command | node BUILD_RENDER.mjs |
| Publish Directory | dist |
| NODE_VERSION | 22 |
| CATALOG_MODE | snapshot |

BUILD_RENDER.mjs фиксирует snapshot и собирает data/supplier-snapshot.json; старое CATALOG_MODE=demo не возвращает демонстрационные товары. Статическая сборка не обращается к API поставщика. API-пароль хранится в GitHub Secrets для отдельного workflow, а не в Render.

Первичная установка даст BUILD_OK IMKONEX-WHEELS-0.4.0-WORKSPACE-1 | snapshot | 6 products. Это исходная проверочная выборка с фотографиями. После первого успешного Sync 4tochki catalog число товаров будет определяться API; в /build-info.json появятся dataScope=account_catalog и sync.complete=true.

Для публикации коммитов workflow используйте Auto-Deploy → On Commit. Альтернативный явный запуск — секрет RENDER_DEPLOY_HOOK_URL в GitHub; в таком варианте Auto-Deploy Render переключается в Off, чтобы не делать две сборки на один коммит. Подключение, первый запуск и расписание: [SYNC_SETUP.md](SYNC_SETUP.md).

Статус Actions и принятие Deploy Hook не заменяют проверку успешной сборки Render. После публикации откройте https://imkonex-wheels-demo.onrender.com/build-info.json и проверьте version, dataUpdatedAt, dataScope и products. Обновите витрину Ctrl+F5.

## Фото и заголовки

Исходные шесть фотографий хранятся в frontend/assets/products/ и копируются в dist. Массовый импорт добавляет ограниченный кеш оригинальных фото; оставшиеся изображения используют публичные адреса www.4tochki.ru. Поэтому, если Content-Security-Policy задан вручную, в img-src должны быть разрешены собственный домен и https://www.4tochki.ru. Текущий render.yaml уже содержит это разрешение. Простая загрузка YAML не применяет заголовки к вручную созданному сервису автоматически.

При недоступности фото показывается нейтральная заглушка. Сами оригиналы не генерируются и не изменяются.

Документация: [Static Sites](https://render.com/docs/static-sites), [Deploy Hooks](https://render.com/docs/deploy-hooks), [HTTP Headers](https://render.com/docs/static-site-headers).
