#!/bin/sh
# 在 Compose 创建 qah-sandbox 网络之后，以 root 执行。只维护本项目专用链。
set -eu
bridge=br-qah-sbx
proxy=172.30.240.2
[ -d "/sys/class/net/$bridge" ] || { echo 'sandbox bridge does not exist' >&2; exit 1; }
[ "$(cat /proc/sys/net/bridge/bridge-nf-call-iptables)" = 1 ] || { echo 'bridge netfilter must already be enabled' >&2; exit 1; }
python3 - <<'PY'
import http.client
import json
import socket
import urllib.parse

connection = http.client.HTTPConnection("localhost", timeout=10)
connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.sock.settimeout(10)
connection.sock.connect("/var/run/docker.sock")
filters = urllib.parse.quote(json.dumps({"label": ["qq-agent-harness.execution=true"]}))
connection.request("GET", "/v1.45/containers/json?filters=" + filters)
response = connection.getresponse()
if response.status != 200:
    raise SystemExit("cannot verify active sandbox executions")
if json.loads(response.read()):
    raise SystemExit("stop sandbox execution before changing its firewall")
connection.close()
PY
iptables -N QAH-SANDBOX 2>/dev/null || true
iptables -F QAH-SANDBOX
iptables -A QAH-SANDBOX -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A QAH-SANDBOX -d "$proxy" -p tcp --dport 3128 -j ACCEPT
iptables -A QAH-SANDBOX -j REJECT
for chain in FORWARD DOCKER-USER; do
  while iptables -C "$chain" -i "$bridge" -j QAH-SANDBOX 2>/dev/null; do iptables -D "$chain" -i "$bridge" -j QAH-SANDBOX; done
  iptables -I "$chain" 1 -i "$bridge" -j QAH-SANDBOX
done
while iptables -C INPUT -i "$bridge" -j REJECT 2>/dev/null; do iptables -D INPUT -i "$bridge" -j REJECT; done
iptables -I INPUT 1 -i "$bridge" -j REJECT
for chain in INPUT FORWARD; do
  while ip6tables -C "$chain" -i "$bridge" -j REJECT 2>/dev/null; do ip6tables -D "$chain" -i "$bridge" -j REJECT; done
  ip6tables -I "$chain" 1 -i "$bridge" -j REJECT
done
