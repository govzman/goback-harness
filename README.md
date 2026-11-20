# GoBack Harness

Среда исполнения coding-агента для задания A курса ИТМО × DeepPavlov. Клиент Electron подключает выбранную папку к Docker-сандбоксу как `/work` через SSHFS и обратный SSH-туннель. Собственный Python-харнес исполняет инструменты через менеджер сандбоксов. GoBack отменяет конкретную правку поля JSON, сохраняя изменения в соседних полях. При конкурирующей правке того же поля GoBack сообщает о конфликте. [Общие условия курса](https://github.com/deeppavlov/agentic-course-itmo/blob/main/projects/README.md) · [Задание A](https://github.com/deeppavlov/agentic-course-itmo/blob/main/projects/A-agent-runtime.md)

## Модель и статус ЧП2

**Измеренная пара — GoBack + OpenAI GPT‑6.1 Terra** (`gpt-6.1-terra`, `reasoning.effort=low`). Для живого прогона использовали Responses API через Sign in with ChatGPT. Модель и effort фиксировались на весь прогон и сетевые повторы. По умолчанию Compose запускает режим `fixture` со сценарными ответами для демо и CI. [Параметры GPT‑6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)

**Artificial Analysis Intelligence Index — 29** на 20 ноября 2026 года. У DeepSeek V4.1 Flash 1017 индекс **29** на `high` и **33** на `max`. Правила курса не уточняют effort эталона. Допуск Terra `low` зависит от выбранной эталонной конфигурации. [Сравнение Artificial Analysis](https://artificialanalysis.ai/models/comparisons/gpt-6-1-terra-vs-deepseek-v4-1-flash-1017)

В промежуточном прогоне с одним повтором выполнено **34/39** задач, с учётом четырёх повторов после сетевых обрывов — **38/39**. B2.2 исчерпала лимит шагов. Проверки клиента, инфраструктуры и smoke-БАФ запущены в CI. Живое монтирование показано на Windows ↔ Ubuntu WSL2. Для допуска к защите остаются подтверждение стенда по A‑2.3, недостающее покрытие изоляции и протоколов по A‑5, A‑6.4 и A‑8, а также минимум две пары по A‑11. Полный эксперимент требует трёх пар. [Отчёт и исходные результаты](docs/report.md) · [Сверка условий 0‑1–0‑9 и A‑1–A‑12](docs/requirements.md) · [CI](https://github.com/govzman/goback-harness/actions)

## Сервер

На Linux нужны Docker Engine, Compose, `/dev/fuse` и shared propagation для каталога монтирования `/run/goback-harness` либо его родительского mountpoint. Проверь хост перед запуском из корня клона.

```sh
test -c /dev/fuse
findmnt -T /run -no TARGET,PROPAGATION
```

Если хосту нужна подготовка, на Ubuntu можно повторить настройку из CI. Команды требуют прав на загрузку FUSE и настройку выделенного mountpoint.

```sh
sudo modprobe fuse
sudo mkdir -p /run/goback-harness
mountpoint -q /run/goback-harness || sudo mount --bind /run/goback-harness /run/goback-harness
sudo mount --make-rshared /run/goback-harness
```

```sh
docker compose up --build
```

Проверь gateway из другого терминала командой `curl -fsS http://127.0.0.1:8080/health`. Для остановки стека и удаления тома трасс используй команду ниже. Если при запуске передавались оверлеи через `-f`, укажи их и при остановке.

```sh
docker compose down -v
```

Первая команда работает в отдельном терминале до окончания демо. В WSL2 это удерживает дистрибутив запущенным. Если сборка контейнеров в WSL2 не достаёт до репозиториев пакетов, используй `docker compose -f docker-compose.yml -f compose.wsl.yml up --build`. Оверлей меняет сеть на этапе сборки.

Сервер по умолчанию слушает loopback на портах `8080` (gateway) и `2222` (SSH) и использует клиентский токен `goback-local-demo`. Настройки можно задать в игнорируемом Git локальном `.env` по [образцу](.env.example). Для отдельной машины укажи в `GOBACK_BIND` адрес сервера в доверенной сети и задай собственный `CLIENT_TOKENS`. В клиенте используй адрес этой машины. При работе вне доверенной сети защити HTTP-канал TLS.

## Клиент и демо

Клиенту нужен Python 3.12, для Electron также нужны Node.js и npm. Из корня репозитория в активированном виртуальном окружении выполни следующие команды.

```sh
python -m pip install -r client/requirements.lock
npm ci --prefix client/desktop
npm start --prefix client/desktop
```

Введи адрес и токен, выбери папку и открой сессию. Режим `ro` предназначен для чтения и анализа, `rw` — для редактирования и отмены правок. Десктоп-клиент показывает поток чата, позволяет остановить запуск и дочитывает события с последнего номера после обрыва SSE. Если потерян SSH-туннель к папке, открой новую сессию. [Подробности запуска Electron и CLI](client/README.md)

Создай отдельную папку `demo-workdir` и положи в неё `config.json` с содержимым `{"port": 8080, "workers": 2}`. С запущенным сервером в режиме `fixture` CLI попросит агента изменить порт, затем сделает тестовую правку со стороны клиента, вызовет отмену и проверит итоговый файл.

```sh
python client/cli.py run --folder ./demo-workdir --mode rw --message "Change the port in config.json to 9090" --demo neighbor
```

Должно получиться `{"port": 8080, "workers": 3}`. Для второго прогона восстанови исходный JSON и укажи `--demo conflict`. На этот раз пользовательская правка `port: 7070` должна сохраниться.

## Запуск с GPT‑6.1 Terra low

Установи `LLM_MODEL=gpt-6.1-terra` и `LLM_REASONING_EFFORT=low` в локальном `.env` или используй значения по умолчанию. На проверенном стенде вход выполнялся через подписку. В Linux или WSL2 с Python и пакетом `cryptography` запусти:

```sh
python3 services/llm-proxy/chatgpt_login.py
```

Открой выведенную ссылку в браузере на том же ПК и подтверди разрешение. Сверь модель с выведенным каталогом, затем запусти сервер. Профиль сохраняется в `~/.config/goback-harness` на хосте, доступ к нему получает только прокси. [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source)

```sh
docker compose -f docker-compose.yml -f compose.chatgpt.yml up --build
```

Для обхода проблем сети Docker в WSL2 задай в локальном `.env` случайный `GOBACK_PROXY_TOKEN` и запусти с обоими WSL-оверлеями. Прокси слушает адрес Docker-хоста `172.17.0.1:18083` и проверяет этот токен. Сеть агента остаётся выключенной.

```sh
docker compose -f docker-compose.yml -f compose.wsl.yml -f compose.chatgpt.yml -f compose.wsl-llm.yml up --build
```

При подключении через OpenAI API-ключ задай в `.env` `LLM_MODE=api` и `LLM_API_KEY` и запусти базовый Compose. Ключ получает только LLM proxy.

## Устройство

```text
клиент ── HTTP и события ──► gateway ──► harness ──► LLM proxy
папка ── SFTP через SSH ─────► sandbox manager ─────► контейнер /work
                                       ▲
                                       └──── вызовы инструментов от harness
```

Отмена доступна в рамках открытой сессии для правок существующих скалярных полей JSON через специальный инструмент. Учитываются значения полей и версия файла при сохранении. Пользователь должен сохранить свою правку перед отменой. Редактор может записать файл в узком интервале между проверкой и атомарной заменой, поскольку не участвует в общем блокировании. Перезапуск сервисов завершает сессии.

Сандбокс работает с UID 65532, read-only root, сброшенными capabilities и отключённой сетью. Установлены лимиты CPU, RAM, числа процессов, временного диска и времени команд. SFTP-сервис клиента ограничивает доступ выбранной папкой и скрывает `.git` и ссылки, а служебное состояние хранится вне `/work`. [Описание харнесса](docs/harness.md) · [Контракты v0](contracts/README.md) · [Архитектурные решения](docs/adr)

Метаданные операций, длительности, исходы и токены записываются в JSONL-трейсы сессий. Список трасс можно получить командой `docker compose exec -T harness ls /var/lib/goback/traces`.

## Проверки и воспроизводимость

Из чистого клона можно пересчитать сводку отчёта по опубликованным JSONL и отдельным отсчётам. Скрипту нужен только Python.

```sh
python results/summarize.py
```

На запущенном сервере в режиме `fixture` выполни команды ниже. `make test-ci` запускает сценарии клиента, инфраструктуру и smoke-БАФ. В CI внешние реестры используются для сборки и установки зависимостей. Во время тестов запросов к API модели нет. Для замеров ФС на медленном стенде можно задать в окружении сервера `SANDBOX_EXEC_TIMEOUT_S=32`. [Покрытие инфраструктурных тестов](tests/infra/README.md)

```sh
make test-ci PYTHON=python
python baf/run.py --smoke --pair goback+fixture --out .local-runtime/repro/fixture-smoke.jsonl
python tests/infra/compare_undo.py --out .local-runtime/repro/undo-comparison.jsonl
python tests/infra/bench_env.py --samples 10 --out .local-runtime/repro/wsl-environment.json
```

На сервере с живой Terra `low` первая команда повторяет промежуточный эксперимент. Вторая повторяет четыре задачи, отобранные для дополнительного прогона после сетевых обрывов. Для проверки сгенерированного кода и замеров памяти раннеру нужен доступ к Docker Engine с образом сандбокса. На Windows для этого можно задать `$env:GOBACK_CHECKER_WSL = 'Ubuntu'` в PowerShell. Код возврата раннера отражает результаты чекеров.

```sh
python baf/run.py --pair goback+gpt-6.1-terra-low --repeats 1 --out .local-runtime/repro/terra.jsonl
python baf/run.py --pair goback+gpt-6.1-terra-low --repeats 1 --case B3.2 --case B4.1 --case B4.3 --case B8.3 --out .local-runtime/repro/terra-transport-retry.jsonl
```

Результаты живой модели и задержки зависят от прогона и стенда. Для итоговой матрицы используй три повтора на каждой паре и отдельный файл результатов для каждой конфигурации. [БАФ и чекеры](docs/baf.md) · [Сырые результаты](results) · [Источники и версии компонентов](docs/requirements.md#компоненты-и-версии)
