# YAPF_Farm - Автономная фабрика ПФ и нагула профилей Яндекса

Современная автономная система прогрева браузерных профилей (Warming) и накрутки поведенческих факторов (ПФ) в поисковой системе Яндекс на базе Playwright Chromium с имитацией моторики человека, обходом антифрод-систем, поддержкой мобильных отпечатков и мультипрокси.

---

## 🚀 Быстрый старт и развертывание

### Вариант 1. Развертывание на Windows Server (2019 / 2022 / Windows 10/11)
Запустите PowerShell от имени Администратора и выполните:
```powershell
cd C:\YAPF_Farm
powershell -ExecutionPolicy Bypass -File deploy_windows.ps1
```
*Скрипт автоматически:*
1. Проверит и установит Python 3.11.
2. Установит все зависимости (`requirements.txt`).
3. Скачает браузерные движки Playwright Chromium и пропишет системные пути.
4. Настроит правила брандмауэра (порты 80 и 8080).
5. Создаст задачу Windows Task Scheduler (`YAPF_Farm`) для круглосуточной непрерывной работы при перезагрузках сервера.

---

### Вариант 2. Развертывание на Linux (Ubuntu 22.04 / 24.04 / Debian)
Выполните под `root`:
```bash
cd yapf_farm
chmod +x deploy_linux.sh
sudo ./deploy_linux.sh
```
*Скрипт автоматически:*
1. Установит системные пакеты `python3`, `venv`, `git`, `curl`.
2. Создаст виртуальное окружение `.venv` и установит зависимости.
3. Скачает Playwright со всеми системными библиотеками (`--with-deps`).
4. Настроит и запустит systemd-сервис `yapf-farm.service` с автоперезапуском при сбоях.

---

### Вариант 3. Локальный запуск (macOS / Linux / Windows)
- **macOS / Linux**:
  ```bash
  chmod +x start_local.sh
  ./start_local.sh
  ```
- **Windows**:
  Дважды кликните `start_local.bat` или выполните:
  ```cmd
  python run.py
  ```

---

## 🔄 Автоматическое обновление из GitHub

Приложение обновляется **полностью автоматически** перед каждым запуском (`run.py`):
1. **Автоматически при старте**: при каждом запуске службы или перезагрузке сервера `run.py` сам связывается с репозиторием GitHub (`https://github.com/tonypulseone-dot/yapf_farm.git`), проверяет наличие свежих коммитов и скачивает их.
2. **Автоустановка зависимостей**: если обновился `requirements.txt`, необходимые библиотеки сразу автоматически доустанавливаются.
3. **Ручной запуск не требуется**: `update_windows.bat` и `update_linux.sh` сохранены как резервный вариант, но вся работа происходит полностью автономно.

---

## 🔐 Доступ к панели управления

- **URL панели**: `http://IP_СЕРВЕРА:8080` (или `http://localhost:8080`)
- **Логин**: `admin`
- **Пароль**: `pre12345`

---

## 📁 Структура проекта

```text
yapf_farm/
├── app/
│   ├── engine/
│   │   ├── captcha.py         # Решение SmartCaptcha через API-сервисы
│   │   ├── fingerprints.py    # Генератор мобильных и десктопных отпечатков
│   │   ├── human.py           # Кривые Безье, человеческий скроллинг и задержки
│   │   ├── logger.py          # Real-time кольцевой буфер терминала
│   │   ├── orchestrator.py    # Диспетчер параллельных браузерных потоков
│   │   ├── promo.py           # Исполнитель продвижения по ключам и доменам
│   │   ├── proxy.py           # Ротация и биндинг прокси к профилям
│   │   ├── telegram.py        # Telegram-бот, алерты и интерактивное управление
│   │   └── warmer.py          # Модуль нагула трастовых кук и серфинга
│   ├── database.py            # SQLite база, настройки, проекты, ключевики
│   └── main.py                # FastAPI сервер, REST API, WebSocket/Polling
├── static/                    # CSS (Tailwind), Fonts (FontAwesome woff2 DataURI), JS
├── templates/
│   ├── index.html             # Главная панель управления (Alpine.js + Tailwind)
│   └── login.html             # Страница авторизации
├── deploy_windows.ps1         # 1-click автоустановщик Windows
├── update_windows.bat         # 1-click автообновлятор Windows
├── deploy_linux.sh            # 1-click автоустановщик Linux
├── update_linux.sh            # 1-click автообновлятор Linux
├── start_local.sh             # Локальный запуск macOS/Linux
├── start_local.bat            # Локальный запуск Windows
├── requirements.txt           # Python-зависимости
├── run.py                     # Точка входа приложения
└── README.md                  # Документация
```
