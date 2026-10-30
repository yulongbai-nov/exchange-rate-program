#!/bin/sh
# Temporary startup diagnostics for the WeChat API TLS failure (verify_code=18).
# Prints only names/paths and masked values; never prints secret values.
echo '=== DIAG START ==='
echo '-- id --'
id 2>&1 || true
echo '-- mounts mentioning cert/mnt --'
(mount 2>/dev/null | grep -Ei 'cert|/mnt' | head -10) || true
echo '-- /mnt/cert --'
ls -la /mnt/cert 2>&1 || true
echo '-- /mnt (top) --'
ls -la /mnt 2>&1 || true
echo '-- proxy / ssl env (values masked) --'
env | grep -iE 'proxy|ssl|cert|ca_' | sed 's/=.*/=<set>/' || true
echo '-- WX_APP_ID present? --'
if [ -n "$WX_APP_ID" ]; then echo 'yes'; else echo 'no'; fi
echo '-- dns --'
getent hosts api.weixin.qq.com 2>&1 || true
echo '-- python default verify paths --'
python -c "import ssl;print(ssl.get_default_verify_paths())" 2>&1 || true
echo '-- candidate CA files --'
find /mnt /usr/local/share/ca-certificates /etc/ssl/certs -maxdepth 3 -type f \( -name '*.crt' -o -name '*.pem' \) 2>/dev/null | head -30
echo '-- openssl present? --'
command -v openssl || true
if command -v openssl >/dev/null 2>&1; then
  echo '-- peer chain summary --'
  (echo | openssl s_client -connect api.weixin.qq.com:443 -servername api.weixin.qq.com -showcerts 2>&1 | grep -E 'depth=|subject=|issuer=' | head -12) || true
fi
echo '=== DIAG END ==='
exec python -m backend.server --host 0.0.0.0 --port 8000 --db /data/rates.sqlite3
