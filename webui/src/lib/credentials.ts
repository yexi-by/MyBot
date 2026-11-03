/** 服务账号密钥 JSON 的前端预检；完整校验以服务端为准，提示不回显任何取值。 */
export interface ServiceAccountSummary {
  /** 提取并压缩后的标准服务账号 JSON，写入配置。 */
  compact: string;
  email: string;
  project: string;
  keyId: string;
  settings: { project_id?: string; location?: string };
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function parseServiceAccount(raw: string): ServiceAccountSummary | string {
  let data: unknown;
  try {
    data = JSON.parse(raw.trim());
  } catch {
    return "内容不是有效的 JSON";
  }
  const settings: ServiceAccountSummary["settings"] = {};
  if (record(data) && "service_account" in data) {
    if (data.project_id !== undefined && data.project_id !== null && data.project_id !== "") {
      if (typeof data.project_id !== "string") return "项目导出 JSON 的 project_id 必须是字符串";
      settings.project_id = data.project_id;
    }
    if (data.location !== undefined && data.location !== null && data.location !== "") {
      if (data.location !== "global" && data.location !== "us" && data.location !== "eu")
        return "项目导出 JSON 的 location 仅支持 global、us、eu，请修改后重新导入";
      settings.location = data.location;
    }
    data = data.service_account;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data.trim());
      } catch {
        return "service_account 不是有效的 JSON";
      }
    }
  }
  if (!record(data) || data.type !== "service_account")
    return "需要标准服务账号密钥，或包含 service_account 的项目导出 JSON";
  if (data.universe_domain !== undefined && data.universe_domain !== "googleapis.com")
    return "只支持 googleapis.com 上的服务账号";
  if (typeof data.client_email !== "string" || !data.client_email.includes("@"))
    return "缺少有效的 client_email";
  if (typeof data.private_key !== "string" || !data.private_key.includes("PRIVATE KEY"))
    return "缺少 private_key";
  return {
    compact: JSON.stringify(data),
    email: data.client_email,
    project: typeof data.project_id === "string" ? data.project_id : "",
    keyId: typeof data.private_key_id === "string" ? data.private_key_id : "",
    settings,
  };
}
