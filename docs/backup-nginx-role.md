# Роль `backup_nginx`

Роль устанавливает и настраивает **Nginx reverse-proxy** для защищённого HTTPS-доступа к
веб-интерфейсу MinIO (S3 API и Console) с поддержкой Let’s Encrypt-сертификатов и базовой аутентификации.

---

## 🧩 Назначение

Эта роль используется в проекте **vff-backup** для публикации MinIO-сервера
на внешнем домене, например:

- **https://s3.vpn-for-friends.com** — REST/S3 API для restic и клиентов;
- **https://s3-console.vpn-for-friends.com** — веб-консоль MinIO (UI).

---

## ⚙️ Основные задачи

- установка пакетов `nginx`, `certbot`, `python3-certbot-nginx`;
- генерация сертификатов Let’s Encrypt (`certonly --webroot`);
- разворачивание двух виртуальных хостов:
  - `s3.<domain>` — прокси на `127.0.0.1:9000`;
  - `s3-console.<domain>` — прокси на `127.0.0.1:9001`;
- автоматическое продление сертификатов;
- базовая защита через HTTP Basic Auth (опционально);
- Reload nginx при изменениях конфигурации.

---

## 🔧 Переменные

| Переменная | Описание | Пример |
|-------------|-----------|--------|
| `s3_domain` | Домен S3 API | `s3.vpn-for-friends.com` |
| `s3_console_domain` | Домен консоли MinIO | `s3-console.vpn-for-friends.com` |
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
curl -I https://s3.vpn-for-friends.com
curl -I https://s3-console.vpn-for-friends.com
sudo nginx -t
sudo systemctl status nginx
```
