"""Vercel Serverless Function — DeepSeek ChatBI 代理

前端将所有消息（含 data context + 对话历史）打包发送到此端点，
服务端注入 DEEPSEEK_API_KEY 后转发到 DeepSeek API，返回原始响应。
"""
import os
import json

try:
    from flask import Flask, request, jsonify
    _has_flask = True
except ImportError:
    _has_flask = False

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _forward(messages, model="deepseek-chat", temperature=0.2, max_tokens=2048):
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
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=30,
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
        return {"error": "DeepSeek API timeout after 30s"}, 504
    except requests.exceptions.RequestException as e:
        return {"error": f"DeepSeek API error: {str(e)}"}, 502


if _has_flask:
    app = Flask(__name__)

    @app.after_request
    def add_cors(response):
        for k, v in CORS_HEADERS.items():
            response.headers[k] = v
        return response

    @app.route("/api/chatbi", methods=["POST", "OPTIONS"])
    def chatbi():
        if request.method == "OPTIONS":
            return "", 204
        body = request.get_json(silent=True) or {}
        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return jsonify({"error": "messages array required"}), 400
        result, status = _forward(messages)
        return jsonify(result), status

    @app.route("/api/chatbi", methods=["GET"])
    def chatbi_health():
        key_ok = bool(DEEPSEEK_API_KEY)
        return jsonify({"status": "ok", "deepseek_key_configured": key_ok})

else:
    def handler(event, context):
        if event.get("httpMethod") == "OPTIONS":
            return {
                "statusCode": 204,
                "headers": CORS_HEADERS,
                "body": "",
            }
        if event.get("httpMethod") == "GET":
            key_ok = bool(DEEPSEEK_API_KEY)
            return {
                "statusCode": 200,
                "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
                "body": json.dumps({"status": "ok", "deepseek_key_configured": key_ok}),
            }
        body = json.loads(event.get("body", "{}"))
        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return {
                "statusCode": 400,
                "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
                "body": json.dumps({"error": "messages array required"}),
            }
        result, status = _forward(messages)
        return {
            "statusCode": status,
            "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
            "body": json.dumps(result),
        }
