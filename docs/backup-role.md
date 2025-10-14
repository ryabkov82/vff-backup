
# Роль `backup`

Роль **устанавливает и настраивает агента резервного копирования** на узлах,
используя `restic` и хранилище MinIO (S3-совместимое).

---

## 🧩 Назначение

Роль отвечает за **создание, ротацию и мониторинг бэкапов** сервисов `shm` и `marzban`
в проекте `vff-backup`.

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
    containers: []
    db_dump:
      enabled: true
      dump_dir: /var/backups/db
      container: mysql
      command: >
        /bin/bash -lc 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysqldump -u root
        --single-transaction --routines --triggers --events --hex-blob --quick
        --databases shm | gzip -c > /var/backups/db/shm_$(date +%F_%H%M%S).sql.gz'
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
backup_last_run_timestamp_seconds{job="shm"} 1739491210
backup_last_duration_seconds{job="shm"} 5
backup_last_status{job="shm"} 0
backup_last_size_bytes{job="shm"} 246751
```

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

Перед каждым запуском `vff-backup.sh` автоматически удаляются старые SQL-дампы
(по `backup_dump_keep_count` или `backup_dump_keep_days`).

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
└── marzban/<host>/
```

---

## 🔐 Секреты

Секреты `restic` и `minio` хранятся локально на контроллере Ansible:
```
~/.ansible/secrets/restic/
~/.ansible/secrets/minio/
```

Роль автоматически создаёт недостающие пароли при первом запуске.

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
