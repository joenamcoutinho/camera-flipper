"""Patch the non-secret settings in .env, leaving credentials untouched.

Runs on the VM as part of deploy. Only the keys listed in SETTINGS are
touched - EBAY_CLIENT_ID/SECRET, the Telegram token and the webapp
password are never read, written or logged here. Also strips any stray
NUL bytes, which is what a PowerShell '>>' append leaves behind and what
makes python-dotenv silently skip keys.
"""
from pathlib import Path

SETTINGS = {
    "SEARCH_QUERIES": (
        "sony cybershot,sony cyber-shot camera,sony dsc camera,"
        "sony cybershot untested,sony dsc untested,sony cybershot no battery,"
        "sony cybershot spares repairs,sony cybershot faulty,"
        "sony digital camera job lot,sony cybershot bundle,"
        "sony compact digital camera"
    ),
    "EBAY_MAX_PRICE": "60",
    "EBAY_PAGES_PER_QUERY": "3",
    "EBAY_DAILY_BUDGET": "4000",
    "MAX_NEW_PER_SCAN": "40",
    "POLL_INTERVAL_MINUTES": "30",
}

path = Path(__file__).parent / ".env"
raw = path.read_bytes()
text = raw.decode("utf-8", errors="replace").replace("\x00", "").replace("\ufeff", "")

lines, seen = [], set()
for line in text.splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    key, value = s.split("=", 1)
    key = key.strip()
    if key in seen:
        lines = [l for l in lines if not l.startswith(key + "=")]
    seen.add(key)
    lines.append(f"{key}={SETTINGS[key] if key in SETTINGS else value.strip()}")

for key, value in SETTINGS.items():
    if key not in seen:
        lines.append(f"{key}={value}")

# Path.write_text() only accepts newline= on Python 3.10+, and the VM
# runs 3.9 - open() explicitly instead so this works on both.
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(lines) + "\n")
print(f"settings updated ({len(lines)} keys, credentials untouched)")
