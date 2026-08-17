/** MCP 服务管理页：总开关 + stdio server 增删改。 */

import { useState } from "react";
import { useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  KeyValueField,
  StringListField,
  SwitchField,
  TextField,
} from "@/lib/fields";
import type { MCPServerConfig, MyBotConfigData } from "@/lib/types";

export default function McpPage() {
  const { watch, setValue } = useFormContext<MyBotConfigData>();
  const [newServerName, setNewServerName] = useState("");
  const servers = watch("mcp.servers") ?? {};
  const serverNames = Object.keys(servers);

  const addServer = () => {
    const name = newServerName.trim();
    if (name === "") {
      toast.error("服务名不能为空");
      return;
    }
    if (name in servers) {
      toast.error(`服务 ${name} 已存在`);
      return;
    }
    const next: Record<string, MCPServerConfig> = {
      ...servers,
      [name]: { command: "", args: [], disabled: false },
    };
    setValue("mcp.servers", next, { shouldDirty: true });
    setNewServerName("");
  };

  const removeServer = (name: string) => {
    const next = { ...servers };
    delete next[name];
    setValue("mcp.servers", next, { shouldDirty: true });
  };

  return (
    <div className="space-y-4">
      <Alert>
        <AlertTitle>MCP 改动需要重启进程后生效</AlertTitle>
        <AlertDescription>
          MCP stdio 服务只在启动时拉起，自动保存后需重启进程才会应用。
        </AlertDescription>
      </Alert>

      <SettingsGrid>
      <SectionCard title="MCP 总开关" description="关闭后所有 MCP server 都不会启动。">
        <SwitchField
          path="mcp.enabled"
          label="启用 MCP"
          description="以 mcp__{server}__{tool} 形式暴露给 AI 工具调用"
        />
      </SectionCard>

      {serverNames.map((name) => (
        <SectionCard
          key={name}
          title={name}
          actions={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`删除 MCP 服务 ${name}`}
              onClick={() => removeServer(name)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          }
        >
          <TextField
            path={`mcp.servers.${name}.command`}
            label="启动命令"
            placeholder="如 npx / uvx"
          />
          <TextField
            path={`mcp.servers.${name}.cwd`}
            label="工作目录"
            placeholder="留空使用进程当前目录"
          />
          <div className="xl:col-span-2">
            <StringListField
              path={`mcp.servers.${name}.args`}
              label="命令参数"
              placeholder="如 -y"
              addLabel="添加参数"
            />
          </div>
          <div className="xl:col-span-2">
            <KeyValueField
              path={`mcp.servers.${name}.env`}
              label="环境变量"
              description="按需注入 API Key 等敏感变量"
            />
          </div>
          <SwitchField
            path={`mcp.servers.${name}.disabled`}
            label="禁用此服务"
            description="保留配置但不启动"
          />
        </SectionCard>
      ))}

      <SectionCard title="新增 MCP 服务">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center xl:col-span-2">
          <Input
            aria-label="新 MCP 服务名"
            placeholder="服务名，如 firecrawl"
            value={newServerName}
            onChange={(event) => setNewServerName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addServer();
              }
            }}
          />
          <Button type="button" className="sm:w-auto" onClick={addServer}>
            <Plus className="mr-1 h-4 w-4" />
            添加
          </Button>
        </div>
      </SectionCard>
      </SettingsGrid>
    </div>
  );
}
