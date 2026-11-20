"""One reproducible fixture for each course BAF."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    files: dict
    outputs: dict
    code_check: str | None = None


@dataclass(frozen=True)
class Yaml:
    value: object


PORT_FIXTURE = Path(__file__).parent / "B1-read/B1.1-service-port/fixture"
PORT_FILES = {p.relative_to(PORT_FIXTURE).as_posix(): p.read_bytes()
              for p in PORT_FIXTURE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
README_BEFORE = "# Tool\n\n## Install\n\n    pip install goback==1.2\n\n## Compatibility\n\nKeep 1.2 for Python 3.10.\n"
SLUG_CHECK = (
    "from slug import slugify\n"
    "assert slugify(' Test__Title! ') == 'test-title'\n"
    "assert slugify('a-b 42') == 'a-b-42'\n"
    "assert slugify('') == ''\n"
    "assert slugify('!!!') == ''\n"
)

CASES = [
    Case("B1.1", "На каком порту слушает сервис? Запиши только число в answer.txt.",
         PORT_FILES, {"answer.txt": "8137"}),
    Case("B2.1", "В каком файле определена функция parse_date? Запиши относительный путь в answer.txt.",
         {"src/dates.py": "def parse_date(text):\n    return tuple(map(int,text.split('-')))\n",
          "src/main.py": "from dates import parse_date\n"},
         {"answer.txt": "src/dates.py"}),
    Case("B3.1", "Создай config.json по файлу spec.md.",
         {"spec.md": "JSON object: service='metrics', port=9120 (integer), debug=false (boolean)."},
         {"config.json": {"service": "metrics", "port": 9120, "debug": False}}),
    Case("B4.1", "В разделе Install файла README.md обнови версию goback до 1.3. Остальное сохрани побайтно.",
         {"README.md": README_BEFORE}, {"README.md": README_BEFORE.replace("goback==1.2", "goback==1.3")}),
    Case("B5.1", "Запусти python stats.py data.csv. Запиши показанную скриптом медиану в answer.txt.",
         {"stats.py": "import csv, statistics, sys\nwith open(sys.argv[1]) as f:\n"
                      "    print(statistics.median(int(row['amount']) for row in csv.DictReader(f)))\n",
          "data.csv": "amount\n11\n3\n7\n"}, {"answer.txt": "7"}),
    Case("B6.1", "Напиши slugify(text) в slug.py по spec.md.",
         {"spec.md": "ASCII slug: lowercase, consecutive non-alphanumeric characters become a single hyphen, "
                     "strip leading and trailing hyphens. Empty input returns the empty string."},
         {"slug.py": None}, SLUG_CHECK),
    Case("B7.1", "По schedule.csv реши, успеет ли пассажир на пересадку при минимуме 12 минут. "
                 "Ответ в answer.txt строго в формате «yes N minutes» или «no N minutes».",
         {"schedule.csv": "service,event,station,time\nA,arrive,Y,13:52\nB,depart,Y,14:06\n"},
         {"answer.txt": "yes 14 minutes"}),
    Case("B8.1", "Посчитай сумму продаж в файлах sales/*.csv и запиши только число в answer.txt.",
         {f"sales/week{index}.csv": f"amount\n{amount}\n{extra}\n"
          for index, (amount, extra) in enumerate([(10, 25), (12, 18), (26, 9), (15, 20), (22, 8)], 1)},
         {"answer.txt": "165"}),
]
