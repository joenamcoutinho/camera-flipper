# Deployment

## The server

| | |
|---|---|
| Host | Oracle Cloud "Always Free" VM |
| Shape | `VM.Standard.E2.1.Micro` (1 core, 1 GB) — genuinely free, verified |
| OS | Oracle Linux 9, **Python 3.9** |
| Public IP | `145.241.226.235` |
| SSH user | `opc` |
| App URL | `http://145.241.226.235:8000` (plain HTTP, Basic Auth) |
| Service | `camera-flipper.service` (systemd, auto-start, auto-restart) |
| App path | `/home/opc/camera-flipper` |

SELinux is set to **permissive**. It blocks systemd services from executing
binaries inside home directories, which made the service fail with status
`203/EXEC` while the identical command worked fine in an SSH session. The
alternative was relabelling the venv; permissive was chosen as proportionate
for a single-user personal box.

## Deploying

Joe double-clicks `DEPLOY - double click me.bat`. It runs `deploy_finish.ps1`,
which:

1. `scp`s the Python files and `static/` to the VM
2. runs `update_env.py` there (patches non-secret settings; **never** touches
   `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `TELEGRAM_*`, `WEBAPP_PASSWORD`)
3. `pip install -r requirements.txt`
4. `sudo systemctl restart camera-flipper`
5. runs `rescore.py` — re-analyses stored listing text and re-scores the queue

All output is captured to `deploy_log.txt` in the project folder. Read that
file to check a deploy; the console window closes and is hard to read.

The deploy is safe to repeat. The database migrates itself on startup, so a
deploy never wipes swipe history, blocked models or settings.

### Manual equivalent

```bash
scp -i <key> webapp.py db.py schema.sql signals.py cli.py scoring.py \
    camera_knowledge.py ebay_client.py requirements.txt update_env.py \
    rescore.py opc@145.241.226.235:~/camera-flipper/
scp -i <key> -r static opc@145.241.226.235:~/camera-flipper/
ssh -i <key> opc@145.241.226.235 \
  "cd ~/camera-flipper && source venv/bin/activate && \
   pip install -q -r requirements.txt && python rescore.py"
ssh -i <key> opc@145.241.226.235 "sudo systemctl restart camera-flipper"
```

### Watching it

```bash
ssh -i <key> opc@145.241.226.235 'journalctl -u camera-flipper -f'
```

## Networking

Two firewalls both had to be opened for port 8000:

1. **OCI Security List** (web console): Networking → VCN → Security Lists →
   Ingress rule, source `0.0.0.0/0`, TCP, destination port 8000.
2. **firewalld on the VM**: `sudo firewall-cmd --permanent --add-port=8000/tcp
   && sudo firewall-cmd --reload`

Cloudflare Tunnel was tried and deliberately abandoned: a free "quick tunnel"
generates a **new random URL every restart**, which made it useless as a
bookmark. Plain IP:port was chosen for a stable address, accepting no HTTPS.

**The HTTPS trade-off is real**: Basic Auth over plain HTTP sends credentials
base64-encoded, readable by anyone on the same network. Acceptable for a
personal tool on trusted networks; not acceptable if this ever holds anything
sensitive. Fixing it properly means a domain + a named Cloudflare Tunnel.

## eBay API limits

**5,000 calls/day**, resetting midnight UTC ([eBay's limits page][1]). Each
search *page* is one call; each brand-new listing costs one more for its
photos and description; each availability check is one more.

The app tracks its own usage in the `api_usage` table (surviving restarts) and
stops scanning at `daily_call_budget` (default 4,000) so it is never cut off
mid-day. Usage is shown in the Settings tab.

Current settings work out at roughly 1,700 calls/day typical, 3,500 worst case:
11 search phrases × 3 pages every 30 minutes, capped at 40 new listings a scan.

Do the arithmetic before changing any of those. An earlier configuration —
11 phrases × 3 pages every 15 min, 120 new/scan — would have used **14,688
calls/day**, nearly three times the limit.

[1]: https://developer.ebay.com/develop/get-started/api-call-limits

## Credentials

All in `.env` on the VM, never in the repo:
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_ENV=PRODUCTION`,
`WEBAPP_USERNAME`, `WEBAPP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`.

eBay production access additionally requires, in the eBay developer portal:
the production keyset **enabled**, and Marketplace Account Deletion set to
**Exempted** (this app stores no eBay user data).

### Security debt

- The webapp password and the SSH private key were both pasted into a chat
  during development. Rotating both is worth doing: change `WEBAPP_PASSWORD`
  in the VM's `.env`, and generate a new SSH keypair and swap it on the
  instance.
- `.env` on Windows once acquired NUL bytes from a PowerShell `>>` append
  writing UTF-16, which makes python-dotenv silently skip keys. If settings
  mysteriously don't apply, check the file is plain UTF-8.
