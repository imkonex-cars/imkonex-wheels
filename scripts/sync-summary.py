"""Write only a whitelisted import summary to the GitHub Actions run."""
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
path = root / 'runtime/sync-summary.json'
if path.exists():
    report = json.loads(path.read_text())
    # The report is produced by our importer, not an arbitrary supplier response.
    status = report.get('status')
    count = report.get('products', 0)
    rows = ['## IMKONEX CARS · выгрузка каталога', '',
            f'Статус выгрузки: {status}', f'Подготовлено товаров: {count}',
            f'Запросов API: {report.get("apiCalls", 0)}', '',
            'Снимок публикуется в GitHub. Обновление каталога на REG.ru '
            'проверяется отдельно командой wheels status; Render не вызывается.']
    if status == 'failed':
        rows += ['', 'Рабочий снимок сохранён. Код: ' + report.get('error', 'unknown_error')]
    for kind, data in report.get('sync', {}).get('categories', {}).items():
        rows.append(f'{kind}: страниц {data["pages"]}, артикулов {data["scanned"]}, '
                    f'опубликовано в снимке {data["published"]}, исключено {data["excluded"]}.')
else:
    rows = ['Выгрузка не завершилась. Проверьте первый шаг с ошибкой в журнале Actions.']
result = '\n\n'.join(rows) + '\n'
print(result)
if os.getenv('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
        output.write(result)
