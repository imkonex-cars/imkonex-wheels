# IMKONEX CARS · полная рабочая область каталога шин и дисков

Версия приложения 0.3.0 · пакет WORKSPACE-1 · подготовлен 21.09.2026.

Этот архив содержит все исходники обычными файлами и папками. Он предназначен для создания рабочего проекта и нового GitHub-репозитория. Вложенного SOURCE_CODE.zip нет. Загрузите содержимое архива с сохранением структуры папок: BUILD_RENDER.mjs, package.json и render.yaml должны находиться в корне репозитория.

Витрина содержит проверенную выборку «4точки»: 3 шины и 3 диска. Характеристики, розничные цены и остатки получены 17.09.2026, 19:23 UTC. Автообновление, весь ассортимент, оформление заказа и оплата пока не подключены. Закупочные цены и пароль API не входят в публичные данные. Наценка IMKONEX не применяется.

## Файлы в корне GitHub

| Файл | Назначение |
|---|---|
| BUILD_RENDER.mjs | Основной вход сборки из исходников |
| package.json | Команды проекта и зависимости для тестов |
| package-lock.json | Зафиксированные версии npm-зависимостей |
| render.yaml | Конфигурация бесплатной статической витрины |
| README.md | Эта инструкция |
| START_HERE_PC1.txt | Короткие шаги для Windows |
| FILES_MANIFEST.txt | Полный список всех файлов архива |
| .gitignore | Исключает локальные зависимости, отчёты и секреты |
| .env.example | Шаблон настроек для будущего серверного этапа |
| requirements.txt, requirements-dev.txt | Зависимости Python для серверного кода, диагностики и тестов |
| Dockerfile, compose.yaml, render-api.yaml | Конфигурации серверной разработки; Static Site их не запускает |

## Папки в корне GitHub

| Папка | Содержимое |
|---|---|
| frontend/ | index.html, app.js, domain.js, styles.css, config.js, data/catalog.js, изображения в assets/ |
| data/ | supplier-snapshot.json с 6 реальными товарами и demo-catalog.json с прежними тестовыми данными |
| scripts/ | build.mjs, public-catalog.mjs, упаковка, локальные проверки и вспомогательные команды |
| backend/ | Диагностика API, экспорт публичного снимка, прежний демонстрационный API и заготовки PostgreSQL |
| tests/ | Тесты JavaScript и Python, тестовые входные данные в fixtures/ |
| docs/ | Инструкции по Render, API, проверкам и дальнейшему серверному этапу |
| deploy/ | Caddyfile для отдельного сервера |
| .github/workflows/ | ci.yml для автоматических проверок GitHub Actions |

Полный перечень без сокращений находится в FILES_MANIFEST.txt. .github — обычная папка проекта, .git — служебная папка Git; в архив .git не включается.

## Загрузка в GitHub

1. Распакуйте IMKONEX_WHEELS_WORKSPACE_v0.3.0.zip в пустую локальную папку.
2. Для чистого проекта создайте отдельный репозиторий. Если обновляете существующий, сначала сохраните его через Code → Download ZIP.
3. На странице репозитория выберите Add file → Upload files. Перетащите все файлы и папки из распакованного каталога, сохранив вложенные пути. Не загружайте сам ZIP вместо файлов и не помещайте весь проект в лишнюю внешнюю папку.
4. Сохраните изменения через Commit changes. Убедитесь, что package.json и BUILD_RENDER.mjs видны в корне, а frontend/index.html находится именно в frontend/.
5. Если .github не удалось загрузить через браузер, создайте в GitHub файл через Add file → Create new file с именем .github/workflows/ci.yml и скопируйте в него содержимое одноимённого файла архива. Публикация Render работает и без GitHub Actions; папка нужна для автопроверок полного проекта.

При желании можно перенести папки через GitHub Desktop, сохранив все пути и файлы с точкой в начале имени. [Инструкция загрузки](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

## Render — создать заново или переподключить

Для новой рабочей области создайте New → Static Site и подключите репозиторий и ветку с этим проектом. Если меняете репозиторий у существующего сервиса, проверьте поля Repository и Branch.

| Поле | Значение |
|---|---|
| Тип | Static Site |
| Root Directory | Оставить пустым |
| Build Command | node BUILD_RENDER.mjs |
| Publish Directory | dist |
| Environment → NODE_VERSION | 22 |
| Environment → CATALOG_MODE | snapshot |

npm run build также вызывает BUILD_RENDER.mjs. Этот вход фиксирует snapshot даже при старой переменной CATALOG_MODE=demo. PUBLIC_API_BASE очищается при этой сборке: живой сервер API ещё не подключён. Для публикации данной выборки пароль API не требуется.

После изменения настроек существующего сервиса используйте Manual Deploy → Clear build cache & deploy. У нового сервиса адрес выдаст Render после создания; прежний адрес сохраняется только у прежнего сервиса. [Static Sites](https://render.com/docs/static-sites), [публикация Render](https://render.com/docs/deploys).

Ожидаемый конец журнала:

```text
Built dist: 6 products. Mode: snapshot. No client credentials.
BUILD_OK IMKONEX-WHEELS-0.3.0-WORKSPACE-1 | snapshot | 6 products
```

После публикации откройте адрес Render и нажмите Ctrl+F5. Вверху появится «Реальная выборка · 6 товаров». По адресу /build-info.json должны быть release=IMKONEX-WHEELS-0.3.0-WORKSPACE-1, mode=snapshot, products=6.

Главный HTML исходников — frontend/index.html. Публикуемый HTML — dist/index.html, он создаётся сборкой. Для новой рабочей области используйте только содержимое текущего архива, включая новый BUILD_RENDER.mjs. Корневой index.html и SOURCE_CODE.zip от предыдущего пакета из восьми файлов здесь не нужны.

## Проверить локально

Нужен Node.js 22+. Из корня проекта:

```text
node BUILD_RENDER.mjs
```

Для просмотра на Windows с установленным Python 3.12:

```text
py -3.12 -m http.server 4173 --bind 127.0.0.1 --directory dist
```

Откройте http://localhost:4173 . Можно также после сборки выполнить node scripts/standalone.mjs и открыть созданный artifacts/IMKONEX_WHEELS_REAL_DATA.html. Сам frontend/index.html — исходник приложения с ES-модулями; для проверки используйте сервер или созданный самостоятельный HTML.

## Проверки и упаковка

```text
npm ci
npm run check
npm test
python scripts/package-workspace.py
```

Последняя команда создаёт полный архив в artifacts/. Прежний scripts/package-github.py сохраняет возможность сделать отдельный пакет из 8 файлов; его описание находится в docs/GITHUB_FULL_README.md и не относится к текущей рабочей области.

Для Python-тестов: pip install -r requirements-dev.txt, затем python -m pytest tests/test_api.py tests/test_export_snapshot.py -q. Результаты проверки рабочего пакета — docs/VALIDATION_WORKSPACE.md.

## Данные, изображения и серверный код

Для обновления снимка используйте backend.export_snapshot с новым успешным отчётом версии 1.3, затем выполните сборку. Исходный отчёт храните вне репозитория: он содержит приватные коммерческие данные. В витрину экспортируется розничная price_rozn поставщика; поле price в публичном JSON содержит эту розничную цену.

Проверки по текущему снимку: 4 Pirelli 2398000 — 132 200 ₽ со склада Домодедово (Кучино); 4 Venti WHS121894 — 30 640 ₽ со склада Уфа 2. Доставка не включена; остатки разных складов не суммируются. Сроки уточняются.

Фотографии загружаются по ссылкам поставщика. Их доступность в этой среде не подтверждена; предусмотрена заглушка. Для существующего сервиса настройка Content-Security-Policy приведена в docs/RENDER.md.

backend/, Docker и render-api.yaml сохранены как исходники следующего серверного этапа. Они не обслуживают реальную статическую выборку. Серверные модели демонстрационной схемы 1 не подходят для прямого импорта публичного снимка схемы 2. Для прежней демовитрины разработчик может напрямую вызвать scripts/build.mjs с CATALOG_MODE=demo; штатная команда публикации всегда собирает snapshot.

dist/, node_modules/, .venv/, .check-venv/, .env, приватные отчёты, бэкапы и .git/ не загружаются на GitHub. Рабочая область воспроизводит исходники проекта, но не содержит учётных данных, настроек вашего аккаунта Render или сохранённых в браузере расчётов.
