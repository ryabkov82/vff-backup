# 🧰 VFF Backup

**VFF Backup** — система централизованных резервных копий для инфраструктуры [VPN for Friends](https://vpn-for-friends.com).  
Решение построено на базе **MinIO (S3)** и **Restic**, с автоматизацией через **Ansible** и системой метрик Prometheus.

---

## 📦 Архитектура

```
[Marzban, SHM, Remnawave]
   │
   │ Restic → S3
   ▼
[MinIO Server] — локальное S3-хранилище
   ├── API:      https://s3.portalbase.link
   ├── Console:  только 127.0.0.1:9001, публично закрыта
   └── Бакет:    vff-backups
```

---

## ⚙️ Компоненты

| Компонент | Назначение |
|------------|------------|
| **MinIO** | Локальное S3-хранилище для бэкапов |
| **Nginx (backup_nginx)** | HTTPS-прокси для MinIO (S3 и Console) |
| **Restic** | Клиент бэкапов (используется на узлах) |
| **Ansible roles** | Автоматизация установки и настройки MinIO, Nginx, Restic |
| **Prometheus exporter** | Метрики состояния бэкапов (интеграция с VFF Monitoring) |

---

## 🧩 Структура проекта

```
ansible/
├── group_vars/
│   ├── all.yml                # Общие параметры
│   ├── backup_clients.yml     # Настройки клиентов (шаблон)
│   ├── shm.yml                # Настройки бэкапа SHM
│   ├── marzban.yml            # Настройки бэкапа Marzban
│   ├── remnawave.yml          # Настройки бэкапа Remnawave
│   └── hub.vault.yml          # Секреты (MinIO root / user secrets)
├── hosts.ini                  # Инвентарь (hub, vpn, ...)
├── playbooks/
│   ├── nginx.yml              # Установка reverse-proxy
│   ├── minio.yml              # Развёртывание MinIO
│   ├── backup.yml             # Установка backup-агента на узлах
│   └── restore.yml            # Восстановление снапшота
└── roles/
    ├── backup_nginx/          # Nginx + Let's Encrypt
    ├── minio/                 # MinIO + пользователи
    ├── backup/                # Роль бэкапов (restic, timers)
    └── restore/               # Роль восстановления (restic restore)
docs/
├── backup-nginx-role.md
├── minio-role.md
├── backup-role.md
└── restore-role.md
Makefile
```

---

## 🚀 Основные команды Makefile

```bash
# Проверка доступности хостов
make ping

# Установка Nginx reverse-proxy на хабе
make nginx HOST=monitoring-hub ANSIBLE_FLAGS=--ask-vault-pass

# Установка MinIO на хабе
make minio HOST=monitoring-hub ANSIBLE_FLAGS=--ask-vault-pass

# Установка backup-агента (restic) на клиентских узлах
make backup LIMIT=ru-msk-1

# Ручной запуск и просмотр логов бэкапа
make backup-run  LIMIT=ru-msk-1 BACKUP_JOB=shm
make backup-logs LIMIT=ru-msk-1 BACKUP_JOB=shm

# Генерация restic.env и (опц.) восстановление снапшота
make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=0
make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
  RESTORE_INCLUDES='/var/lib/marzban/**,/opt/marzban/.env'
# Конкретный снапшот:
make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
  ANSIBLE_FLAGS='-e snapshot_id=a54d7b64'
# За пределы /tmp (нужен RESTORE_FORCE=1):
make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
  RESTORE_TARGET=/srv/restore/marzban RESTORE_FORCE=1 \
  RESTORE_INCLUDES='/var/lib/marzban/**'
```

---

## 🔐 Хранение секретов

Большие секреты (root MinIO и т.д.) хранятся в **Ansible Vault** (`ansible/group_vars/hub/vault.yml`).  
При этом пользователи S3 и пароли Restic для сервисов размещаются **на контроллере** во внешнем дереве `~/.ansible/secrets/`:

```
~/.ansible/secrets/
├── minio/
│   ├── marzban-user
│   └── shm-user
└── restic/
    ├── marzban
    ├── remnawave
    └── shm
```

Создание или редактирование vault-файла:
```bash
EDITOR=nano ansible-vault edit ansible/group_vars/hub/vault.yml
```

Пример содержимого vault:
```yaml
vault_minio_root_user: vffadmin
vault_minio_root_password: "StrongRootPass123!"
vault_shm_user_secret: "..."        # будет записан в ~/.ansible/secrets/minio/shm-user
vault_marzban_user_secret: "..."    # будет записан в ~/.ansible/secrets/minio/marzban-user
```

---

## 📘 Документация по ролям

| Роль | Назначение | Документация |
|------|-------------|---------------|
| **backup_nginx** | Nginx reverse-proxy с certbot для MinIO | [docs/backup-nginx-role.md](docs/backup-nginx-role.md) |
| **minio** | Развёртывание S3-хранилища, политики и пользователи | [docs/minio-role.md](docs/minio-role.md) |
| **backup** | Restic, systemd timers, очистка дампов, метрики | [docs/backup-role.md](docs/backup-role.md) |
| **restore** | Восстановление снапшотов | [docs/restore-role.md](docs/restore-role.md) |

---

## 🧾 Лицензия

Проект распространяется под лицензией **MIT**.  
© 2025 [VPN for Friends](https://vpn-for-friends.com)

---

## 🧩 Контакты

- Telegram: [@vpn_for_friends_support](https://t.me/vpn_for_friends_support)
