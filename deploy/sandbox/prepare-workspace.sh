#!/bin/sh
# 仅由部署操作者在目标 Linux 主机执行；不会清空或迁移已有目录。
set -eu
workspace=${1:?usage: prepare-workspace.sh ABSOLUTE_NEW_PATH [QUOTA_BYTES]}
quota=${2:-50000000000}
case "$workspace" in /*) ;; *) echo 'workspace must be absolute' >&2; exit 1;; esac
case "$quota" in ''|*[!0-9]*) echo 'quota must be an integer' >&2; exit 1;; esac
[ "$quota" -ge 1000000 ] || exit 1
[ ! -e "$workspace" ] || { echo 'target already exists; inspect it instead of replacing it' >&2; exit 1; }
parent=$(dirname -- "$workspace")
[ -d "$parent" ] || { echo 'parent directory must already exist' >&2; exit 1; }
[ "$(findmnt -n -o FSTYPE -T "$parent")" = btrfs ] || { echo 'Btrfs is required' >&2; exit 1; }
# 不自动给整个已有存储卷启用配额或重建配额层级。
btrfs qgroup show "$parent" >/dev/null
btrfs subvolume create "$workspace"
btrfs qgroup limit "$quota" "$workspace"
chown 1000:1000 "$workspace"
chmod 0700 "$workspace"
btrfs qgroup show --raw -reF "$workspace"
