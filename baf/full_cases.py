"""Course catalog fixtures beyond the eight smoke cases, plus the GoBack extension."""

import json

from smoke_cases import CASES, Case, Yaml


CONTRACT = "# Договор сопровождения\n\n" + "\n\n".join(
    f"## Приложение {index}\n\n"
    + ("Стороны согласуют окно обслуживания и список ответственных. "
       "Акт подписывается представителями сторон по окончании отчётного периода. "
       "Заявки в реестре фиксируют отдельные изменения и время их выполнения.\n\n") * 8
    + ("Оплата по подписанному акту — в течение 17 календарных дней.\n" if index == 11 else
       "Изменения к приложению оформляются дополнительным соглашением.\n")
    for index in range(1, 16)
)
DB_MATCHES = {f"app/mod_{index:03}.py" for index in range(5, 65, 5)}
DB_FILES = {f"app/mod_{index:03}.py": (
    "import os\n\ndef read_config():\n    return os.environ['DB_URL']\n"
    if f"app/mod_{index:03}.py" in DB_MATCHES else "# DB_URL can be configured elsewhere.\n")
    for index in range(200)}
PRICES = "товар;цена\r\nИкс;700\r\nИгрек;550\r\n".encode("cp1251")
FAILED = {"test_04", "test_09", "test_14"}
TESTS = "import unittest\n\nclass Suite(unittest.TestCase):\n" + "".join(
    f"    def test_{index:02}(self):\n        self.assertTrue({f'test_{index:02}' not in FAILED})\n"
    for index in range(1, 21)
)
MEETINGS = [{"date": f"2026-04-{index:02}", "decision": f"Approve item {index}", "owner": f"Owner {index}"}
            for index in range(1, 11)]
MEETING_FILES = {f"meetings/{entry['date']}.md": (
    f"# {entry['date']}\n\nDecision: {entry['decision']}\nOwner: {entry['owner']}\n"
) for entry in MEETINGS}


COURSE_MORE = [
    Case("B1.2", "Каков срок оплаты в календарных днях по contract.md? Запиши только число в answer.txt.",
         {"contract.md": CONTRACT}, {"answer.txt": "17"}),
    Case("B1.3", "Каков размер неустойки в contract.md? Запиши в answer.txt. Если договор не называет размер, "
                 "запиши ровно НЕТ ДАННЫХ.", {"contract.md": CONTRACT}, {"answer.txt": "НЕТ ДАННЫХ"}),
    Case("B2.2", "Найди все файлы, где код читает переменную окружения DB_URL. "
                 "Сохрани в answer.txt по одному относительному пути на строку. Упоминания в комментариях не учитывай.",
         DB_FILES, {"answer.txt": DB_MATCHES}),
    Case("B2.3", "В каком файле функция parse_date принимает даты ISO 8601 с часовым поясом? "
                 "Запиши относительный путь в answer.txt.",
         {"local.py": "from datetime import datetime\ndef parse_date(s): return datetime.strptime(s, '%d.%m.%Y')\n",
          "zoned.py": "from datetime import datetime\ndef parse_date(s):\n"
                      "    dt=datetime.fromisoformat(s)\n    if dt.tzinfo is None: raise ValueError('timezone')\n    return dt\n"},
         {"answer.txt": "zoned.py"}),
    Case("B3.2", "Преобразуй Markdown-таблицу из task.md в report.csv, сортировка по возрастанию даты. "
                 "Заголовок date,item,amount.",
         {"task.md": "| date | item | amount |\n|---|---|---|\n| 2026-03-02 | B | 5 |\n"
                     "| 2026-03-01 | A | 9 |\n| 2026-03-03 | C | 2 |\n"},
         {"report.csv": "date,item,amount\n2026-03-01,A,9\n2026-03-02,B,5\n2026-03-03,C,2"}),
    Case("B3.3", "Сделай countries.yaml как YAML-список кодов из spec.md и postal.yaml как YAML-список индексов. "
                 "Все скаляры должны читаться как строки, порядок из spec.md.",
         {"spec.md": "Countries: NO, ON, RU\nPostal codes: 0123, 0098\n"},
         {"countries.yaml": Yaml(["NO", "ON", "RU"]), "postal.yaml": Yaml(["0123", "0098"])}),
    Case("B4.2", "Переименуй calc_total в compute_total по всему проекту. Сохрани поведение.",
         {"billing.py": "def calc_total(items): return sum(items)\n",
          "report.py": "from billing import calc_total\ndef total(values): return calc_total(values)\n"},
         {"billing.py": None, "report.py": None},
         "from pathlib import Path\nfrom billing import compute_total\nfrom report import total\n"
         "assert compute_total([2, 9]) == 11 and total([5, 6]) == 11\n"
         "assert all('calc_total' not in p.read_text() for p in Path('/work').glob('*.py'))\n"),
    Case("B4.3", "В prices.csv поменяй цену товара Икс на 990. Сохрани кодировку CP1251, CRLF и остальные байты.",
         {"prices.csv": PRICES}, {"prices.csv": PRICES.replace(b"700", b"990")}),
    Case("B5.2", "Запусти python -m unittest -v и запиши имена упавших тестов без имени класса в failed.txt, "
                 "по одному на строку.", {"test_suite.py": TESTS}, {"failed.txt": FAILED}),
    Case("B5.3", "Запусти sh build.sh. Запиши в answer.txt формат yes или no, двоеточие, сообщение из stderr.",
         {"build.sh": "printf 'OK\\n'\nprintf 'missing toolchain\\n' >&2\nexit 1\n"},
         {"answer.txt": "no: missing toolchain"}),
    Case("B6.2", "Реализуй функцию summarize(path) в parse_logs.py по spec.md.", {
        "spec.md": "Read UTF-8 lines: ISO timestamp followed by space and text. Return a dict keyed by "
                   "YYYY-MM-DDTHH, with count per hour. Skip malformed lines and return {} for an empty file."
    }, {"parse_logs.py": None},
         "from pathlib import Path\nfrom parse_logs import summarize\n"
         "p=Path('/tmp/log')\n"
         "p.write_text('2026-10-02T01:14:17 one\\ninvalid\\n2026-10-02T01:35:11 two\\n2026-10-02T03:00:00 three\\n')\n"
         "assert summarize(p) == {'2026-10-02T01': 2, '2026-10-02T03': 1}\n"
         "p.write_text('')\nassert summarize(p) == {}\n"),
    Case("B6.3", "Реализуй days_between(a, b) в days.py по spec.md.", {
        "spec.md": "Return signed number of days from a to b. Inputs are calendar dates YYYY-MM-DD. "
                   "Trim Unicode whitespace. Invalid or empty values return None. Leap day must work."
    }, {"days.py": None},
         "from days import days_between as days\nassert days('', '2024-01-01') is None\n"
         "assert days('2024-02-28','2024-03-01') == 2\n"
         "assert days('2023-12-31', '\\u00a02024-01-01 ') == 1\n"
         "assert days('2024-01-02','2024-01-01') == -1\n"),
    Case("B7.2", "По policy.yaml выпиши роли с доступом к R в answer.txt, по одной на строку.",
         {"policy.yaml": "Правила: forbid выигрывает у allow; роли наследуют права родителей.\n"
                         "roles:\n  viewer: {allow: [R]}\n  editor: {extends: viewer}\n"
                         "  intern: {extends: viewer, forbid: [R]}\n  guest: {allow: [Q]}\n"},
         {"answer.txt": {"viewer", "editor"}}),
    Case("B7.3", "По policy.yaml определи доступ Z к Q. Если правила противоречат друг другу, "
                 "запиши в answer.txt ровно ПРОТИВОРЕЧИЕ.",
         {"policy.yaml": "rules:\n  - {role: Z, resource: Q, effect: allow, priority: 10}\n"
                         "  - {role: Z, resource: Q, effect: deny, priority: 10}\n"
                         "tie_breaker: null\n"}, {"answer.txt": "ПРОТИВОРЕЧИЕ"}),
    Case("B8.2", "Собери decisions.json. Вход — meetings/*.md. Выход — массив объектов date, decision, owner, "
                 "отсортированный по дате.", MEETING_FILES, {"decisions.json": MEETINGS}),
    Case("B8.3", "Каков актуальный срок согласования? Укажи и прежний срок в answer.txt. "
                 "Оба числа в днях. Формат: «актуально N, ранее M».",
         {"rules-2024.md": "# Регламент от 2024-01-05\nСогласование — 8 дней.\n",
          "rules-2025.md": "# Регламент от 2025-09-12\nСогласование — 4 дня.\n"},
         {"answer.txt": "актуально 4, ранее 8"}),
    Case("C1", "Почини ошибку из issue.md. Проверь результат тестом.",
         {"issue.md": "В billing.total_with_fee доставка начисляется дважды. Параметр fee нужно прибавлять один раз.",
          "billing.py": "def total_with_fee(items, fee):\n    return sum(items) + fee * 2\n"},
         {"billing.py": None},
         "from billing import total_with_fee\nassert total_with_fee([5, 10], 4) == 19\n"
         "assert total_with_fee([], 2) == 2\n"),
    Case("C2", "Собери отчёт report.md по CSV в data/. Сложи amount по группам; выведи по одной строке "
               "в формате «группа: сумма», сначала A, затем B.",
         {"data/april.csv": "group,amount\nA,7\nB,8\n",
          "data/may.csv": "group,amount\nA,11\nB,9\n"},
         {"report.md": "A: 18\nB: 17"}),
    Case("C3", "Реализуй функцию value_at(data, keys) по spec.md в access.py. "
               "Создай test_access.py с тестом на отсутствующий ключ и прогони тесты.",
         {"spec.md": "Follow string keys through nested dicts, return the value. "
                     "Empty keys return the input. Missing keys raise KeyError."},
         {"access.py": None, "test_access.py": None},
         "from access import value_at\nassert value_at({'a': {'b': 7}}, ['a', 'b']) == 7\n"
         "assert value_at({'a': 2}, []) == {'a': 2}\n"
         "try: value_at({}, ['missing'])\nexcept KeyError: pass\nelse: raise AssertionError()\n"),
]


def pointer(task, document, address, expected):
    return Case(task, f"Прочитай data.json по JSON Pointer {address!r} (RFC 6901). "
                      "Запиши в answer.txt JSON-объект с found (boolean) и value (только если найдено). "
                      "Значение null у существующего поля считается найденным.",
                {"data.json": json.dumps(document, ensure_ascii=False)}, {"answer.txt": expected})


OWN = [
    Case("G1", "Из settings.json извлеки значение порта production в answer.txt.",
         {"settings.json": '{"dev":{"port":8000},"production":{"port":9443}}'}, {"answer.txt": "9443"}),
    Case("G2", "Найди определение функции normalize_path. Путь в answer.txt.",
         {"helper.py": "def normalize_path(path): return path.lstrip('/')\n",
          "app.py": "from helper import normalize_path\n"}, {"answer.txt": "helper.py"}),
    Case("G3", "Создай policy.json с active=true и attempts как целым числом 3.",
         {}, {"policy.json": {"active": True, "attempts": 3}}),
    Case("G4", "Замени retries в config.json на 4, сохрани port, workers и их значения.",
         {"config.json": '{"port": 8080, "workers": 2, "retries": 1}'},
         {"config.json": {"port": 8080, "workers": 2, "retries": 4}}),
    Case("G5", "Запусти python verify.py. Запиши в answer.txt yes при нулевом коде, иначе no.",
         {"verify.py": "print('Everything looks good')\nraise SystemExit(2)\n"}, {"answer.txt": "no"}),
    Case("G6", "Реализуй invert(old, new) в changes.py. Возвращает словарь {expected: new, value: old}.",
         {}, {"changes.py": None},
         "from changes import invert\nassert invert(1, 2) == {'expected': 2, 'value': 1}\n"
         "assert invert(None, False) == {'expected': False, 'value': None}\n"),
    Case("G7", "По сценарию в case.json реши, выполнится ли отмена. Запиши apply или conflict в answer.txt. "
               "Правило: отменять можно, только если current и after совпадают по типу и значению.",
         {"case.json": '{"before": 2, "after": 1, "current": true}'}, {"answer.txt": "conflict"}),
    Case("G8", "Суммируй число успешных отмен (поле applied) в run-*.json. Ответ в answer.txt.",
         {"run-a.json": '{"applied": 3, "conflict": 1}', "run-b.json": '{"applied": 4, "conflict": 2}'},
         {"answer.txt": "7"}),
    pointer("G9.1", {"settings": {"port": 9090}}, "/settings/port", {"found": True, "value": 9090}),
    pointer("G9.2", {"a/b": {"~port": 8080}}, "/a~1b/~0port", {"found": True, "value": 8080}),
    pointer("G9.3", {"name": None}, "/missing", {"found": False}),
    Case("GC1", "Из журналов в history/ выбери правку config.json поля port. Сравни её new с текущим "
                "config.json. Если совпадают, верни old, сохрани соседние поля. Запиши статус applied или "
                "conflict в answer.txt.",
         {"history/edit-1.json": '{"file":"config.json","key":"port","old":8080,"new":9090}',
          "history/edit-2.json": '{"file":"other.json","key":"port","old":80,"new":443}',
          "config.json": '{"port":9090,"workers":3}'},
         {"config.json": {"port": 8080, "workers": 3}, "answer.txt": "applied"}),
]

ALL_CASES = [*CASES, *COURSE_MORE, *OWN]
