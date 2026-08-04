#!/usr/bin/env bash
set -Eeuo pipefail

# Run as root:
#   REPO_URL=https://github.com/Ugo5738/swimbuddz-backend.git \
#   bash bootstrap-server.sh
#
# Optional:
#   DEPLOY_USER=deploy DEPLOY_BRANCH=main DEPLOY_PATH=/home/deploy/swimbuddz-backend

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_PATH="${DEPLOY_PATH:-/home/${DEPLOY_USER}/swimbuddz-backend}"
REPO_URL="${REPO_URL:-https://github.com/Ugo5738/swimbuddz-backend.git}"
SWAP_SIZE_GB="${SWAP_SIZE_GB:-4}"

log() {
  printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die "Run this script as root."

if [ ! -f /etc/os-release ]; then
  die "Cannot identify the operating system."
fi

. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) die "This script currently supports Ubuntu and Debian only. Found: ${ID:-unknown}" ;;
esac

log "Setting the server timezone to UTC"
timedatectl set-timezone UTC

log "Installing system packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg git ufw jq unzip

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose v2 is not available after Docker installation."
fi

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  log "Creating deployment user: $DEPLOY_USER"
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi

usermod -aG docker "$DEPLOY_USER"

log "Configuring firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

if ! swapon --show --noheadings | grep -q .; then
  log "Creating ${SWAP_SIZE_GB} GB swap file"
  fallocate -l "${SWAP_SIZE_GB}G" /swapfile || \
    dd if=/dev/zero of=/swapfile bs=1M count="$((SWAP_SIZE_GB * 1024))"
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  log "Swap already exists; leaving it unchanged"
fi

log "Preparing deployment directory"
mkdir -p "$DEPLOY_PATH"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$(dirname "$DEPLOY_PATH")"

if [ ! -d "$DEPLOY_PATH/.git" ]; then
  log "Cloning SwimBuddz backend"
  sudo -u "$DEPLOY_USER" git clone --branch "$DEPLOY_BRANCH" "$REPO_URL" "$DEPLOY_PATH"
else
  log "Repository already exists; updating it"
  sudo -u "$DEPLOY_USER" git -C "$DEPLOY_PATH" fetch origin
  sudo -u "$DEPLOY_USER" git -C "$DEPLOY_PATH" checkout "$DEPLOY_BRANCH"
  sudo -u "$DEPLOY_USER" git -C "$DEPLOY_PATH" reset --hard "origin/$DEPLOY_BRANCH"
fi

log "Preparing Traefik certificate storage"
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_PATH/letsencrypt"
touch "$DEPLOY_PATH/letsencrypt/acme.json"
chown "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_PATH/letsencrypt/acme.json"
chmod 600 "$DEPLOY_PATH/letsencrypt/acme.json"

log "Creating the external Docker proxy network"
docker network inspect swimbuddz_proxy >/dev/null 2>&1 || docker network create swimbuddz_proxy

log "Server bootstrap complete"
printf '\nDocker: %s\n' "$(docker --version)"
printf 'Compose: %s\n' "$(docker compose version)"
printf 'Timezone: %s\n' "$(timedatectl show --property=Timezone --value)"
printf 'Deploy path: %s\n' "$DEPLOY_PATH"
printf '\nLog out and reconnect before using Docker as %s, so the new group membership applies.\n' "$DEPLOY_USER"
