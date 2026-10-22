# Portfolio Deployment

[English version](README.md)

Централизованная платформа Docker/GitOps-деплоя независимо собираемых портфолио-
приложений на одном Ubuntu VPS. **Текущее состояние: Stage 09.4A — подготовка production deployment реализована и проверена офлайн (1120 тестов).**
Stage 08 смержен в `develop`; реальный локальный VM E2E Stage 07 пройден: 15/15 сценариев.
Реализованы registry/admission, offline-планирование и retained Caddy previews;
PR #4 Stage 04, PR #5 Stage 05 и PR #6 Stage 06 смержены в `develop`. Stage 05 добавляет детерминированный Compose,
ограниченный Docker discovery, preflight, lock и адресный executor с offline mocks.
Stage 06 добавляет bounded readiness, SQLite journal/LKG, ограниченный rollback
и анализ crash recovery в существующий executor. Stage 07 добавляет отдельную
disposable Ubuntu VM и test-only readers/probes; локальная приёмка пройдена,
а будущие запуски по-прежнему требуют отдельных opt-in.
Production-деплой остаётся **Planned**: Docker не даёт полный registry/gateway
snapshot Stage 03. Production apply интерфейса нет. Реальный реестр пуст до
подтверждения digest с нужным origin; admission резервирует ProjectRouter. Отдельный локальный smoke Stage 05 не запускался.

[Подготовка Stage 09.4A](docs/production-preparation.md) и
[execution ADR](docs/adr/0001-production-execution.md) добавляют общую policy
стабильного Compose plugin >= 2 с отдельными проверками CLI/конфигурации, authenticated source bindings, канонические production lock/storage,
офлайн-план первой установки и source/artifact/approval contracts.
Рекомендуется локальное исполнение на VDS через отдельно подготавливаемую
privileged boundary; sudo fallback и production apply отсутствуют. Protected-main
evidence обоих репозиториев, доверенные execution/trust mechanisms и изолированные
runtime-тесты точных images остаются blockers. Stage 09.4B — First Authorized
ProjectRouter Rollout — требует отдельного разрешения; Stages 09.5/09.6 остаются
Planned. Наблюдения оператора не являются trusted planner state. Подключений к VDS
и mutations в 09.4A не было; полный offline check: 1120 tests passed.

[Контракты и безопасность Stage 09.1](docs/production-contracts.md) фиксируют
`project-router` → `project-router-web:8080` в `portfolio-proxy`, readiness `/en/`,
hostname `danil-miftakhov.dev` и ожидаемый build origin `https://danil-miftakhov.dev`.
[Неактивный шаблон](docs/templates/project-router.yaml) находится вне реестра
и не проходит validation до подстановки digest. Production digest не заявлен.
Следующие prerequisites: защищённый source/CI, доказательство artifact/origin,
отдельно разрешённая read-only диагностика VDS и согласованные live integration
transports/operation gates. Документ содержит acceptance gates 09.2–09.5 и несекретные сведения
от оператора. Подключений к VPS и production mutations не выполнялось.

[Operator guide Stage 09.2](docs/production-inspection.md) описывает `make production-check`
(офлайн-проверка локальных prerequisites/trust), `make production-inspect` (отдельно
разрешённый интерактивный read-only SSH) и `make production-report` (офлайн-просмотр
последнего отчёта). Используй существующий
`PRODUCTION_INVENTORY=../portfolio-server-infrastructure/inventories/production.yml`:
из него берутся managed user, фактический порт и существующий ключ автоматизации;
trust берётся из `~/.ssh/known_hosts`, с strict host checking и public-key-only auth.
До разрешённого запуска независимо проверь исходное доверие к host key и разблокируй
выбранный зашифрованный ключ в существующем agent. Приватный отчёт (каталог 0700,
файл 0600): `~/.local/share/portfolio-deployment/production-inspection/latest.json`.
Passed/blocked/unknown относятся к ограниченным наблюдениям; отсутствие Docker
permissions — blocker, а не доказательство отсутствия Docker. DNS/TLS/GHCR,
совместимость конкретного image, trusted live transports и согласованные пути/subnets
остаются prerequisites для Stage 09.4B. Реального SSH и production mutations в этом этапе не было.

[Operator guide Stage 09.3](docs/production-integration.md) описывает версионированный
per-component runtime security contract, production Engine read adapter,
полную композицию источников, OCI mapping/source verification ports и интеграцию
managed Caddy runtime/candidates. Переиспользованы executor, host lock, readiness,
SQLite journal/LKG и recovery. `make production-integration-check` работает офлайн;
`make production-preflight PRODUCTION_CONTRACT=<saved.json>` и
`make production-plan PRODUCTION_CONTRACT=<saved.json> OBSERVED=<saved.json> GIT_REVISION=<full-sha>`
возвращают только blocked preview (`authorization=false`, exit 2). Используй `PYTHON=.venv/bin/python`.
Production apply команды и настроенного live write transport нет.
Stage 09.4B требует отдельно согласованных identities, путей/budgets/subnets,
protected-main exact-SHA source/artifact/origin evidence, trusted live transports
и разрешения операции. Stage 09.6 automated digest-update PR/CD запланирован после
безопасного ручного rollout/rollback; автоматический CD здесь не реализован.

[Как добавить новое приложение в Portfolio Deployment](docs/application-acceptance.md):
добавить один валидируемый YAML с release metadata и пройти независимое
admission/security-ревью. Один ручной workflow **Application isolated acceptance**
проверяет подписи образов, readiness/security, зависимости, restart и очистку своих
ресурсов для HTTP/worker-компонентов. `make acceptance-check` проверяет неактивные
декларации offline. Бизнес-тесты остаются в CI приложения; реальная приёмка и
production admission требуют отдельных review/разрешений. Универсальная приёмка не запускалась.

## Назначение и архитектура

Целевая цепочка: CI приложения → immutable-образ в GHCR → утверждённое desired
state в Git → reconciliation → Docker Compose → проверка готовности → маршрутизация
Caddy. Реальная production integration и активация gateway — **Planned**; readiness
и recovery имеют offline-tested contracts и VM-only adapters, проверенные реальным E2E Stage 07.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  отвечает за Ubuntu, SSH/пользователей, Docker Engine и безопасность хоста.
- **Portfolio Deployment** реализует контракт реестра, офлайн-admission и
  планирование по явному snapshot, детерминированный Compose и ограниченный
  execution foundation. Следующие этапы добавят полную runtime-интеграцию,
  домены/HTTPS и production observation/transport adapters. Readiness и постоянная
  история результатов реализованы офлайн в Stage 06.
- Прикладные репозитории, например
  [project-router](https://github.com/DanilFaritovich/project-router), отвечают за
  исходный код, тесты/CI, сборку и публикацию образов в GHCR и предоставляют
  deployment contract. Офлайн-валидация не подтверждает их готовность к деплою.

Границы ответственности, потоки, доверие и ограничения восстановления описаны в
[ARCHITECTURE.md](ARCHITECTURE.md).

## Планируемые возможности и технологии

**Реализовано офлайн:** закрытые схемы приложений/admission, immutable-ссылки на
образы компонентов, проверки всего реестра и позитивные/негативные тесты. Поля,
policy и лимиты валидации описаны в [контракте v1](docs/manifest-v1.md).
[Контракт Stage 03](docs/reconciliation-planning.md) описывает неизменяемые модели,
ограниченные snapshots, обновления компонентов, no-op, маршруты, drift, health
и блокировки lifecycle/storage. Планирование не выполняет операции.
[Stage 04](docs/caddy-gateway.md) добавляет immutable routes/candidates, фиксированный
Caddyfile renderer, раздельные desired/current/retained наборы и явную проверку
официальным локальным Caddy. Неподтверждённые изменения сохраняют active routes;
unknown или недоверенный gateway scope блокирует полный кандидат.

[Stage 05](docs/docker-deployment-engine.md) добавляет immutable Compose artifacts,
независимые projects/private networks, named volumes, ownership/OCI preflight,
host flock и адресное выполнение компонентов. Неопределённые refs, запрещённые
storage changes, неизвестные/чужие ресурсы и недоказанный image mapping блокируют
выполнение. Docker-only discovery намеренно возвращает неполный snapshot Stage 03.

[Stage 06](docs/readiness-recovery.md) добавляет bounded HTTP/worker readiness,
приватный SQLite intent/events, atomic application last-known-good, ограниченный
stateless image rollback, анализ crash recovery и composite observation contracts.
Stage 07 добавляет VM-only probes/readers; mutations после crash остаются запрещены.

**Planned:** production rollouts, полное live reconciliation и общий Caddy HTTPS.
Zero-downtime не обещается.

Целевые технологии: Git, Docker Engine/Compose, GHCR и Caddy. Для Stage 05 выбран
Python CLI с повторным использованием PyYAML/jsonschema и pytest. Журнал использует
SQLite стандартной библиотеки; сервер БД, daemon и Kubernetes не реализованы.
Stage 09.1 выбирает ручное разрешение из защищённой `main`; настройка защиты GitHub
и production-валидация точного SHA остаются prerequisites. Автоактивации нет.

## Структура репозитория

```text
.agents/skills/                 Локальные skills, references, upstream MIT notice
.codex-standards.lock.yaml      Ревизия upstream, профиль и состав пакетов
.editorconfig / .gitignore     Формат текста и исключения локальных файлов/секретов
AGENTS.md                      Workflow Codex и границы разрешённых операций
ARCHITECTURE.md                 Архитектура и runtime-границы
README.md / README.ru.md        Английская / русская точки входа
docs/roadmap.md                 Предварительные этапы и границы решений
scripts/check_bootstrap.py      Offline-проверки и безопасная нормализация текста
scripts/validate_registry.py    Ограниченный офлайн-валидатор и API загрузки
scripts/planning_inputs.py      Валидация неизменяемых desired/observed моделей
scripts/reconciliation.py       Чистое сравнение и неизменяемый план
scripts/plan_registry.py        Offline CLI с явно заданными входами
scripts/gateway.py              Чистые routes, retained candidates и Caddyfile renderer
scripts/gateway_cli.py          Offline CLI для gateway preview
scripts/gateway_validation.py   Явная проверка официальным локальным Caddy
scripts/compose.py              Immutable детерминированный Compose artifact
scripts/deployment.py           Preflight, host flock, адресные operations/results
scripts/docker_adapter.py       Ограниченный локальный adapter и offline mock
scripts/docker_discovery.py     Docker-only observations, неполный registry provider
scripts/deployment_cli.py       Offline Compose и deployment previews
scripts/readiness.py            Bounded probe interfaces, без реального transport
scripts/composite_state.py      Контракт полных observation sources
scripts/deployment_journal.py   Приватный versioned SQLite intent/events/LKG
scripts/deployment_lifecycle.py Readiness, ограниченный rollback и recovery analysis
scripts/local_docker_smoke.py   Отдельный opt-in disposable test lifecycle
registry/applications/         Пустой реальный реестр; один manifest на приложение
policy/admission.yaml           Допуск ProjectRouter; требуется security review
schemas/                       Версионированные закрытые схемы
tests/fixtures/                Синтетические примеры и недопустимый YAML
requirements-dev.txt           Закреплённые инструменты офлайн-валидации
Makefile                       Интерфейс fix / validate / check / plan / gateway-*
```

Генерация Compose реализована; доставка runtime-конфигурации и production Caddy
activation остаются будущими задачами. Синтетические fixtures не являются
production-манифестами.

## Локальная разработка и проверки

Нужны Git, GNU Make, Python 3.10+ с `venv` и pip. Docker, доступ к VPS, домены и
production-учётные данные не нужны. Из корня репозитория:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make fix PYTHON=.venv/bin/python
make validate PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
```

Установка зависимостей может использовать сеть; обычные проверки/previews — офлайн.
Официальная syntax validation и локальный smoke — отдельные opt-in команды.
`fix` нормализует окончания строк, завершающий перевод строки и пробелы в конце
строк собственных файлов проекта, сохраняя vendored skills. `check` работает без
изменений файлов: проверяет формат текста, синтаксис YAML/дубликаты ключей,
метаданные standards lock, полный состав пакетов/frontmatter, локальные Markdown-
ссылки/якоря и совпадение структуры/roadmap README, затем весь реестр и pytest.
`validate` проверяет только реестр/admission. Негативные YAML fixtures исключены
из общего синтаксического обхода и проверяются специальными тестами. Точность
перевода требует отдельного review: проверки не доказывают смысловую
эквивалентность языков. Внешние URL не запрашиваются.

Офлайн-планирование требует явного локального snapshot и полного Git SHA:

```sh
make plan PYTHON=.venv/bin/python OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa PLAN_FORMAT=json
```

SHA синтетический; укажи нужную ревизию. Snapshot по умолчанию отсутствует.
Неизвестные/неполные наблюдения, конфликт ownership и изменения существующих
storage/mounts блокируют план. Config/secret IDs не доказывают неизменность
содержимого; связь OCI index с manifest требует явных сведений. План не разрешает
выполнение. CLI возвращает 0 для сравнимых changes/no-op, 1 для ошибок входов,
2 для блокировок, 3 для degraded health; Make использует свой код ошибки recipe.
API, текстовый вывод и будущие preflight/readiness gates описаны в контракте.

Gateway preview требует те же явно заданные входы:

```sh
make gateway-render PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa GATEWAY_FORMAT=json
make gateway-check PYTHON=.venv/bin/python
make gateway-validate PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

`GATEWAY_FORMAT=caddyfile` выводит полный retained candidate. Пустой набор — явный
no-routes outcome (JSON), без публичного fallback. JSON разделяет предложенные
изменения и сохранённые active routes. Gateway CLI: 0 — успешный preview (включая
pending), 1 — ошибка входов, 2 — blocked, 3 — нет Caddyfile, 4 — запрошенная
официальная проверка не прошла/не выполнялась. Отсутствие Caddy сообщается явно;
ничего не скачивается. Официальная проверка подтверждает синтаксис, без readiness
и разрешения активации. `make check` включает Stage 04. API, границы доверия,
будущий reload и TLS prerequisites описаны в документе gateway.

Интерфейсы Stage 05 требуют явные root, application и revision:

```sh
make compose-render PYTHON=.venv/bin/python REGISTRY_ROOT=. APPLICATION=APP GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa COMPOSE_FORMAT=json
make deployment-plan PYTHON=.venv/bin/python REGISTRY_ROOT=. APPLICATION=APP GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa OBSERVED=tests/fixtures/observed/noop.json
make compose-check PYTHON=.venv/bin/python
```

Замени APP на admitted application; реальный реестр пуст. Машинный JSON preview
не разрешает выполнение. `compose-validate` с теми же root/application/revision
отдельно вызывает установленный Compose v2 `config -q` без daemon contact;
отсутствующие prerequisites дают `not-performed`.

`local-docker-smoke` требует явные SMOKE_ENDPOINT (канонический Unix socket),
SMOKE_SESSION (неиспользованный 16-hex token), два заранее загруженных immutable
GHCR manifest image SMOKE_IMAGE_A/B с Engine Descriptor evidence и
SMOKE_AUTHORIZE=disposable-local-docker. `check` никогда его не вызывает.
[Документ Stage 05](docs/docker-deployment-engine.md) описывает prerequisites,
изоляцию, адресный cleanup и неполный live discovery. Реальный smoke не запускался.
Успех Compose — `applied-unverified`; timeout/disconnect — `unknown` с обязательным
fresh discovery. Readiness и release success остаются неподтверждёнными.

Stage 08 добавляет offline GitHub Actions для PR в `develop` и `main`: закреплённые
Python/tools, pip cache, минимальные read permissions, `make check` и
`make workflow-check` (actionlint). Отдельный `make ci` не нужен. Реальные VM/E2E,
Docker, публикация образов и деплой в CI не запускаются. Required check
**Offline CI / Offline checks** нужно вручную включить в branch rules GitHub.
См. [Stage 08](docs/stage-08-automation.md): установка инструментов и ручная процедура
экспорта/проверки evidence успешной сессии перед отдельно подтверждаемыми остановкой
и удалением VM. Stage 08 не выполнял эту процедуру; текущее наличие VM здесь не утверждается.
Targets `verify`, deploy и production apply отсутствуют. Успешные offline
проверки не доказывают live Docker, HTTPS, reconciliation или rollback.

## Стандарты разработки и Git delivery

Начинай с [AGENTS.md](AGENTS.md). Установлен профиль
**docker-deployment-platform v1** из
[codex-development-standards](https://github.com/DanilFaritovich/codex-development-standards)
на `104a8ed8e10a07fbb9a9ac2d7be669cc93913590` (источник — `develop`).

Обязательные локальные skills: `task-development-workflow`, `standards-sync`,
`code-quality`, `docker`, `github-actions`, `continuous-delivery`, `caddy-gateway`,
`gitops-deployment`, `documentation`, `licensing`. Установлены полные пакеты с
references; читать references нужно только для текущей задачи. Опциональные
`ansible`, `python-testing`, `structured-logging` не установлены.

[Standards lock](.codex-standards.lock.yaml) хранит источник и состав пакетов.
Обновления выполняются через локальный `standards-sync` и сравнение изменённых
файлов, без загрузки всего upstream на каждой задаче. Совместимые локальные
адаптации допустимы; проверка состава не требует побайтового совпадения с upstream.

`main` — стабильная ветка, `develop` — интеграционная, рабочие ветки создаются
строго от `develop`. После завершения обычных задач разрешены commit, push и PR в
`develop`, если пользователь не установил более строгую границу. Review и merge
выполняются отдельно: автоматические merge, tags/releases и production-операции
не разрешены.

## План этапов

Предварительный [roadmap](docs/roadmap.md) может уточняться после architecture review:

| Этап | Область | Статус |
| --- | --- | --- |
| Stage 01 | Project Bootstrap | Реализован; PR #1 смержен |
| Stage 02 | Service Registry & Manifest Validation | Implemented offline; PR #2 merged |
| Stage 03 | Desired State & Reconciliation Planning | Implemented offline; PR #3 merged |
| Stage 04 | Caddy Gateway Foundation | Implemented offline; PR #4 merged into develop |
| Stage 05 | Docker Deployment Engine | Implemented foundation; PR #5 merged; smoke not-performed; production apply blocked |
| Stage 06 | Readiness, Journal & Recovery | Implemented offline; PR #6 merged; SQLite/LKG and analysis-only recovery |
| Stage 07 | Local E2E & Integration Testing | Реализован; реальный локальный VM E2E пройден, 15/15; production acceptance ожидается |
| Stage 08 | GitHub Actions & Automation | Реализован офлайн; ручная процедура завершения VM ожидает live review |
| Stage 09 | Production Integration & Release | 09.1 реализован офлайн; 09.2 реализован офлайн; 09.3/09.4A реализованы офлайн; 09.4B/09.5/09.6 Planned; операции требуют отдельных разрешений |

Stage 05 выбирает Python CLI без изменений контрактов Stage 02–04. Реальному
ProjectRouter всё ещё нужен подтверждённый digest. Stage 06 отвечает за readiness,
rollback, journal и recovery; Stage 07 расширяет изолированные E2E. Полные live
observations, настройка защиты production source, доставка конфигурации и index mapping
требуют review до production-выполнения.

SQLite durability зависит от надёжного локального storage/fsync. Журнал не заменяет
live state или backup. Незавершённые runs блокируют новые journaled operations;
crash analysis не повторяет mutations и не снимает неопределённость. Для production
ещё нужны полные реальные Docker/gateway/runtime observations, защищённый source
и authorization contract, доставка конфигурации, artifact retention, backup/recovery
ownership и live acceptance. См. [ограничения и API Stage 06](docs/readiness-recovery.md).

## Изолированный локальный E2E

`make e2e` выполняет первый и повторные запуски без экспорта переменных окружения.
Lowercase GHCR owner определяется из проверенного GitHub origin; приватный каталог
состояния по умолчанию — `~/.local/share/portfolio-deployment/e2e-state/`, fixtures —
`~/.local/share/portfolio-deployment/e2e-fixtures/`, независимо от checkout и текущей
директории. Недостающие каталоги безопасно создаются с правами `0700`, приватные
файлы — `0600`. Права существующих каталогов не меняются; checkout с правами `775`
не требует изменений. Overrides `E2E_STATE_DIR` и `E2E_FIXTURE_DIR` проходят те же
проверки владельцев, permissions, symlink и traversal. Непустые legacy-каталоги
`.e2e-state`/`.e2e-fixtures` блокируют команды до
[ручной проверки и перехода](docs/local-e2e.md#legacy-state-transition); автоматического
переноса, удаления или reset нет. Без готового lock команда предлагает подготовить
шесть образов, разрешает официальный Python 3 source из `e2e/python-base.json`
в проверенный direct linux/amd64 digest и фиксирует raw manifest/config до сборки.
Отдельные подтверждения обязательны для создания builder VM, сборок, создания
publisher VM, публикации GHCR, проверки публичности, создания тестовой VM, immutable
pulls, Docker mutations и активации loopback Caddy. Credentials остаются только в
publisher VM. При готовом проверенном lock сборка и публикация пропускаются.
Нужны Linux amd64, KVM, аутентифицированный Multipass, доступ к сети/packages и
достаточные ресурсы VM. Docker основной системы не используется; все `E2E_*`
переменные — необязательные overrides.

`make e2e-fixtures` отдельно готовит fixtures с теми же defaults и подтверждениями.
CLI `fixtures --plan --base <immutable-reference>` и импорт raw manifests остаются
офлайн. Unknown outcome сохраняет evidence и блокирует retry; автоматического
reset или cleanup нет. См. [инструкцию оператора Stage 07](docs/local-e2e.md).
Все шесть fixture-образов (**6/6**) собраны, опубликованы в GHCR и анонимно проверены по
immutable digest в fixture-сессии `5b780834b80ffe9e`; последующие E2E-попытки использовали их повторно.
`make e2e-report` читает приватные локальные receipts сессии; `e2e-diagnose` читает VM.
CLI: `python -m scripts.e2e.cli`; отдельные prepare/images/test/status/clean остаются
доступны. Failed/unknown run блокирует новый оркестрированный запуск в том же
каталоге состояния, сохраняет evidence и не возобновляется и не очищается
автоматически. `make e2e-check` и `make check` остаются полностью офлайн.
**Подтверждённая локальная приёмка (2026-10-21):** сессия `4963fe050c4567f2`, receipt
`complete/passed`, `realE2E=performed`, **15/15 реальных сценариев passed**; принятая
реализация также прошла **779 offline tests**. Production deployment не выполнялся,
Stage 08 добавляет offline CI; Stage 09.1 — офлайн-контракты/security, а 09.2 реализован офлайн; 09.3/09.4A реализованы офлайн; 09.4B/09.5/09.6 остаются Planned. Предыдущие неудачные попытки и evidence их закрытия
сохранены; journals намеренных crash/unknown сценариев остаются analysis-only,
без replay. См. [результаты приёмки и историю](docs/local-e2e.md#verified-local-acceptance).

## Безопасность, ограничения и лицензирование

Stage 07 разрешает только отдельно авторизованные E2E в disposable Ubuntu VM,
включая test-only активацию loopback Caddy;
запрещены VPS/SSH, production Ansible и контейнеры, production-активация Caddy, изменения DNS/доменов, production-секреты,
автоматический деплой, изменения других репозиториев, merge и release tags.
Будущие production-работы требуют отдельного разрешения. PR-проверки не должны
получать production-секреты. Git хранит ссылки на конфигурацию, без реальных
учётных данных. `.gitignore` — только дополнительная защита; проверяй staged-файлы.

Целевая однохостовая архитектура не гарантирует высокую доступность и отсутствие
простоя. Общая proxy-сеть требует оценки доступа между приложениями. Rollback
образа не отменяет миграции базы и не заменяет резервные копии; постоянные данные
нельзя удалять автоматически.

Лицензия проекта пока не выбрана. Для vendored standards сохранён upstream
[MIT notice](.agents/skills/UPSTREAM-LICENSE.txt); он не задаёт лицензию остального
репозитория. Лицензию проекта необходимо определить до release.
