# Portfolio Deployment

[English version](README.md)

Централизованная платформа Docker/GitOps-деплоя независимо собираемых портфолио-
приложений на одном Ubuntu VPS. **Текущее состояние: Stage 03 — Desired State
& Reconciliation Planning.** Реализованы валидация реестра с admission, глубоко
неизменяемые desired/observed модели и детерминированное офлайн-планирование. Runtime-деплой — **Planned**:
контроллер, gateway и приложения здесь не развёрнуты. Реальный реестр и allowlist
пусты до подтверждения артефактов и review.

## Назначение и архитектура

Целевая цепочка: CI приложения → immutable-образ в GHCR → утверждённое desired
state в Git → reconciliation → Docker Compose → проверка готовности → маршрутизация
Caddy. Все runtime-компоненты этой цепочки здесь имеют статус **Planned**.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  отвечает за Ubuntu, SSH/пользователей, Docker Engine и безопасность хоста.
- **Portfolio Deployment** реализует контракт реестра, офлайн-admission и
  планирование desired state по явно заданному snapshot; следующие этапы добавят
  проверку артефактов,
  выполнение Compose, общую proxy-сеть, домены/HTTPS,
  проверки готовности и историю результатов деплоя.
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

**Planned:** обновления по immutable `ghcr.io/<owner>/<image>@sha256:<digest>`; обнаружение
реального состояния и runtime reconciliation; общий Caddy HTTPS gateway;
изоляция приватных зависимостей; ограниченные по времени health checks; адресные
обновления; сохранение последнего работоспособного состояния и результатов
восстановления.

Целевые технологии: Git, Docker Engine/Compose, GHCR и Caddy. Язык контроллера,
способ его запуска и формат журнала не выбраны. Python, PyYAML, jsonschema и pytest
используются только для офлайн-валидации и планирования. Контроллер, daemon, база данных и Kubernetes-стек
не выбраны и не реализованы.

## Структура репозитория

```text
.agents/skills/                 Локальные skills, references, upstream MIT notice
.codex-standards.lock.yaml      Ревизия upstream, профиль и состав пакетов
.editorconfig / .gitignore     Формат текста и исключения локальных файлов/секретов
AGENTS.md                      Workflow Codex и границы разрешённых операций
ARCHITECTURE.md                 Планируемая runtime-архитектура
README.md / README.ru.md        Английская / русская точки входа
docs/roadmap.md                 Предварительные этапы и границы решений
scripts/check_bootstrap.py      Offline-проверки и безопасная нормализация текста
scripts/validate_registry.py    Ограниченный офлайн-валидатор и API загрузки
scripts/planning_inputs.py      Валидация неизменяемых desired/observed моделей
scripts/reconciliation.py       Чистое сравнение и неизменяемый план
scripts/plan_registry.py        Offline CLI с явно заданными входами
registry/applications/         Пустой реальный реестр; один manifest на приложение
policy/admission.yaml           Пустой allowlist для отдельного review
schemas/                       Версионированные закрытые схемы
tests/fixtures/                Синтетические примеры и недопустимый YAML
requirements-dev.txt           Закреплённые инструменты офлайн-валидации
Makefile                       Интерфейс fix / validate / check / plan
```

Генерация Compose и runtime-конфигурация отложены; готовых к запуску Compose- и
Caddy-файлов нет. Синтетические fixtures не являются production-манифестами.

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

Установка зависимостей может использовать сеть; все Make targets работают офлайн.
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

GitHub Actions до Stage 03 включительно не настроен; offline CI — **Planned** в Stage 08.
Targets `verify`, `ci` и deployment пока отсутствуют. Успешные проверки не
подтверждают работу Docker, HTTPS, reconciliation или rollback на сервере.

## Стандарты разработки и Git delivery

Начинай с [AGENTS.md](AGENTS.md). Установлен профиль
**docker-deployment-platform v1** из
[codex-development-standards](https://github.com/DanilFaritovich/codex-development-standards)
на `c98740e6cc66fa20da0c81c6f394dd9a2b03013a` (источник — `develop`).

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
| Stage 03 | Desired State & Reconciliation Planning | Implemented offline; review pending |
| Stage 04 | Caddy Gateway Foundation | Planned |
| Stage 05 | Docker Deployment Engine | Planned |
| Stage 06 | Health Checks, Rollback & Recovery | Planned |
| Stage 07 | Local E2E & Integration Testing | Planned |
| Stage 08 | GitHub Actions & Automation | Planned |
| Stage 09 | Production Integration & Release | Planned |

Stages 02–03 используют Python для офлайн-инструментов, не выбирая runtime
контроллера. Реальный ProjectRouter требует подтверждённого digest; production
hostname не выдумывается. Выполнение контроллера, production-trigger/source,
доставка секретов и журнал/восстановление требуют отдельных решений позже.

## Безопасность, ограничения и лицензирование

Bootstrap выполняется только локально и в GitHub: запрещены VPS/SSH, production
Ansible и контейнеры, активация Caddy, изменения DNS/доменов, production-секреты,
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
