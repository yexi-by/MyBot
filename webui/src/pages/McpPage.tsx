/** MCP 服务管理页：动态服务名始终作为不透明键整体写回。 */

import { useState } from "react";
import { useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SwitchField } from "@/lib/fields";
import type { MCPServerConfig, MyBotConfigData } from "@/lib/types";

export default function McpPage() {
  const { watch, setValue } = useFormContext<MyBotConfigData>();
  const [newServerName, setNewServerName] = useState("");
  const servers = watch("mcp.servers") ?? {};
  const serverNames = Object.keys(servers);

  const commit = (next: Record<string, MCPServerConfig>) => {
    setValue("mcp.servers", next, { shouldDirty: true });
  };
  const updateServer = (name: string, patch: Partial<MCPServerConfig>) => {
    commit({ ...servers, [name]: { ...servers[name], ...patch } });
  };

  const addServer = () => {
    const name = newServerName.trim();
    if (name === "") {
      toast.error("服务名不能为空");
      return;
    }
    if (name !== newServerName) {
      toast.error("服务名不能包含首尾空格");
      return;
    }
    if (Object.hasOwn(servers, name)) {
      toast.error(`服务 ${name} 已存在`);
      return;
    }
    commit({
      ...servers,
      [name]: { command: "", args: [], env: null, cwd: null, disabled: false },
    });
    setNewServerName("");
  };

  const removeServer = (name: string) => {
    const next = { ...servers };
    delete next[name];
    commit(next);
  };

  return (
    <div className="space-y-4">
      <Alert>
        <AlertTitle>MCP 改动需要重启进程后生效</AlertTitle>
        <AlertDescription>
          服务名和环境变量名可包含点号，界面会原样保存完整键名。
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

        {serverNames.map((name) => {
          const server = servers[name];
          const args = server.args ?? [];
          const envEntries = Object.entries(server.env ?? {});
          return (
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
              <div className="space-y-1.5">
                <Label>启动命令</Label>
                <Input
                  value={server.command}
                  placeholder="如 npx / uvx"
                  onChange={(event) =>
                    updateServer(name, { command: event.target.value })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>工作目录</Label>
                <Input
                  value={server.cwd ?? ""}
                  placeholder="留空使用进程当前目录"
                  onChange={(event) =>
                    updateServer(name, { cwd: event.target.value || null })
                  }
                />
              </div>
              <div className="space-y-2 xl:col-span-2">
                <Label>命令参数</Label>
                {args.map((argument, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      aria-label={`命令参数 ${index + 1}`}
                      value={argument}
                      onChange={(event) => {
                        const next = [...args];
                        next[index] = event.target.value;
                        updateServer(name, { args: next });
                      }}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`删除命令参数 ${index + 1}`}
                      onClick={() =>
                        updateServer(name, {
                          args: args.filter((_, i) => i !== index),
                        })
                      }
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => updateServer(name, { args: [...args, ""] })}
                >
                  <Plus className="mr-1 h-4 w-4" />添加参数
                </Button>
              </div>
              <div className="space-y-2 xl:col-span-2">
                <Label>环境变量</Label>
                {envEntries.map(([key, value], index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      className="w-2/5"
                      aria-label={`变量名 ${index + 1}`}
                      value={key}
                      onChange={(event) => {
                        const next = envEntries.map((entry, i) =>
                          i === index ? [event.target.value, entry[1]] : entry,
                        );
                        updateServer(name, { env: Object.fromEntries(next) });
                      }}
                    />
                    <Input
                      aria-label={`变量值 ${index + 1}`}
                      value={value}
                      onChange={(event) => {
                        const next = envEntries.map((entry, i) =>
                          i === index ? [entry[0], event.target.value] : entry,
                        );
                        updateServer(name, { env: Object.fromEntries(next) });
                      }}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`删除环境变量 ${key || index + 1}`}
                      onClick={() => {
                        const next = envEntries.filter((_, i) => i !== index);
                        updateServer(name, {
                          env: next.length ? Object.fromEntries(next) : null,
                        });
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    updateServer(name, {
                      env: Object.fromEntries([...envEntries, ["", ""]]),
                    })
                  }
                >
                  <Plus className="mr-1 h-4 w-4" />添加变量
                </Button>
              </div>
              <div className="space-y-1.5">
                <Label>禁用此服务</Label>
                <Switch
                  checked={Boolean(server.disabled)}
                  onCheckedChange={(checked) =>
                    updateServer(name, { disabled: checked })
                  }
                />
              </div>
            </SectionCard>
          );
        })}

        <SectionCard title="新增 MCP 服务">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center xl:col-span-2">
            <Input
              aria-label="新 MCP 服务名"
              placeholder="服务名，如 vendor.tool"
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
              <Plus className="mr-1 h-4 w-4" />添加
            </Button>
          </div>
        </SectionCard>
      </SettingsGrid>
    </div>
  );
}
