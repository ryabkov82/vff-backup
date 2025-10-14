# === Общие переменные ===
ANSIBLE      ?= ansible-playbook
ANSIBLE_ADHOC?= ansible
INVENTORY    ?= ansible/hosts.ini

# Плейбук MinIO
PLAY_MINIO   ?= ansible/playbooks/minio.yml
PLAY_NGINX   ?= ansible/playbooks/nginx.yml
PLAY_BACKUP  ?= ansible/playbooks/backup.yml
PLAY_SITE    ?= ansible/playbooks/site.yml

# Доп. флаги для ansible/ansible-playbook (например: --ask-vault-pass)
ANSIBLE_FLAGS?=
# Ограничение по хосту/группе (например: LIMIT=monitoring-hub или LIMIT=hub)
LIMIT 		 ?= all

# Вспомогательные флаги
LIMIT_FLAG    = $(if $(strip $(LIMIT)),--limit $(LIMIT),)

# --- Цели ---

minio: ## Установка/настройка MinIO (роль minio)
	@# Примеры:
	@#   make minio
	@#   make minio LIMIT=monitoring-hub
	@#   make minio LIMIT=hub ANSIBLE_FLAGS=--ask-vault-pass
	$(ANSIBLE) -i $(INVENTORY) $(PLAY_MINIO) $(LIMIT_FLAG) $(ANSIBLE_FLAGS)

minio-check: ## Проверка Health MinIO на целевых хостах (HTTP /minio/health/ready)
	@# Примеры:
	@#   make minio-check LIMIT=monitoring-hub
	@#   make minio-check LIMIT=hub
	$(ANSIBLE_ADHOC) -i $(INVENTORY) $(LIMIT) -m shell -a \
		"curl -sS -I http://127.0.0.1:9000/minio/health/ready | head -n1" $(ANSIBLE_FLAGS) || true

minio-restart: ## Мягкий рестарт контейнера MinIO через compose v2
	@# Примеры:
	@#   make minio-restart LIMIT=monitoring-hub
	@#   make minio-restart LIMIT=hub
	$(ANSIBLE_ADHOC) -i $(INVENTORY) $(LIMIT) -m community.docker.docker_compose_v2 -a \
		"project_src=/opt/minio state=present restarted=true" $(ANSIBLE_FLAGS)

minio-logs: ## Быстрые логи контейнера MinIO (docker logs)
	@# Примеры:
	@#   make minio-logs LIMIT=monitoring-hub
	@#   make minio-logs LIMIT=hub
	$(ANSIBLE_ADHOC) -i $(INVENTORY) $(LIMIT) -m ansible.builtin.shell -a \
		"docker logs --tail=200 -n 1 minio 2>&1 || journalctl -u docker -n 200 --no-pager" $(ANSIBLE_FLAGS)

.PHONY: nginx-install

nginx: ## Настроить Nginx на хабе (reverse-proxy, certs, htpasswd)
	@# Примеры:
	@#   make nginx
	@#   make nginx LIMIT=monitoring-hub
	@#   make nginx LIMIT=hub ANSIBLE_FLAGS=--ask-vault-pass
	$(ANSIBLE) -i $(INVENTORY) $(PLAY_NGINX) $(if $(strip $(LIMIT)),--limit $(LIMIT),) $(ANSIBLE_FLAGS)

.PHONY: backup site

backup: ## Установка/обновление backup-агента (роль backup)
	@# Примеры:
	@#   make backup
	@#   make backup LIMIT=shm
	@#   make backup LIMIT=backup_clients ANSIBLE_FLAGS=--ask-vault-pass
	$(ANSIBLE) -i $(INVENTORY) $(PLAY_BACKUP) $(LIMIT_FLAG) $(ANSIBLE_FLAGS)

site: ## Запуск общего оркестратора site.yml (minio -> nginx -> backup)
	@# Примеры:
	@#   make site
	@#   make site LIMIT=backup_clients
	@#   make site ANSIBLE_FLAGS="--ask-vault-pass -t backup"
	$(ANSIBLE) -i $(INVENTORY) $(PLAY_SITE) $(LIMIT_FLAG) $(ANSIBLE_FLAGS)

# === Backup (systemd) ===
BACKUP_JOB ?= shm

backup-run: ## Разово запустить backup@$(BACKUP_JOB).service на хостах (используй LIMIT=...)
	@# Примеры:
	@#   make backup-run
	@#   make backup-run LIMIT=ru-msk-1 BACKUP_JOB=shm
	$(ANSIBLE_ADHOC) -i $(INVENTORY) $(LIMIT) -b -m ansible.builtin.shell -a \
		"systemctl start backup@$(BACKUP_JOB).service" $(ANSIBLE_FLAGS)

backup-logs: ## Показать последние 100 строк журнала backup@$(BACKUP_JOB).service
	@# Примеры:
	@#   make backup-logs
	@#   make backup-logs LIMIT=ru-msk-1 BACKUP_JOB=shm
	$(ANSIBLE_ADHOC) -i $(INVENTORY) $(LIMIT) -b -m ansible.builtin.shell -a \
		"journalctl -u backup@$(BACKUP_JOB).service -n 100 --no-pager" $(ANSIBLE_FLAGS)


# ===== Restore (restic) — defaults =====

# Роль/плейбук восстановления
PLAY_RESTORE   ?= ansible/playbooks/restore.yml

# --------- Параметры по умолчанию (можно переопределять в команде make) ---------
SERVICE         ?= marzban
SRC_HOST        ?= $(LIMIT)                  # по умолчанию совпадает с --limit
S3_ENDPOINT     ?= https://s3.vpn-for-friends.com
S3_BUCKET       ?= vff-backups
S3_REGION       ?= us-east-1
MINIO_USER      ?= $(SERVICE)-user

INSTALL_RESTIC  ?= 1
INSTALL_FUSE    ?= 0
PERFORM_RESTORE ?= 0

RESTORE_TARGET  ?=
RESTORE_FORCE   ?= 0

# Пример: RESTORE_INCLUDES='/var/lib/marzban/**,/opt/marzban/.env'
# Пример: RESTORE_EXCLUDES='/var/lib/marzban/xray-core/**'
RESTORE_INCLUDES ?=
RESTORE_EXCLUDES ?=

# ===== Цель: restore-env-play =====
.PHONY: restore-env-play
restore-env-play: ## Сгенерировать /etc/vff-backup/restic.env и (опц.) восстановить снапшот через роль restore
	@# Примеры:
	@#   make restore-env-play
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=0
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 RESTORE_INCLUDES='/var/lib/marzban/**,/opt/marzban/.env'
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 ANSIBLE_FLAGS='-e snapshot_id=abcd1234'
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 RESTORE_TARGET=/srv/restore/marzban RESTORE_FORCE=1 RESTORE_INCLUDES='/var/lib/marzban/**'
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 RESTORE_INCLUDES='/var/lib/marzban/**' RESTORE_EXCLUDES='/var/lib/marzban/xray-core/**'
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 INSTALL_RESTIC=1 INSTALL_FUSE=1
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=shm SRC_HOST=nl-1.example PERFORM_RESTORE=1 RESTORE_INCLUDES='/var/lib/shm/**'
	@#   make restore-env-play LIMIT=ru-msk-1 SERVICE=marzban PERFORM_RESTORE=1 S3_ENDPOINT=https://s3.alt.example S3_BUCKET=alt-bucket
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
