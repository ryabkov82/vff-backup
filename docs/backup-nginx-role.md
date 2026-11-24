# Роль `backup_nginx`

Роль устанавливает и настраивает **Nginx reverse-proxy** для защищённого HTTPS-доступа к
веб-интерфейсу MinIO (S3 API и Console) с поддержкой Let’s Encrypt-сертификатов и базовой аутентификации.

---

## 🧩 Назначение

Эта роль используется в проекте **vff-backup** для публикации MinIO-сервера
на внешнем домене, например:

- **https://s3.portalbase.link** — REST/S3 API для restic и клиентов.

Публичная веб-консоль MinIO не публикуется. Локальный интерфейс остаётся на `127.0.0.1:9001`.

---

## ⚙️ Основные задачи

- установка пакетов `nginx`, `certbot`, `python3-certbot-nginx`;
- генерация сертификатов Let’s Encrypt (`certonly --webroot`);
- разворачивание виртуального хоста S3 API (`s3.conf`) на `127.0.0.1:9000`;
- публичный vhost консоли не создаётся, пока `s3_console_enabled` выключен;
- автоматическое продление сертификатов;
- базовая защита через HTTP Basic Auth (опционально);
- Reload nginx при изменениях конфигурации.

---

## 🔧 Переменные

| Переменная | Описание | Пример |
|-------------|-----------|--------|
| `s3_domain` | Домен S3 API | `s3.portalbase.link` |
| `s3_console_enabled` | Публиковать консоль | `false` |
| `certbot_email` | Email для Let’s Encrypt | `admin@vpn-for-friends.com` |
| `nginx_cert_method` | `certbot` или `existing` | `certbot` |
| `nginx_basic_auth_enabled` | Включить Basic Auth | `false` |

---

## 🚀 Пример запуска

```bash
make nginx-install HOST=monitoring-hub
# или вручную:
ansible-playbook -i ansible/hosts.ini ansible/playbooks/nginx.yml --limit monitoring-hub --tags nginx --ask-vault-pass
```

---

## 🔍 Проверка

```bash
curl -I https://s3.portalbase.link
sudo nginx -t
sudo systemctl status nginx
```
