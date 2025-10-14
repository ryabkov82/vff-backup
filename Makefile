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
LIMIT        ?=

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
