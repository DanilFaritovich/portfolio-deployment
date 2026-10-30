# Portfolio Deployment

[English version](README.md)

Централизованная платформа Docker/GitOps-деплоя портфолио-приложений на одном
Ubuntu VPS. **Production-деплой на сервер завершён, включая первый ProjectRouter
rollout (подтверждение оператора от 2026-10-28).** Автоматические обновления
ProjectRouter подтверждены успешными production CD от 2026-10-29 и 2026-10-30.
Manager CD ранее проходил успешно; последний проверенный запуск от 2026-10-29
завершился ошибкой на этапе remote intake. Это отдельные пути доставки.

[Текущий статус и evidence](docs/production-status.md) содержат историю `main`,
ссылки на CI/release/CD и границы подтверждённых результатов. Ранние документы
Stage 01–09/C3/C4 описывают состояние на момент подготовки и не означают, что
сервер всё ещё ждёт первой установки. Stage 07 ранее прошёл 15/15 реальных VM E2E;
отдельный локальный smoke Stage 05 не запускался.

ProjectRouter зарегистрирован в [реестре](registry/applications/project-router.yaml)
и [admission policy](policy/admission.yaml). Его workflow после успешного CI для
push в `main` вызывает общий production CD с закреплённой версией Manager.
Автоматическое обновление проверяет immutable digest, подписанные release claims,
standing policy, свежее состояние сервера, readiness и публичный HTTPS/content.
Environment может требовать ручное approval.
[ProjectRouter CD 41065912995](https://github.com/DanilFaritovich/project-router/actions/runs/41065912995)
успешно завершился 2026-10-30, включая выполнение обновления приложения и шаг
с независимой проверкой HTTPS. Политика и проверки могут блокировать дальнейшую
доставку; production rollback drill этой проверкой пока не подтверждён.

Контракты и процедуры: [manifest v1](docs/manifest-v1.md),
[добавление приложения](docs/application-acceptance.md),
[production installation](docs/production-installation.md),
[execution/authorization](docs/production-execution.md) и
[operator SSHSIG](docs/operator-approval.md). Исторические
[ProjectRouter acceptance](docs/project-router-acceptance.md),
[Caddy acceptance](docs/caddy-runtime-acceptance.md) и
[C3 release review](docs/protected-main-release.md) сохраняют исходное evidence.
Новые production-операции требуют соответствующего разрешения или действующей
узкой подписанной policy; наличие работающего сервера не разрешает их автоматически.

## Назначение и архитектура

Рабочая схема: CI приложения → immutable-образ в GHCR → проверка release/policy
→ host-local executor → Docker Compose → readiness → Caddy и публичный HTTPS.
Registry и offline planner задают контракт; live adapters и CD проверяют факты
перед выполнением. Текущая production-интеграция использует тот же executor,
SQLite journal/LKG и host lock, что и offline/VM-проверки.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  отвечает за Ubuntu, SSH/пользователей, Docker Engine и безопасность хоста.
- **Portfolio Deployment** отвечает за admission, desired state, планирование,
  Compose, production adapters, Caddy, readiness, журнал и policy-gated CD.
- Прикладные репозитории, например
  [project-router](https://github.com/DanilFaritovich/project-router), отвечают за
  исходный код, тесты/CI, сборку и публикацию образов в GHCR и предоставляют
  deployment contract. Офлайн-валидация не подтверждает их готовность к деплою.

Границы ответственности, потоки, доверие и ограничения восстановления описаны в
[ARCHITECTURE.md](ARCHITECTURE.md).

## Возможности и технологии

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

Production executor, live adapters, Caddy и CD реализованы. **Planned:** дальнейшая
приёмка production rollback и расширение автоматизации за пределами текущего узкого
ProjectRouter image-only контракта. Zero-downtime не обещается.

Технологии: Git, Docker Engine/Compose, GHCR, Caddy и Python CLI с PyYAML/jsonschema.
Журнал использует SQLite стандартной библиотеки; сервер БД, daemon и Kubernetes
не используются. One-shot executor проверяет exact candidate, подпись, source,
свежие observations и одноразовый nonce под canonical host lock.

Manager владеет своей установкой, private storage, Caddy и сетями приложений;
Server Infrastructure — подготовкой и обслуживанием хоста. Installation, image
preparation, `first-bootstrap` и `first-project-router-rollout` — отдельные
операционные scopes. Они не запускаются повторно обычными offline-командами.
После первого rollout обновление образа идёт через CD; bootstrap и recovery
сохраняют отдельные разрешения. См. [статус и delivery flow](docs/production-status.md).

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
scripts/production/            Read-only диагностика сервера и приватные отчёты
scripts/production_integration/ Аутентифицированные observations, execution и recovery
scripts/cd/                    Доставка Manager и приложений с проверкой policy
installation/                  Sealed launchers, CD capsule и настройка оператором
.github/workflows/             Offline CI, изолированная приёмка, release и CD
registry/applications/         ProjectRouter зарегистрирован; один manifest на приложение
policy/admission.yaml           Допуск ProjectRouter; требуется security review
schemas/                       Версионированные закрытые схемы
tests/fixtures/                Синтетические примеры и недопустимый YAML
requirements-dev.txt           Закреплённые инструменты офлайн-валидации
requirements-offline-ci.txt    Дополнительные закреплённые инструменты parallel pytest для CI
Makefile                       Интерфейс fix / validate / check / plan / gateway-*
```

Compose и production Caddy реализованы; runtime-конфигурация остаётся server-side
state. Синтетические fixtures не являются production-манифестами.

## Локальная разработка и проверки

Нужны Git, GNU Make, Python 3.10+ с `venv` и pip (CI использует Python 3.12.12).
Docker, доступ к VPS, домены и
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

Замени APP на admitted application, сейчас `project-router`. Машинный JSON preview
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
fresh discovery. Этот низкоуровневый результат сам по себе не подтверждает readiness или release success.

Stage 08 добавляет offline GitHub Actions для PR в `develop` и `main`: закреплённые
Python/tools, pip cache, минимальные read permissions, `make check` и
`make workflow-check` (actionlint). Отдельный `make ci` не нужен. Эти offline PR
checks не запускают VM/E2E, Docker, публикацию образов или деплой; release/CD
workflows выполняются отдельно. Required check
**Offline CI / Offline checks** нужно вручную включить в branch rules GitHub.
CI устанавливает `requirements-offline-ci.txt` и запускает pytest в два worker;
локальная установка выше использует последовательный запуск тестов. Отдельные
integration workflows могут запускать приёмку в одноразовом Docker-окружении;
они не входят в offline `make check`.
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
выполняются отдельно: автоматические merge и tags/releases не разрешены;
production-операции следуют своей policy.

## План этапов

Предварительный [roadmap](docs/roadmap.md) может уточняться после architecture review:

| Этап | Область | Статус |
| --- | --- | --- |
| Stage 01 | Project Bootstrap | Реализован; PR #1 смержен |
| Stage 02 | Service Registry & Manifest Validation | Implemented offline; PR #2 merged |
| Stage 03 | Desired State & Reconciliation Planning | Implemented offline; PR #3 merged |
| Stage 04 | Caddy Gateway Foundation | Implemented offline; PR #4 merged into develop |
| Stage 05 | Docker Deployment Engine | Implemented foundation; PR #5 merged; smoke not-performed; foundation boundary; production через Stage 09 |
| Stage 06 | Readiness, Journal & Recovery | Implemented offline; PR #6 merged; SQLite/LKG and analysis-only recovery |
| Stage 07 | Local E2E & Integration Testing | Реализован; реальный локальный VM E2E пройден, 15/15; историческая локальная приёмка |
| Stage 08 | GitHub Actions & Automation | Реализован офлайн; ручная процедура завершения VM ожидает live review |
| Stage 09 | Production Integration & Release | Первый rollout завершён; автоматические обновления ProjectRouter подтверждены по 2026-10-30; последний проверенный Manager CD заблокирован на remote intake; приёмка live rollback ожидается |

Stage 05 выбирает Python CLI без изменений контрактов Stage 02–04. Stage C2
зарегистрировал ProjectRouter с независимо проверенным digest. Stage 06 отвечает за readiness,
rollback, journal и recovery; Stage 07 расширяет изолированные E2E. Production adapters и policy-gated CD реализованы поверх того же engine.

SQLite durability зависит от надёжного storage/fsync. Журнал не заменяет live state
или backup. Незавершённые runs блокируют новые операции; recovery требует своего
подписанного разрешения и не повторяет mutations автоматически.
См. [ограничения и API Stage 06](docs/readiness-recovery.md).

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
реализация также прошла **779 offline tests**. Production deployment в рамках
Stage 07 не выполнялся. Позднее серверный деплой
завершён: см. [текущий production-статус](docs/production-status.md).
Предыдущие неудачные попытки и evidence их закрытия
сохранены; journals намеренных crash/unknown сценариев остаются analysis-only,
без replay. См. [результаты приёмки и историю](docs/local-e2e.md#verified-local-acceptance).

## Безопасность, ограничения и лицензирование

Обычная разработка и PR-проверки остаются офлайн. Disposable VM E2E требует
отдельных opt-in. Новые SSH/production-операции, изменения policy/ключей, bootstrap
и recovery требуют применимого разрешения либо действующей узкой подписанной
policy. Изменения других репозиториев, merge и release tags не разрешаются
завершённым деплоем. PR-проверки не должны
получать production-секреты. Git хранит ссылки на конфигурацию, без реальных
учётных данных. `.gitignore` — только дополнительная защита; проверяй staged-файлы.

Целевая однохостовая архитектура не гарантирует высокую доступность и отсутствие
простоя. Общая proxy-сеть требует оценки доступа между приложениями. Rollback
образа не отменяет миграции базы и не заменяет резервные копии; постоянные данные
нельзя удалять автоматически.

Код проекта распространяется по [лицензии MIT](LICENSE), copyright 2026
Danil Miftakhov. Для vendored standards сохранён upstream
[MIT notice](.agents/skills/UPSTREAM-LICENSE.txt); сторонние notices продолжают действовать.
