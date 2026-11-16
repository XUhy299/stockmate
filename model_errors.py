"""Actionable provider errors without exposing request credentials or payloads."""


def model_error_message(error, model):
    status = getattr(error, "status_code", None)
    if status == 401:
        return "模型服务认证失败，请检查 API Key"
    if status == 403:
        return f"当前密钥没有访问模型 {model} 的权限"
    if status == 404:
        return f"模型 {model} 不可用，请修改 OPENAI_MODEL"
    if status == 400:
        return f"模型 {model} 的请求参数不兼容，请检查模型与 temperature 配置"
    if status == 429:
        return "模型服务繁忙或额度不足，请稍后重试"
    if "timeout" in type(error).__name__.lower():
        return "模型服务连接超时，请重试"
    if "connection" in type(error).__name__.lower():
        return "无法连接模型服务，请检查网络"
    return "模型服务暂时不可用，请稍后重试"
