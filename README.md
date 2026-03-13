# Alexa → AnyList

A Flask web app that syncs your Alexa shopping list to AnyList. Active Alexa items are pushed to a named AnyList list and then marked complete on Alexa so they don't accumulate.

## Features

- Dashboard showing your current Alexa shopping list items
- One-click manual sync or automatic sync on a configurable schedule
- In-browser Amazon authentication flow to capture fresh session cookies
- Optional site password to restrict access to the web UI
- Forgot-password / credential reset flow to recover from lockout
- Sync log with timestamps displayed in your configured time zone
- Time zone selector (60+ IANA zones across all regions)

## Requirements

- Python 3.11+
- Google Chrome (for the browser-based Amazon auth flow)
- An Amazon account with Alexa shopping list
- An AnyList account

---

## Quick start (local / development)

```bash
git clone <repository_url>
cd alexa-to-anylist

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python app.py
```

The app listens on **port 5123**. Open `http://localhost:5123` in your browser, then go to **Settings** to enter your credentials.

---

## Ubuntu server + nginx installation

These steps deploy the app to `/srv/alexa-to-anylist` under the `www-data` user and serve it through nginx with Gunicorn.

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
```

Chrome is required for the browser-based Amazon auth flow.  Install it system-wide:

```bash
wget -q -O /tmp/google-chrome.deb \
  https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/google-chrome.deb
```

### 2. Create the application directory

```bash
sudo mkdir -p /srv/alexa-to-anylist
sudo chown www-data:www-data /srv/alexa-to-anylist
```

### 3. Clone the repository

```bash
sudo -u www-data git clone <repository_url> /srv/alexa-to-anylist
```

### 4. Create the Python virtual environment and install dependencies

```bash
cd /srv/alexa-to-anylist
sudo -u www-data python3 -m venv .venv
sudo -u www-data .venv/bin/pip install --upgrade pip
sudo -u www-data .venv/bin/pip install -r requirements.txt
sudo -u www-data .venv/bin/pip install gunicorn
```

### 5. Set a strong secret key

The app reads its Flask session secret from the `SECRET_KEY` environment variable. Generate one and write it to the `.env` file that the systemd service will load:

```bash
sudo -u www-data bash -c 'echo "SECRET_KEY=$(python3 -c \"import secrets; print(secrets.token_hex(32))\")" \
  > /srv/alexa-to-anylist/.env'
sudo chmod 600 /srv/alexa-to-anylist/.env
```

> **Note:** If `SECRET_KEY` is not set, the app will generate a random key at startup and log a warning. Sessions will not survive restarts and cookies can be forged. Always set this variable in production.

### 6. Create a systemd service

Create `/etc/systemd/system/alexa-to-anylist.service`:

```ini
[Unit]
Description=Alexa to AnyList sync service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/srv/alexa-to-anylist
EnvironmentFile=/srv/alexa-to-anylist/.env
ExecStart=/srv/alexa-to-anylist/.venv/bin/gunicorn \
    --workers 1 \
    --bind 127.0.0.1:5123 \
    --timeout 120 \
    app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **Note:** Use a single worker (`--workers 1`). The app holds in-memory state for the browser auth flow and the APScheduler background scheduler; running multiple workers would split that state across processes.

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable alexa-to-anylist
sudo systemctl start alexa-to-anylist
sudo systemctl status alexa-to-anylist
```

### 7. Configure nginx

Create `/etc/nginx/sites-available/alexa-to-anylist`:

```nginx
server {
    listen 80;
    server_name your-server-hostname-or-ip;

    # Increase body size limit for cookie JSON payloads
    client_max_body_size 1m;

    location / {
        proxy_pass         http://127.0.0.1:5123;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Enable the site and reload nginx:

```bash
sudo ln -s /etc/nginx/sites-available/alexa-to-anylist \
           /etc/nginx/sites-enabled/alexa-to-anylist
sudo nginx -t
sudo systemctl reload nginx
```

The app is now reachable on `http://your-server-hostname-or-ip`.

### 8. (Optional) HTTPS with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example.com
```

Certbot will update the nginx config automatically and install a renewal timer.

### 9. Updating the app

```bash
cd /srv/alexa-to-anylist
sudo -u www-data git pull
sudo -u www-data .venv/bin/pip install -r requirements.txt
sudo systemctl restart alexa-to-anylist
```

---

## Configuration

All settings are saved through the **Settings** page in the UI. Nothing requires editing config files.

| Setting | Description |
|---|---|
| AnyList Email | Your AnyList account email |
| AnyList Password | Your AnyList account password |
| Target List Name | The AnyList list to sync into (default: `Shopping List`) |
| Amazon Base URL | Your regional Amazon URL (default: `https://www.amazon.com`) |
| Amazon Cookies | Session cookies from amazon.com (JSON array) |
| Time Zone | IANA timezone used to display all timestamps in the UI |
| Auto-sync interval | How often to sync automatically, in minutes (0 = disabled) |
| Site Password | Optional password to restrict access to the web UI |

## Amazon authentication

Amazon session cookies are required to read your Alexa shopping list. Two methods:

**Option A — Browser flow (recommended):**
Click **Open Amazon Browser** on the dashboard. A Chrome window opens, you log in manually, then click **I've Logged In** to capture the cookies automatically.

**Option B — Manual export:**
Export cookies from your browser using the [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) extension (Export → JSON format) and paste the result into the Amazon Session Cookies field on the Settings page.

Amazon sessions expire periodically. When sync stops working, re-authenticate using either method above.

## Forgot password

If you are locked out, click **Forgot password?** on the login page. This clears the site password, AnyList credentials, and Amazon cookies from the database. You can then re-enter your settings without needing the old password.

## Automatic sync

Set **Auto-sync interval** on the Settings page to a positive number of minutes. The scheduler runs in the background and logs each run to the sync log on the dashboard. Set to `0` to disable.

## Running tests

```bash
pip install pytest pytest-mock
pytest tests/
```

