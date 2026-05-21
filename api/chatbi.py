"""Vercel Serverless Function — DeepSeek ChatBI 透明代理

接收前端的 messages 数组（含 system prompt + data context + 对话历史 + 用户问题），
注入服务端环境变量 DEEPSEEK_API_KEY 后直接转发到 DeepSeek API，
将原始响应透传回前端。
"""
import os
import json

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def forward_to_deepseek(messages):
    if not DEEPSEEK_API_KEY:
        return {"error": "DEEPSEEK_API_KEY not configured on server"}, 500

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 2048,
            },
            timeout=25,
        )
        if not resp.ok:
            try:
                err = resp.json()
                msg = err.get("error", {}).get("message", resp.text[:300])
            except Exception:
                msg = resp.text[:300]
            return {"error": msg}, resp.status_code
        return resp.json(), 200
    except requests.exceptions.Timeout:
        return {"error": "DeepSeek API timeout after 25s"}, 504
    except requests.exceptions.RequestException as e:
        return {"error": f"DeepSeek API error: {str(e)}"}, 502


def json_response(body, status=200):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    method = event.get("httpMethod", "GET")

    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": CORS_HEADERS,
            "body": "",
        }

    if method == "GET":
        return json_response({
            "status": "ok",
            "deepseek_key_configured": bool(DEEPSEEK_API_KEY),
        })

    if method != "POST":
        return json_response({"error": "Method not allowed"}, 405)

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return json_response({"error": "Invalid JSON body"}, 400)

    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return json_response({"error": "messages array required"}, 400)

    result, status = forward_to_deepseek(messages)
    return json_response(result, status)
