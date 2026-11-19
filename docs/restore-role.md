# Роль `restore`

Роль создает `/etc/vff-backup/restic.env` на целевом хосте из локальных секретов контроллера и **по желанию выполняет восстановление** (restic `restore`) из последнего или указанного снапшота.

---

## 🧩 Назначение

- Подготовить корректное окружение Restic на целевой машине (S3, пароль, кэш).
- Безопасно и детерминированно восстановить данные:
  - по **последнему** снапшоту (с фильтрами по `tag`/`host`);
  - по **конкретному** `snapshot_id`;
  - только **часть путей** через `--include/--exclude`.
- Встроенные предохранители от случайной записи вне проверенного системного временного каталога (обычно `/tmp`).

---

## ⚙️ Что делает роль

1. **Читает секреты на контроллере** (файлы в `~/.ansible/secrets/...`) и прокидывает их на целевой хост:
   - Restic password: `~/.ansible/secrets/restic/<service>`
   - MinIO user secret: `~/.ansible/secrets/minio/<minio_user>`
2. (Опционально) **устанавливает** `restic`, `jq`, `fuse3`.
3. Создает каталоги `/etc/vff-backup` и `RESTIC_CACHE_DIR` (по умолчанию `/var/cache/restic`).
4. Пишет `/etc/vff-backup/restic.env` (0640).
5. (Опционально) Выполняет `restic restore` в указанный каталог (по умолчанию новая частная папка в системном temp, обычно `/tmp`).

---

## 🔧 Переменные роли (основные)

| Переменная | Тип | По умолчанию | Описание |
|---|---|---|---|
| `service` | str | `marzban` | Логическое имя сервиса (используется как тег `snapshot_tag` и сегмент пути в S3). |
| `src_host` | str | `{{ inventory_hostname }}` | Имя **каталога** в S3-репозитории (откуда берем бэкап). Не путать с меткой `Host` внутри снапшота. |
| `s3_endpoint` | str | `https://s3.portalbase.link` | Endpoint S3/MinIO. |
| `s3_bucket` | str | `vff-backups` | Бакет с бэкапами. |
| `s3_region` | str | `us-east-1` | Регион (для AWS совместимости). |
| `minio_user` | str | `{{ service }}-user` | Имя пользователя MinIO, секрет ищется в `~/.ansible/secrets/minio/<minio_user>`. |
| `restic_cache_dir` | str | `/var/cache/restic` | Кэш Restic на целевом. |
| `install_restic` | bool | `false` | Установить `restic` на целевой. |
| `install_fuse` | bool | `false` | Установить `fuse3` (нужно для `restic mount`). |
| `install_jq` | bool | `true` | Установить `jq` (нужно для парсинга JSON снапшотов). |
| `perform_restore` | bool | `false` | Выполнить восстановление. |
| `restore_target` | str | `""` | Новый частный каталог восстановления (0700), фактические данные в `data/`. Если пусто — создаётся `mkdtemp` в проверенном системном temp (обычно `/tmp`). |
| `restore_includes` | list[str] | `[]` | Маски путей для `--include`. Пример: `/var/lib/marzban/**`. |
| `restore_excludes` | list[str] | `[]` | Маски путей для `--exclude`. |
| `restore_force` | bool | `false` | Разрешить восстановление не в непосредственный дочерний каталог системного temp (предохранитель). |
| `snapshot_id` | str | `""` | Конкретный ID (уникальный префикс 8–64 hex символа). Проверяются указанные host/tag; используется полный ID. |
| `snapshot_newest` | bool | `true` | Если `snapshot_id` пуст — брать самый новый по `snapshot_tag`/`snapshot_host`. |
| `snapshot_tag` | str | `{{ service }}` | Тег снапшота (проставляется при бэкапе). |
| `snapshot_host` | str | `{{ src_host }}` | Обязательная точная метка Host. Если не совпадает с src_host, задайте её явно; автоматического fallback нет. |

**RESTIC_REPOSITORY** собирается как:  
`s3:{{ s3_endpoint }}/{{ s3_bucket }}/{{ service }}/{{ src_host }}`

> ℹ️ `src_host` — это **имя папки в S3**, например `nl-ams-2`. Метка `Host` внутри снапшота может быть другой (например, `v358283.hosted-by-vdsina.com`). Если нужно — передайте `snapshot_host` явно.

---

## 🧷 Требования на целевом

- Доступ по SSH (Ansible).
- Наличие `restic` (или `install_restic: true`).
- Для восстановления — свободное место в `restore_target`.
- Для S3 — доступность `s3_endpoint` с ключами `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY` (от `minio_user`).

---

## 📦 Секреты (на контроллере)

Учётные данные для DR читаются на Ansible-контроллере. В git и в вывод playbook их значения не попадают.

- `~/.ansible/secrets/restic/<service>` — пароль репозитория Restic;
- `~/.ansible/secrets/minio/<minio-user>` — секрет пользователя MinIO.

```
~/.ansible/secrets/
├── minio/
│   ├── marzban-user
│   ├── remnawave-user
│   └── shm-user
└── restic/
    ├── marzban
    ├── remnawave
    └── shm
```

Если файл отсутствует — lookup `password` **создаст** его с заданной длиной и алфавитом (в роли `backup`).

Snapshot SHM для DR содержит SQL-дамп в `/var/backups/db` и каталог `/opt/shm`.
Новый snapshot не считается успешным, если при `db_dump.enabled: true` свежий дамп в этом запуске не был создан и не прошёл `gzip -t`: `restic backup` в таком случае не стартует.

---

## 🛠 Плейбук

Минимальный плейбук использует только роль:

```yaml
# ansible/playbooks/restore.yml
- name: Generate restic env and optionally restore
  hosts: all
  gather_facts: false
  roles:
    - role: restore
      tags: restore
```

---

## 🧰 Makefile-цель

```make
# defaults (пример)
PLAY_RESTORE ?= ansible/playbooks/restore.yml

restore-env-play: ## Сгенерировать env и (опц.) восстановить снапшот через роль restore
	@# Примеры:
	@#   make restore-env-play
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=0
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
	@#       RESTORE_INCLUDES='/var/lib/marzban/**,/opt/marzban/.env'
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
	@#       ANSIBLE_FLAGS='-e snapshot_id=abcd1234'
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
	@#       RESTORE_TARGET=/srv/restore/marzban RESTORE_FORCE=1 \
	@#       RESTORE_INCLUDES='/var/lib/marzban/**'
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
	@#       RESTORE_INCLUDES='/var/lib/marzban/**' \
	@#       RESTORE_EXCLUDES='/var/lib/marzban/xray-core/**'
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
	@#       INSTALL_RESTIC=1 INSTALL_FUSE=1
	@#   make restore-env-play LIMIT=nl-ams-1 SERVICE=shm SRC_HOST=nl-ams-2 \
	@#       PERFORM_RESTORE=1 RESTORE_INCLUDES='/var/lib/shm/**'
	$(ANSIBLE) -i $(INVENTORY) $(PLAY_RESTORE) $(LIMIT_FLAG) \
		-e service="$(SERVICE)" \
		-e src_host="$(SRC_HOST)" \
		-e s3_endpoint="$(S3_ENDPOINT)" \
		-e s3_bucket="$(S3_BUCKET)" \
		-e s3_region="$(S3_REGION)" \
		-e minio_user="$(MINIO_USER)" \
		-e install_restic=$(INSTALL_RESTIC) \
		-e install_fuse=$(INSTALL_FUSE) \
		-e install_jq=1 \
		-e perform_restore=$(PERFORM_RESTORE) \
		-e restore_target='$(RESTORE_TARGET)' \
		-e restore_includes="$$( if [ -n '$(strip $(RESTORE_INCLUDES))' ]; then \
				printf '[\"%s\"]' '$(RESTORE_INCLUDES)' | sed 's/,/\",\"/g'; \
			else printf '[]'; fi )" \
		-e restore_excludes="$$( if [ -n '$(strip $(RESTORE_EXCLUDES))' ]; then \
				printf '[\"%s\"]' '$(RESTORE_EXCLUDES)' | sed 's/,/\",\"/g'; \
			else printf '[]'; fi )" \
		-e restore_force=$(RESTORE_FORCE) \
		$(ANSIBLE_FLAGS)
```

---

## 🚀 Примеры

- Только сгенерировать `restic.env`:
  ```bash
  make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=0
  ```

- Восстановить **последний** снапшот в `/tmp`:
  ```bash
  make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
    RESTORE_INCLUDES='/var/lib/marzban/**,/opt/marzban/.env'
  ```

- Восстановить **конкретный** снапшот:
  ```bash
  make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
    ANSIBLE_FLAGS='-e snapshot_id=a54d7b64'
  ```

- Восстановить за пределы `/tmp` (нужен `RESTORE_FORCE=1`):
  ```bash
  make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
    RESTORE_TARGET=/srv/restore/marzban RESTORE_FORCE=1 \
    RESTORE_INCLUDES='/var/lib/marzban/**'
  ```

- С фильтром по `Host` внутри снапшота (если он отличается от `src_host`):
  ```bash
  make restore-env-play LIMIT=nl-ams-1 SERVICE=marzban PERFORM_RESTORE=1 \
    ANSIBLE_FLAGS='-e snapshot_host=v358283.hosted-by-vdsina.com' \
    RESTORE_INCLUDES='/var/lib/marzban/**'
  ```

---

## 🛡️ Безопасность и защита от ошибок

- По умолчанию — только новый непосредственный дочерний каталог системного temp (обычно `/tmp`). Для автоматического и явного пути проверяются все родительские каталоги; общие sticky-каталоги допускаются только на пути системного temp и только при владельце root. Для иных путей требуется `RESTORE_FORCE=true`, существующий доверенный родитель и новый каталог. Ссылки, неканонические пути, существующий каталог назначения и небезопасные родители запрещены даже при force. Данные Restic размещаются внутри `data/` под корневым каталогом 0700. Неполное восстановление не удаляется автоматически; повторный запуск требует нового каталога. Перенос в рабочий путь — отдельное операторское действие.
- Если нужный host/tag (включая явно выбранный ID) не найден, операция завершается ошибкой. Для переноса между хостами укажите фактические `src_host` и `snapshot_host`, при необходимости явный ID; вывод успешной операции содержит полный ID, host и каталог.
- Нормализация `restore_includes`/`restore_excludes`: роль принимает как списки, так и строки вида `"[]"`/`'a,b,c'` и корректно собирает флаги.
- Идемпотентный репозиторий: проверка через `restic cat config`; для неверного пароля/ключей роль выдаёт понятную ошибку вместо `restic init` на существующий репозиторий.

---

## 🧪 Диагностика

Показать, куда смотрит алиас `vff` и какие есть SRC_HOST (с контроллера, удалённо):
```bash
make mc-alias-list-remote MINIO_HOST=monitoring-hub
make list-src-hosts-mc-remote-ephemeral MINIO_HOST=monitoring-hub MINIO_URL=http://127.0.0.1:9000 MINIO_ACCESS=minio MINIO_SECRET=*** SERVICE=marzban
```

Проверить репозиторий и пароль на целевом:
```bash
ansible -i ansible/hosts.ini nl-ams-1 -b -m shell -a \
  "set -a; source /etc/vff-backup/restic.env; set +a; restic cat config && echo OK"
```

---

## ❓FAQ

**Q:** Чем отличаются `src_host` и `snapshot_host`?  
**A:** `src_host` — сегмент пути в S3 (`.../<service>/<src_host>/...`). `snapshot_host` — поле `Host` внутри снапшота Restic. Они могут отличаться; если нужно — задайте `snapshot_host` явно.

**Q:** Как сменить пароль репозитория?  
**A:** Откройте репозиторий старым паролем и выполните `restic key passwd` (или `restic key passwd --new-password-file`), затем обновите секрет на контроллере и перегенерируйте `restic.env`.

**Q:** Где лежат секреты?  
**A:** На контроллере: `~/.ansible/secrets/restic/<service>` и `~/.ansible/secrets/minio/<minio_user>`. Роль не хранит секреты в git.

---

## 📄 Лицензия

MIT / BSD-2-Clause (на выбор проекта).
