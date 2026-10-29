# QQ 原图上传快速建连

这是针对已证实的 TCP 握手间歇性无回应的可选缓解方案。默认部署不启动它；正常网络无需启用。它没有修复路由器或运营商侧的丢包，也不保证每次发送都在固定时间内完成。

## 已验证的行为

- 每次连接最多尝试 4 次，每次建连等待 0.5 秒；仅在尚未向 QQ 上传节点发送任何数据时重试。
- 一旦连接建立，直接双向转发原始字节。传输期间断开就结束连接，不重放已发送数据，不重新调用 QQ 发消息接口。
- 不压缩、转码或改变原图；不调整 MyBot/NapCat 的动作超时。
- 只重定向指定来源 IPv4、目标 IPv4 清单和 TCP 80/8080。清单外的流量保持原路径。相同节点上的其他 QQ 上传也可能匹配，规则不解析业务内容。
- 最多接受 32 个转发连接，单方向读写空闲 180 秒后关闭。日志只输出事件、次数、耗时、字节数和错误类型。

一次真实测试使用约 2.30 MB 的 PNG，保持像素与 PNG 编码，仅增加随机文本元数据以强制上传。冷连接下，经临时转发的两次发送分别用时 6.01 秒、4.82 秒；撤销并空闲 90 秒后，原路径在 60 秒超时。转发记录显示，失败建连在 0.5～1 秒内重建成功。样本有限，这支持局部缓解方向，不能当作长期稳定性保证。

## 部署前核对

适用于 Linux 上使用桥接 IPv4 网络的 NapCat。转发服务使用宿主网络及 `NET_ADMIN`，在宿主 NAT `PREROUTING` 前置自己的 `QAH-QQ-UPLOAD` 链。每个重定向规则同时限定 NapCat 来源和上传目标。服务不挂载 Docker socket、配置目录或 secrets。

必须核对宿主与镜像使用相同的 iptables 后端，并只运行一个本服务实例。现有透明代理若需要连接标记绕过，使用该代理当前已经支持的标记；不要猜测或新增全局旁路规则。没有透明代理时标记填写 `0`。

先只读确认下面的值，再逐项授权新增独立的 `qq-upload-relay.env`。它不替代现有 `.env`，也不改 `config/`、提示词、模型、群权限、secrets 或 NapCat 配置。

| 新增字段 | 值的来源 |
| --- | --- |
| `QQ_UPLOAD_IMAGE` | 用仓库 Dockerfile 构建并推送的完整应用镜像，使用明确标签 |
| `QQ_UPLOAD_SOURCE_IP` | NapCat 当前且稳定的容器 IPv4 |
| `QQ_UPLOAD_LISTEN_IP` | 宿主在 NapCat 桥接网络上的网关 IPv4 |
| `QQ_UPLOAD_DESTINATIONS` | 抓包确认的 QQ 上传节点 IPv4，逗号分隔；不要填写整个公网网段 |
| `QQ_UPLOAD_BYPASS_MARK` | 现有透明代理允许旁路的 socket mark；没有代理时为 `0` |

监听端口固定为 `52347`，需要确认宿主端口空闲。若容器地址变化，先停止本服务并核对来源；若 QQ 改用清单外的上传节点，该连接不会得到加速。不要自动扩大地址范围。

## 发布与启停

构建使用仓库根目录的完整 `Dockerfile`，与现有应用镜像流程一致。只给独立转发服务使用新标签，原有 MyBot 和 NapCat 不需要重启或更新镜像。远端只接收 Compose 模板和经授权的独立环境配置，应用代码必须通过镜像仓库交付。

将 [Compose 模板](../deploy/qq-upload-relay.compose.yml) 与新建的 `qq-upload-relay.env` 放在同一部署目录，字段填写远端核对值。授权后运行：

```sh
docker compose --env-file ./qq-upload-relay.env -f ./qq-upload-relay.compose.yml pull
docker compose --env-file ./qq-upload-relay.env -f ./qq-upload-relay.compose.yml up -d
docker compose --env-file ./qq-upload-relay.env -f ./qq-upload-relay.compose.yml logs --tail=30
```

先确认出现 `qq_upload_ready`，再在已授权测试群发送新原图验收。已有上传 TCP 连接不会迁移，后续新连接才适用新规则。

撤销时正常停止独立服务即可：

```sh
docker compose --env-file ./qq-upload-relay.env -f ./qq-upload-relay.compose.yml down
iptables -t nat -S
```

正常退出先移除本服务的规则，再关闭转发连接；检查输出中不再有 `QAH-QQ-UPLOAD`。正在经过它上传的连接可能中断，不要在上传进行时停服务。

进程被 `SIGKILL` 或 OOM 强制结束时，宿主规则会暂时保留并影响匹配连接；Compose 自动重启后会重建本服务专用链。如果不再启动服务，需要在确认容器已停止后执行下面的定向清理，不能清空整个 NAT 表：

```sh
iptables -t nat -D PREROUTING -p tcp -j QAH-QQ-UPLOAD
iptables -t nat -F QAH-QQ-UPLOAD
iptables -t nat -X QAH-QQ-UPLOAD
```

透明代理重置宿主 NAT 规则后应重新核对链的位置；本服务不会轮询或反复覆盖其他服务的规则。

## 隔离验收

测试在本机创建专用的内部 Docker 网络和小配额容器，所有故障规则都在测试容器网络空间内，不使用宿主网络或联系 QQ：

```powershell
docker build -t qq-agent-harness:qq-upload-relay-candidate .
$env:QAH_UPLOAD_RELAY_TEST = '1'
$env:QAH_APP_IMAGE = 'qq-agent-harness:qq-upload-relay-candidate'
uv run --locked pytest tests/test_upload_relay.py -q
```

验收覆盖：真实 SYN 丢弃后的重连、约 2.30 MB 内容逐字节一致、连接中断不重放、未匹配来源/端口旁路、建连重试耗尽及时关闭，以及退出后防火墙规则完整恢复。
