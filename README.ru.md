# Portfolio Deployment

[English](README.md) · [Русский](README.ru.md)

Portfolio Deployment — платформа Данила Мифтахова для развёртывания Docker-приложений
на одном Ubuntu VPS. Она связывает подписанные релизы в GitHub и GHCR с управляемым
обновлением контейнеров, проверками готовности, HTTPS-маршрутизацией и восстановлением.

**[Портфолио автора → danil-miftakhov.dev](https://danil-miftakhov.dev/ru/)**

[![CI](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml)
[![Manager CD](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/cd-deploy.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/cd-deploy.yml)
[![ProjectRouter CD](https://github.com/DanilFaritovich/project-router/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/project-router/actions/workflows/deploy.yml)
[![Python](https://img.shields.io/badge/Python-CLI-3776AB?logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://www.docker.com/)
[![Caddy](https://img.shields.io/badge/Caddy-HTTPS-1F88C0)](https://caddyserver.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Возможности

- Манифесты приложений в Git с проверкой схем и отдельной политикой допуска.
- Офлайн-планы развёртывания и просмотр Compose/Caddy-конфигурации до изменений сервера.
- Неизменяемые образы GHCR с проверкой подписанных свидетельств релиза и исходного кода.
- Подписанные установочные bundles Manager и собственная публикация доверенного образа Caddy.
- Доставка по подписанной политике со свежими данными о сервере, адресными разрешениями и блокировкой хоста.
- Общий HTTPS-шлюз Caddy, внутренние проверки готовности и независимая проверка
  публичного HTTPS и содержимого релизов приложений.
- Журнал развёртываний SQLite, последние работоспособные релизы и ограниченные откат и восстановление.

## Используемые технологии

| Область | Технологии |
| --- | --- |
| Движок развёртывания | Python CLI, dataclasses, PyYAML, jsonschema |
| Контейнеры и изоляция | Docker Engine, Docker Compose, приватные сети, named volumes |
| Маршрутизация и TLS | Caddy, генерация конфигурации, приватный admin socket |
| Состояние и координация | SQLite через sqlite3, файловые квитанции операций, Linux flock |
| CI и артефакты | GitHub Actions, workflow artifacts, GHCR, неизменяемые digest образов |
| Доверие к релизам и разрешения | GitHub OIDC/Sigstore attestations, подписи SSHSIG, подписанные политики |
| Тестирование | pytest, pytest-xdist в CI, изолированная Docker-приёмка, Multipass VM E2E |
| Разработка | Git, GNU Make, Python 3.10+; CI использует Python 3.12.12 |

## Ключевые инженерные решения

- **Общий движок для разных окружений.** Офлайн-моки, одноразовые VM и production-адаптеры
  используют общие планировщик, исполнитель и правила жизненного цикла. Операции
  выполняются отдельными запусками CLI.
- **Проверка фактов до изменений.** Желаемая конфигурация в Git не подтверждает состояние
  сервера: устаревшие данные, конфликты владения или отсутствие подписей блокируют выполнение.
- **Восстановление после сбоев.** Намерение записывается до изменений. Незавершённые операции
  блокируют доставку до восстановления с отдельным разрешением.
- **Разделение ответственности.** Прикладные репозитории собирают образы; этот проект
  управляет доставкой и маршрутами; подготовка сервера вынесена в отдельный репозиторий.

Проект показывает автоматизацию инфраструктуры, проектирование систем на Python,
проверку цепочки поставки ПО и тестирование сбоев при развёртывании.
Модули и границы доверия описаны в [ARCHITECTURE.md](ARCHITECTURE.md).

## Быстрый старт

Это CLI-инструмент развёртывания: у него нет веб-интерфейса на localhost.
Начните с офлайн-сценария ниже: он проверяет реестр и генерирует план/конфигурацию
без Docker и доступа к серверу. Реальное развёртывание описано в разделе
[Запуск развёртываний](#запуск-развёртываний).

### 1. Установите локальные инструменты

Нужны Python **3.10+** с `venv` и pip, Git и GNU Make. CI использует Python 3.12.12.
Для рабочей машины с Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install --yes git make python3 python3-venv python3-pip
```

### 2. Склонируйте проект и подготовьте окружение

```sh
git clone https://github.com/DanilFaritovich/portfolio-deployment.git
cd portfolio-deployment
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make validate PYTHON=.venv/bin/python
```

Следующие команды выполняйте из каталога репозитория. Активация virtualenv
не нужна: команды явно используют `.venv/bin/python`. Валидация должна вывести
`OK: registry validated` для включённого манифеста ProjectRouter. Если отсутствуют
`venv` или `make`, сначала выполните шаг 1.

### 3. Получите первый план развёртывания

```sh
make plan PYTHON=.venv/bin/python OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION="$(git rev-parse HEAD)" PLAN_FORMAT=text
```

Включённый snapshot описывает синтетическое пустое окружение. Для текущего реестра
план покажет `changes`, создание приложения/компонента и изменение маршрута,
с `executed=false`. Ничего не развёртывается. Для собственного анализа замените
`OBSERVED` валидным snapshot в формате [контракта планирования](docs/reconciliation-planning.md);
этот пример не обнаруживает состояние вашего компьютера или production-сервера.

### 4. Посмотрите сгенерированную Compose-конфигурацию

```sh
make compose-render PYTHON=.venv/bin/python APPLICATION=project-router GIT_REVISION="$(git rev-parse HEAD)" COMPOSE_FORMAT=yaml
```

В терминале появится Compose-документ с сервисом `web`, закреплённым GHCR-образом,
настройками безопасности и сетями. Это preview, а не самостоятельная конфигурация
для `docker compose up`: proxy-сеть и runtime-предпосылки создаются управляемым
развёртыванием. Подробнее — в [руководстве по Compose-движку](docs/docker-deployment-engine.md).
Полный офлайн-набор проверок запускается командой `make check PYTHON=.venv/bin/python`
и может занять несколько минут.

## Качество и тестирование

```sh
make fix PYTHON=.venv/bin/python
make validate PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
```

`fix` нормализует текст проекта; `validate` проверяет реестр и политику допуска;
`check` проверяет контракты репозитория и запускает офлайн-тесты pytest. В CI
добавлены закреплённый actionlint и два worker pytest. Отдельная Docker/VM-приёмка
требует явного согласия и никогда не запускается через `make check`.

Зафиксированная VM-приёмка прошла **15/15 сценариев**. Исторические результаты
и требования к fixtures — в [руководстве по локальным E2E](docs/local-e2e.md).

## Production-доставка

[ProjectRouter](https://github.com/DanilFaritovich/project-router) — первое
зарегистрированное приложение: портфолио по ссылке выше. Успешный CI после push
в main передаёт подписанный неизменяемый релиз общему CD workflow. Исполнитель
проверяет политику, исходный код, состояние сервера и готовность приложения перед
подтверждением обновления. У релизов Manager отдельный путь доставки: проверка
установленного bundle и работоспособности с сохранением предыдущей версии для отката.

Первый rollout завершён; автоматические обновления приложения и Manager
Production CD подтверждены по 2026-10-30. Последняя попытка Manager прошла
с результатом `no-op`: нужная версия уже установлена и прошла проверку
работоспособности, поэтому менять установку не потребовалось. Приёмка
production-отката пока не подтверждена. Ссылки на запуски и границы
свидетельств — в [production-статусе](docs/production-status.md).

Подготовка хоста принадлежит
[portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
Production-установка и восстановление требуют отдельных процедур и полномочий оператора.

## Запуск развёртываний

### Одноразовая локальная VM

После локальной установки выше интерактивный E2E-runner позволяет проверить реальные
развёртывания в одноразовых Ubuntu VM. Нужны Linux amd64 с KVM, работающий и
аутентифицированный Multipass и минимум 20 GiB свободного места на файловой системе
домашнего каталога. Первая подготовка fixtures также может потребовать права
публикации в GHCR. По умолчанию используется владелец GitHub `origin`; при
подготовке собственных образов задайте в `E2E_FIXTURE_OWNER` владельца, от имени
которого вы можете публиковать пакеты.

```sh
make e2e PYTHON=.venv/bin/python
```

Runner запрашивает отдельные подтверждения перед созданием VM, подготовкой образов
и операциями Docker/Caddy. Используется Docker внутри VM, а не daemon хоста.
Перед запуском прочитайте [требования и процедуру E2E](docs/local-e2e.md#prerequisites-and-topology);
сохраните созданные квитанции и отчёты.

### Собственный VPS

Сейчас платформа адаптирована к инфраструктуре автора и не имеет универсального
установщика. Для другого сервера недостаточно клонирования и `.env`: идентификаторы
репозиториев/домена, доверенные workflow и привязки к хосту требуют проверенной
адаптации кода и политики.

Порядок подготовки:

1. Подготовьте Ubuntu 24.04 amd64, SSH-доступ, Docker/Compose и DNS через
   [проект серверной инфраструктуры](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
2. Проверьте манифест/допуск приложения и требования к подписям исходного кода
   по [руководству подключения приложений](docs/application-acceptance.md).
3. Подготовьте проверенные артефакты Manager/Caddy, фактические host bindings,
   ключи оператора, подписанную политику и CD capsule; настройте защищённые GitHub
   Environments. См. [установку](docs/production-installation.md) и
   [текущую архитектуру CD](ARCHITECTURE.md#minimal-continuous-delivery--production-deployed).
4. Выполните отдельно разрешённые установку, bootstrap шлюза, подготовку образа
   и первый rollout через [bootstrap workflow](.github/workflows/production-bootstrap.yml).
5. Проверьте готовность и публичный HTTPS, затем используйте CD подписанных релизов
   для обновлений. См. [схему доставки](docs/production-status.md#what-happens-after-a-projectrouter-update).

Production-руководства включают исторические параметры хоста и этапы подготовки;
это справочные процедуры, а не готовые разрешения для нового VPS. Переносимая
установка без участия оператора пока не реализована.

## Ограничения

- Рассчитан на один VPS; высокая доступность и отсутствие простоя не гарантируются.
- Текущая автоматическая доставка приложений поддерживает узкий контракт обновления
  образа ProjectRouter; другим приложениям нужны приёмка, ревью политики и явная
  интеграция с CD.
- Откат образа не отменяет миграции базы данных и не заменяет резервные копии.
- Офлайн-проверки не подтверждают текущее состояние Docker, DNS, TLS или сервера.

## Структура репозитория

```text
registry/ + policy/ + schemas/   Контракты приложений и допуск
scripts/                       Офлайн-планы, Compose, шлюз и жизненный цикл
scripts/production/            Read-only диагностика хоста
scripts/production_integration/ Аутентифицированное выполнение и восстановление
scripts/cd/ + installation/    Доставка, защищённый runtime и настройка оператором
tests/ + scripts/e2e/ + e2e/    Офлайн-тесты и изолированные integration harnesses
.github/workflows/             CI, приёмка, подписанная публикация и CD
```

## Документация

- [Архитектура](ARCHITECTURE.md): компоненты, потоки и границы безопасности.
- [Добавление приложения](docs/application-acceptance.md) и [контракт манифеста](docs/manifest-v1.md).
- [Production-статус](docs/production-status.md): проверенные запуски и свидетельства доставки.
- [Установка](docs/production-installation.md), [выполнение операций](docs/production-execution.md) и [разрешения оператора](docs/operator-approval.md).
- [Готовность и восстановление](docs/readiness-recovery.md), [локальные E2E](docs/local-e2e.md) и [этапы/история](docs/roadmap.md).
- [Правила участников и агентов](AGENTS.md); установленные стандарты перечислены в [standards lock](.codex-standards.lock.yaml).

## Лицензия

Код и техническая документация используют [MIT License](LICENSE), copyright 2026
Danil Miftakhov. Для включённых стандартов разработки сохранён
[upstream MIT notice](.agents/skills/UPSTREAM-LICENSE.txt).
