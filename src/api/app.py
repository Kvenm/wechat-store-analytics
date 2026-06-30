from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api.repository import LocalRepository
from api.task_runner import TaskRunnerError
from api.task_runner import get_runtime_task
from api.task_runner import list_runtime_tasks
from api.task_runner import normalize_web_export_payload
from api.task_runner import start_web_export_task
from shared.paths import PROJECT_ROOT


app = FastAPI(
    title="Wechat Store Analytics Local Admin API",
    version="0.1.0",
)

ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
DEFAULT_WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
ACCESS_TOKEN_SOURCE = "stable_token"
WEB_LOGIN_URL = "https://store.weixin.qq.com/"
WEB_LOGIN_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "auth" / "open_wechat_store_login.mjs"
WEB_LOGIN_PROFILE_DIR = PROJECT_ROOT / "data" / "browser-profile"
PLAYWRIGHT_NODE_MODULE_DIR = PROJECT_ROOT / "node_modules" / "playwright"
_web_login_process: Any = None

API_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("app_id", "WECHAT_STORE_APP_ID"),
    ("app_secret", "WECHAT_STORE_APP_SECRET"),
    ("access_token", "WECHAT_STORE_ACCESS_TOKEN"),
    ("api_base_url", "WECHAT_STORE_API_BASE_URL"),
    ("sync_dry_run", "WECHAT_STORE_SYNC_DRY_RUN"),
    ("raw_archive_dir", "WECHAT_STORE_RAW_ARCHIVE_DIR"),
    ("shop_id", "WECHAT_STORE_SHOP_ID"),
    ("shop_name", "WECHAT_STORE_SHOP_NAME"),
)
SENSITIVE_API_CONFIG_FIELDS = {"app_secret", "access_token"}
API_CONFIG_FIELD_TO_KEY = dict(API_CONFIG_FIELDS)
API_CONFIG_KEYS = tuple(key for _, key in API_CONFIG_FIELDS)
ACCESS_TOKEN_ENV_KEYS: tuple[str, ...] = (
    "WECHAT_STORE_ACCESS_TOKEN",
    "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN",
    "WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT",
    "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT",
    "WECHAT_STORE_ACCESS_TOKEN_SOURCE",
)


class TaskRequest(BaseModel):
    shop_id: Optional[str] = Field(default=None, description="Local shop identifier")
    task_name: Optional[str] = Field(default=None, description="Human-readable task name")
    source_type: Optional[str] = Field(default="manual", description="Requested collection source")
    params: dict[str, Any] = Field(default_factory=dict)


class ApiConfigRequest(BaseModel):
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    access_token: Optional[str] = None
    api_base_url: Optional[str] = None
    sync_dry_run: Optional[bool] = None
    raw_archive_dir: Optional[str] = None
    shop_id: Optional[str] = None
    shop_name: Optional[str] = None


def response(status: str, message: str, data: Any) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "data": data,
    }


def repository() -> LocalRepository:
    return LocalRepository()


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def merge_runtime_and_persisted_tasks() -> list[dict[str, Any]]:
    runtime_tasks = list_runtime_tasks()
    runtime_ids = {
        str(task_id)
        for task in runtime_tasks
        for task_id in (task.get("id"), task.get("task_id"), task.get("collection_task_id"))
        if task_id
    }
    persisted_tasks = [
        task
        for task in repository().list_tasks()
        if str(task.get("id") or task.get("task_id") or "") not in runtime_ids
        and str(task.get("task_id") or "") not in runtime_ids
    ]
    return [*runtime_tasks, *persisted_tasks]


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    return HTMLResponse(render_admin_ui())


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(render_web_login_ui())


@app.get("/health")
def health() -> dict[str, Any]:
    return response("ok", "API is ready.", repository().health())


@app.get("/api-config")
def api_config() -> dict[str, Any]:
    return response("ok", "API config loaded.", read_api_config())


@app.post("/api-config")
def update_api_config(payload: ApiConfigRequest) -> dict[str, Any]:
    write_api_config(payload)
    return response("ok", "API config saved.", read_api_config())


@app.post("/api-token/fetch")
def fetch_api_token() -> dict[str, Any]:
    result = fetch_and_store_access_token()
    if result["status"] == "error":
        return response("error", result["message"], result["data"])
    return response("ok", result["message"], result["data"])


@app.post("/web-login/open")
def open_web_login() -> dict[str, Any]:
    result = start_web_login_browser()
    if result["status"] == "error":
        return response("error", result["message"], result["data"])
    return response("ok", result["message"], result["data"])


@app.get("/web-login/status")
def web_login_status() -> dict[str, Any]:
    return response("ok", "Web login status loaded.", web_login_status_data())


@app.get("/shops")
def shops() -> dict[str, Any]:
    return response("ok", "Shops loaded.", repository().list_shops())


@app.get("/tasks")
def tasks() -> dict[str, Any]:
    return response("ok", "Tasks loaded.", merge_runtime_and_persisted_tasks())


@app.get("/tasks/{task_id}")
def task_detail(task_id: str) -> dict[str, Any]:
    runtime_task = get_runtime_task(task_id)
    if runtime_task is not None:
        return response("ok", "Runtime task loaded.", runtime_task)
    task = repository().get_task(task_id)
    if task is None:
        return response("not_found", "Task not found.", None)
    return response("ok", "Task loaded.", task)


@app.post("/tasks")
def create_task(payload: TaskRequest) -> dict[str, Any]:
    try:
        task = start_web_export_task(model_to_dict(payload))
    except TaskRunnerError as exc:
        return response("error", str(exc), {"request": model_to_dict(payload)})
    return response("ok", "真实采集任务已启动，请在页面等待进度完成。", task)


@app.post("/tasks/check")
def check_task_request(payload: TaskRequest) -> dict[str, Any]:
    try:
        spec = normalize_web_export_payload(model_to_dict(payload))
    except TaskRunnerError as exc:
        return response("error", str(exc), {"request": model_to_dict(payload)})
    return response("ok", "Task request is valid.", spec)


@app.get("/reports")
def reports() -> dict[str, Any]:
    return response("ok", "Reports loaded.", repository().list_reports())


@app.get("/reports/{report_id}")
def report_detail(report_id: str) -> dict[str, Any]:
    report = repository().get_report(report_id)
    if report is None:
        return response("not_found", "Report not found.", None)
    return response("ok", "Report loaded.", report)


@app.get("/exports")
def exports() -> dict[str, Any]:
    return response("ok", "Exports loaded.", repository().list_exports())


@app.get("/sync-runs")
def sync_runs(limit: int = 50, shop_id: Optional[str] = None) -> dict[str, Any]:
    return response("ok", "Sync runs loaded.", repository().list_sync_runs(limit=limit, shop_id=shop_id))


@app.get("/sync-runs/{sync_run_id}")
def sync_run_detail(sync_run_id: str) -> dict[str, Any]:
    sync_run = repository().get_sync_run(sync_run_id)
    if sync_run is None:
        return response("not_found", "Sync run not found.", None)
    return response("ok", "Sync run loaded.", sync_run)


@app.get("/sync-runs/{sync_run_id}/items")
def sync_run_items(sync_run_id: str) -> dict[str, Any]:
    sync_run = repository().get_sync_run(sync_run_id)
    if sync_run is None:
        return response("not_found", "Sync run not found.", None)
    return response("ok", "Sync run items loaded.", repository().list_sync_run_items(sync_run_id))


@app.get("/raw-api-responses")
def raw_api_responses(
    sync_run_id: Optional[str] = None,
    sync_item_id: Optional[str] = None,
    shop_id: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    return response(
        "ok",
        "Raw API responses loaded.",
        repository().list_raw_api_responses(
            sync_run_id=sync_run_id,
            sync_item_id=sync_item_id,
            shop_id=shop_id,
            limit=limit,
        ),
    )


def read_api_config() -> dict[str, Any]:
    env_values = parse_env_file(ENV_LOCAL_PATH)
    return {
        "env_path": str(ENV_LOCAL_PATH),
        "exists": ENV_LOCAL_PATH.exists(),
        "values": {
            "app_id": env_values.get(API_CONFIG_FIELD_TO_KEY["app_id"], ""),
            "api_base_url": env_values.get(API_CONFIG_FIELD_TO_KEY["api_base_url"], ""),
            "sync_dry_run": parse_bool(env_values.get(API_CONFIG_FIELD_TO_KEY["sync_dry_run"], "")),
            "raw_archive_dir": env_values.get(API_CONFIG_FIELD_TO_KEY["raw_archive_dir"], ""),
            "shop_id": env_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""),
            "shop_name": env_values.get(API_CONFIG_FIELD_TO_KEY["shop_name"], ""),
        },
        "secrets": {
            "app_secret": {
                "configured": bool(env_values.get(API_CONFIG_FIELD_TO_KEY["app_secret"], "")),
            },
            "access_token": {
                "configured": bool(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], "")),
                "expires_in": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN", ""),
                "fetched_at": env_values.get("WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT", ""),
                "expires_at": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT", ""),
                "source": env_values.get("WECHAT_STORE_ACCESS_TOKEN_SOURCE", ""),
            },
        },
    }


def write_api_config(payload: ApiConfigRequest) -> None:
    existing_values = parse_env_file(ENV_LOCAL_PATH)
    updates: dict[str, str] = {}
    for field, env_key in API_CONFIG_FIELDS:
        value = getattr(payload, field)
        if field in SENSITIVE_API_CONFIG_FIELDS:
            clean_value = clean_config_value(value)
            updates[env_key] = clean_value if clean_value else existing_values.get(env_key, "")
        elif field == "sync_dry_run":
            updates[env_key] = existing_values.get(env_key, "false") if value is None else format_bool(value)
        else:
            updates[env_key] = existing_values.get(env_key, "") if value is None else clean_config_value(value)

    write_env_file(ENV_LOCAL_PATH, updates)


def fetch_and_store_access_token() -> dict[str, Any]:
    env_values = parse_env_file(ENV_LOCAL_PATH)
    app_id = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["app_id"], ""))
    app_secret = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["app_secret"], ""))
    base_url = clean_api_base_url(env_values.get(API_CONFIG_FIELD_TO_KEY["api_base_url"], ""))

    if not app_id or not app_secret:
        return {
            "status": "error",
            "message": "请先填写 AppID 和 AppSecret，再获取 AccessToken。",
            "data": {
                "configured": bool(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], "")),
                "missing": {
                    "app_id": not bool(app_id),
                    "app_secret": not bool(app_secret),
                },
                "expires_in": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN", ""),
                "expires_at": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT", ""),
                "source": env_values.get("WECHAT_STORE_ACCESS_TOKEN_SOURCE", ""),
            },
        }

    try:
        token_payload = request_stable_access_token(base_url=base_url, app_id=app_id, app_secret=app_secret)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"AccessToken 获取失败：{exc}",
            "data": {
                "configured": bool(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], "")),
                "expires_in": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN", ""),
                "expires_at": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT", ""),
                "source": env_values.get("WECHAT_STORE_ACCESS_TOKEN_SOURCE", ""),
            },
        }

    errcode = token_payload.get("errcode")
    errmsg = token_payload.get("errmsg", "")
    access_token = clean_config_value(token_payload.get("access_token", ""))
    if errcode not in (None, 0, "0") or not access_token:
        return {
            "status": "error",
            "message": errmsg or "微信未返回可保存的 AccessToken。",
            "data": {
                "configured": bool(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], "")),
                "errcode": errcode,
                "errmsg": errmsg,
                "expires_in": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN", ""),
                "expires_at": env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT", ""),
                "source": env_values.get("WECHAT_STORE_ACCESS_TOKEN_SOURCE", ""),
            },
        }

    fetched_at = datetime.utcnow()
    expires_in = parse_int(token_payload.get("expires_in"), default=0)
    expires_at = fetched_at + timedelta(seconds=expires_in) if expires_in > 0 else fetched_at
    token_updates = {
        "WECHAT_STORE_ACCESS_TOKEN": access_token,
        "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN": str(expires_in) if expires_in > 0 else "",
        "WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT": format_utc_datetime(fetched_at),
        "WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT": format_utc_datetime(expires_at),
        "WECHAT_STORE_ACCESS_TOKEN_SOURCE": ACCESS_TOKEN_SOURCE,
    }
    write_access_token_env(ENV_LOCAL_PATH, token_updates)

    return {
        "status": "ok",
        "message": "AccessToken 已获取并保存到 .env.local。",
        "data": {
            "configured": True,
            "expires_in": token_updates["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN"],
            "fetched_at": token_updates["WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT"],
            "expires_at": token_updates["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT"],
            "source": token_updates["WECHAT_STORE_ACCESS_TOKEN_SOURCE"],
            "errcode": errcode,
            "errmsg": errmsg,
        },
    }


def start_web_login_browser() -> dict[str, Any]:
    global _web_login_process

    existing_process = current_web_login_process()
    if existing_process is not None:
        return {
            "status": "ok",
            "message": "扫码登录窗口已在运行。",
            "data": {
                **web_login_status_data(),
                "already_running": True,
            },
        }

    dependency_error = web_login_dependency_error()
    if dependency_error is not None:
        return dependency_error

    WEB_LOGIN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _web_login_process = subprocess.Popen(
            ["node", str(WEB_LOGIN_SCRIPT_PATH)],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        _web_login_process = None
        return {
            "status": "error",
            "message": f"扫码登录窗口启动失败：{exc}",
            "data": web_login_status_data(),
        }

    return {
        "status": "ok",
        "message": "扫码登录窗口已打开，请在浏览器中手动扫码登录。",
        "data": {
            **web_login_status_data(),
            "already_running": False,
        },
    }


def web_login_dependency_error() -> dict[str, Any] | None:
    if not WEB_LOGIN_SCRIPT_PATH.exists():
        return {
            "status": "error",
            "message": "扫码登录脚本不存在。",
            "data": {
                **web_login_status_data(),
                "missing": "script",
                "script_path": str(WEB_LOGIN_SCRIPT_PATH),
            },
        }
    if not PLAYWRIGHT_NODE_MODULE_DIR.exists():
        return {
            "status": "error",
            "message": "Playwright 依赖不存在，请先确认 node_modules/playwright 已在项目中。",
            "data": {
                **web_login_status_data(),
                "missing": "playwright",
                "playwright_dir": str(PLAYWRIGHT_NODE_MODULE_DIR),
            },
        }
    return None


def current_web_login_process() -> Any:
    global _web_login_process
    if _web_login_process is not None and _web_login_process.poll() is not None:
        _web_login_process = None
    return _web_login_process


def web_login_status_data() -> dict[str, Any]:
    process = current_web_login_process()
    process_running = process is not None and process.poll() is None
    return {
        "profile_dir": str(WEB_LOGIN_PROFILE_DIR),
        "profile_dir_exists": WEB_LOGIN_PROFILE_DIR.exists(),
        "process_running": process_running,
        "pid": process.pid if process_running else None,
        "login_url": WEB_LOGIN_URL,
    }


def request_stable_access_token(base_url: str, app_id: str, app_secret: str, timeout: int = 15) -> dict[str, Any]:
    url = f"{clean_api_base_url(base_url)}/cgi-bin/stable_token"
    body = json.dumps(
        {
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
            "force_refresh": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response_obj:
            response_body = response_obj.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")

    if not response_body.strip():
        return {}
    parsed = json.loads(response_body)
    if not isinstance(parsed, dict):
        raise ValueError("微信 token 接口返回了非对象 JSON。")
    return parsed


def write_access_token_env(path: Path, token_updates: dict[str, str]) -> None:
    write_env_file_with_keys(path, token_updates, ACCESS_TOKEN_ENV_KEYS)


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, value = split_env_line(raw_line)
        if key:
            values[key] = value
    return values


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    write_env_file_with_keys(path, updates, API_CONFIG_KEYS)


def write_env_file_with_keys(path: Path, updates: dict[str, str], ordered_keys: tuple[str, ...]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output_lines: list[str] = []
    written_keys: set[str] = set()

    for line in lines:
        key, _ = split_env_line(line)
        if key in updates:
            if key not in written_keys:
                output_lines.append(f"{key}={format_env_value(updates[key])}")
            written_keys.add(key)
            continue
        output_lines.append(line)

    for key in ordered_keys:
        if key not in written_keys:
            output_lines.append(f"{key}={format_env_value(updates.get(key, ''))}")
            written_keys.add(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")


def split_env_line(raw_line: str) -> tuple[str | None, str]:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None, ""
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").lstrip()
    key, separator, value = stripped.partition("=")
    if not separator:
        return None, ""
    key = key.strip()
    if not key:
        return None, ""
    return key, parse_env_value(value.strip())


def parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        unquoted = value[1:-1]
        if value[0] == '"':
            return (
                unquoted.replace(r"\\", "\\")
                .replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\t", "\t")
            )
        return unquoted

    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def format_env_value(value: str) -> str:
    if value == "":
        return ""
    if all(character not in value for character in (" ", "\t", "\n", "#", '"', "'")):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def clean_config_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def clean_api_base_url(value: Any) -> str:
    cleaned = clean_config_value(value).rstrip("/")
    return cleaned or DEFAULT_WECHAT_API_BASE_URL


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_utc_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def format_bool(value: Any) -> str:
    return "true" if parse_bool(value) else "false"


def render_web_login_ui() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>微信小店扫码登录</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --panel-soft: #f9fafc;
      --text: #18202f;
      --muted: #667085;
      --line: #d9e0ea;
      --accent: #176b55;
      --accent-strong: #0d563f;
      --danger: #b42318;
      --ok: #12703f;
      --warn-bg: #fff8e6;
      --warn-line: #ead28a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      line-height: 1.5;
    }
    .shell {
      width: min(880px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 26px;
      line-height: 1.18;
      letter-spacing: 0;
    }
    h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    p { margin: 0; color: var(--muted); }
    a { color: var(--accent-strong); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }
    .panel-body {
      display: grid;
      gap: 16px;
      padding: 18px;
    }
    .notice {
      border: 1px solid var(--warn-line);
      background: var(--warn-bg);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .notice strong {
      display: block;
      margin-bottom: 4px;
      color: var(--text);
    }
    .status-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--panel-soft);
      color: #344054;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .chip.ok { color: var(--ok); border-color: #b8dec6; background: #f0fbf3; }
    .chip.bad { color: var(--danger); border-color: #f0c4bd; background: #fff4f2; }
    .button-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    button, .button-link {
      appearance: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      border: 1px solid #c9d3df;
      border-radius: 7px;
      background: #ffffff;
      color: #243044;
      padding: 8px 12px;
      font: inherit;
      font-size: 13px;
      font-weight: 750;
      cursor: pointer;
      text-decoration: none;
    }
    button:hover, .button-link:hover {
      background: #f8fafc;
      text-decoration: none;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }
    button.primary:hover { background: var(--accent-strong); }
    .message {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
    }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    dl {
      display: grid;
      grid-template-columns: 132px minmax(0, 1fr);
      gap: 8px 10px;
      margin: 0;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
    }
    dt { color: var(--muted); }
    dd {
      margin: 0;
      color: var(--text);
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .steps {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .steps li {
      display: grid;
      grid-template-columns: 26px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      color: #344054;
    }
    .step-number {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      background: #e9f4ef;
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 800;
    }
    .url-fallback {
      display: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 10px 12px;
      color: #344054;
      font-size: 12px;
      word-break: break-all;
    }
    .url-fallback.visible { display: block; }
    @media (max-width: 720px) {
      .shell { width: min(100vw - 24px, 880px); padding: 18px 0; }
      header { display: grid; }
      h1 { font-size: 22px; }
      .panel-header { display: grid; }
      dl { grid-template-columns: 96px minmax(0, 1fr); }
      .button-row > * { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>微信小店扫码登录</h1>
        <p>在本机打开微信小店官方后台，请使用店铺管理员微信扫码登录。</p>
      </div>
      <a class="button-link" href="/">返回管理台</a>
    </header>

    <section class="panel" aria-labelledby="loginTitle">
      <div class="panel-header">
        <h2 id="loginTitle">后台登录窗口</h2>
        <button type="button" id="refreshStatus">刷新登录状态</button>
      </div>
      <div class="panel-body">
        <div class="notice">
          <strong>这个页面只负责打开微信小店官方后台</strong>
          <p>本地分析工具不会向你索要微信账号、密码，也不会保存微信密码。扫码完成后，后续网页导出采集会复用本机浏览器登录态。</p>
        </div>

        <ol class="steps" aria-label="扫码登录步骤">
          <li><span class="step-number">1</span><span>点击“打开微信小店扫码登录窗口”。</span></li>
          <li><span class="step-number">2</span><span>在新打开的官方后台窗口中用微信扫码。</span></li>
          <li><span class="step-number">3</span><span>进入店铺后台后回到管理台继续真实采集或导出分析。</span></li>
        </ol>

        <div class="status-row">
          <span id="processStatus" class="chip">扫码窗口未读取</span>
          <span id="profileStatus" class="chip">浏览器配置未读取</span>
        </div>

        <dl id="statusList">
          <dt>窗口进程</dt><dd>未读取</dd>
          <dt>官方后台</dt><dd>未读取</dd>
          <dt>浏览器配置</dt><dd>未读取</dd>
        </dl>

        <div class="button-row">
          <button type="button" id="openLogin" class="primary">打开微信小店扫码登录窗口</button>
          <button type="button" id="copyLoginUrl">复制后台登录链接</button>
          <span id="loginMessage" class="message"></span>
        </div>

        <div id="urlFallback" class="url-fallback"></div>
      </div>
    </section>
  </main>

  <script>
    const loginUrlFallback = "https://store.weixin.qq.com/";
    const $ = (id) => document.getElementById(id);

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok || payload.status === "error") {
        throw new Error(payload.message || `${url} 请求失败`);
      }
      return payload.data;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function setMessage(text, kind = "") {
      const el = $("loginMessage");
      el.textContent = text;
      el.className = `message ${kind}`.trim();
    }

    function setChip(id, text, ok) {
      const el = $(id);
      el.textContent = text;
      el.className = `chip ${ok ? "ok" : "bad"}`;
    }

    function renderStatus(data) {
      setChip("processStatus", data.process_running ? "扫码窗口运行中" : "扫码窗口未运行", Boolean(data.process_running));
      setChip("profileStatus", data.profile_dir_exists ? "浏览器配置已创建" : "浏览器配置未创建", Boolean(data.profile_dir_exists));
      $("statusList").innerHTML = [
        ["窗口进程", data.process_running ? `运行中 pid=${data.pid}` : "未运行"],
        ["官方后台", data.login_url || loginUrlFallback],
        ["浏览器配置", data.profile_dir_exists ? data.profile_dir : "未创建"],
      ].map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
      $("urlFallback").textContent = data.login_url || loginUrlFallback;
    }

    async function loadStatus() {
      setMessage("读取登录状态中...");
      try {
        const data = await fetchJson("/web-login/status");
        renderStatus(data);
        setMessage("登录状态已刷新。", "ok");
      } catch (error) {
        setMessage(error.message, "error");
      }
    }

    async function openLogin() {
      setMessage("正在打开微信小店扫码登录窗口...");
      try {
        const data = await fetchJson("/web-login/open", { method: "POST" });
        renderStatus(data);
        setMessage(data.already_running ? "扫码登录窗口已在运行。" : "扫码登录窗口已打开，请在官方后台窗口中扫码。", "ok");
      } catch (error) {
        setMessage(`${error.message} 可以复制后台登录链接手动打开。`, "error");
        $("urlFallback").classList.add("visible");
        await loadStatus();
      }
    }

    async function copyLoginUrl() {
      const url = $("urlFallback").textContent || loginUrlFallback;
      try {
        await navigator.clipboard.writeText(url);
        setMessage("后台登录链接已复制。", "ok");
      } catch (error) {
        $("urlFallback").classList.add("visible");
        setMessage("复制失败，已显示后台登录链接。", "error");
      }
    }

    $("openLogin").addEventListener("click", openLogin);
    $("refreshStatus").addEventListener("click", loadStatus);
    $("copyLoginUrl").addEventListener("click", copyLoginUrl);
    loadStatus();
  </script>
</body>
</html>
"""


def render_admin_ui() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>微信小店数据本地管理台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --panel-soft: #f9fafc;
      --text: #18202f;
      --muted: #667085;
      --line: #d9e0ea;
      --accent: #176b55;
      --accent-strong: #0d563f;
      --warn-bg: #fff8e6;
      --warn-line: #ead28a;
      --info-bg: #eef6ff;
      --info-line: #bfd8f5;
      --danger: #b42318;
      --ok: #12703f;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      line-height: 1.45;
    }
    .shell {
      max-width: 1180px;
      margin: 0 auto;
      padding: 22px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    p { margin: 0; color: var(--muted); }
    a { color: var(--accent-strong); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 7px 11px;
      border: 1px solid var(--accent);
      border-radius: 7px;
      background: var(--accent);
      color: #ffffff;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }
    .button-link:hover {
      background: var(--accent-strong);
      text-decoration: none;
    }
    .top-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
      gap: 14px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .panel-body { padding: 16px; }
    .stack { display: grid; gap: 14px; }
    .notice-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 14px;
    }
    .notice {
      border: 1px solid var(--info-line);
      background: var(--info-bg);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .notice.warn {
      border-color: var(--warn-line);
      background: var(--warn-bg);
    }
    .notice strong {
      display: block;
      margin-bottom: 2px;
      color: var(--text);
      font-size: 13px;
    }
    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    label {
      display: grid;
      gap: 5px;
      color: #344054;
      font-size: 12px;
      font-weight: 650;
    }
    input {
      width: 100%;
      border: 1px solid #cfd7e3;
      border-radius: 7px;
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      min-height: 36px;
      font: inherit;
      font-size: 13px;
      outline: none;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(23, 107, 85, 0.12);
    }
    .full { grid-column: 1 / -1; }
    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 36px;
      padding-top: 18px;
      color: #344054;
      font-weight: 650;
      font-size: 13px;
    }
    .checkbox-row input {
      width: 16px;
      height: 16px;
      min-height: 0;
      padding: 0;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      font-weight: 400;
    }
    .status-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 14px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      background: var(--panel-soft);
      color: #344054;
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .chip.ok { color: var(--ok); border-color: #b8dec6; background: #f0fbf3; }
    .chip.bad { color: var(--danger); border-color: #f0c4bd; background: #fff4f2; }
    button {
      appearance: none;
      border: 1px solid #c9d3df;
      border-radius: 7px;
      background: #ffffff;
      color: #243044;
      min-height: 34px;
      padding: 7px 11px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: #f8fafc; }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }
    button.primary:hover { background: var(--accent-strong); }
    .button-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      flex-wrap: wrap;
    }
    .message {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
    }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    .task-steps {
      display: grid;
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 8px;
    }
    .task-step {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 9px 10px;
      min-height: 58px;
    }
    .task-step strong {
      display: block;
      margin-bottom: 4px;
      color: var(--text);
      font-size: 12px;
    }
    .task-step span {
      color: var(--muted);
      font-size: 12px;
      word-break: break-word;
    }
    .task-step.running {
      border-color: var(--info-line);
      background: var(--info-bg);
    }
    .task-step.completed {
      border-color: #b8dec6;
      background: #f0fbf3;
    }
    .task-step.failed {
      border-color: #f0c4bd;
      background: #fff4f2;
    }
    .report-preview {
      max-height: 360px;
      overflow: auto;
      white-space: pre-wrap;
      color: #243044;
      font-size: 12px;
      line-height: 1.55;
    }
    .result-box {
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 10px 12px;
      color: var(--muted);
      font-size: 12px;
      word-break: break-word;
    }
    .result-box.ok {
      border-color: #b8dec6;
      background: #f0fbf3;
      color: var(--ok);
    }
    .result-box.error {
      border-color: #f0c4bd;
      background: #fff4f2;
      color: var(--danger);
    }
    dl {
      display: grid;
      grid-template-columns: 120px minmax(0, 1fr);
      gap: 8px 10px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd {
      margin: 0;
      color: var(--text);
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 680px;
    }
    th, td {
      padding: 9px 10px;
      border-bottom: 1px solid #edf1f5;
      text-align: left;
      vertical-align: top;
      font-size: 12px;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: #f8fafc;
      color: #475467;
      font-weight: 750;
    }
    tr:last-child td { border-bottom: 0; }
    .empty {
      padding: 18px;
      color: var(--muted);
      text-align: center;
      font-size: 13px;
    }
    .records {
      grid-column: 1 / -1;
    }
    .tabs {
      display: flex;
      gap: 6px;
      align-items: center;
    }
    .tab {
      min-height: 30px;
      padding: 5px 9px;
      font-size: 12px;
    }
    .tab.active {
      background: #e9f4ef;
      border-color: #b7d5c7;
      color: var(--accent-strong);
    }
    .hidden { display: none; }
    @media (max-width: 860px) {
      .shell { padding: 16px; }
      header { display: grid; }
      .top-actions { justify-content: flex-start; }
      .grid, .notice-grid, .form-grid { grid-template-columns: 1fr; }
      .task-steps { grid-template-columns: 1fr; }
      .full { grid-column: auto; }
      dl { grid-template-columns: 100px minmax(0, 1fr); }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>微信小店数据本地管理台</h1>
        <p>本地配置、服务状态和同步记录入口；Swagger 文档仍在 <a href="/docs">/docs</a>。</p>
      </div>
      <div class="top-actions">
        <a class="button-link" href="/login">扫码登录</a>
        <button type="button" id="refreshAll">刷新全部</button>
        <a href="/docs">打开 /docs</a>
      </div>
    </header>

    <section class="notice-grid" aria-label="采集方式说明">
      <div class="notice warn">
        <strong>网页导出需要扫码登录</strong>
        <p>浏览器导出依赖微信后台登录态；需要你手动扫码，不会代替登录或验证码。</p>
      </div>
      <div class="notice">
        <strong>开放 API token 测试不需要扫码登录</strong>
        <p>开放 API 使用 AppID、AppSecret 或 AccessToken；密钥只保存到本地 .env.local，不在页面回显。</p>
      </div>
    </section>

    <div class="grid">
      <section class="panel" aria-labelledby="healthTitle">
        <div class="panel-header">
          <h2 id="healthTitle">服务状态</h2>
          <button type="button" id="refreshHealth">刷新状态</button>
        </div>
        <div class="panel-body">
          <div id="healthMessage" class="message">读取 /health 中...</div>
          <dl id="healthList"></dl>
        </div>
      </section>

      <section class="panel" aria-labelledby="configStatusTitle">
        <div class="panel-header">
          <h2 id="configStatusTitle">配置状态</h2>
          <button type="button" id="refreshConfig">刷新配置</button>
        </div>
        <div class="panel-body">
          <div class="status-row">
            <span id="appSecretStatus" class="chip">AppSecret 未读取</span>
            <span id="accessTokenStatus" class="chip">AccessToken 未读取</span>
          </div>
          <dl id="configSummary"></dl>
        </div>
      </section>

      <section class="panel" aria-labelledby="webLoginTitle">
        <div class="panel-header">
          <h2 id="webLoginTitle">网页后台扫码登录</h2>
          <button type="button" id="refreshWebLogin">刷新状态</button>
        </div>
        <div class="panel-body stack">
          <p>会打开微信小店网页版，手动扫码登录；登录态保存在 data/browser-profile；不是开放 API token。</p>
          <div class="status-row">
            <span id="webLoginProcessStatus" class="chip">扫码窗口未读取</span>
            <span id="webLoginProfileStatus" class="chip">Profile 未读取</span>
          </div>
          <dl id="webLoginStatusList"></dl>
          <div class="button-row">
            <button type="button" id="openWebLogin" class="primary">打开扫码登录窗口</button>
            <span id="webLoginMessage" class="message"></span>
          </div>
        </div>
      </section>

      <section class="panel records" aria-labelledby="orderTaskTitle">
        <div class="panel-header">
          <h2 id="orderTaskTitle">订单真实采集与分析</h2>
          <button type="button" id="refreshTasks">刷新任务</button>
        </div>
        <div class="panel-body stack">
          <div class="notice warn">
            <strong>启动前请确认扫码登录窗口已关闭</strong>
            <p>采集会复用 data/browser-profile；如果扫码登录窗口仍在运行，同一个浏览器配置可能被占用，真实采集会失败。</p>
          </div>
          <form id="orderTaskForm" class="form-grid">
            <label>
              Shop ID
              <input id="collectShopId" name="shop_id" autocomplete="off" placeholder="yijia-baihuo">
            </label>
            <label>
              Shop Name
              <input id="collectShopName" name="shop_name" autocomplete="off" placeholder="艺家百货甄选店">
            </label>
            <label>
              开始日期
              <input id="collectFrom" name="from" type="date" required>
            </label>
            <label>
              结束日期
              <input id="collectTo" name="to" type="date" required>
            </label>
            <label class="checkbox-row">
              <input id="collectHeadless" name="headless" type="checkbox">
              后台无头运行
            </label>
            <div class="button-row">
              <button type="submit" id="startOrderTask" class="primary">启动订单导出分析</button>
              <span id="taskMessage" class="message"></span>
            </div>
          </form>
          <div class="notice">
            <strong>离线导入：本地导出复跑</strong>
            <p>无法扫码测试时，可选择项目 data/raw 下已经下载过的导出目录，使用 source_type=local_export 直接导入、分析、生成报告。</p>
          </div>
          <form id="localExportForm" class="form-grid">
            <label class="full">
              source_dir
              <input id="localSourceDir" name="source_dir" autocomplete="off" placeholder="data/raw/collect_yijia-baihuo_2026-06-01_2026-06-03_20260628T094048Z">
            </label>
            <div class="button-row full">
              <button type="submit" id="startLocalExportTask">复跑本地导出文件</button>
              <span class="hint">只允许项目 data/raw 下包含 task-metadata.json 的目录。</span>
            </div>
          </form>
          <div class="status-row">
            <span id="taskStateChip" class="chip">任务未启动</span>
            <span id="taskReportChip" class="chip">报告未生成</span>
          </div>
          <dl id="taskSummary"></dl>
          <div id="taskSteps" class="task-steps"></div>
          <div id="taskResult" class="result-box">还没有从页面启动过真实订单采集。</div>
          <div id="reportPreview" class="result-box report-preview hidden"></div>
        </div>
      </section>

      <section class="panel" aria-labelledby="configTitle">
        <div class="panel-header">
          <h2 id="configTitle">开放 API 配置</h2>
          <span class="hint">保存到项目根目录 .env.local</span>
        </div>
        <form id="configForm" class="panel-body">
          <div class="form-grid">
            <label>
              AppID
              <input id="appId" name="app_id" autocomplete="off" placeholder="WECHAT_STORE_APP_ID">
            </label>
            <label>
              API Base URL
              <input id="apiBaseUrl" name="api_base_url" autocomplete="off" placeholder="https://api.weixin.qq.com">
            </label>
            <label>
              AppSecret
              <input id="appSecret" name="app_secret" type="password" autocomplete="new-password" placeholder="留空则保留当前值">
              <span class="hint">不会通过 /api-config 回显。</span>
            </label>
            <label>
              AccessToken
              <input id="accessToken" name="access_token" type="password" autocomplete="new-password" placeholder="留空则保留当前值">
              <span class="hint">不会通过 /api-config 回显。</span>
            </label>
            <label>
              Raw Archive Dir
              <input id="rawArchiveDir" name="raw_archive_dir" autocomplete="off" placeholder="data/raw/api">
            </label>
            <label class="checkbox-row">
              <input id="syncDryRun" name="sync_dry_run" type="checkbox">
              Dry Run
            </label>
            <label>
              Shop ID
              <input id="shopId" name="shop_id" autocomplete="off" placeholder="local-shop-id">
            </label>
            <label>
              Shop Name
              <input id="shopName" name="shop_name" autocomplete="off" placeholder="门店名称">
            </label>
          </div>
          <div class="button-row">
            <button type="submit" class="primary">保存配置</button>
            <button type="button" id="clearSensitive">清空密钥输入框</button>
            <span id="configMessage" class="message"></span>
          </div>
        </form>
      </section>

      <section class="panel" aria-labelledby="tokenTitle">
        <div class="panel-header">
          <h2 id="tokenTitle">AccessToken 获取</h2>
          <button type="button" id="fetchAccessToken">获取 AccessToken</button>
        </div>
        <div class="panel-body stack">
          <p>需要先填 AppID/AppSecret；获取后才可继续真实接口测试。页面只显示过期时间和来源，不显示 token 明文。</p>
          <div id="tokenResult" class="result-box">尚未请求 /api-token/fetch。</div>
        </div>
      </section>

      <section class="panel" aria-labelledby="quickGuideTitle">
        <div class="panel-header">
          <h2 id="quickGuideTitle">操作提示</h2>
        </div>
        <div class="panel-body stack">
          <div>
            <strong>只做本地配置</strong>
            <p>保存配置不会启动真实微信 API，也不会发起浏览器导出任务。</p>
          </div>
          <div>
            <strong>密钥保护</strong>
            <p>AppSecret 和 AccessToken 只显示已配置/未配置；留空保存会保留旧值。</p>
          </div>
          <div>
            <strong>同步记录</strong>
            <p>下方读取 /sync-runs 和 /raw-api-responses，便于回看 API 同步元数据和原始归档索引。</p>
          </div>
        </div>
      </section>

      <section class="panel records" aria-labelledby="recordsTitle">
        <div class="panel-header">
          <h2 id="recordsTitle">同步记录</h2>
          <div class="tabs" role="tablist" aria-label="同步记录切换">
            <button type="button" id="tasksTab" class="tab active">采集任务</button>
            <button type="button" id="syncRunsTab" class="tab">/sync-runs</button>
            <button type="button" id="rawResponsesTab" class="tab">/raw-api-responses</button>
            <button type="button" id="refreshRecords">刷新记录</button>
          </div>
        </div>
        <div class="panel-body">
          <div id="recordsMessage" class="message">读取同步记录中...</div>
          <div id="tasksPanel" class="table-wrap"></div>
          <div id="syncRunsPanel" class="table-wrap hidden"></div>
          <div id="rawResponsesPanel" class="table-wrap hidden"></div>
        </div>
      </section>
    </div>
  </main>

  <script>
    const state = { activeRecords: "tasks", activeTaskId: null, taskPoller: null };
    const $ = (id) => document.getElementById(id);
    const taskStepLabels = {
      collect: "导出/本地文件",
      import_metadata: "导入任务",
      import_files: "导入订单",
      analyze: "计算指标",
      report: "生成报告",
    };
    const defaultLocalExportDir = "data/raw/collect_yijia-baihuo_2026-06-01_2026-06-03_20260628T094048Z";

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok || payload.status === "error") {
        throw new Error(payload.message || `${url} 请求失败`);
      }
      return payload.data;
    }

    function setMessage(id, text, kind = "") {
      const el = $(id);
      el.textContent = text;
      el.className = `message ${kind}`.trim();
    }

    function renderDefinitionList(id, rows) {
      const el = $(id);
      el.innerHTML = rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value || "-"))}</dd>`).join("");
    }

    function escapeHtml(value) {
      return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function statusChip(id, label, configured) {
      const el = $(id);
      el.textContent = `${label} ${configured ? "已配置" : "未配置"}`;
      el.className = `chip ${configured ? "ok" : "bad"}`;
    }

    async function loadHealth() {
      setMessage("healthMessage", "读取 /health 中...");
      try {
        const data = await fetchJson("/health");
        renderDefinitionList("healthList", [
          ["数据库", data.db_exists ? "已找到" : "未找到"],
          ["DB Path", data.db_path],
          ["Reports", data.reports_dir_exists ? "已找到" : "未找到"],
          ["Reports Path", data.reports_dir],
        ]);
        setMessage("healthMessage", "服务可用。", "ok");
      } catch (error) {
        setMessage("healthMessage", error.message, "error");
      }
    }

    async function loadConfig() {
      setMessage("configMessage", "读取 /api-config 中...");
      try {
        const data = await fetchJson("/api-config");
        $("appId").value = data.values.app_id || "";
        $("apiBaseUrl").value = data.values.api_base_url || "";
        $("rawArchiveDir").value = data.values.raw_archive_dir || "";
        $("syncDryRun").checked = Boolean(data.values.sync_dry_run);
        $("shopId").value = data.values.shop_id || "";
        $("shopName").value = data.values.shop_name || "";
        $("collectShopId").value = $("collectShopId").value || data.values.shop_id || "";
        $("collectShopName").value = $("collectShopName").value || data.values.shop_name || "";
        $("appSecret").value = "";
        $("accessToken").value = "";

        statusChip("appSecretStatus", "AppSecret", data.secrets.app_secret.configured);
        statusChip("accessTokenStatus", "AccessToken", data.secrets.access_token.configured);
        const tokenStatus = data.secrets.access_token || {};
        renderDefinitionList("configSummary", [
          [".env.local", data.exists ? "已创建" : "未创建"],
          ["Env Path", data.env_path],
          ["AppID", data.values.app_id || "未填写"],
          ["Base URL", data.values.api_base_url || "未填写"],
          ["Raw Dir", data.values.raw_archive_dir || "未填写"],
          ["Dry Run", data.values.sync_dry_run ? "true" : "false"],
          ["Shop", data.values.shop_name || data.values.shop_id || "未填写"],
          ["Token Source", tokenStatus.source || "未获取"],
          ["Token Expires", tokenStatus.expires_at || "未获取"],
        ]);
        setMessage("configMessage", "配置已加载，密钥输入框已清空。", "ok");
      } catch (error) {
        setMessage("configMessage", error.message, "error");
      }
    }

    async function saveConfig(event) {
      event.preventDefault();
      setMessage("configMessage", "保存配置中...");
      const payload = {
        app_id: $("appId").value,
        app_secret: $("appSecret").value,
        access_token: $("accessToken").value,
        api_base_url: $("apiBaseUrl").value,
        sync_dry_run: $("syncDryRun").checked,
        raw_archive_dir: $("rawArchiveDir").value,
        shop_id: $("shopId").value,
        shop_name: $("shopName").value,
      };
      try {
        await fetchJson("/api-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await loadConfig();
        setMessage("configMessage", "配置已保存。留空的 AppSecret/AccessToken 已保留旧值。", "ok");
      } catch (error) {
        setMessage("configMessage", error.message, "error");
      }
    }

    function setTokenResult(text, kind = "") {
      const el = $("tokenResult");
      el.textContent = text;
      el.className = `result-box ${kind}`.trim();
    }

    async function fetchAccessToken() {
      setTokenResult("正在请求 /api-token/fetch...");
      try {
        const response = await fetch("/api-token/fetch", { method: "POST" });
        const payload = await response.json();
        const data = payload.data || {};
        if (!response.ok || payload.status === "error") {
          const errcodeText = data.errcode === undefined || data.errcode === null ? "" : ` errcode=${data.errcode}`;
          setTokenResult(`${payload.message || "AccessToken 获取失败。"}${errcodeText}`, "error");
          await loadConfig();
          return;
        }
        const parts = [
          "AccessToken 已保存。",
          `source=${data.source || "unknown"}`,
          `expires_in=${data.expires_in || "-"}`,
          `expires_at=${data.expires_at || "-"}`,
        ];
        setTokenResult(parts.join(" "), "ok");
        await loadConfig();
      } catch (error) {
        setTokenResult(error.message, "error");
      }
    }

    function renderWebLoginStatus(data) {
      statusChip("webLoginProcessStatus", "扫码窗口", Boolean(data.process_running));
      statusChip("webLoginProfileStatus", "Profile", Boolean(data.profile_dir_exists));
      renderDefinitionList("webLoginStatusList", [
        ["Profile Dir", data.profile_dir],
        ["窗口进程", data.process_running ? `运行中 pid=${data.pid}` : "未运行"],
        ["Login URL", data.login_url],
      ]);
    }

    async function loadWebLoginStatus() {
      setMessage("webLoginMessage", "读取 /web-login/status 中...");
      try {
        const data = await fetchJson("/web-login/status");
        renderWebLoginStatus(data);
        setMessage("webLoginMessage", "扫码登录状态已加载。", "ok");
      } catch (error) {
        setMessage("webLoginMessage", error.message, "error");
      }
    }

    async function openWebLogin() {
      setMessage("webLoginMessage", "正在打开扫码登录窗口...");
      try {
        const data = await fetchJson("/web-login/open", { method: "POST" });
        renderWebLoginStatus(data);
        setMessage(
          "webLoginMessage",
          data.already_running ? "扫码登录窗口已在运行。" : "扫码登录窗口已打开，请手动扫码登录。",
          "ok"
        );
      } catch (error) {
        setMessage("webLoginMessage", error.message, "error");
        await loadWebLoginStatus();
      }
    }

    function setTaskResult(text, kind = "") {
      const el = $("taskResult");
      el.textContent = text;
      el.className = `result-box ${kind}`.trim();
    }

    function renderTask(task) {
      if (!task) {
        $("taskStateChip").textContent = "任务未启动";
        $("taskStateChip").className = "chip";
        $("taskReportChip").textContent = "报告未生成";
        $("taskReportChip").className = "chip";
        $("taskSummary").innerHTML = "";
        renderTaskSteps({});
        return;
      }
      const stateText = task.state || task.status || "unknown";
      const isOk = stateText === "completed";
      const isBad = stateText === "failed";
      $("taskStateChip").textContent = `任务 ${stateText}`;
      $("taskStateChip").className = `chip ${isOk ? "ok" : isBad ? "bad" : ""}`.trim();

      const reportId = task.result?.report?.report_id || "";
      $("taskReportChip").textContent = reportId ? `报告 ${reportId}` : "报告未生成";
      $("taskReportChip").className = `chip ${reportId ? "ok" : isBad ? "bad" : ""}`.trim();
      renderDefinitionList("taskSummary", [
        ["Task ID", task.id || task.task_id],
        ["采集任务", task.collection_task_id || "-"],
        ["店铺", task.shop_name_snapshot || task.shop_id || "-"],
        ["日期", `${task.date_range?.from || "-"} 至 ${task.date_range?.to || "-"}`],
        ["状态", stateText],
        ["原始目录", task.result?.source_dir || task.source_dir || "-"],
        ["报告", reportId || "-"],
      ]);
      renderTaskSteps(task.steps || {});

      if (task.error?.message) {
        setTaskResult(task.error.message, "error");
      } else if (task.result) {
        const parts = [
          `订单导出文件数：${task.result.artifact_count ?? "-"}`,
          `analysis_run_id：${task.result.analysis_run_id || "-"}`,
          `report_id：${reportId || "-"}`,
          `source_dir：${task.result.source_dir || "-"}`,
        ];
        setTaskResult(parts.join("\\n"), "ok");
      } else {
        setTaskResult(task.source_type === "local_export" ? "本地导出文件正在导入、分析并生成报告。" : "任务正在执行，请保持微信后台登录态有效。");
      }
    }

    function renderTaskSteps(steps) {
      $("taskSteps").innerHTML = Object.entries(taskStepLabels).map(([key, label]) => {
        const step = steps[key] || {};
        const status = step.status || "pending";
        const detail = step.error || step.completed_at || step.started_at || "";
        return `<div class="task-step ${escapeHtml(status)}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(status)}${detail ? ` · ${escapeHtml(String(detail))}` : ""}</span></div>`;
      }).join("");
    }

    function setDefaultDates() {
      const now = new Date();
      const end = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      const start = new Date(end.getTime() - 6 * 24 * 60 * 60 * 1000);
      $("collectFrom").value = $("collectFrom").value || start.toISOString().slice(0, 10);
      $("collectTo").value = $("collectTo").value || end.toISOString().slice(0, 10);
      $("localSourceDir").value = $("localSourceDir").value || defaultLocalExportDir;
    }

    async function startOrderTask(event) {
      event.preventDefault();
      const loginStatus = await fetchJson("/web-login/status");
      if (loginStatus.process_running) {
        setMessage("taskMessage", "扫码登录窗口仍在运行，请先关闭官方后台登录窗口，再启动采集。", "error");
        return;
      }
      const payload = {
        shop_id: $("collectShopId").value,
        task_name: "订单真实导出分析",
        source_type: "web_export",
        params: {
          shop_name: $("collectShopName").value,
          from: $("collectFrom").value,
          to: $("collectTo").value,
          types: ["orders"],
          headless: $("collectHeadless").checked,
        },
      };
      setMessage("taskMessage", "正在启动真实订单采集任务...");
      try {
        const task = await fetchJson("/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.activeTaskId = task.id || task.task_id;
        renderTask(task);
        setMessage("taskMessage", "任务已启动，正在轮询进度。", "ok");
        startTaskPolling(state.activeTaskId);
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    async function startLocalExportTask(event) {
      event.preventDefault();
      const payload = {
        shop_id: $("collectShopId").value,
        task_name: "本地导出复跑分析",
        source_type: "local_export",
        params: {
          mode: "local_export",
          shop_name: $("collectShopName").value,
          from: $("collectFrom").value,
          to: $("collectTo").value,
          types: ["orders"],
          source_dir: $("localSourceDir").value,
        },
      };
      setMessage("taskMessage", "正在复跑本地导出文件...");
      try {
        const task = await fetchJson("/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.activeTaskId = task.id || task.task_id;
        renderTask(task);
        setMessage("taskMessage", "本地复跑任务已启动，正在轮询进度。", "ok");
        startTaskPolling(state.activeTaskId);
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    function startTaskPolling(taskId) {
      if (state.taskPoller) {
        clearInterval(state.taskPoller);
      }
      state.taskPoller = setInterval(() => {
        pollTask(taskId);
      }, 2000);
      pollTask(taskId);
    }

    async function pollTask(taskId) {
      if (!taskId) return;
      try {
        const task = await fetchJson(`/tasks/${encodeURIComponent(taskId)}`);
        renderTask(task);
        if (["completed", "failed"].includes(task.state || task.status)) {
          clearInterval(state.taskPoller);
          state.taskPoller = null;
          await loadTasks();
          await loadRecords();
          if (task.result?.report?.report_id) {
            await loadReportPreview(task.result.report.report_id);
          }
        }
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    async function loadReportPreview(reportId) {
      try {
        const report = await fetchJson(`/reports/${encodeURIComponent(reportId)}`);
        $("reportPreview").textContent = report.content || "报告已生成，但没有可展示的正文。";
        $("reportPreview").classList.remove("hidden");
      } catch (error) {
        $("reportPreview").textContent = `报告读取失败：${error.message}`;
        $("reportPreview").classList.remove("hidden");
      }
    }

    async function loadTasks() {
      setMessage("taskMessage", "读取采集任务中...");
      try {
        const tasks = await fetchJson("/tasks");
        const latest = (tasks || [])[0];
        if (!state.activeTaskId && latest) {
          renderTask(latest);
        } else if (!state.activeTaskId) {
          renderTask(null);
        }
        setMessage("taskMessage", "采集任务已刷新。", "ok");
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    function renderTable(id, rows, columns) {
      const el = $(id);
      if (!rows.length) {
        el.innerHTML = '<div class="empty">暂无记录</div>';
        return;
      }
      const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
      const body = rows.map((row) => {
        const cells = columns.map((column) => `<td>${escapeHtml(String(row[column.key] ?? "-"))}</td>`).join("");
        return `<tr>${cells}</tr>`;
      }).join("");
      el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    async function loadRecords() {
      setMessage("recordsMessage", "读取采集任务、/sync-runs 和 /raw-api-responses 中...");
      try {
        const [tasks, syncRuns, rawResponses] = await Promise.all([
          fetchJson("/tasks"),
          fetchJson("/sync-runs?limit=20"),
          fetchJson("/raw-api-responses?limit=20"),
        ]);
        renderTable("tasksPanel", tasks || [], [
          { key: "id", label: "Task ID" },
          { key: "shop_id", label: "Shop ID" },
          { key: "shop_name_snapshot", label: "Shop Name" },
          { key: "source_type", label: "Source" },
          { key: "status", label: "Status" },
          { key: "started_at", label: "Started" },
          { key: "completed_at", label: "Finished" },
          { key: "collection_task_id", label: "Collector Task" },
        ]);
        renderTable("syncRunsPanel", syncRuns || [], [
          { key: "sync_run_id", label: "Sync Run ID" },
          { key: "shop_id", label: "Shop ID" },
          { key: "shop_name_snapshot", label: "Shop Name" },
          { key: "source_kind", label: "Source" },
          { key: "status", label: "Status" },
          { key: "started_at", label: "Started" },
          { key: "finished_at", label: "Finished" },
          { key: "item_count", label: "Items" },
          { key: "raw_response_count", label: "Raw" },
        ]);
        renderTable("rawResponsesPanel", rawResponses || [], [
          { key: "raw_response_id", label: "Raw Response ID" },
          { key: "sync_run_id", label: "Sync Run ID" },
          { key: "shop_id", label: "Shop ID" },
          { key: "endpoint", label: "Endpoint" },
          { key: "status", label: "Status" },
          { key: "record_count", label: "Records" },
          { key: "size_bytes", label: "Bytes" },
          { key: "pulled_at", label: "Pulled" },
        ]);
        setMessage("recordsMessage", "同步记录已加载。", "ok");
      } catch (error) {
        setMessage("recordsMessage", error.message, "error");
      }
    }

    function selectRecordsTab(tab) {
      state.activeRecords = tab;
      const showTasks = tab === "tasks";
      const showSyncRuns = tab === "syncRuns";
      $("tasksPanel").classList.toggle("hidden", !showTasks);
      $("syncRunsPanel").classList.toggle("hidden", !showSyncRuns);
      $("rawResponsesPanel").classList.toggle("hidden", tab !== "rawResponses");
      $("tasksTab").classList.toggle("active", showTasks);
      $("syncRunsTab").classList.toggle("active", showSyncRuns);
      $("rawResponsesTab").classList.toggle("active", tab === "rawResponses");
    }

    async function refreshAll() {
      setDefaultDates();
      await Promise.all([loadHealth(), loadConfig(), loadWebLoginStatus(), loadTasks(), loadRecords()]);
    }

    $("configForm").addEventListener("submit", saveConfig);
    $("refreshHealth").addEventListener("click", loadHealth);
    $("refreshConfig").addEventListener("click", loadConfig);
    $("refreshWebLogin").addEventListener("click", loadWebLoginStatus);
    $("refreshRecords").addEventListener("click", loadRecords);
    $("refreshAll").addEventListener("click", refreshAll);
    $("fetchAccessToken").addEventListener("click", fetchAccessToken);
    $("openWebLogin").addEventListener("click", openWebLogin);
    $("orderTaskForm").addEventListener("submit", startOrderTask);
    $("localExportForm").addEventListener("submit", startLocalExportTask);
    $("refreshTasks").addEventListener("click", loadTasks);
    $("tasksTab").addEventListener("click", () => selectRecordsTab("tasks"));
    $("syncRunsTab").addEventListener("click", () => selectRecordsTab("syncRuns"));
    $("rawResponsesTab").addEventListener("click", () => selectRecordsTab("rawResponses"));
    $("clearSensitive").addEventListener("click", () => {
      $("appSecret").value = "";
      $("accessToken").value = "";
      setMessage("configMessage", "密钥输入框已清空；保存时会保留旧值。");
    });

    refreshAll();
  </script>
</body>
</html>
"""
