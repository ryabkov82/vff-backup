
# Роль `backup`

Роль **устанавливает и настраивает агента резервного копирования** на узлах,
используя `restic` и хранилище MinIO (S3-совместимое).

---

## 🧩 Назначение

Роль отвечает за **создание, ротацию и мониторинг бэкапов** сервисов `shm`, `marzban` и `remnawave` в проекте `vff-backup`.

Каждый узел получает собственный systemd-сервис и таймер (`backup@<job>.service/.timer`),
которые регулярно выполняют резервное копирование данных и/или дампы БД
в S3-хранилище `vff-backups`.

---

## ⚙️ Основные задачи

- установка и настройка агента `restic`;
- генерация `restic.env` с параметрами доступа к S3;
- создание systemd-юнитов `backup@.service` и `backup@.timer`;
- генерация shell-скрипта `/usr/local/bin/vff-backup.sh`;
- настройка периодичности и случайных задержек (`OnCalendar`, `RandomizedDelaySec`);
- автоматическое создание docker-override-файлов для дампов БД;
- формирование textfile-метрик для `node_exporter` (опционально);
- локальная очистка старых SQL-дампов перед запуском restic;
- поддержка нескольких «джобов» (backup_jobs) на одном узле.

---

## 🔧 Основные переменные

| Переменная | Описание | Пример |
|-------------|-----------|--------|
| `backup_s3_endpoint` | S3-эндпоинт MinIO | `https://s3.vpn-for-friends.com` |
| `backup_s3_bucket` | Бакет в S3 | `vff-backups` |
| `backup_s3_region` | Регион | `us-east-1` |
| `backup_restic_repository` | Полный путь к репозиторию | `s3:{{ backup_s3_endpoint }}/{{ backup_s3_bucket }}/shm/{{ inventory_hostname }}` |
| `backup_restic_password` | Пароль для restic | из `~/.ansible/secrets/restic/<job>` |
| `backup_aws_access_key_id` | Ключ доступа к S3 | генерируется ролью `minio` |
| `backup_aws_secret_access_key` | Секретный ключ к S3 | генерируется ролью `minio` |
| `backup_env_dir` | Каталог окружения | `/etc/vff-backup` |
| `backup_env_file` | Файл с переменными | `/etc/vff-backup/restic.env` |
| `backup_bin_path` | Скрипт бэкапа | `/usr/local/bin/vff-backup.sh` |
| `backup_node_exporter_textfile_dirs` | Каталоги с метриками | `["/var/lib/node_exporter/textfile_collector"]` |
| `backup_enable_metrics` | Запись метрик включена | `true` |
| `backup_forget_policy` | Политика retention | `{keep_last:7, keep_daily:7, keep_weekly:5, keep_monthly:6}` |
| `backup_dump_keep_count` | Кол-во локальных SQL-дампов, хранимых на узле | `7` |
| `backup_dump_keep_days` | Максимальный срок хранения дампов (если `count=0`) | `30` |

---

## 🗂️ Пример описания джоба

Пример из `ansible/group_vars/shm.yml`:

```yaml
backup_jobs_map:
  shm:
    name: shm
    compose_dir: /opt/shm
    paths:
      - /var/backups/db
      - /opt/shm
    containers: []
    db_dump:
      enabled: true
      dump_dir: /var/backups/db
      container: mysql
      command: >
        /bin/bash -lc 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysqldump -u root
        --single-transaction --routines --triggers --events --hex-blob --quick
        --databases shm | gzip -c > "$DUMP_OUT"'
```

Пример для `marzban`:

```yaml
backup_jobs_map:
  marzban:
    name: marzban
    compose_dir: /opt/marzban
    paths:
      - /var/lib/marzban
      - /opt/marzban/.env
      - /opt/marzban/docker-compose.yml
    containers: []
    db_dump:
      enabled: false
```

Пример для `remnawave` (PostgreSQL + конфиги docker-compose):

```yaml
backup_jobs_map:
  remnawave:
    name: remnawave
    compose_dir: /opt/remnawave
    paths:
      - "/var/backups/db"
      - "/opt/remnawave/.env"
      - "/opt/remnawave/docker-compose.yml"
    containers: []
    db_dump:
      enabled: true
      dump_dir: /var/backups/db
      container: remnawave-db
      command: >
        PGPASSWORD="${POSTGRES_PASSWORD}" pg_dumpall --clean --if-exists -U "${POSTGRES_USER:-postgres}" |
        gzip -c > "$DUMP_OUT"
```

---

## 🔄 Периодичность

Задаётся таймером `backup@<job>.timer`:

```ini
[Timer]
OnCalendar=*-*-* 03:15:00
RandomizedDelaySec=600s
Persistent=true
```

Можно переопределить через переменные:
```yaml
backup_timer_oncalendar: "*-*-* 03:15:00"
backup_timer_randomized_delay: "600s"
```

---

## 📈 Метрики Prometheus (опционально)

Если `backup_enable_metrics: true`, в каталоге `textfile_collector` создаются метрики:

```
backup_last_run_timestamp_seconds{backup_job="shm"} 1739491210
backup_last_duration_seconds{backup_job="shm"} 5
backup_last_status{backup_job="shm"} 0
backup_last_size_bytes{backup_job="shm"} 246751
```

`backup_last_status` пишется **один раз** в `EXIT` trap по фактическому коду выхода процесса: `0` если job завершился успешно, `1` при любом ненулевом коде. Ошибка не перезаписывается последующим успешным trap. Тот же trap один раз снимает pause с контейнеров, которые в этом запуске удалось поставить на pause. Ошибка одного `unpause` не останавливает остальные. Если job уже был non-zero, cleanup не делает его успешным. Если job был успешен, но хотя бы один `unpause` не удался, итоговый код и `backup_last_status` становятся ошибочными.

---

## 🚀 Пример запуска

Через `make`:
```bash
make backup LIMIT=ru-msk-1
```

Вручную:
```bash
ansible-playbook -i ansible/hosts.ini ansible/playbooks/backup.yml -l ru-msk-1
```

---

## ⚙️ Ручной запуск и просмотр логов

```bash
sudo systemctl start backup@shm.service
sudo journalctl -u backup@shm.service -n 100 --no-pager
```

---

## 🧰 Проверка и восстановление

Посмотреть список снапшотов:
```bash
set -a; source /etc/vff-backup/restic.env; set +a
restic snapshots --tag shm
```

Посмотреть содержимое последнего снапшота:
```bash
SNAP=$(restic snapshots --json --tag shm | jq -r '.[-1].short_id')
restic ls "$SNAP" | head -50
```

Восстановить в тестовый каталог:
```bash
RESTORE_DIR=$(mktemp -d /tmp/restore-shm-XXXXXX)
restic restore "$SNAP" --target "$RESTORE_DIR"
```

---

## 🧹 Очистка старых дампов

После **успешного** создания и проверки нового SQL-дампа `vff-backup.sh` оставляет
последние файлы `*.sql.gz` в `dump_dir` (по `backup_dump_keep_count`, сейчас 7,
или по `backup_dump_keep_days`, если count равен 0).

Если новый дамп не создан или не прошёл проверку, локальная ротация **не**
запускается: последний рабочий дамп не удаляется.

## Обязательный дамп БД

При `db_dump.enabled: true` новый дамп — предусловие `restic backup`.
Job завершается с ненулевым кодом, метрика статуса становится `1`, а
`restic backup` / `restic forget` не запускаются, если:

- нет каталога `compose_dir`;
- указанный DB service/container отсутствует или список сервисов compose получить нельзя;
- команда дампа завершилась с ошибкой (внутри команды включён `pipefail`, поэтому сбой `mysqldump` / `pg_dump` не маскируется успешным `gzip`);
- в **этом** запуске не появился новый `*.sql.gz` (изменение уже существовавшего файла не считается);
- новый файл пустой (`size == 0`) или не проходит `gzip -t`.

Перед командой скрипт выбирает ещё не существующий путь и передаёт его как `DUMP_OUT` (дата с наносекундами, PID и случайный суффикс). Команда дампа должна писать в `"$DUMP_OUT"`, а не в фиксированное имя. Оболочка команды запускается с `noclobber`: перенаправление `>` не открывает уже существующий файл на перезапись, поэтому неуспешный run не затирает прежний `*.sql.gz`.

Наличие старого `*.sql.gz` само по себе backup успешным не делает.
Неудачный файл, созданный текущим запуском, удаляется; файлы, которые уже были до запуска, error cleanup не изменяет и не удаляет.

Политика `restic forget` (`keep_last` / `keep_daily` / `keep_weekly` / `keep_monthly`) не меняется и применяется только после успешного `restic backup`. Если backup завершился с ошибкой, `restic forget --prune` не запускается.

## Состав DR-бэкапа SHM

Snapshot SHM включает:

- `/var/backups/db` — согласованный `mysqldump` базы `shm`;
- `/opt/shm` целиком (`.env`, compose, `pay_systems`, `template-backups`, `mysql/conf.d` и любые новые локальные файлы).

`/opt/shm/mysql` исключать не нужно. Это не datadir: на хосте там только
`mysql/conf.d` (на `ru-msk-1` — `memory.cnf` с лимитами InnoDB), и compose
монтирует его в `/etc/mysql/conf.d`. Файлы данных MySQL лежат в named volume
`mysql-data` (`/var/lib/mysql`), вне `/opt/shm`; их консистентная копия — SQL-дамп.
Named volume `shm-data` тоже вне `/opt/shm`. Платёжные интеграции примонтированы
из `./pay_systems` и поэтому попадают в backup вместе с каталогом.

---

## 🧩 Связанные роли

- [`minio`](docs/minio-role.md) — создаёт пользователей и политики доступа;
- [`nginx`](docs/backup_nginx-role.md) — публикует веб-доступ к S3 и консоли MinIO.

---

## 🪣 Хранилище

Каждая джоба сохраняет данные в свой префикс бакета:
```
vff-backups/
├── shm/<host>/
├── marzban/<host>/
└── remnawave/<host>/
```

---

## 🔐 Секреты

Секреты для backup и DR лежат только на контроллере Ansible, не в git и не на восстанавливаемом хосте:

- пароль репозитория Restic: `~/.ansible/secrets/restic/<service>` (`shm`, `remnawave`, `marzban`);
- секрет пользователя MinIO: `~/.ansible/secrets/minio/<minio-user>` (`shm-user`, `remnawave-user`, `marzban-user`).

Роль автоматически создаёт недостающие пароли при первом запуске. Значения секретов в репозиторий не записываются.

---

## 🧩 Пример: добавление нового джоба

1. Добавить описание в `group_vars/<service>.yml`;
2. Запустить:
   ```bash
   make backup LIMIT=<host>
   ```
3. Проверить:
   ```bash
   sudo systemctl status backup@<job>.service
   ```

---
