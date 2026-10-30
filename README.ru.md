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
- Доставка по подписанной политике со свежими данными о сервере, адресными разрешениями и блокировкой хоста.
- Внутренние проверки готовности и независимая проверка публичного HTTPS и содержимого.
- Журнал развёртываний SQLite, последние работоспособные релизы и ограниченные откат и восстановление.

## Используемые технологии

| Область | Технологии |
| --- | --- |
| Движок развёртывания | Python CLI, dataclasses, PyYAML, jsonschema |
| Контейнеры и изоляция | Docker Engine, Docker Compose, приватные сети, named volumes |
| Маршрутизация и TLS | Caddy, генерация конфигурации, приватный admin socket |
| Состояние и координация | SQLite через sqlite3, файловые квитанции операций, Linux flock |
| CI и артефакты | GitHub Actions, GHCR, неизменяемые digest образов |
| Доверие к релизам и разрешения | GitHub OIDC/Sigstore attestations, подписи SSHSIG, подписанные политики |
| Тестирование | pytest, pytest-xdist в CI, изолированная Docker-приёмка, Multipass VM E2E |
| Разработка | Git, GNU Make, Python 3.10+; CI использует Python 3.12.12 |

## Ключевые инженерные решения

- **Общий движок для разных окружений.** Офлайн-моки, одноразовые VM и production-адаптеры
  используют общие планировщик, исполнитель и правила жизненного цикла.
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

Нужны Git, GNU Make, Python с `venv` и pip. Для локальных офлайн-проверок
Docker, доступ к серверу и production-учётные данные не требуются.

```sh
git clone https://github.com/DanilFaritovich/portfolio-deployment.git
cd portfolio-deployment
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make check PYTHON=.venv/bin/python
```

Просмотр Compose-конфигурации зарегистрированного приложения без развёртывания:

```sh
make compose-render PYTHON=.venv/bin/python APPLICATION=project-router GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa COMPOSE_FORMAT=json
```

SHA выше — синтетический пример; замените его полным SHA нужного коммита.
Для планирования с учётом состояния сервера также нужен явный snapshot наблюдений.
Подробности — в [контракте планирования](docs/reconciliation-planning.md) и
[руководстве по Compose-движку](docs/docker-deployment-engine.md).

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
подтверждением обновления. У релизов Manager отдельный путь доставки.

Первый rollout завершён; автоматические обновления приложения подтверждены
по 2026-10-30. Последний проверенный Manager CD завершился ошибкой на remote intake;
приёмка production-отката пока не подтверждена. Ссылки на запуски и границы
свидетельств — в [production-статусе](docs/production-status.md).

Подготовка хоста принадлежит
[portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
Production-установка и восстановление требуют отдельных процедур и полномочий оператора.

## Ограничения

- Рассчитан на один VPS; высокая доступность и отсутствие простоя не гарантируются.
- Текущая автоматическая доставка приложений поддерживает узкий контракт обновления
  образа ProjectRouter; другим приложениям нужны декларации и ревью допуска.
- Откат образа не отменяет миграции базы данных и не заменяет резервные копии.
- Офлайн-проверки не подтверждают текущее состояние Docker, DNS, TLS или сервера.

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
