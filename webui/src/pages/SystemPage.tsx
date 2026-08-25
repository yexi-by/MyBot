/** 系统设置页：app/server/napcat/database/network/storage/logging，改动均需重启。 */

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import {
  NumberField,
  NumberListField,
  SelectField,
  SwitchField,
  TextField,
} from "@/lib/fields";

export default function SystemPage() {
  return (
    <div className="space-y-4">
      <Alert>
        <AlertTitle>本页改动需要重启进程后生效</AlertTitle>
        <AlertDescription>
          停止编辑后会自动写入配置文件，但运行中的服务继续使用启动时的配置。
        </AlertDescription>
      </Alert>

      <SettingsGrid>

      <SectionCard title="服务监听" description="HTTP 与 WebSocket 服务监听参数。">
        <TextField path="server.host" label="监听地址" placeholder="例如 0.0.0.0" />
        <NumberField path="server.port" label="端口" placeholder="例如 6055" />
        <TextField
          path="server.websocket_path_prefix"
          label="WebSocket 路由前缀"
          placeholder="例如 /ws"
        />
        <SelectField
          path="server.log_level"
          label="Uvicorn 日志级别"
          options={["critical", "error", "warning", "info", "debug", "trace"].map(
            (value) => ({ value, label: value }),
          )}
        />
        <SwitchField
          path="server.access_log"
          label="访问日志"
          description="记录每个 HTTP 请求"
        />
        <NumberField
          path="server.config_watch_debounce_ms"
          label="配置监听防抖（毫秒）"
        />
        <NumberField
          path="server.config_watch_step_ms"
          label="配置监听轮询（毫秒）"
        />
        <NumberField
          path="server.config_watcher_stop_timeout_seconds"
          label="配置监听停止超时（秒）"
        />
        <NumberField
          path="server.power_action_delay_seconds"
          label="电源操作延迟（秒）"
          description="让重启或关机接口先返回响应；0 表示下一次事件循环立即执行"
        />
      </SectionCard>

      <SectionCard title="NapCat 连接" description="NapCat 反向 WebSocket 接入与发送重试。">
        <TextField
          path="napcat.websocket_token"
          label="WebSocket Token"
          description="留空时不校验，填写后要求 NapCat 发送相同 Bearer Token"
        />
        <NumberField
          path="napcat.action_timeout_seconds"
          label="Action 等待超时（秒）"
        />
        <NumberField
          path="napcat.send_max_attempts"
          label="发送最大尝试次数"
          placeholder="例如 5"
        />
        <NumberField
          path="napcat.send_retry_delay_seconds"
          label="发送重试间隔（秒）"
          placeholder="例如 0"
        />
        <NumberField
          path="napcat.send_retry_max_delay_seconds"
          label="发送最大退避（秒）"
          description="0 表示不限制指数退避上限"
        />
        <NumberField
          path="napcat.response_summary_max_chars"
          label="失败响应摘要字符数"
          description="0 表示不截断"
        />
      </SectionCard>

      <SectionCard
        title="数据库"
        description="PostgreSQL 连接池；无密码服务可留空，需要认证时密码与密码文件二选一。"
      >
        <TextField path="database.host" label="主机" placeholder="例如 localhost" />
        <NumberField path="database.port" label="端口" placeholder="例如 5432" />
        <TextField path="database.name" label="数据库名" placeholder="例如 mybot" />
        <TextField path="database.user" label="用户" placeholder="例如 mybot" />
        <TextField
          path="database.password"
          label="密码"
          description="与密码文件二选一"
        />
        <TextField
          path="database.password_file"
          label="密码文件路径"
          description="Docker secrets 场景使用"
        />
        <NumberField path="database.pool_size" label="连接池大小" placeholder="例如 20" />
        <NumberField
          path="database.max_overflow"
          label="最大溢出连接"
          placeholder="例如 20"
        />
        <NumberField
          path="database.pool_timeout_seconds"
          label="取连接超时（秒）"
          placeholder="例如 2"
        />
        <NumberField
          path="database.statement_timeout_seconds"
          label="语句超时（秒）"
          placeholder="例如 5；0 表示关闭语句超时"
        />
        <div className="xl:col-span-2">
          <NumberListField
            path="database.persistence_retry_delays_seconds"
            label="持久化重试延迟（秒）"
            description="空列表表示首次失败后不再重试"
            addLabel="添加重试"
          />
        </div>
      </SectionCard>

      <SectionCard title="网络" description="项目通用 HTTP 访问配置。">
        <TextField
          path="network.proxy"
          label="代理地址"
          placeholder="如 http://127.0.0.1:7890，留空不走代理"
        />
        <NumberField
          path="network.timeout_seconds"
          label="请求超时（秒）"
          placeholder="例如 15"
        />
      </SectionCard>

      <SectionCard title="图片存储" description="群图片归档目录与下载策略。">
        <TextField
          path="storage.images.directory"
          label="归档目录"
          placeholder="例如 images"
        />
        <NumberField
          path="storage.images.download_concurrency"
          label="下载并发数"
          placeholder="例如 16"
        />
        <NumberField
          path="storage.images.download_timeout_seconds"
          label="下载超时（秒）"
          placeholder="例如 20"
        />
        <NumberField
          path="storage.images.max_bytes"
          label="单图最大字节"
          placeholder="例如 52428800"
        />
        <div className="xl:col-span-2">
          <NumberListField
            path="storage.images.retry_delays_seconds"
            label="归档重试延迟（秒）"
            description="列表长度决定重试次数；空列表表示不重试"
            addLabel="添加重试"
          />
        </div>
        <NumberField
          path="storage.images.lease_seconds"
          label="任务租约（秒）"
          placeholder="例如 45"
        />
        <NumberField
          path="storage.images.worker_poll_interval_seconds"
          label="Worker 轮询间隔（秒）"
        />
        <NumberField
          path="storage.images.worker_stop_timeout_seconds"
          label="Worker 停止超时（秒）"
        />
      </SectionCard>

      <SectionCard title="插件执行" description="所有已加载插件共用的启动期执行参数。">
        <NumberField
          path="plugin_execution.stop_timeout_seconds"
          label="消费者停止超时（秒）"
        />
      </SectionCard>

      <SectionCard title="日志" description="日志输出与归档策略。">
        <TextField path="logging.directory" label="日志目录" placeholder="例如 logs" />
        <SelectField
          path="logging.console_level"
          label="控制台级别"
          options={["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"].map(
            (value) => ({ value, label: value }),
          )}
        />
        <SelectField
          path="logging.file_level"
          label="文件级别"
          options={["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"].map(
            (value) => ({ value, label: value }),
          )}
        />
        <TextField path="logging.rotation" label="滚动大小" placeholder="例如 50 MB" />
        <TextField path="logging.retention" label="保留时长" placeholder="例如 30 days" />
        <TextField path="logging.compression" label="压缩格式" placeholder="例如 gz" />
      </SectionCard>

      <SectionCard title="应用" description="应用元信息。">
        <TextField path="app.name" label="应用名" placeholder="例如 MyBot" />
        <SelectField
          path="app.environment"
          label="运行环境"
          options={["development", "staging", "production", "test"].map(
            (value) => ({ value, label: value }),
          )}
        />
      </SectionCard>
      </SettingsGrid>
    </div>
  );
}
