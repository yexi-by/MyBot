# MyBot

基于 FastAPI 和 NapCat 的 QQ 机器人框架，使用 Python 3.13+ 开发。

本项目主要用于学习和开发 QQ 机器人，集成了依赖注入、插件系统以及 LLM/RAG 等功能。

## 🛠️ 技术栈

*   **Web 框架**: FastAPI, Uvicorn (全异步设计)
*   **依赖注入**: Dishka (APP/SESSION 双层作用域)
*   **协议端**: NapCat (OneBot11/Red 协议)
*   **插件机制**: 包含基于 AST 的静态死锁检测
*   **数据存储**: Redis (消息队列与缓存), FAISS (向量数据库)
*   **AI 支持**: OpenAI / Gemini / DeepSeek 接口集成, SiliconFlow Embedding
*   **包管理**: uv

---

## 🚀 部署 (Docker)

提供构建好的 Docker 镜像，可直接通过 Docker Compose 启动。

### 1. 准备工作

创建必要的目录和配置文件：

```bash
mkdir -p debug logs plugins_config vector
touch setting.toml
```

### 2. 启动服务

创建 `docker-compose.yml`：

```yaml
services:
  mybot:
    image: docker.io/yexi12345/mybotdev:latest
    container_name: mybot
    restart: unless-stopped
    ports:
      - "6055:6055"
    volumes:
      - ./debug:/app/debug
      - ./logs:/app/logs
      - ./plugins_config:/app/plugins_config
      - ./vector:/app/vector
      - ./setting.toml:/app/setting.toml
```

运行：

```bash
docker-compose up -d
```

---

## 💻 本地开发

如需进行插件开发或调试，请参考以下步骤。**注意：本项目仅支持使用 `uv` 进行依赖管理。**

### 1. 环境准备

*   Python 3.13+
*   Redis
*   [NapCat](https://github.com/NapNeko/NapCatQQ)
*   [uv](https://github.com/astral-sh/uv)

### 2. 安装与运行

```bash
# 1. 安装依赖
uv sync

# 2. 配置 setting.toml (参考下方配置说明)
cp setting.example.toml setting.toml  # 如果有示例文件的话，或者手动创建

# 3. 运行
uv run main.py
```

### 配置文件示例 (`setting.toml`)

```toml
faiss_file_location = "./vector"
video_and_image_path = "./logs/media"
password = "YOUR_NAPCAT_TOKEN"  # NapCat Token

[redis_config]
host = "localhost"
port = 6379
db = 0
password = ""

[[llm_settings]]
api_key = "sk-xxxx"
base_url = "https://api.openai.com/v1"
model_vendors = "openai"
provider_type = "openai"

[embedding_settings]
api_key = "sk-xxxx"
provider_type = "siliconflow"
```

---

## 🏗️ 架构说明

### 1. 系统架构图

系统通过 Dishka 容器进行组件管理，分为核心层、服务层和插件层。

```mermaid
graph TB
    subgraph External["外部环境"]
        NapCat["NapCat (QQ协议)"]
    end

    subgraph Core["核心层"]
        Server["NapCatServer"]
        Dispatcher["EventDispatcher"]
        PluginCtrl["PluginController"]
    end

    subgraph DI["依赖注入 (Dishka)"]
        ScopeApp["Scope: APP (全局)"]
        ScopeSession["Scope: SESSION (会话)"]
    end

    subgraph Plugins["插件层"]
        P_List["各类业务插件"]
        Queue["异步任务队列"]
    end

    NapCat <--> Server
    Server --> Dispatcher
    Dispatcher --> PluginCtrl
    PluginCtrl --> Queue
    Queue --> P_List
    
    P_List --> DI
    DI --> ScopeApp & ScopeSession
```

### 2. 插件加载流程

在启动时，PluginController 会通过 AST 分析插件源码，检测潜在的死锁风险。

```mermaid
sequenceDiagram
    participant Boot as 启动入口
    participant Ctrl as PluginController
    participant AST as AST分析
    
    Boot->>Ctrl: 加载插件类
    Ctrl->>AST: 读取源码
    AST->>AST: 分析 emit 调用链
    AST-->>Ctrl: 返回依赖关系
    Ctrl->>Ctrl: 检测是否存在环
    
    alt 存在死锁
        Ctrl--xBoot: 报错并终止
    else 检测通过
        Ctrl->>Boot: 继续启动
    end
```

### 3. 说明

*   **NapCatServer**: 处理 WebSocket 连接和数据接收。
*   **依赖注入**: 使用 Dishka 管理对象生命周期。`Scope.APP` 用于全局共享资源（如 Redis、LLM 客户端），`Scope.SESSION` 用于单次连接资源（如 BotClient）。
*   **AST 分析**: 为了避免插件间互相 `emit` 事件导致死锁，项目在启动阶段会解析插件源码并构建调用图，发现闭环则禁止启动。

---

## 📂 项目结构

```
MyBot/
├── app/
│   ├── api/             # QQ 协议 API 封装
│   ├── config/          # 配置定义
│   ├── core/            # 核心组件 (Server, DI, Dispatcher)
│   ├── database/        # 数据库操作
│   ├── models/          # 数据模型 (Pydantic)
│   ├── plugins/         # 插件目录
│   │   ├── base.py      # 插件基类
│   │   └── ...
│   ├── services/        # 业务服务 (LLM, RAG 等)
│   └── utils/           # 工具类
├── main.py              # 入口文件
└── ...
```

---

## 🔌 插件开发

继承 `BasePlugin` 类即可开发插件。

### 1. 基础示例

```python
from app.plugins import BasePlugin
from app.models import GroupMessage

class MyPlugin(BasePlugin[GroupMessage]):
    name = "demo_plugin"
    consumers_count = 1
    priority = 10

    def setup(self) -> None:
        # 初始化逻辑
        pass

    async def run(self, msg: GroupMessage) -> bool:
        if msg.raw_message == "ping":
            # 使用 self.context 调用 API
            await self.context.bot.send_group_msg(
                group_id=msg.group_id,
                message="pong"
            )
            return True
        return False
```

### 2. Context 对象

插件可以通过 `self.context` 访问系统服务：

*   `self.context.bot`: QQ 机器人 API
*   `self.context.llm`: LLM 调用接口
*   `self.context.database`: Redis 操作
*   `self.context.search_vectors`: 向量检索
*   `self.context.settings`: 全局配置

## 📄 License

GPL-3.0 License
