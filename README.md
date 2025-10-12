# 🧰 VFF Backup

**VFF Backup** — система централизованных резервных копий для инфраструктуры [VPN for Friends](https://vpn-for-friends.com).  
Решение построено на базе **MinIO (S3)** и **Restic**, с автоматизацией через **Ansible** и системой метрик Prometheus.

---

## 📦 Архитектура

```
VPN Nodes (Marzban, SHM)
   │
   │ Restic → S3
   ▼
[MinIO Server] — локальное S3-хранилище
   ├── API:      https://s3.vpn-for-friends.com
   ├── Console:  https://s3-console.vpn-for-friends.com
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
│   └── hub.vault.yml        # Секреты (MinIO root / user secrets)
├── hosts.ini                # Инвентарь (hub, vpn, ...)
├── playbooks/
│   ├── nginx.yml            # Установка reverse-proxy
│   └── minio.yml            # Развёртывание MinIO
└── roles/
    ├── backup_nginx/        # Nginx + Let's Encrypt
    ├── minio/               # MinIO + пользователи
    └── backup/              # Роль бэкапов (restic, timers)
docs/
├── backup-nginx-role.md
├── minio-role.md
└── (позже) backup-role.md
Makefile
```

---

## 🚀 Основные команды Makefile

```bash
# Проверка доступности хостов
make ping

# Установка Nginx reverse-proxy на хабе
make nginx-install HOST=monitoring-hub ANSIBLE_FLAGS=--ask-vault-pass

# Установка MinIO на хабе
make minio HOST=monitoring-hub ANSIBLE_FLAGS=--ask-vault-pass
```

---

## 🔐 Хранение секретов

Все ключи и пароли хранятся в **Ansible Vault** (`ansible/group_vars/hub.vault.yml`).

Создание или редактирование:
```bash
EDITOR=nano ansible-vault edit ansible/group_vars/hub.vault.yml
```

Пример содержимого:
```yaml
vault_minio_root_user: vffadmin
vault_minio_root_password: "StrongRootPass123!"
vault_shm_user_secret: "..."
vault_marzban_user_secret: "..."
```

---

## 📘 Документация по ролям

| Роль | Назначение | Документация |
|------|-------------|---------------|
| **backup_nginx** | Nginx reverse-proxy с certbot для MinIO | [docs/backup-nginx-role.md](docs/backup-nginx-role.md) |
| **minio** | Развёртывание S3-хранилища, политики и пользователи | [docs/minio-role.md](docs/minio-role.md) |
| **backup** | (в разработке) restic, systemd timers, метрики | *(будет добавлено позже)* |

---

## 🧾 Лицензия

Проект распространяется под лицензией **MIT**.  
© 2025 [VPN for Friends](https://vpn-for-friends.com)

---

## 🧩 Контакты

- Telegram: [@vpn_for_friends_support](https://t.me/vpn_for_friends_support)
- Email: `admin@vpn-for-friends.com`
