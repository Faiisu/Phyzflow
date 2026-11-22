#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
    printf 'Usage: %s\n' "$0" >&2
    exit 1
fi

if [ "$(uname -s)" != Linux ] || [ ! -d /run/systemd/system ]; then
    printf '%s\n' 'This installer requires Linux running systemd.' >&2
    exit 1
fi

deploy_uid=$(id -u)
if [ "$deploy_uid" -eq 0 ]; then
    printf '%s\n' 'Run this script as the deployment user. It uses sudo to install the system service.' >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

unit_value() {
    if [ "$(printf '%s' "$1" | LC_ALL=C tr -d '[:cntrl:]')" != "$1" ]; then
        printf '%s\n' 'Service paths cannot contain control characters.' >&2
        return 1
    fi
    printf '%s' "$1" | sed 's/[\\"]/\\&/g; s/%/%%/g'
}

exec_path=$(unit_value "$repo_dir/.venv/bin/workspace")

sudo -v
"$repo_dir/deploy/phyzflow.sh" --install-only
uv_dir=$(CDPATH= cd -- "$(dirname -- "$(command -v uv)")" && pwd -P)
service_path=$(unit_value "$uv_dir:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

deploy_home=$(getent passwd "$deploy_uid" | cut -d: -f6)
: "${deploy_home:?Could not determine the home directory for the deployment account.}"
state_dir="$deploy_home/.phyzflow"
(
    umask 077
    mkdir -p -- "$state_dir"
    if [ ! -e "$state_dir/network.json" ]; then
        set -C
        printf '%s\n' '{"ipv4":"127.0.0.1","port":18080}' > "$state_dir/network.json"
    fi
)

unit_dir=$(mktemp -d)
trap 'rm -rf -- "$unit_dir"' 0
unit=phyzflow.service
cat > "$unit_dir/$unit" <<EOF
[Unit]
Description=Phyzflow application

[Service]
Type=exec
User=$deploy_uid
WorkingDirectory=~
Environment="PATH=$service_path"
ExecStart=":$exec_path" app
KillMode=mixed
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

if systemd-analyze --help | grep -q -- '--recursive-errors'; then
    set -- --recursive-errors=no
fi
verify_log="$unit_dir/verify.log"
if ! systemd-analyze "$@" verify "$unit_dir/$unit" > "$verify_log" 2>&1; then
    cat "$verify_log" >&2
    exit 1
fi
grep -F -- "$unit" "$verify_log" >&2 || :
sudo install -m 0644 "$unit_dir/$unit" "/etc/systemd/system/$unit"
sudo systemctl daemon-reload
sudo systemctl restart "$unit"
sudo systemctl enable "$unit"
printf 'Check startup with: systemctl status %s --no-pager\n' "$unit"
