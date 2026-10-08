/** Recovery guidance. Unrecognized remote text is never echoed. */
const guidance: Record<string, string> = {
  local_auth_required: "需要本机授权，请打开连接授权。",
  local_auth_not_initialized: "后端尚未配对，请配置授权哈希并重启后端。",
  local_secure_storage_unavailable: "系统凭据库不可用，请检查系统钥匙串。",
  infrastructure_unavailable: "数据库或 Redis 不可用，请检查诊断与本机服务。",
  worker_unavailable: "交互 worker 未就绪，请启动对应任务服务。",
  queue_unavailable: "任务队列暂时不可用，请检查 Redis 和对应 worker。",
  provider_key_missing: "聊天模型尚未配置凭据，请检查服务器环境与 Settings。",
  provider_request_or_schema_failed:
    "模型请求或响应格式失败，请检查服务商连通性与模型配置。",
  embedding_key_missing: "嵌入服务缺少凭据，请检查后端环境。",
  embedding_failed: "嵌入请求失败，请检查嵌入模型服务。",
  local_embedding_failed: "本地嵌入模型加载或推理失败，请检查权重与资源。",
  reranking_failed: "本地重排模型加载或推理失败，请检查权重与资源。",
  model_revision_resolution_failed:
    "无法解析模型版本，请检查权重可达性与固定 revision。",
  local_model_artifacts_changed:
    "本地模型权重发生变化，请恢复固定权重或重建匹配索引。",
  embedding_model_mismatch:
    "嵌入模型与索引不匹配，请使用建库时的模型或重建索引。",
  job_failed: "任务执行失败，请检查模型资源和安全诊断后重试。",
  worker_interrupted: "任务 worker 中断，请恢复任务服务后重试。",
  context_budget_exceeded: "会话改写上下文超出预算，请缩短问题或检查会话记忆。",
  untrusted_origin: "浏览器来源校验失败，请使用本机同源入口。",
  untrusted_host: "请求主机校验失败，请使用本机入口。",
  request_failed: "请求失败，请检查本机后端与诊断信息。",
  invalid_response: "后端响应格式不兼容，请检查客户端与后端版本。",
  local_backend_unavailable: "本机后端不可用，请启动后端并检查连接。",
};
export function errorMessage(value: unknown): string {
  const match = typeof value === "string" ? value : "request_failed";
  const code = /^[a-z][a-z0-9_]{0,100}$/.test(match) ? match : "request_failed";
  return `${guidance[code] ?? "操作失败，请查看安全错误码并检查配置。"} (${code})`;
}
