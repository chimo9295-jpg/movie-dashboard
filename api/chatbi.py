"""Vercel Serverless Function — DeepSeek ChatBI 代理

前端将所有消息（含 data context + 对话历史）打包发送到此端点，
服务端注入 DEEPSEEK_API_KEY 后转发到 DeepSeek API，返回原始响应。

部署: 推送后 Vercel 自动将 api/ 目录下 Python 文件部署为 Serverless Function。
      需要在 Vercel Dashboard → Settings → Environment Variables 设置 DEEPSEEK_API_KEY。
"""
import os
import json
import sys

try:
    from flask import Flask, request, jsonify
    _has_flask = True
except ImportError:
    _has_flask = False

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def _forward(messages, model="deepseek-chat", temperature=0.2, max_tokens=2048):
    """转发请求到 DeepSeek API，返回 (response_dict, http_status)"""
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
            err = resp.json() if resp.text else {}
            return {"error": err.get("error", {}).get("message", resp.text[:300])}, resp.status_code
        return resp.json(), 200
    except requests.exceptions.Timeout:
        return {"error": "DeepSeek API timeout after 30s"}, 504
    except requests.exceptions.RequestException as e:
        return {"error": f"DeepSeek API error: {str(e)}"}, 502


if _has_flask:
    app = Flask(__name__)

    @app.route("/api/chatbi", methods=["POST"])
    def chatbi():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return jsonify({"error": "messages array required"}), 400
        result, status = _forward(messages)
        return jsonify(result), status

else:
    # Vercel raw HTTP handler (fallback, no Flask dep needed)
    def handler(event, context):
        body = json.loads(event.get("body", "{}"))
        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "messages array required"}),
            }
        result, status = _forward(messages)
        return {
            "statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }
