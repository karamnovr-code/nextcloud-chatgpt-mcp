# Nextcloud + ChatGPT через MCP: полная установка, OCR, создание файлов и диагностика

Дата первоначальной реализации и проверки: 18 сентября 2026 года.
Проверенная связка: Ubuntu 24.04, Python 3.12, FastMCP 4.0.5, Nextcloud 33.0.8, ChatGPT Developer mode, Tesseract 5.3.4, LibreOffice 24.2.7.2, bubblewrap 0.9.0.

Это руководство описывает не только успешную установку, но и ошибки, которые возникли в реальной работе: неверное понимание OAuth-колбэка, зависание мобильного возврата из Nextcloud, `state mismatch` после второго нажатия, пустой список инструментов, падение OCR-обработчика из-за ограничений systemd, вложенный `bubblewrap`, подмена шрифта при PDF-рендере и недостаточные проверки, которые сначала дали ложное чувство готовности.

Пример рассчитан на одного владельца Nextcloud. Для нескольких пользователей потребуется отдельная модель полномочий и изоляции.

## 1. Что получится

После установки ChatGPT сможет через подключённое MCP-приложение:

- просматривать разрешённые папки Nextcloud;
- искать файлы по имени;
- читать PDF, DOCX, XLSX, TXT, CSV, JSON, изображения и многостраничный TIFF;
- выполнять локальное OCR сканов на русском и английском;
- показывать изображение страницы для визуальной проверки OCR;
- создавать DOCX и XLSX;
- преобразовывать DOCX/XLSX в PDF, а PDF — в редактируемый DOCX или извлечённый XLSX;
- сохранять новые файлы в разрешённые папки Nextcloud;
- повторно скачивать созданный файл и проверять SHA-256;
- копировать фотографии в новую структуру без удаления оригиналов.

Интеграция намеренно не предоставляет shell, удаление файлов, массовое перемещение, произвольные URL или безусловную перезапись существующих файлов.

## 2. Архитектура

```text
ChatGPT в браузере
  │  streaming HTTP MCP + OAuth 2.1/PKCE
  ▼
публичный HTTPS MCP
  │
  ├── FastMCP OAuthProxy
  │     └── upstream OAuth2 Nextcloud
  │
  ├── WebDAV / OCS API Nextcloud
  │     ├── список/чтение разрешённых папок
  │     └── create-only запись + обратное скачивание + SHA-256
  │
  └── локальный обработчик документов
        ├── PyMuPDF + Tesseract rus/eng
        ├── python-docx + openpyxl
        ├── LibreOffice headless
        └── bubblewrap без сети и без OAuth-секретов
```

Здесь есть два разных участка OAuth:

1. **ChatGPT → MCP.** FastMCP публикует metadata, поддерживает DCR, downstream PKCE S256, выдаёт собственные токены MCP и хранит привязку к upstream-токену.
2. **MCP → Nextcloud.** Nextcloud использует статический confidential OAuth client с `client_id` и `client_secret`. Этот секрет не вводится в ChatGPT.

Нельзя передавать upstream-секрет Nextcloud в форму DCR ChatGPT: это разные клиенты и разные участки протокола.

## 3. Почему не использовался Nextcloud Context Agent

На проверенной установке Nextcloud были включены `app_api` и `oauth2`, но отсутствовали Assistant, Context Agent и Deploy Daemon. Полный путь через Nextcloud Context Agent потребовал бы отдельного развёртывания AppAPI daemon и дополнительных компонентов. Для задачи чтения, OCR, создания и сохранения документов был выбран отдельный MCP-сервис поверх официальных OAuth2, OCS и WebDAV API Nextcloud.

Это решение даёт явную политику путей, независимый документный обработчик, изоляцию входящих файлов и предсказуемый откат без изменения контейнера Nextcloud.

## 4. Ключевые ограничения безопасности

### 4.1 Nextcloud OAuth2 не имеет scopes

Официальная документация Nextcloud предупреждает: OAuth2-токен имеет полномочия всей выбранной учётной записи. Поэтому защита должна быть многослойной:

- авторизовать только ожидаемого владельца;
- проверять владельца каждого upstream-токена через фиксированный OCS endpoint;
- не принимать произвольные Nextcloud URL;
- разрешать только относительные пути;
- задать отдельные `read_roots` и `write_roots`;
- сделать источники read-only политикой MCP;
- не предоставлять delete/move/overwrite инструменты;
- хранить OAuth state и токены зашифрованно;
- запускать парсеры документов без сети и без доступа к OAuth-конфигурации.

Если нужен строгий принцип минимальных привилегий на уровне самого Nextcloud, создайте отдельную учётную запись Nextcloud и расшарьте ей только нужные папки. Политика MCP остаётся дополнительным, а не единственным барьером.

### 4.2 Документы являются недоверенными данными

PDF, Office-файлы и изображения могут быть повреждены или специально подготовлены. Обработчик:

- получает только байты текущего документа;
- не получает токены Nextcloud;
- не имеет сети;
- видит только read-only системные библиотеки, виртуальное окружение, код обработчика и каталог текущего задания;
- ограничен по памяти и CPU;
- проверяет ZIP-пути, размеры распаковки и активные формулы;
- не выполняет макросы.

Это снижает риск, но не является формальным доказательством безопасности всех нативных парсеров.

## 5. Предварительные условия

- работающий Nextcloud с публичным HTTPS;
- административный доступ к Nextcloud для создания OAuth-клиента;
- Ubuntu 24.04 или совместимый Linux-сервер;
- отдельный публичный HTTPS hostname для MCP;
- Python 3.12;
- ChatGPT с доступным Developer mode в веб-интерфейсе;
- резервная копия конфигурации Nextcloud и базы данных перед изменениями;
- независимый административный доступ для отката.

OpenAI сейчас указывает Developer mode для Pro, Plus, Business, Enterprise и Education на web. Интерфейс и доступность могут меняться, поэтому перед внедрением проверьте актуальную документацию.

## 6. Системные зависимости

Пример для Ubuntu:

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
  libreoffice bubblewrap \
  fonts-dejavu-core fontconfig
```

Проверьте:

```bash
python3 --version
tesseract --version
tesseract --list-langs
libreoffice --version
bwrap --version
```

Для Times New Roman нужен законно полученный локальный комплект шрифтов. Репозиторий его не распространяет. Без него LibreOffice может использовать метрически совместимую замену. После установки шрифтов выполните `fc-cache -f` и проверьте `fc-match 'Times New Roman'`.

## 7. Каталоги и системный пользователь

Получите исходники в отдельный checkout и сначала просмотрите изменения:

```bash
git clone https://github.com/karamnovr-code/nextcloud-chatgpt-mcp.git
cd nextcloud-chatgpt-mcp
```

Команды ниже предполагают, что содержимое этого checkout скопировано в
`/opt/nextcloud-mcp`. Файл `config.example.json` служит только шаблоном: рабочую
конфигурацию с секретами создавайте отдельно в `/etc/nextcloud-mcp/config.json`.

```bash
sudo useradd --system --home /var/lib/nextcloud-mcp \
  --shell /usr/sbin/nologin nextcloud-mcp

sudo install -d -o root -g root -m 0755 /opt/nextcloud-mcp
sudo install -d -o root -g nextcloud-mcp -m 0750 /etc/nextcloud-mcp
sudo install -d -o nextcloud-mcp -g nextcloud-mcp -m 0700 /var/lib/nextcloud-mcp
sudo install -d -o nextcloud-mcp -g nextcloud-mcp -m 0700 /var/lib/nextcloud-mcp/oauth
sudo install -d -o nextcloud-mcp -g nextcloud-mcp -m 0700 /var/lib/nextcloud-mcp/jobs
```

Скопируйте исходники в `/opt/nextcloud-mcp`, затем:

```bash
python3 -m venv /opt/nextcloud-mcp/.venv
/opt/nextcloud-mcp/.venv/bin/pip install --upgrade pip
/opt/nextcloud-mcp/.venv/bin/pip install -r /opt/nextcloud-mcp/requirements.lock.txt
sudo chown -R root:root /opt/nextcloud-mcp
sudo chmod -R go-w /opt/nextcloud-mcp
```

Не размещайте конфигурацию с секретами внутри Git-репозитория.

## 8. Создание OAuth-клиента Nextcloud

Redirect URI upstream-клиента должен указывать на MCP, а не на ChatGPT:

```text
https://mcp.example.com/auth/callback
```

Через интерфейс Nextcloud: **Administration settings → Security → OAuth 2.0 clients**.

Либо через `occ`:

```bash
sudo -u www-data php occ oauth2:add-client \
  'ChatGPT Nextcloud' \
  'https://mcp.example.com/auth/callback' \
  --output=json
```

Команда возвращает `client_id` и `client_secret`. Сразу сохраните их в root-only файл или менеджер секретов. Не вставляйте вывод в журналы, issue, чат или публичную документацию.

Сохраните ID созданного клиента из результата команды или административного
интерфейса для точечного отката. В проверенной версии отдельной команды
`oauth2:list-clients` нет. Синтаксис удаления зависит от версии; сначала
откройте `oauth2:delete-client --help`.

## 9. Конфигурация MCP

Создайте `/etc/nextcloud-mcp/config.json` с правами `0640`, владельцем `root:nextcloud-mcp`:

```json
{
  "public_url": "https://mcp.example.com",
  "nextcloud_url": "https://cloud.example.com",
  "owner": "nextcloud-user-id",
  "client_id": "NEXTCLOUD_OAUTH_CLIENT_ID",
  "client_secret": "NEXTCLOUD_OAUTH_CLIENT_SECRET",
  "jwt_signing_key": "GENERATE_A_LONG_RANDOM_VALUE",
  "state_dir": "/var/lib/nextcloud-mcp/oauth",
  "jobs_dir": "/var/lib/nextcloud-mcp/jobs",
  "port": 9385,
  "max_input_bytes": 268435456,
  "read_roots": [
    "ChatGPT Nextcloud",
    "Documents",
    "Photos"
  ],
  "write_roots": [
    "ChatGPT Nextcloud"
  ],
  "deny_roots": [
    "Documents/System"
  ],
  "read_only_segments": [
    "Source materials"
  ],
  "oauth_redirect_bridge": true
}
```

Сгенерируйте `jwt_signing_key` криптографически стойким способом и сохраните постоянным. Его замена разрывает сохранённые регистрации и зашифрованное OAuth-состояние.

Проверьте права без вывода содержимого:

```bash
sudo chown root:nextcloud-mcp /etc/nextcloud-mcp/config.json
sudo chmod 0640 /etc/nextcloud-mcp/config.json
sudo -u nextcloud-mcp test -r /etc/nextcloud-mcp/config.json
```

### Почему параметры OAuth выглядят именно так

- Downstream ChatGPT использует authorization code + PKCE S256.
- Nextcloud upstream в проверенной версии — confidential client без PKCE, поэтому прокси не пересылает downstream PKCE upstream-провайдеру.
- Nextcloud не принимает MCP `resource`, поэтому он не пересылается upstream.
- Upstream-токен проверяется OCS-запросом и точным совпадением user ID владельца.
- Разрешён только текущий стабильный callback ChatGPT:
  `https://chatgpt.com/connector_platform_oauth_redirect`.
- Issuer сравнивается буквально. Завершающий `/` имеет значение.

Не копируйте старые callback-пути из случайных примеров. Откройте страницу управления MCP-приложением и актуальную документацию OpenAI перед настройкой.

## 10. HTTPS и reverse proxy

MCP должен быть доступен по HTTPS. Пример Caddy:

```caddyfile
mcp.example.com {
    request_body {
        max_size 32MiB
    }
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
    }
    reverse_proxy 127.0.0.1:9385 {
        flush_interval -1
    }
}
```

Проверьте конфигурацию до reload:

```bash
sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Если TLS-edge находится на другом сервере, можно использовать отдельный reverse SSH tunnel с удалённым listener только на `127.0.0.1`. Ключ должен разрешать только требуемый port-forward, без shell, agent/X11 forwarding и PTY. Не открывайте backend MCP напрямую в интернет.

## 11. systemd

Пример unit:

```ini
[Unit]
Description=ChatGPT Nextcloud document tools with OAuth and local OCR
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nextcloud-mcp
Group=nextcloud-mcp
WorkingDirectory=/opt/nextcloud-mcp
Environment=NEXTCLOUD_MCP_CONFIG=/etc/nextcloud-mcp/config.json
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/nextcloud-mcp/.venv/bin/python /opt/nextcloud-mcp/server.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/nextcloud-mcp
ProtectKernelTunables=false
CapabilityBoundingSet=
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
LimitCORE=0
MemoryHigh=3G
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

Обратите внимание на `ProtectKernelTunables=false`. Это не случайное ослабление: `ProtectKernelTunables=true` маскировал `/proc` так, что непривилегированный inner `bubblewrap` не мог выполнить `--proc /proc`. Процесс всё равно работает под отдельным непривилегированным UID, без capabilities, с `NoNewPrivileges`, read-only системой и отдельным sandbox каждого документа.

Применение:

```bash
sudo install -m 0644 deployment/nextcloud-mcp.service.example \
  /etc/systemd/system/nextcloud-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now nextcloud-mcp.service
sudo systemctl status nextcloud-mcp.service --no-pager
```

## 12. Почему нужен bubblewrap и как он устроен

Каждая операция чтения/конвертации запускается через фиксированную команду `bwrap --unshare-all`. В sandbox монтируются:

- `/usr`, `/lib`, `/lib64` read-only;
- виртуальное окружение read-only;
- только `worker.py`, `jobs.py`, `doc_engine.py` read-only;
- один каталог задания как `/work`;
- системные font/loader/LibreOffice-конфиги read-only;
- приватные `/tmp`, `/proc`, `/dev`;
- сеть отсутствует.

Нельзя передавать пользовательские строки в shell-команду. Имя файла превращается в basename, операция выбирается из фиксированного allowlist.

LibreOffice внутри уже созданного outer sandbox не должен запускать второй вложенный `bubblewrap`: на части систем user namespaces запрещены. Код разрешает повторное использование outer sandbox только при наличии launcher-маркера, read-only кода, ожидаемого cwd/HOME и request-файла текущего задания.

## 13. OCR и работа с форматами

### PDF

- сначала извлекается нативный текст;
- если текста недостаточно или страница содержит скан, применяется Tesseract rus+eng;
- каждая страница имеет собственный источник `#page=N`;
- полный документ прочитан только когда `next_start` равен `null`;
- важные суммы и таблицы проверяются через `preview_page`.

### DOCX

Читаются абзацы, таблицы, вложенные таблицы, headers/footers и content controls. Физические страницы DOCX из OOXML определить нельзя: единица чтения называется блоком, а не страницей.

### XLSX

Читаются листы, значения и формулы. Границы определяются по фактическим ячейкам, а не только по потенциально устаревшему `dimension`. В новой книге строки не превращаются в формулы автоматически: формула передаётся только явным объектом `{formula: "=SUM(B2:B10)"}`.

Сетевые/активные формулы и внешние ссылки отклоняются, в том числе в сохранённом шаблоне и defined names.

### Изображения

Поддерживаются распространённые форматы, HEIC/HEIF и многостраничный TIFF. Читаются только реально существующие EXIF-поля; отсутствующая timezone или дата не угадывается. Очень крупные изображения уменьшаются для preview/OCR с явной пометкой.

### Практические границы

- OCR ошибается: в приёмочном тесте латинское `OCR` было распознано как кириллическое `ОСВ`, хотя суммы совпали;
- PDF → DOCX/XLSX сохраняет извлечённое содержание, но не гарантирует исходную вёрстку;
- рукопись, печати и сложные таблицы требуют визуальной проверки;
- DOC/XLS и макросы не поддерживаются;
- формулы XLSX могут потребовать пересчёта настоящим Excel;
- серверная проверка LibreOffice не равна проверке Microsoft Word/Excel.

## 14. Создание Word и Excel

MCP передаёт ChatGPT серверные инструкции по последовательности инструментов и качеству результата. Для нового официального DOCX без шаблона доступен профиль:

```json
{
  "profile": "official",
  "title": "СПРАВКА",
  "paragraphs": ["Текст"],
  "tables": [{"rows": [["Показатель", "Значение"]]}]
}
```

Он задаёт A4, базовые поля, Times New Roman 14, выравнивание и абзацный отступ. Реальный утверждённый шаблон всегда важнее: при `template_path` его стили и поля не заменяются.

Excel создаётся через `rows`, `start_row`, `cells` и явные формулы. Для сложного фирменного оформления нужен реальный XLSX-шаблон. После создания документ нужно:

1. дождаться статуса `completed`;
2. сохранить через `save_job`;
3. убедиться в `verified: true` и SHA-256;
4. повторно прочитать сохранённый файл;
5. при необходимости преобразовать в PDF и проверить каждую страницу визуально.

## 15. Подключение в ChatGPT

По актуальной документации OpenAI:

1. Откройте ChatGPT в браузере.
2. **Settings → Security and login → Developer mode**.
3. Перейдите в Plugins/Apps и создайте developer-mode приложение.
4. Укажите MCP URL: `https://mcp.example.com/mcp`.
5. Выберите OAuth. Если интерфейс предлагает выбор регистрации, для этой реализации используйте DCR.
6. Не вводите upstream `client_id`/`client_secret` Nextcloud в ChatGPT.
7. Войдите в Nextcloud и нажмите «Разрешить» один раз.
8. После возврата проверьте подключённую учётную запись.
9. Нажмите **Refresh/Обновить**, чтобы ChatGPT перечитал инструменты, описания и server instructions.
10. Начните новый чат, выберите Developer mode и это приложение.

Write-инструменты по умолчанию могут потребовать подтверждения в ChatGPT. Проверяйте JSON параметров перед подтверждением.

## 16. Мобильная проблема OAuth: кнопка «Разрешить» и state mismatch

### Симптом

На iPhone первое нажатие «Разрешить доступ» визуально ничего не делает. Второе нажатие показывает:

```text
Доступ запрещён. Токен состояния не совпадает.
```

### Что происходило на самом деле

Первый POST формы уже проходил, Nextcloud обрабатывал одноразовый state и MCP получал upstream callback. FastMCP отвечал 302 на ChatGPT. Однако браузер не выполнял следующий переход.

Nextcloud разрешал `form-action` на зарегистрированный callback MCP. Цепочка выглядела так:

```text
Nextcloud POST → MCP /auth/callback → 302 ChatGPT callback
```

Некоторые браузеры применяют CSP `form-action` и к перенаправлениям после формы. В итоге MCP callback достигался, но переход в ChatGPT блокировался. При повторном POST одноразовый state уже был потреблён, поэтому `state mismatch` был вторичным симптомом.

### Исправление

`OAuthRedirectBridge` действует только для:

- метода GET;
- пути `/auth/callback`;
- ответа 302;
- единственного `Location`;
- точного `https://chatgpt.com/connector_platform_oauth_redirect`;
- URL без fragment/control characters.

Он заменяет финальный 302 на страницу 200 с meta refresh и ручной ссылкой. Сохраняется `Set-Cookie`, удаляются `Location` и старые content headers, добавляются `no-store`, `no-referrer` и строгая CSP. OAuth code/state не создаются и не меняются.

Это совместимый workaround для наблюдавшейся цепочки, а не универсальное требование каждой версии Nextcloud или браузера. После обновлений FastMCP/Nextcloud его необходимость нужно перепроверить.

### Диагностика без утечки секретов

Временная трассировка должна записывать только:

- метод;
- категорию пути;
- HTTP-статус;
- категорию redirect destination (`mcp`, `nextcloud`, `chatgpt`, `none`).

Нельзя записывать query string callback, code, state, cookies, Authorization, access/refresh token или client secret.

Важно: строка `302 → chatgpt` доказывает только то, что сервер выдал браузеру инструкцию. Она не доказывает, что браузер перешёл и ChatGPT получил запрос.

## 17. Ошибка document worker даже на маленьком PDF

### Симптом

ChatGPT сообщает, что обработчик многократно падает даже на PDF около 14 КБ.

### Ошибочный вывод

Размер файла казался причиной, но все задания `read` и `preview` падали до анализа документа.

### Реальная причина

В production unit было `ProtectKernelTunables=true`. systemd маскировал `/proc`, после чего inner bubblewrap завершался:

```text
bwrap: Can't mount proc on /newroot/proc: Operation not permitted
```

Попытка заменить `--proc` на пустую директорию позволила OCR, но сломала LibreOffice. Она была отклонена.

### Исправление

- `ProtectKernelTunables=false`;
- `CapabilityBoundingSet=` пустой;
- остальные ограничения сохранены;
- приёмочный smoke запускается с **каждым** свойством установленного `[Service]`, заменяя только `ExecStart` на синтетический тест.

Старые failed job IDs не становятся успешными после ремонта. Нужно создать новые задания.

### Главный урок

Тест под тем же UID — ещё не точная production-проверка. Он должен воспроизводить все systemd properties, namespaces, writable paths и environment.

## 18. Другие найденные ошибки и их исправления

| Ошибка или риск | Причина | Исправление |
|---|---|---|
| Использовали предполагаемый callback ChatGPT | Старый/угаданный путь | Взяли точный callback из актуальной документации и allowlist |
| `/healthz` 200 принимался за готовый OAuth | Проверена только доступность процесса | Отдельно проверять metadata, 401 challenge, OAuth, discovery и реальный tool call |
| Учётная запись подключена, но инструментов нет | ChatGPT ещё не обновил snapshot | Нажать Refresh и дождаться появления инструментов |
| `state mismatch` после второго нажатия | Первый POST уже потребил state | Начать новую OAuth-попытку; не отключать state/CSRF |
| Вложенный bubblewrap не запускается | User namespace уже изолирован | LibreOffice повторно использует доказанный outer sandbox |
| Smoke считался «точным», но не включал все unit properties | Ручной набор параметров разошёлся с unit | `verify_service.py` читает каждый `[Service]` property из фактического unit |
| PDF визуально использовал замену шрифта | Font был в Word, но не виден sandbox LibreOffice | Read-only mount законно установленного font directory; проверка embedded PDF font |
| DOCX терял nested tables/content controls | Неполный обход OOXML | Рекурсивное чтение и регрессионные тесты |
| XLSX скрывал реальные ячейки | Устаревший worksheet dimension | Определение границ по фактическим cell records |
| Опасная формула оставалась в шаблоне | Проверялись только новые значения | Проверка всех формул, defined names и внешних функций |
| Большой stream без Content-Length мог переполнить память | Проверялся только заголовок | Считать фактически полученные байты и не возвращать частичный результат |
| Временный credential попал в диагностический traceback | Ошибка захвата CLI-вывода | Немедленно отозвать credential; парсить структурированный/последний результат без печати секрета |
| `pytest` собрал тесты чужого upstream checkout | Не было ограниченного testpath | `pytest.ini` с `testpaths = tests docs/review` |
| Широкий журнал утонул в WebDAV-шуме | Слишком большой интервал/endpoints | Короткое окно и фиксированный allowlist OAuth-путей |
| Открытие MCP URL в браузере использовалось как тест | `/mcp` не обычная страница | Проверять через MCP client/ChatGPT; 401 без токена ожидаем |
| OCR принял `OCR` за `ОСВ` | Неидеальное распознавание | Сверять значимые фрагменты через preview; OCR не считать доказательством |

## 19. Приёмочная матрица

Минимальная приёмка должна включать:

### Сервер

```bash
curl -fsS https://mcp.example.com/healthz
curl -i https://mcp.example.com/mcp
curl -fsS https://mcp.example.com/.well-known/oauth-authorization-server
curl -fsS https://mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Ожидается:

- `/healthz` — 200;
- `/mcp` без OAuth — 401;
- metadata — 200;
- issuer и authorization server совпадают буквально;
- `code_challenge_methods_supported` содержит `S256`.

### Негативные OAuth-тесты

- неизвестный redirect URI отклоняется;
- запрос без PKCE отклоняется;
- прямой upstream bearer не принимается как downstream MCP token;
- токен другого Nextcloud пользователя отклоняется;
- состояние OAuth переживает штатный рестарт сервиса.

### Пути

- разрешённая папка читается;
- `..`, percent-encoding, URL, backslash, control chars отклоняются;
- запись в read-only root отклоняется;
- существующий файл не перезаписывается;
- новый файл скачивается обратно, SHA-256 совпадает.

### Документы

- нативный PDF;
- русский скан PDF;
- preview возвращает изображение;
- pagination до `next_start = null`;
- DOCX с nested table/content control;
- XLSX с формулами и намеренно неверным dimension;
- DOCX → PDF;
- XLSX → PDF;
- scanned PDF → DOCX;
- scanned PDF → XLSX;
- большой JPEG/HEIC/TIFF в пределах ресурсной политики.

### Пользовательский маршрут

1. ChatGPT подключает учётную запись.
2. После Refresh видны инструменты.
3. В новом чате выбран именно этот MCP.
4. Известный скан прочитан заново.
5. Контрольные цифры сверены по preview.
6. Word и Excel созданы и сохранены.
7. Оба файла повторно открыты через Nextcloud.
8. Ссылки ведут к нужным файлам.

Discovery инструментов не доказывает чтение файлов. Чтение не доказывает запись. Запись не доказана до обратного скачивания и проверки.

## 20. Первый тестовый запрос ChatGPT

```text
Используй только приложение ChatGPT Nextcloud. Открой папку
«ChatGPT Nextcloud/Проверка подключения». Полностью прочитай тестовый PDF,
продолжая чтение до конца. Для скана используй OCR и проверь суммы по
изображению страницы. Создай Word с выводом и Excel с позициями и итогом.
Сохрани оба файла в «ChatGPT Nextcloud» под новыми датированными именами.
Повторно открой сохранённые файлы и дай ссылки. Не перезаписывай существующие.
```

Используйте только синтетические данные, пока весь маршрут не принят.

## 21. Обновление

Перед обновлением:

1. сохранить хеши активных исходников;
2. сделать копию `/etc/nextcloud-mcp/config.json` в закрытый backup;
3. сохранить `/var/lib/nextcloud-mcp/oauth`;
4. сохранить текущий unit и reverse proxy block;
5. проверить свободное место;
6. прогнать тесты новой версии в отдельном checkout.

После обновления:

1. установить только проверенные исходники;
2. перезапустить только MCP-сервис;
3. проверить health, metadata, 401 и worker smoke;
4. в ChatGPT нажать Refresh после изменения tools/instructions;
5. выполнить новый user-route тест.

Не удаляйте OAuth state при обычной очистке job cache. Не меняйте `jwt_signing_key`, если не планируете полное переподключение клиентов.

## 22. Откат

Точечный откат:

1. остановить/отключить только `nextcloud-mcp.service` и его отдельный tunnel;
2. удалить только собственный блок MCP из текущей конфигурации reverse proxy;
3. валидировать конфигурацию и reload proxy;
4. отозвать только созданный OAuth client Nextcloud;
5. удалить только dedicated tunnel key/authorized_keys line;
6. сохранить пользовательские результаты;
7. проверить, что исходный Nextcloud по-прежнему работает.

Не восстанавливайте целый старый Caddyfile или дамп базы поверх более поздних изменений. Дамп базы — аварийная контрольная точка, а не стандартный способ отмены отдельного MCP.

## 23. Что нельзя публиковать

- `client_secret` Nextcloud;
- `jwt_signing_key`;
- OAuth access/refresh tokens;
- cookies, authorization code и state;
- callback URL вместе с query string;
- private SSH keys и `known_hosts`, если они раскрывают приватную топологию;
- реальные IP/домены частной инфраструктуры без необходимости;
- имена пользователей, папок и файлов с персональными данными;
- database dump;
- приватные логи;
- `/etc/nextcloud-mcp/config.json`;
- `/var/lib/nextcloud-mcp/oauth` и jobs с реальными документами.

Публичный репозиторий должен содержать только шаблон конфигурации и placeholder-домены.

## 24. Источники

- OpenAI, ChatGPT Developer mode:
  https://developers.openai.com/api/docs/guides/developer-mode
- OpenAI, MCP/Plugin OAuth authentication:
  https://developers.openai.com/plugins/build/auth
- Nextcloud, OAuth2:
  https://docs.nextcloud.com/server/stable/admin_manual/configuration_server/oauth2.html
- Nextcloud, WebDAV basic APIs:
  https://docs.nextcloud.com/server/stable/developer_manual/client_apis/WebDAV/basic.html
- Nextcloud, WebDAV SEARCH:
  https://docs.nextcloud.com/server/stable/developer_manual/client_apis/WebDAV/search.html
- FastMCP, OAuth Proxy source documentation:
  https://github.com/PrefectHQ/fastmcp/blob/main/docs/servers/auth/oauth-proxy.mdx
- MDN, CSP `form-action`:
  https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/form-action

## 25. Статус доказательств в исходном внедрении

Подтверждено:

- Nextcloud 33.0.8 работал без maintenance mode;
- FastMCP 4.0.5 публиковал OAuth metadata;
- публичный MCP без токена возвращал 401;
- DCR, S256 и точный callback проверялись;
- пользователь завершил OAuth в ChatGPT;
- после Refresh появились 14 инструментов;
- русский скан был прочитан, сумма `10 000 + 5 000 = 15 000` сверена по изображению;
- Word и Excel были сохранены, повторно открыты и визуально проверены пользователем;
- 12 ранее падавших jobs прошли после исправления systemd;
- Word official profile прошёл структурную проверку, PDF-рендер и проверку реального Times New Roman;
- полный локальный набор после добавления redirect bridge: 82 теста.

Не заявляется:

- формальная безопасность всех сторонних парсеров;
- идеальный OCR;
- точное восстановление сложной PDF-вёрстки;
- Microsoft Word/Excel compatibility для каждого будущего документа без реального открытия;
- универсальная необходимость OAuth redirect bridge во всех браузерах и версиях;
- проверка автоматического refresh token после полного срока жизни в многодневном цикле.

Главный критерий готовности — не зелёный systemd и не `healthz`, а реальный маршрут пользователя: **поиск → чтение всего документа → визуальная сверка → создание → сохранение → обратное открытие проверенного файла**.
