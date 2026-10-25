/** 系统设置页：全部为启动期配置节，写入后需要重启进程。 */

import { ids, int, num, numtags, plain, secret, select, sw, text } from "./helpers";
import type { SectionDef } from "./types";

const LOG_LEVELS = plain(["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]);

export const SERVER_SECTION: SectionDef = {
  id: "server",
  title: "服务监听",
  base: ["server"],
  restart: true,
  desc: "HTTP 与 NapCat 反向 WebSocket 的监听参数，以及配置文件监听与电源操作的节奏。",
  fields: [
    text("host", "监听地址", { mono: true, placeholder: "0.0.0.0", required: true }),
    int("port", "端口", { min: 1, max: 65535, placeholder: "6055" }),
    text("websocket_path_prefix", "WebSocket 路由前缀", { mono: true, placeholder: "/ws", required: true }),
    select("log_level", "Uvicorn 日志级别", plain(["critical", "error", "warning", "info", "debug", "trace"])),
    sw("access_log", "访问日志", { desc: "记录每个 HTTP 请求" }),
    num("power_action_delay_seconds", "电源操作延迟", { unit: "秒", min: 0, zero: "下一轮事件循环执行", desc: "先让重启或关机接口返回响应，再优雅停机" }),
    int("config_watch_debounce_ms", "配置监听防抖", { unit: "毫秒", min: 0, desc: "文件变化后等待多久再重载" }),
    int("config_watch_step_ms", "配置监听轮询", { unit: "毫秒", min: 1 }),
    num("config_watcher_stop_timeout_seconds", "监听停止超时", { unit: "秒", gt: 0 }),
  ],
};

export const APP_SECTION: SectionDef = {
  id: "app",
  title: "应用",
  base: ["app"],
  restart: true,
  desc: "应用名与运行环境。",
  cols: 2,
  fields: [
    text("name", "应用名", { placeholder: "MyBot", required: true }),
    select("environment", "运行环境", plain(["development", "staging", "production", "test"])),
  ],
};

export const NETWORK_SECTION: SectionDef = {
  id: "network",
  title: "网络",
  base: ["network"],
  restart: true,
  desc: "项目通用的 HTTP 访问配置。",
  cols: 2,
  fields: [
    text("proxy", "代理地址", { mono: true, nullable: true, placeholder: "http://127.0.0.1:7890", desc: "留空不走代理" }),
    num("timeout_seconds", "请求超时", { unit: "秒", gt: 0 }),
  ],
};

export const NAPCAT_SECTION: SectionDef = {
  id: "napcat",
  title: "NapCat 连接",
  base: ["napcat"],
  restart: true,
  desc: "NapCat 反向 WebSocket 接入鉴权、Action 等待与发送重试。",
  fields: [
    secret("websocket_token", "WebSocket Token", { desc: "留空不校验；填写后要求 NapCat 携带相同的 Bearer Token" }),
    num("action_timeout_seconds", "Action 等待超时", { unit: "秒", gt: 0 }),
    int("response_summary_max_chars", "失败响应摘要长度", { unit: "字", min: 0, zero: "不截断" }),
    int("send_max_attempts", "发送最大尝试次数", { unit: "次", min: 1 }),
    num("send_retry_delay_seconds", "发送重试间隔", { unit: "秒", min: 0 }),
    num("send_retry_max_delay_seconds", "发送最大退避", { unit: "秒", min: 0, zero: "不限制退避上限" }),
  ],
};

export const DATABASE_SECTION: SectionDef = {
  id: "database",
  title: "数据库",
  base: ["database"],
  restart: true,
  desc: "PostgreSQL 连接与连接池；需要认证时，密码与密码文件二选一。",
  fields: [
    text("host", "主机", { mono: true, placeholder: "localhost", required: true }),
    int("port", "端口", { min: 1, max: 65535, placeholder: "5432" }),
    text("name", "数据库名", { mono: true, placeholder: "mybot", required: true }),
    text("user", "用户", { mono: true, placeholder: "mybot", required: true }),
    secret("password", "密码", { desc: "留空表示不使用密码认证" }),
    text("password_file", "密码文件", { mono: true, nullable: true, placeholder: "/run/secrets/postgres_password", desc: "Docker secrets 场景使用" }),
    int("pool_size", "连接池大小", { unit: "个", min: 1 }),
    int("max_overflow", "最大溢出连接", { unit: "个", min: 0 }),
    num("pool_timeout_seconds", "取连接超时", { unit: "秒", gt: 0 }),
    num("statement_timeout_seconds", "语句超时", { unit: "秒", min: 0, zero: "关闭语句超时" }),
    numtags("persistence_retry_delays_seconds", "持久化重试延迟", { unit: "秒", min: 0, span: 2, desc: "每项是一次重试前的等待秒数；空列表表示首次失败后不再重试", addLabel: "添加一次重试" }),
  ],
};

export const STORAGE_SECTION: SectionDef = {
  id: "storage",
  title: "媒体存储",
  base: ["storage", "images"],
  restart: true,
  desc: "群图片与视频共用的归档目录、下载与重试策略。",
  fields: [
    text("directory", "归档目录", { mono: true, placeholder: "images", required: true }),
    int("max_bytes", "单个媒体体积上限", { unit: "字节", min: 1, bytes: true }),
    int("download_concurrency", "下载并发数", { min: 1 }),
    num("download_timeout_seconds", "下载超时", { unit: "秒", gt: 0 }),
    num("lease_seconds", "任务租约", { unit: "秒", gt: 0 }),
    num("worker_poll_interval_seconds", "Worker 轮询间隔", { unit: "秒", gt: 0 }),
    num("worker_stop_timeout_seconds", "Worker 停止超时", { unit: "秒", gt: 0 }),
    numtags("retry_delays_seconds", "归档重试延迟", { unit: "秒", min: 0, span: 2, desc: "每项是一次重试前的等待秒数；空列表表示不重试", addLabel: "添加一次重试" }),
  ],
};

export const LOGGING_SECTION: SectionDef = {
  id: "logging",
  title: "日志",
  base: ["logging"],
  restart: true,
  desc: "日志输出级别与归档策略。",
  fields: [
    text("directory", "日志目录", { mono: true, placeholder: "logs" }),
    select("console_level", "控制台级别", LOG_LEVELS),
    select("file_level", "文件级别", LOG_LEVELS),
    text("rotation", "滚动大小", { mono: true, placeholder: "50 MB" }),
    text("retention", "保留时长", { mono: true, placeholder: "30 days" }),
    text("compression", "压缩格式", { mono: true, placeholder: "gz" }),
  ],
};

export const EXECUTION_SECTION: SectionDef = {
  id: "plugin_execution",
  title: "插件执行",
  base: ["plugin_execution"],
  restart: true,
  desc: "所有插件共用的启动期执行参数；单个插件的并发与优先级在各插件页里。",
  fields: [
    num("stop_timeout_seconds", "消费者停止超时", { unit: "秒", gt: 0 }),
    ids("blocked_user_ids", "屏蔽用户 QQ", { span: 2, placeholder: "QQ 号", desc: "这些用户的群聊和私聊消息不触发任何插件；群历史与通知事件照常处理" }),
  ],
};

/** 页面按行排布：一行一个分节或两个字段较少的分节并排。 */
export const SYSTEM_ROWS: SectionDef[][] = [
  [SERVER_SECTION],
  [APP_SECTION, NETWORK_SECTION],
  [NAPCAT_SECTION],
  [DATABASE_SECTION],
  [STORAGE_SECTION],
  [LOGGING_SECTION],
  [EXECUTION_SECTION],
];

export const SYSTEM_SECTIONS: SectionDef[] = SYSTEM_ROWS.flat();
