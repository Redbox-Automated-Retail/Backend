#!/bin/sh
# Set the admin dashboard login in .env: the Docker Compose (Linux) counterpart of `rb.ps1 set-admin-password`.
#   scripts/set-admin-password.sh [user]        (default user "admin"), then: docker compose up -d rb-admin
# Asks for the password twice (at least 10 characters), hashes it in the rb-admin image
# (python -m redbox_backend.dashboard.auth: PBKDF2-SHA256) and replaces ADMIN_USERNAME / ADMIN_PASSWORD_HASH in .env.
set -eu
cd "$(dirname "$0")/.."
user=${1:-admin}

ask() {
    printf '%s' "$1" >&2
    if [ -t 0 ]; then
        stty -echo
        trap 'stty echo' EXIT INT TERM
        IFS= read -r answer
        stty echo
        trap - EXIT INT TERM
        echo >&2
    else
        IFS= read -r answer
    fi
    printf '%s' "$answer"
}

p1=$(ask "Dashboard password for '$user': ")
p2=$(ask "Repeat it: ")
[ "$p1" = "$p2" ] || { echo "The passwords differ." >&2; exit 1; }
[ "${#p1}" -ge 10 ] || { echo "Use at least 10 characters." >&2; exit 1; }

hash=$(printf '%s\n' "$p1" | docker compose run --rm --no-deps -T rb-admin python -m redbox_backend.dashboard.auth | tail -n 1)
case "$hash" in
    pbkdf2_sha256\$*) ;;
    *) echo "Hashing failed (is the image built? docker compose build)." >&2; exit 1 ;;
esac

[ -f .env ] || cp .env.example .env
umask 077
grep -vE '^(ADMIN_USERNAME|ADMIN_PASSWORD_HASH)=' .env > .env.tmp || true
# single quotes: the hash contains $, which Compose would otherwise interpolate
printf "ADMIN_USERNAME=%s\nADMIN_PASSWORD_HASH='%s'\n" "$user" "$hash" >> .env.tmp
mv .env.tmp .env
echo "Saved the password hash for '$user' in .env. Run 'docker compose up -d rb-admin' to apply it." >&2
