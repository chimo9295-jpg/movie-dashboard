"""Vercel Serverless Function — DeepSeek ChatBI 透明代理"""
import os
import json

import requests
from flask import Flask, request, jsonify

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

app = Flask(__name__)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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


@app.route("/api/chatbi", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "deepseek_key_configured": bool(DEEPSEEK_API_KEY),
    })


@app.route("/api/chatbi", methods=["POST", "OPTIONS"])
def chatbi():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return jsonify({"error": "messages array required"}), 400

    result, status = forward_to_deepseek(messages)
    return jsonify(result), status
