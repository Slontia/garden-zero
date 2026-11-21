#!/usr/bin/env bash
# Does not print proxy URLs, credentials or HTTP headers.
set -u
printf 'User: '; id -un
printf 'Configured proxy environment variable names (values hidden):\n'
python3 - <<'PY'
import os
keys = ('http_proxy','https_proxy','all_proxy','no_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
found = [k for k in keys if os.environ.get(k)]
print(', '.join(found) if found else '(none; transparent/TUN proxy may still apply)')
PY
url=https://download.docker.com/linux/ubuntu/gpg
printf '\nCurrent environment, target: Docker official signing key\n'
curl -q --fail --silent --show-error --location --connect-timeout 8 --max-time 20 \
  --output /dev/null --write-out 'HTTP=%{http_code} explicit_proxy=%{proxy_used}\n' "$url"
current=$?
printf 'curl exit=%s\n' "$current"
printf '\nExplicit curl proxy bypass (does not bypass a transparent/TUN proxy)\n'
curl -q --proxy '' --noproxy '*' --fail --silent --show-error --location --connect-timeout 8 --max-time 20 \
  --output /dev/null --write-out 'HTTP=%{http_code} explicit_proxy=%{proxy_used}\n' "$url"
printf 'curl exit=%s\n' "$?"
exit "$current"
