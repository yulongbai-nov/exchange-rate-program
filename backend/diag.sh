#!/bin/sh
# Temporary startup diagnostics for the WeChat API TLS failure (verify_code=18).
# Prints only names/paths/subjects; never prints secret values.
echo '=== DIAG START ==='
echo '-- env var names only --'
env | cut -d= -f1 | sort | tr '\n' ' '
echo
echo '-- CBR_CA_ID --'
echo "${CBR_CA_ID:-unset}"
echo '-- mounts --'
(mount 2>/dev/null | grep -Ei 'cert|mnt' | head -10) || true
echo '-- /app/cert tree --'
ls -laR /app/cert 2>&1 | head -60
echo '-- cert file subjects/issuers --'
for f in $(find /app/cert -maxdepth 3 -type f 2>/dev/null | head -20); do
  echo "FILE: $f"
  case "$f" in
    *.crt|*.pem|*.cer|*.cert)
      openssl x509 -in "$f" -noout -subject -issuer -dates 2>&1 | head -4 ;;
    *) echo "  (non-cert, $(wc -c < "$f" 2>/dev/null) bytes)" ;;
  esac
done
echo '-- openssl s_client api.weixin.qq.com --'
(echo | timeout 12 openssl s_client -connect api.weixin.qq.com:443 -servername api.weixin.qq.com -showcerts 2>&1 | sed -n '1,40p') || true
echo '-- python urllib with /app/cert CA if present --'
python - <<'PY' 2>&1 | head -20
import glob, ssl, urllib.request
cands = glob.glob('/app/cert/**/*.crt', recursive=True) + glob.glob('/app/cert/**/*.pem', recursive=True)
print('candidate CAs:', cands[:10])
try:
    ctx = ssl.create_default_context()
    for f in cands[:10]:
        try:
            ctx.load_verify_locations(cafile=f)
            print('loaded extra CA:', f)
        except Exception as e:
            print('load failed', f, type(e).__name__)
    req = urllib.request.Request('https://api.weixin.qq.com/sns/jscode2session?appid=x&secret=y&js_code=z&grant_type=authorization_code')
    try:
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print('request outcome:', type(e).__name__, str(e)[:120])
except Exception as e:
    print('ctx error', type(e).__name__, str(e)[:120])
PY
echo '=== DIAG END ==='
exec python -m backend.server --host 0.0.0.0 --port 8000 --db /data/rates.sqlite3
