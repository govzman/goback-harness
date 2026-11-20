# Клиент GoBack

Electron показывает подключение и права на папку слева, поток разговора и карточки правок справа. Каждая подтверждённая адресная правка получает своё действие **«Отменить правку»**. Кнопка **«Остановить»** завершает текущий запуск.

Из корня репозитория установи зависимости Python 3.12 в виртуальное окружение и зависимости Electron через npm.

```sh
python -m pip install -r client/requirements.lock
npm ci --prefix client/desktop
npm start --prefix client/desktop
```

Десктоп запускает `python.exe` на Windows и `python3` на Linux. Укажи `GOBACK_PYTHON` с абсолютным путём интерпретатора, если нужное виртуальное окружение не активировано.

На стенде с уже установленными переносимыми зависимостями можно запустить из PowerShell.

```powershell
$env:GOBACK_PYTHON = (Resolve-Path .local-runtime/client-venv/Scripts/python.exe).Path
& .local-runtime/electron/electron.exe client/desktop
```

Введи адрес gateway и токен `goback-local-demo` для сервера по умолчанию. Выбери папку и режим, затем открой сессию. Для демо отмены нужен режим **«Чтение и запись»**.

Для сценариев и автоматизации используй `python client/cli.py run --help` или `python client/cli.py bridge`. Bridge принимает команды по stdin и выдаёт события и результаты по stdout в формате NDJSON. Туннель и SSE работают через общий Python-клиент.
