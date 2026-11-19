# Роль `minio`

Роль устанавливает и настраивает **MinIO** — S3-совместимое хранилище
для резервных копий в проекте `vff-backup`.

---

## 🧩 Назначение

MinIO используется как **централизованное хранилище** для бэкапов систем `marzban` и `shm`
(дампы баз данных, архивы и т.п.).

---

## ⚙️ Основные задачи

- деплой контейнера `minio` через Docker Compose;
- настройка root-учётных данных (`MINIO_ROOT_USER/PASSWORD`);
- проброс портов: `9000` — S3 API, `9001` — Console;
- настройка alias в `mc-minio`;
- создание бакета `vff-backups`;
- создание пользователей и политик доступа:
  - `shm-user` → `vff-backups/shm/*`;
  - `marzban-user` → `vff-backups/marzban/*`;
- хранение секретов в `.ansible/secrets/minio`.

---

## 🔧 Переменные

| Переменная | Описание | Пример |
|-------------|-----------|--------|
| `minio_version` | Версия контейнера | `RELEASE.2025-09-30T20-14-40Z` |
| `minio_data_dir` | Каталог данных | `/srv/minio/data` |
| `minio_bucket` | Бакет для бэкапов | `vff-backups` |
| `minio_root_user` | Root логин | `vffadmin` |
| `minio_root_password` | Root пароль | `********` |

Пример пользовательских секретов:
```yaml
minio_users:
  - name: shm-user
    secret: "{{ vault_shm_user_secret }}"
    prefix: "shm/"
  - name: marzban-user
    secret: "{{ vault_marzban_user_secret }}"
    prefix: "marzban/"
```

---

## 🚀 Пример запуска

```bash
make minio HOST=monitoring-hub ANSIBLE_FLAGS=--ask-vault-pass
# или вручную:
ansible-playbook -i ansible/hosts.ini ansible/playbooks/minio.yml --limit hub --ask-vault-pass
```

---

## 🔍 Проверка

```bash
/usr/local/bin/mc-minio alias list
/usr/local/bin/mc-minio admin user list vff
/usr/local/bin/mc-minio admin policy list vff
```

Smoke-тест:
```bash
SHM_SECRET=$(cat ~/.ansible/secrets/minio/shm-user)
mc-minio alias set shm https://s3.portalbase.link shm-user "$SHM_SECRET"
echo ok | mc-minio pipe shm/vff-backups/shm/test.txt
```
