# 共享代码沙箱

沙箱工具权限由来源群的 `sandbox.execute`、`sandbox.files` 能力及工具覆盖决定。没有个人 ACL。所有获准使用沙箱的会话共享工作区；文件跨重启保留，运行中的命令不恢复。

## 部署契约

应用与可信 runner 在同一 Linux 主机，以 host network 运行。runner 仅监听 `127.0.0.1:6060`，使用配置中的独立 `runner_token`（至少 24 字符）认证。运行程序的容器不获得该凭据、Docker socket 或应用配置。应用镜像还需安装 `btrfs-progs`、`iptables` 和 `util-linux`。

仓库根目录的 `docker-compose.yml` 是服务和网络的唯一模板。`HARNESS_IMAGE` 必须指定已构建的应用镜像标签；`SANDBOX_IMAGE` 指向单独构建的工具链镜像；`SANDBOX_WORKSPACE` 是宿主上的专用 Btrfs 子卷。工作区挂载地址与该变量必须完全一致。

工具链镜像：

```sh
docker build -f Dockerfile.sandbox -t qq-agent-harness-sandbox:1.0.0 .
```

Python 3.14.8、uv/uvx 0.13.4、Node.js 24.22.1/npm 来自固定版本镜像，预装 Git、ripgrep、fd、jq、编译与全格式压缩工具、FFmpeg、ImageMagick、Poppler/QPDF、Pandoc、Graphviz、Tesseract 中英 OCR、SQLite、Noto CJK/Emoji 字体，以及常用科学计算、绘图、文档与网络 Python 库。运行时根目录只读，新增依赖和缓存应写到 `/workspace`，例如 `uv venv .venv` 后用该环境安装包。`HOME`、临时目录、uv/npm/Matplotlib 缓存均位于同一配额空间。

## 初始化工作区与公网出口

仅在目标主机上通过已发布的应用镜像执行；不需要复制应用源码或脚本到远端。不得指向已有业务目录。脚本会拒绝覆盖已有路径，不会自动给整个存储卷启用配额，也不会更改 Docker 存储驱动。先由操作者设置 `SANDBOX_PARENT` 为已有的 Btrfs 父目录，`SANDBOX_NAME` 为新的子目录名；Compose 中 `SANDBOX_WORKSPACE` 使用两者组成的宿主绝对路径。

```sh
docker pull "$HARNESS_IMAGE"
docker pull "$SANDBOX_IMAGE"
docker run --rm --privileged --user 0:0 \
  --mount "type=bind,src=$SANDBOX_PARENT,dst=/sandbox-parent" \
  --entrypoint sh "$HARNESS_IMAGE" \
  /app/deploy/sandbox/prepare-workspace.sh "/sandbox-parent/$SANDBOX_NAME" 50000000000
docker compose up -d egress
docker run --rm --privileged --network host --user 0:0 \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --entrypoint sh "$HARNESS_IMAGE" /app/deploy/sandbox/prepare-network.sh
docker compose up -d runner
```

配额使用子卷的 referenced 空间限制，默认 50,000,000,000 字节；镜像层和宿主诊断日志不计入工作区。每次执行/写入前检查实际限额，Btrfs 报告不一致或未设置配额时拒绝执行。变更配置中的容量后，部署操作者需同步修改实际 qgroup 限额再重启 runner；不会静默缩容、删除文件或退化成定时扫描。

runner 在启动时读取资源配置和连接令牌，随后保持这份生效快照。修改沙箱参数后，需要执行 `docker compose up -d --force-recreate runner`；WebUI 中的应用重启只重启 Harness，不会重建 runner。`GET /status` 在可用和不可用状态都返回 `applied_settings`，供应用对比实际生效资源；该字段不包含 `runner_token` 或 `runner_url`。连接地址或令牌变更后，还需重启 Harness 使连接快照一致。

沙箱只连接 Docker internal 网络。host 防火墙只放行其到专用出口代理的 TCP 3128，禁止连接宿主或其他目标，并拒绝 IPv6。出口代理接受核验过的公网 IPv4/IPv6 地址，按 DNS 候选逐个尝试连接，阻止私网地址、IPv4 别名、DNS 重绑定与重定向至私网。默认开放 80/443，可通过 egress 的 `HARNESS_EGRESS_PORTS` 显式设置允许的 HTTP/HTTPS 端口；`HARNESS_EGRESS_IDLE_TIMEOUT` 默认 180 秒，仅限制无流量的空闲连接，`HARNESS_EGRESS_CONNECTIONS` 默认 32。实际 IPv6 出口还依赖 Docker 和宿主网络路由。代理不持有应用凭据。

需要在宿主重启或防火墙规则重建后重新应用 `prepare-network.sh`。它只维护来源为沙箱网桥的专用规则，活动执行存在时拒绝更改；runner 在执行前核验 INPUT/FORWARD/DOCKER-USER 入口处的首条规则、完整专用链顺序、IPv6 拒绝规则以及实际 Docker internal 网络配置，避免规则虽然存在却被更早的 ACCEPT/RETURN 绕过。bridge netfilter 必须已经启用。核验失败时关闭执行能力，不得将代理放在普通沙箱可访问的应用控制网络里。

## 生命周期与清理

每个命令创建无特权、只读根文件系统的容器；同一时间只执行一个命令，其他命令在内存中等待，按 owner 可取消尚未开始的执行；文件读取与写入同样等待工作区释放。`max_pending_operations` 默认允许 32 项等待操作，超过容量返回 429 并说明本次未执行。默认 2 CPU、4 GiB 内存、256 进程、120 秒超时和 1 MiB 输出预览。超出预览时完整输出保存到工作区 `.outputs/`，回执带 `output_path` 供分段读取；日志收尾受本次执行剩余时间约束并可取消，未完整读取会标明 `output_complete=false`，不再固定 5 秒丢弃尾部。结束、超时、取消后强制回收整个容器，清除子进程及短暂容器日志；共享文件不删除。runner 启动时仅回收带专用执行标签的遗留容器。

普通目录写权限不足以防止 Btrfs 子卷绕过父子卷的配额：Linux v6.12 的子卷创建路径只检查目录写入/执行权限，拥有源子卷也可创建快照。因此执行镜像包含只读 bootstrap，runner 固定以 `/usr/local/bin/python -I -S /usr/local/libexec/qah-sandbox-guard.py` 启动；共享 HOME 的用户 site、`.pth` 和 `PYTHONPATH` 不会在防护安装前运行。[内核创建路径](https://github.com/torvalds/linux/blob/v6.12/fs/btrfs/ioctl.c)

bootstrap 设置 `no_new_privs` 后叠加 seccomp BPF，保留 Docker 默认过滤器。它按 ioctl 命令低 16 位拒绝 `0x9401/0x940e/0x9417/0x9418`（子卷/快照创建）和 `0x9428–0x942b`（配额控制），返回 `EPERM`；正常 `FICLONE` 文件复制不受这层过滤影响。过滤随 fork/exec 继承且不能撤销。只有原生小端 64 位 Linux x86_64/aarch64 支持启动；x32、32 位 compat 或其他 ABI 被拒绝，无法安装过滤器时退出 126，绝不继续执行命令。跨架构模拟不属于支持的部署方式。[Btrfs ioctl 定义](https://github.com/torvalds/linux/blob/v6.12/include/uapi/linux/btrfs.h)、[叠加 seccomp 语义](https://docs.kernel.org/userspace-api/seccomp_filter.html)

WebUI 可以分页查看文件与占用，清理缓存、选定文件或全部工作区；配额或网络检查失败时仍保留文件与清理入口。正在执行时清理返回 409；停止后才能清理。清理不跟随符号链接，不触及配置和知识库。工作区满时返回错误，不自动删除其他成果。

API：`GET /status`、`GET /files?path=&offset=&limit=`、`GET /file?path=`、`GET /file/text?path=&offset=&limit=`、`PUT /file`（支持 append）、`POST /exec`、`POST /cancel`、`POST /cleanup`。只有可信应用调用 runner API，WebUI 通过应用代理访问。代码工具本身没有清空工作区的 API。

文本不再因超过 1 MiB 整份拒读；`/file/text` 返回 `total_bytes` 和 `next_offset`，UTF-8 字符不会被切断。`sandbox.transfer_max_bytes` 统一二进制传输和文本写入的单次预算，默认 50 MB，大文本可以分段追加。追加也按修改已有文件授权，不能绕过群策略对 `overwrite` 的限制。

## 验证

`tests/test_sandbox.py` 覆盖配额失效阻断执行、取消/超时回收、输出上限、文件共享保留、鉴权、忙时清理冲突、路径/符号链接逃逸、非公网地址和 DNS 别名拒绝、连接 IP 固定，以及防火墙顺序和实际网络配置漂移。

显式设置 `QAH_DOCKER_TEST=1` 后运行 `tests/test_sandbox_docker.py`，验证真实工具链、容器限制和文件跨容器保留。显式设置 `QAH_NETWORK_TEST=1` 后运行 `tests/test_sandbox_network_integration.py`，验证真实代理 HTTP/HTTPS、uv/npm 公网访问、宿主/私网/IPv6/绕代理拒绝和公网重定向至私网的拒绝。网络测试需要本机 Docker 特权容器；发现已有项目网络、网桥或专用防火墙链时拒绝接管，并在结束时回收自己创建的全部资源。

`tests/test_sandbox_bootstrap.py` 在同一开关下比较未过滤时的 `ENOTTY` 与过滤后的 `EPERM`，验证 ioctl 方向/尺寸变体、x32/compat 拒绝、默认 seccomp 保留和持久 HOME 启动钩子隔离；无需创建任何真实子卷。aarch64 的 BPF 分支有平台无关验证，实际容器验收按测试机原生架构执行。

真实部署验收须在独立测试子卷使用小配额，写入不可压缩数据直到系统返回空间不足，并确认原有文件仍可读。Docker Desktop 没有 Btrfs 内核支持时不能替代该项；不能因模拟测试通过而宣称 NAS 硬配额已经实测。

依据：[Btrfs qgroup](https://btrfs.readthedocs.io/en/latest/btrfs-qgroup.html)、[Docker 资源约束](https://docs.docker.com/engine/containers/resource_constraints/)、[Docker 安全边界](https://docs.docker.com/engine/security/)。
