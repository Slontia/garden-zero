#!/usr/bin/env bash
# Fresh Ubuntu/systemd host only. No removal, upgrade, Desktop or disk migration.
set -euo pipefail
if [[ $# != 1 || ( ${1:-} != --install && ${1:-} != --resume ) ]]; then
  echo 'Usage: sudo bash core/scripts/install-docker-engine.sh --install|--resume'
  echo 'Installs native Engine from Docker official stable apt repository on a fresh host.'
  exit 2
fi
[[ $EUID == 0 ]] || { echo 'Requires root on the normal WSL/Linux host.' >&2; exit 1; }
[[ $(ps -p 1 -o comm=) == systemd ]] || {
  echo 'BLOCKED: systemd is not PID 1. Inspect normal WSL host configuration first.' >&2; exit 1;
}
. /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Only official Ubuntu is handled.' >&2; exit 1; }
case ${VERSION_CODENAME:-} in resolute|stonking|noble|jammy) ;; *) echo 'Ubuntu release not validated by this installer.' >&2; exit 1 ;; esac
for package in docker-ce docker-ce-cli docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd containerd.io runc; do
  state=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)
  [[ $state != installed ]] || { echo "BLOCKED: existing package $package; inspect and reuse/migrate explicitly." >&2; exit 1; }
done
for directory in /var/lib/docker /var/lib/containerd; do
  if [[ -d $directory && -n $(find "$directory" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
    echo "BLOCKED: existing state in $directory; preserve and inspect first." >&2; exit 1
  fi
done
[[ ! -e /var/run/docker.sock && ! -e /etc/docker/daemon.json ]] || {
  echo 'BLOCKED: existing Docker socket/configuration; inspect first.' >&2; exit 1;
}
# Resume only after validating the exact source and freshly downloaded official key.
if [[ $1 == --install ]]; then
if grep -rl 'download\.docker\.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  echo 'BLOCKED: existing Docker apt repository; inspect it before continuing.' >&2; exit 1
fi
[[ ! -e /etc/apt/sources.list.d/docker.sources && ! -e /etc/apt/keyrings/docker.asc ]] || {
  echo 'BLOCKED: Docker repository files already exist; no files overwritten.' >&2; exit 1;
}
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
mkdir -p "$root/system/runtime"
work=$(mktemp -d "$root/system/runtime/engine-install-XXXXXXXX")
exec > >(tee "$work/install.log") 2>&1
printf 'Artifacts retained: %s\n' "$work"
apt-get -o APT::Update::Error-Mode=any update
apt-get install --no-remove -y ca-certificates curl
install -d -m 0755 /etc/apt/keyrings
echo 'Downloading official signing key (up to 3 retries, certificate verification enabled).'
if ! curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
  --connect-timeout 10 --max-time 30 --retry 3 --retry-all-errors --retry-delay 2 --retry-max-time 100 \
  https://download.docker.com/linux/ubuntu/gpg -o "$work/docker.asc"; then
  echo 'BLOCKED: official Docker HTTPS endpoint unreachable; no key/source installed.' >&2
  echo 'Check host network or proxy access to download.docker.com. Do not disable TLS verification.' >&2
  exit 1
fi
[[ -s $work/docker.asc ]] || { echo 'Empty signing key download; refusing install.' >&2; exit 1; }
architecture=$(dpkg --print-architecture)
cat > "$work/docker.sources" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $VERSION_CODENAME
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
if [[ $1 == --resume ]]; then
  [[ -f /etc/apt/keyrings/docker.asc && ! -L /etc/apt/keyrings/docker.asc && -f /etc/apt/sources.list.d/docker.sources && ! -L /etc/apt/sources.list.d/docker.sources ]] || {
    echo 'BLOCKED: expected regular repository files missing.' >&2; exit 1;
  }
  cmp -s "$work/docker.asc" /etc/apt/keyrings/docker.asc && cmp -s "$work/docker.sources" /etc/apt/sources.list.d/docker.sources || {
    echo 'BLOCKED: existing source/key differ from official expected files; no overwrite.' >&2; exit 1;
  }
  while IFS= read -r entry; do
    [[ $entry == /etc/apt/sources.list.d/docker.sources ]] || {
      echo 'BLOCKED: additional Docker repository entry; inspect first.' >&2; exit 1;
    }
  done < <(grep -rl 'download\.docker\.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null || true)
else
  install -m 0644 "$work/docker.asc" /etc/apt/keyrings/docker.asc
  install -m 0644 "$work/docker.sources" /etc/apt/sources.list.d/docker.sources
fi
apt-get -o APT::Update::Error-Mode=any update
packages=()
for package in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
  # Select only versions listed from the official stable repository for this suite.
  version=$(apt-cache madison "$package" | awk -F '|' -v suite="$VERSION_CODENAME/stable" \
    '!found && $3 ~ /https:\/\/download.docker.com\/linux\/ubuntu/ && index($3,suite) {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; found=1}')
  [[ -n $version ]] || { echo "No official stable version found for $package" >&2; exit 1; }
  packages+=("$package=$version")
done
printf '%s\n' "${packages[@]}" | tee "$work/package-versions.txt"
apt-get install --no-remove -y "${packages[@]}"
# Do not add users to the high-privilege docker group or alter firewall rules.
systemctl start docker
/usr/bin/docker --host unix:///var/run/docker.sock version
/usr/bin/docker --host unix:///var/run/docker.sock compose version
/usr/bin/docker --host unix:///var/run/docker.sock info --format 'Name={{.Name}} OS={{.OperatingSystem}} Root={{.DockerRootDir}}'
/usr/bin/docker --host unix:///var/run/docker.sock ps -a --format '{{.Names}} | {{.Image}} | {{.Status}}'
/usr/bin/docker --host unix:///var/run/docker.sock volume ls
dpkg-query -W -f='${binary:Package} ${Version}\n' docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin > "$work/installed-versions.txt"
echo 'Native Engine installed. No NAS applications or database tests have been started.'
echo 'Package versions are recorded, not held; review future apt upgrades explicitly.'
