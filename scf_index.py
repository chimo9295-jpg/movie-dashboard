"""腾讯云函数 SCF — DeepSeek ChatBI 代理

零第三方依赖，仅用 Python 标准库。
入口函数: main_handler(event, context)

环境变量:
  DEEPSEEK_API_KEY — DeepSeek API 密钥（必需）
"""
import os
import json
import urllib.request
import urllib.error


DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _json_reply(body, status=200):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def main_handler(event, context):
    method = event.get("httpMethod", event.get("method", "GET"))

    # OPTIONS 预检
    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": CORS_HEADERS,
            "body": "",
        }

    # GET 健康检查
    if method == "GET":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        key_ok = bool(api_key) and api_key.startswith("sk-")
        return _json_reply({
            "status": "ok",
            "deepseek_key_configured": bool(api_key),
            "deepseek_key_valid": key_ok,
        })

    # POST 转发到 DeepSeek
    if method != "POST":
        return _json_reply({"error": "Method not allowed"}, 405)

    # 解析请求体
    try:
        if isinstance(event.get("body"), dict):
            body = event["body"]
        else:
            body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return _json_reply({"error": "Invalid JSON body"}, 400)

    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return _json_reply({"error": "messages array required"}, 400)

    # 读取 API Key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return _json_reply({
            "error": "DEEPSEEK_API_KEY 环境变量未设置",
            "hint": "请在腾讯云函数控制台 → 函数配置 → 环境变量中添加 DEEPSEEK_API_KEY",
        }, 500)

    if not api_key.startswith("sk-"):
        return _json_reply({
            "error": "DEEPSEEK_API_KEY 格式异常（应以 sk- 开头）",
        }, 500)

    # 调用 DeepSeek API
    req_body = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 2048,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=req_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=28) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return _json_reply(result, 200)
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {"error": {"message": e.reason or str(e)}}
        error_msg = err_body.get("error", {}).get("message", str(e))
        return _json_reply({"error": error_msg}, e.code or 502)
    except Exception as e:
        return _json_reply({"error": f"DeepSeek API error: {str(e)}"}, 502)
