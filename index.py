"""腾讯云函数 SCF — GitHub Actions 定时触发器

每天 23:50 北京时间精准触发 GitHub Actions workflow_dispatch。
零第三方依赖，仅用 Python 标准库。

环境变量:
  GITHUB_TOKEN — GitHub Personal Access Token (repo 权限)
  GITHUB_OWNER — 仓库所有者 (默认: chimo9295-jpg)
  GITHUB_REPO — 仓库名 (默认: movie-dashboard)
  WORKFLOW_FILE — 工作流文件名 (默认: daily-crawl.yml)
"""

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

GITHUB_API = "https://api.github.com"
DEFAULT_OWNER = "chimo9295-jpg"
DEFAULT_REPO = "movie-dashboard"
DEFAULT_WORKFLOW = "daily-crawl.yml"


def _log(msg):
    print(f"[{datetime.now(CST).isoformat()}] {msg}")


def main_handler(event, context):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        _log("ERROR: GITHUB_TOKEN 未设置")
        return {"status": "error", "message": "GITHUB_TOKEN not set"}

    owner = os.environ.get("GITHUB_OWNER", DEFAULT_OWNER).strip()
    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO).strip()
    workflow = os.environ.get("WORKFLOW_FILE", DEFAULT_WORKFLOW).strip()

    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"

    body = json.dumps({"ref": "main"}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    _log(f"触发 workflow: {owner}/{repo}/{workflow}")
    _log(f"请求 URL: {url}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            _log(f"GitHub API 响应: {status}")
            return {"status": "ok", "http_code": status}
    except urllib.error.HTTPError as e:
        _log(f"GitHub API 错误: {e.code} {e.reason}")
        return {"status": "error", "http_code": e.code, "message": str(e.reason)}
    except Exception as e:
        _log(f"请求异常: {str(e)}")
        return {"status": "error", "message": str(e)}
