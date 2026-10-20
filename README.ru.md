# Portfolio Deployment

[English version](README.md)

Централизованная платформа Docker/GitOps-деплоя независимо собираемых портфолио-
приложений на одном Ubuntu VPS. **Текущее состояние: Stage 01 — Project
Bootstrap.** В этой ветке подготовлены стандарты, архитектура, документация и
локальные offline-проверки. Платформа деплоя имеет статус **Planned**: контроллер,
реестр, gateway и приложения этим репозиторием пока не развёртываются.

## Назначение и архитектура

Целевая цепочка: CI приложения → immutable-образ в GHCR → утверждённое desired
state в Git → reconciliation → Docker Compose → проверка готовности → маршрутизация
Caddy. Все runtime-компоненты этой цепочки здесь имеют статус **Planned**.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  отвечает за Ubuntu, SSH/пользователей, Docker Engine и безопасность хоста.
- **Portfolio Deployment** будет отвечать за регистрацию сервисов, версионируемое
  desired state, выбор digest, выполнение Compose, общую proxy-сеть, домены/HTTPS,
  проверки готовности и историю результатов деплоя.
- Прикладные репозитории, например
  [project-router](https://github.com/DanilFaritovich/project-router), отвечают за
  исходный код, тесты/CI, сборку и публикацию образов в GHCR и предоставляют
  deployment contract. Этот Bootstrap не подтверждает их готовность к деплою.

Границы ответственности, потоки, доверие и ограничения восстановления описаны в
[ARCHITECTURE.md](ARCHITECTURE.md).

## Планируемые возможности и технологии

**Planned:** утверждаемый централизованный реестр; валидация манифестов сервисов;
обновления по immutable `ghcr.io/<owner>/<image>@sha256:<digest>`; детерминированный
план desired/actual state и no-op reconciliation; общий Caddy HTTPS gateway;
изоляция приватных зависимостей; ограниченные по времени health checks; адресные
обновления; сохранение последнего работоспособного состояния и результатов
восстановления.

Целевые технологии: Git, Docker Engine/Compose, GHCR и Caddy. Язык контроллера,
способ его запуска и формат журнала не выбраны. Python/PyYAML ниже используются
только для проверки репозитория. Контроллер, daemon, база данных и Kubernetes-стек
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
requirements-dev.txt           Закреплённая зависимость только для Bootstrap
Makefile                       Интерфейс fix / check
```

Runtime-директории и конфигурация появятся на соответствующих этапах реализации;
готовых к запуску Compose- и Caddy-файлов нет.

## Локальная разработка и проверки

Нужны Git, GNU Make, Python 3.10+ с `venv` и pip. Docker, доступ к VPS, домены и
production-учётные данные не нужны. Из корня репозитория:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make fix PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
```

Установка зависимостей может использовать сеть; `make fix` и `make check` — нет.
`fix` нормализует окончания строк, завершающий перевод строки и пробелы в конце
строк собственных файлов проекта, сохраняя vendored skills. `check` работает без
изменений файлов: проверяет формат текста, синтаксис YAML/дубликаты ключей,
метаданные standards lock, полный состав пакетов/frontmatter, локальные Markdown-
ссылки/якоря и совпадение структуры/roadmap README. Точность перевода требует
отдельного review: проверки не доказывают смысловую эквивалентность языков.
Внешние URL не запрашиваются.

GitHub Actions в Stage 01 не настроен; offline CI — **Planned** в Stage 08.
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
| Stage 01 | Project Bootstrap | Реализован в этой ветке; ожидает review |
| Stage 02 | Service Registry & Manifest Validation | Planned |
| Stage 03 | Desired State & Reconciliation Planning | Planned |
| Stage 04 | Caddy Gateway Foundation | Planned |
| Stage 05 | Docker Deployment Engine | Planned |
| Stage 06 | Health Checks, Rollback & Recovery | Planned |
| Stage 07 | Local E2E & Integration Testing | Planned |
| Stage 08 | GitHub Actions & Automation | Planned |
| Stage 09 | Production Integration & Release | Planned |

Перед Stage 02 нужно согласовать версионирование схемы/контракта, admission образов
и доменов, политику OCI digest/платформы, обязательные поля, негативные fixtures и
обоснование инструментов валидатора. Runtime контроллера, production-trigger/source,
хранилище секретов и детали журнала/восстановления требуют отдельных решений позже.

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
