/** 服务账号密钥 JSON 的前端预检；完整校验以服务端为准，提示不回显任何取值。 */
export interface ServiceAccountSummary {
  /** 去掉排版空白后的 JSON 原文，写入配置。 */
  compact: string;
  email: string;
  project: string;
  keyId: string;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function parseServiceAccount(raw: string): ServiceAccountSummary | string {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return "内容不是有效的 JSON";
  }
  if (!record(data) || data.type !== "service_account")
    return "只支持 type 为 service_account 的服务账号密钥";
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
  };
}
