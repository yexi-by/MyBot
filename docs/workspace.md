# 共享工作区说明

QQ Agent Harness 采用单容器持久化共享工作区（`[workspace]`），为开放了 `workspace` 能力的会话提供文件读写、精确文本编辑、全文搜索及受控本地子进程执行能力，无需部署额外的 Docker-in-Docker Runner 或出口网关容器。

## 目录结构与隔离边界

- **工作区根目录（`workspace.root`）**：默认 `./workspace`，容器部署时挂载宿主机独立目录至 `/workspace`（`WORKSPACE_DIR=/path/to/workspace`）。
- **配置目录严格分离与双 UID 内核隔离**：
  - 启动与操作时自动校验 `workspace.root` 与 `config/` 互不重叠、互不包含；任何指向工作区外的路径穿越、外链符号链接或命名管道/设备文件都会被拒绝。
  - 容器部署时启用内核双 UID 隔离：主服务以最小能力集（`SETUID`/`SETGID`/`CHOWN`/`DAC_OVERRIDE`/`FOWNER`/`KILL`）独占 `0700` 的 `/app`（含 `/app/config`），而 `workspace__exec` 拉起的所有子进程均通过内核降权运行在独立低权限用户 `1000:1000`（`HARNESS_WORKSPACE_UID=1000`、`no-new-privileges:true`、空 capability），从内核层彻底阻断子进程读取配置/密钥或触碰主进程 `/proc/1`；主进程在 `/workspace` 写入的文件和目录会自动 `chown` 至 `1000:1000`，保证双向读写畅通。
- **内置系统目录（WebUI 默认折叠）**：
  - `.home/`：命令子进程的 `HOME` 与 npm 全局前缀目录。
  - `.cache/`：`uv`、`npm`、`matplotlib` 与 `XDG_CACHE_HOME` 缓存目录，可通过控制台「清理依赖缓存」一键释放。
  - `.scratch/`：`qq__save_media_to_workspace` 省略目标路径时的临时媒体落盘区。
  - `.outputs/`：命令输出超出 `output_limit` 时的完整日志溢出文件（自动保留最新 64 份或 256 MiB 以内）。
  - `.tmp/`：每次 `workspace__exec` 独占创建 `.tmp/exec-<id>` 作为 `TMPDIR`/`TMP`/`TEMP`，命令结束后自动清理，服务重启时也会清扫残留目录。

## 工具清单（`workspace` 分类）

可在各群与私聊的「能力权限 → 共享工作区」中整体开关，也可按单个工具精细覆盖（例如仅开放文件读写而禁用 `workspace__exec`）：

| 工具 | 说明 |
| --- | --- |
| `workspace__read_file` | 按 UTF-8 字节偏移分段读取文本文件，保持多字节字符完整。 |
| `workspace__write_file` | 创建、覆盖或追加写入 UTF-8 文本文件，自动创建父目录。 |
| `workspace__edit_file` | 基于唯一原文片段精确替换文件内容，无需整文件重写。 |
| `workspace__list_files` | 分页列出工作区目录项（含大小、修改时间与系统目录标记）。 |
| `workspace__search_files` | 在工作区文本文件中按关键字或正则表达式搜索匹配行（自动跳过隐藏系统目录与二进制文件）。 |
| `workspace__delete_path` | 删除工作区内的指定文件或子目录（禁止删除工作区根目录）。 |
| `workspace__exec` | 在工作区内以洁净白名单环境变量执行命令子进程，支持超时强杀整棵子进程树与首尾输出保留。 |

## 子进程环境与内置工具链

- **洁净环境变量**：`workspace__exec` 仅向子进程注入 `HOME`、`PATH`、`TMPDIR`、编码及显式系统代理变量，不会把宿主或进程环境中的凭据泄漏给子进程；若工作区内存在 `.venv/`，自动将其加入 `PATH` 首位。
- **预装工具链（单镜像内置）**：
  - 语言与包管理：Python 3.14、`uv` / `uvx`、Node.js 24、`npm` / `npx`
  - 命令行工具：`bash`、`git`、`rg`（ripgrep）、`fd`、`jq`、`curl`、`ffmpeg` / `ffprobe`、`pdftotext` / `pdftoppm`（poppler）、`qpdf`、`sqlite3`、`7z` / `zip` / `unzip` / `zstd`
  - 字体与排版：Noto Sans/Serif CJK（中日韩字体）、Noto Color Emoji
  - 预装全局 Python 库：`requests`、`httpx`、`beautifulsoup4`、`lxml`、`pyyaml`、`pillow`、`matplotlib`、`cairosvg`、`resvg-py`、`numpy`、`pandas`、`pypdf`、`pdfplumber`、`openpyxl`、`reportlab`、`markdown`、`qrcode`、`imageio`
  - 按需扩展冷门包：可在工作区内执行 `uv venv --system-site-packages .venv && uv pip install <pkg>` 持久化安装，或在单次命令中使用 `uv run --with <pkg> script.py`。

## 容量与并发控制

- `max_bytes`（默认 50 GB）：软容量预算上限。使用量按 5 秒缓存统计；超出预算后拒绝新写入、编辑与命令执行，但文件读取、列表、搜索、删除与控制台清理保持可用，便于释放空间。
- `concurrent_executions`（默认 2）与 `max_pending_operations`（默认 32）：有界信号量控制并发子进程数与等待队列，会话停止或控制台点击「停止执行」会立即终止对应进程组。

## 容器部署示例

```bash
docker build -t qq-agent-harness:latest .
HARNESS_IMAGE=qq-agent-harness:latest WORKSPACE_DIR=/data/mybot-workspace docker compose up -d
```

确保宿主机上的 `WORKSPACE_DIR` 对容器运行用户 `1000:1000` 可读写（`chown -R 1000:1000 /data/mybot-workspace`）。
