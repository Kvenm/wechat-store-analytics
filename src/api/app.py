from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api.repository import LocalRepository
from api.task_runner import TaskRunnerError
from api.task_runner import check_local_export_files
from api.task_runner import get_runtime_task
from api.task_runner import list_runtime_tasks
from api.task_runner import normalize_web_export_payload
from api.task_runner import start_web_export_task
from shared.ids import new_id
from shared.module_registry import module_capabilities
from shared.paths import PROJECT_ROOT
from sync.wechat_api import DEFAULT_API_BASE_URL, create_api_sync_report, run_wechat_api_sync


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


class ApiSyncRunRequest(BaseModel):
    shop_id: Optional[str] = None
    shop_name: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    sync_run_id: Optional[str] = None
    endpoints: Optional[list[str]] = None
    page_size: int = 30
    max_pages: int = 20
    generate_report: bool = True
    endpoint_params: dict[str, dict[str, Any]] = Field(default_factory=dict)


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


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return response("ok", "Collection capabilities loaded.", module_capabilities())


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


@app.post("/api-sync/runs")
def create_api_sync_run(payload: ApiSyncRunRequest) -> dict[str, Any]:
    result = run_configured_api_sync(model_to_dict(payload))
    if result["status"] == "error":
        return response("error", result["message"], result["data"])
    return response("ok", "微信小店 API 同步完成。", result["data"])


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


@app.post("/tasks/local-export/check")
def check_local_export(payload: TaskRequest) -> dict[str, Any]:
    try:
        result = check_local_export_files(model_to_dict(payload))
    except TaskRunnerError as exc:
        return response("error", str(exc), {"request": model_to_dict(payload)})
    return response("ok", "本地导出文件校验完成。", result)


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
            "message": "请先完成服务端接口应用和接口密钥配置，再获取接口授权。",
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
            "message": f"接口授权获取失败：{exc}",
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
            "message": errmsg or "微信未返回可保存的接口授权。",
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
        "message": "接口授权已获取并保存。",
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


def run_configured_api_sync(payload: Mapping[str, Any]) -> dict[str, Any]:
    env_values = parse_env_file(ENV_LOCAL_PATH)
    shop_id = clean_config_value(payload.get("shop_id") or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""))
    shop_name = clean_config_value(payload.get("shop_name") or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_name"], ""))
    access_token = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], ""))
    api_base_url = clean_api_base_url(env_values.get(API_CONFIG_FIELD_TO_KEY["api_base_url"], "") or DEFAULT_API_BASE_URL)
    archive_dir = resolve_project_path(
        clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["raw_archive_dir"], "")) or PROJECT_ROOT / "data" / "raw" / "api"
    )

    missing = {
        "shop_id": not bool(shop_id),
        "access_token": not bool(access_token),
    }
    if any(missing.values()):
        return {
            "status": "error",
            "message": "请先完成服务端店铺和接口授权配置，再启动数据同步。",
            "data": {"missing": missing},
        }

    try:
        repo = repository()
        date_from = clean_config_value(payload.get("date_from") or payload.get("from"))
        date_to = clean_config_value(payload.get("date_to") or payload.get("to"))
        sync_run_id = clean_config_value(payload.get("sync_run_id")) or new_id("sync")
        sync_result = run_wechat_api_sync(
            db_path=repo.db_path,
            archive_dir=archive_dir,
            shop_id=shop_id,
            shop_name=shop_name or None,
            date_from=date_from,
            date_to=date_to,
            sync_run_id=sync_run_id,
            access_token=access_token,
            api_base_url=api_base_url,
            endpoints=payload.get("endpoints"),
            page_size=int(payload.get("page_size") or 30),
            max_pages=int(payload.get("max_pages") or 20),
            endpoint_params=_api_sync_endpoint_params(payload.get("endpoint_params")),
        )
        if sync_result.get("status") == "completed" and parse_bool(payload.get("generate_report", True)):
            sync_result.update(
                create_api_sync_report(
                    db_path=repo.db_path,
                    reports_dir=repo.reports_dir,
                    shop_id=shop_id,
                    date_from=date_from,
                    date_to=date_to,
                    sync_run_id=sync_run_id,
                )
            )
    except Exception as exc:
        return {
            "status": "error",
            "message": f"微信小店 API 同步失败：{exc}",
            "data": {"shop_id": shop_id},
        }
    return {"status": "ok", "message": "微信小店 API 同步完成。", "data": sync_result}


def _api_sync_endpoint_params(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): dict(item)
        for key, item in value.items()
        if isinstance(item, Mapping)
    }


def resolve_project_path(value: Any) -> str:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(PROJECT_ROOT / path)


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
  <title>微信小店经营分析工作台</title>
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
    .app-shell {
      width: min(1280px, 100%);
      margin: 0 auto;
      padding: 24px;
      display: grid;
      grid-template-columns: 232px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }
    .side-nav {
      position: sticky;
      top: 18px;
      display: grid;
      gap: 14px;
      align-self: start;
      min-width: 0;
    }
    .side-nav-header {
      display: grid;
      gap: 6px;
      padding: 14px 8px 8px;
    }
    .side-nav-title {
      color: var(--text);
      font-size: 15px;
      font-weight: 800;
      line-height: 1.25;
    }
    .side-nav-subtitle {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .side-nav-items {
      display: grid;
      gap: 7px;
    }
    .side-nav-item {
      width: 100%;
      min-height: 42px;
      justify-content: flex-start;
      display: grid;
      gap: 2px;
      padding: 9px 12px;
      border-color: transparent;
      background: transparent;
      text-align: left;
      font-weight: 750;
    }
    .side-nav-item span,
    .side-nav-item small {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .side-nav-item small {
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
    }
    .side-nav-item:hover {
      background: #eef3f8;
    }
    .side-nav-item.active {
      border-color: #b7d5c7;
      background: #e9f4ef;
      color: var(--accent-strong);
    }
    .side-nav-item.active small {
      color: #406b5d;
    }
    .side-nav-footer {
      display: grid;
      gap: 8px;
      padding-top: 4px;
    }
    .side-nav-footer a {
      color: var(--muted);
      font-size: 12px;
    }
    .main-content {
      min-width: 0;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 14px;
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
    .menu-section {
      min-width: 0;
    }
    .menu-grid {
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
      margin-bottom: 14px;
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
    .context-panel .panel-body { padding: 14px 16px; }
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
    input,
    select {
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
    input:focus,
    select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(23, 107, 85, 0.12);
    }
    .module-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
    }
    .module-option {
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px 10px;
      color: #243044;
      font-size: 13px;
      font-weight: 700;
    }
    .module-option input {
      width: 16px;
      height: 16px;
      min-height: 0;
      padding: 0;
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
    .check-summary {
      display: grid;
      gap: 10px;
      color: #243044;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 8px;
    }
    .metric-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 9px 10px;
    }
    .metric-card strong {
      display: block;
      color: var(--text);
      font-size: 16px;
      line-height: 1.2;
      margin-bottom: 3px;
    }
    .metric-card span {
      color: var(--muted);
      font-size: 12px;
    }
    .readiness-list {
      display: grid;
      gap: 6px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .readiness-list li {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      color: #344054;
    }
    .readiness-mark {
      flex: 0 0 auto;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 800;
    }
    .readiness-mark.ok {
      background: #e9f7ef;
      color: var(--ok);
    }
    .readiness-mark.warn {
      background: #fff3d6;
      color: #996a00;
    }
    .warning-list {
      margin: 0;
      padding-left: 18px;
      color: #5f4b16;
    }
    .readiness-panels,
    .inspection-list {
      display: grid;
      gap: 8px;
    }
    .readiness-panel,
    .inspection-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 9px 10px;
      display: grid;
      gap: 6px;
    }
    .readiness-panel-header,
    .inspection-item-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
    }
    .readiness-panel strong,
    .inspection-item strong {
      color: var(--text);
      font-size: 13px;
    }
    .inspection-meta,
    .field-match-list,
    .readiness-summary {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .field-match-list {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }
    .capability-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px;
    }
    .capability-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 10px;
      display: grid;
      gap: 7px;
    }
    .capability-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: flex-start;
    }
    .capability-title strong {
      color: var(--text);
      font-size: 13px;
      line-height: 1.35;
    }
    .capability-title code {
      color: var(--muted);
      font-size: 11px;
    }
    .capability-notes {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
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
    .wide-panel {
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
    .menu-hidden { display: none !important; }
    @media (max-width: 860px) {
      .app-shell {
        display: block;
        padding: 16px;
      }
      .side-nav {
        position: static;
        gap: 10px;
        margin-bottom: 14px;
      }
      .side-nav-header {
        padding: 0;
      }
      .side-nav-items {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding-bottom: 2px;
      }
      .side-nav-item {
        flex: 0 0 136px;
      }
      .side-nav-footer {
        display: none;
      }
      header { display: grid; }
      .top-actions { justify-content: flex-start; }
      .grid, .menu-grid, .notice-grid, .form-grid { grid-template-columns: 1fr; }
      .task-steps { grid-template-columns: 1fr; }
      .full { grid-column: auto; }
      dl { grid-template-columns: 100px minmax(0, 1fr); }
    }
  </style>
</head>
<body>
  <main class="app-shell">
    <aside class="side-nav" aria-label="功能菜单">
      <div class="side-nav-header">
        <div class="side-nav-title">经营数据中心</div>
        <div class="side-nav-subtitle">微信小店分析工作台</div>
      </div>
      <nav class="side-nav-items">
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="apiSyncSection" aria-controls="apiSyncSection">
          <span>数据同步</span>
          <small>官方接口</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="authSection" aria-controls="authSection">
          <span>授权状态</span>
          <small>只读状态</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="localExportSection" aria-controls="localExportSection">
          <span>文件导入</span>
          <small>Excel / CSV</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="webOrderSection" aria-controls="webOrderSection">
          <span>订单导出</span>
          <small>后台导出</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="statusSection" aria-controls="statusSection">
          <span>任务进度</span>
          <small>同步与分析状态</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="recordsSection" aria-controls="recordsSection">
          <span>报告中心</span>
          <small>经营分析结果</small>
        </button>
        <button type="button" class="side-nav-item menu-trigger" data-menu-target="capabilitySection" aria-controls="capabilitySection">
          <span>数据范围</span>
          <small>可用数据模块</small>
        </button>
      </nav>
      <div class="side-nav-footer">
        <a href="/login">扫码登录</a>
      </div>
    </aside>

    <section class="main-content">
      <header>
        <div>
          <h1>经营数据中心</h1>
          <p>同步微信小店数据，查看任务进度和经营报告。</p>
        </div>
        <div class="top-actions">
          <a class="button-link" href="/login">扫码登录</a>
          <button type="button" id="refreshAll">刷新全部</button>
        </div>
      </header>

      <section class="panel records context-panel" aria-labelledby="commonTaskTitle">
        <div class="panel-header">
          <h2 id="commonTaskTitle">任务信息</h2>
        </div>
        <div class="panel-body">
          <div class="form-grid">
            <input id="collectShopId" name="shop_id" type="hidden">
            <input id="collectShopName" name="shop_name" type="hidden">
            <dl id="shopInfoSummary" class="full">
              <dt>店铺名称</dt><dd>未读取</dd>
              <dt>店铺 ID</dt><dd>未读取</dd>
            </dl>
            <label>
              开始日期
              <input id="collectFrom" name="from" type="date" required>
            </label>
            <label>
              结束日期
              <input id="collectTo" name="to" type="date" required>
            </label>
            <div class="full">
              <span id="taskMessage" class="message"></span>
            </div>
          </div>
        </div>
      </section>

      <section id="apiSyncSection" class="panel records menu-section" aria-labelledby="apiSyncTitle">
        <div class="panel-header">
          <h2 id="apiSyncTitle">数据同步</h2>
        </div>
        <div class="panel-body stack">
          <div class="notice">
            <strong>扫码登录只用于订单导出</strong>
            <p>数据同步需要服务端先完成接口授权；当前页面只展示授权状态，不提供手动填写。</p>
          </div>
          <form id="apiSyncForm" class="form-grid">
            <label class="full">
              数据模块
              <div class="module-grid">
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="products" checked>商品档案</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="orders" checked>订单明细</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="aftersale" checked>售后退款</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="funds" checked>资金流水</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="compass_shop">店铺经营概览</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="compass_product">商品表现分析</label>
                <label class="module-option"><input type="checkbox" name="api_sync_endpoint" value="compass_audience">客户画像</label>
              </div>
            </label>
            <label class="checkbox-row full">
              <input id="apiSyncGenerateReport" name="generate_report" type="checkbox" checked>
              完成后生成分析报告
            </label>
            <div class="button-row full">
              <button type="submit" id="startApiSyncTask" class="primary">开始数据同步</button>
            </div>
          </form>
        </div>
      </section>

      <section id="authSection" class="panel records menu-section" aria-labelledby="authTitle">
        <div class="panel-header">
          <h2 id="authTitle">授权状态</h2>
          <button type="button" id="refreshConfig">刷新状态</button>
        </div>
        <div class="panel-body stack">
          <div class="notice">
            <strong>此处只展示授权结果</strong>
            <p>店铺、接口应用和密钥由服务端授权流程维护。普通扫码登录不会返回接口授权。</p>
          </div>
          <div class="status-row">
            <span id="appSecretStatus" class="chip">接口密钥未读取</span>
            <span id="accessTokenStatus" class="chip">接口授权未读取</span>
          </div>
          <dl id="configSummary"></dl>
          <span id="configMessage" class="message"></span>
        </div>
      </section>

      <section id="localExportSection" class="panel records menu-section" aria-labelledby="localExportTitle">
        <div class="panel-header">
          <h2 id="localExportTitle">文件导入</h2>
        </div>
        <div class="panel-body stack">
          <form id="localExportForm" class="form-grid">
            <label class="full">
              导出文件夹
              <input id="localSourceDir" name="source_dir" autocomplete="off" placeholder="例如 /Users/kven/Desktop/微信小店导出">
            </label>
            <label>
              文件类型
              <select id="localExportTypes" name="types">
                <option value="auto">自动识别导出文件</option>
              </select>
            </label>
            <div class="button-row full">
              <button type="button" id="checkLocalExportTask">检查文件</button>
              <button type="submit" id="startLocalExportTask" class="primary">导入并分析</button>
              <span class="hint">支持 Excel / CSV。</span>
            </div>
          </form>
        </div>
      </section>

      <section id="webOrderSection" class="panel records menu-section" aria-labelledby="orderTaskTitle">
        <div class="panel-header">
          <h2 id="orderTaskTitle">订单导出</h2>
        </div>
        <div class="panel-body stack">
          <form id="orderTaskForm" class="form-grid">
            <div class="button-row full">
              <button type="submit" id="startOrderTask" class="primary">启动订单导出</button>
            </div>
          </form>
        </div>
      </section>

      <section id="capabilitySection" class="panel records menu-section" aria-labelledby="capabilityTitle">
        <div class="panel-header">
          <h2 id="capabilityTitle">数据范围</h2>
        </div>
        <div class="panel-body stack">
          <div id="capabilityMessage" class="message">读取数据范围中...</div>
          <div id="capabilitiesPanel" class="capability-grid"></div>
        </div>
      </section>

      <section id="statusSection" class="panel records menu-section" aria-labelledby="statusTitle">
        <div class="panel-header">
          <h2 id="statusTitle">任务进度</h2>
          <button type="button" id="refreshTasks">刷新进度</button>
        </div>
        <div class="panel-body stack">
          <div class="status-row">
            <span id="taskStateChip" class="chip">暂无任务</span>
            <span id="taskReportChip" class="chip">暂无报告</span>
          </div>
          <dl id="taskSummary"></dl>
          <div id="taskSteps" class="task-steps"></div>
          <div id="taskResult" class="result-box">暂无任务结果。</div>
          <div id="reportPreview" class="result-box report-preview hidden"></div>
        </div>
      </section>

      <section id="recordsSection" class="panel records menu-section" aria-labelledby="recordsTitle">
        <div class="panel-header">
          <h2 id="recordsTitle">报告中心</h2>
          <button type="button" id="refreshRecords">刷新报告</button>
        </div>
        <div class="panel-body">
          <div id="recordsMessage" class="message">读取分析结果中...</div>
          <div id="reportsPanel" class="table-wrap"></div>
          <div id="reportDetail" class="result-box report-preview hidden"></div>
        </div>
      </section>
    </section>
  </main>

  <script>
    const state = { activeRecords: "tasks", activeTaskId: null, taskPoller: null, capabilities: [] };
    const $ = (id) => document.getElementById(id);
    const taskStepLabels = {
      collect: "获取数据",
      import_metadata: "登记任务",
      import_files: "整理数据",
      analyze: "计算指标",
      report: "生成报告",
    };
    const defaultLocalExportDir = "";

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
      if (!el) return;
      el.textContent = text;
      el.className = `message ${kind}`.trim();
    }

    function renderDefinitionList(id, rows) {
      const el = $(id);
      if (!el) return;
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
      if (!el) return;
      el.textContent = `${label} ${configured ? "已配置" : "未配置"}`;
      el.className = `chip ${configured ? "ok" : "bad"}`;
    }

    function selectMenuSection(targetId = "apiSyncSection") {
      const sections = document.querySelectorAll(".menu-section");
      const triggers = document.querySelectorAll(".side-nav-item");
      const target = $(targetId) ? targetId : "apiSyncSection";
      sections.forEach((section) => {
        section.classList.toggle("menu-hidden", section.id !== target);
      });
      triggers.forEach((trigger) => {
        const isActive = trigger.dataset.menuTarget === target;
        trigger.classList.toggle("active", isActive);
        trigger.setAttribute("aria-current", isActive ? "page" : "false");
      });
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
      if (!$("configSummary")) return;
      setMessage("configMessage", "读取授权状态中...");
      try {
        const data = await fetchJson("/api-config");
        $("collectShopId").value = $("collectShopId").value || data.values.shop_id || "";
        $("collectShopName").value = $("collectShopName").value || data.values.shop_name || "";
        renderDefinitionList("shopInfoSummary", [
          ["店铺名称", data.values.shop_name || "未配置"],
          ["店铺 ID", data.values.shop_id || "未配置"],
        ]);

        statusChip("appSecretStatus", "接口密钥", data.secrets.app_secret.configured);
        statusChip("accessTokenStatus", "接口授权", data.secrets.access_token.configured);
        const tokenStatus = data.secrets.access_token || {};
        renderDefinitionList("configSummary", [
          ["店铺", data.values.shop_name || data.values.shop_id || "未填写"],
          ["接口应用", data.values.app_id ? "已配置" : "未配置"],
          ["授权状态", tokenStatus.configured ? "已获取" : "未获取"],
          ["有效期至", tokenStatus.expires_at || "未获取"],
        ]);
        setMessage("configMessage", "授权状态已刷新。", "ok");
      } catch (error) {
        setMessage("configMessage", error.message, "error");
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
      if (!$("webLoginStatusList")) return;
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

    async function loadCapabilities() {
      setMessage("capabilityMessage", "读取数据范围中...");
      try {
        const capabilities = await fetchJson("/capabilities");
        state.capabilities = capabilities || [];
        renderCapabilities(state.capabilities);
        renderLocalExportTypeOptions(state.capabilities);
        setMessage("capabilityMessage", "数据范围已加载。", "ok");
      } catch (error) {
        setMessage("capabilityMessage", error.message, "error");
      }
    }

    function renderLocalExportTypeOptions(capabilities) {
      const select = $("localExportTypes");
      const previous = select.value || "auto";
      const importable = (capabilities || []).filter((item) => item.import_enabled);
      const options = [
        '<option value="auto">自动识别导出文件</option>',
        ...importable.map((item) => `<option value="${escapeHtml(String(item.export_type))}">${escapeHtml(item.label || item.export_type)}</option>`),
      ];
      select.innerHTML = options.join("");
      const values = new Set([...select.options].map((option) => option.value));
      select.value = values.has(previous) ? previous : "auto";
    }

    function renderCapabilities(capabilities) {
      const el = $("capabilitiesPanel");
      if (!capabilities || !capabilities.length) {
        el.innerHTML = '<div class="empty">暂无数据范围</div>';
        return;
      }
      el.innerHTML = capabilities.map((item) => {
        const webClass = item.web_enabled ? "ok" : "bad";
        const importClass = item.import_enabled ? "ok" : "bad";
        return `
          <div class="capability-item">
            <div class="capability-title">
              <strong>${escapeHtml(item.label || item.export_type)}</strong>
            </div>
            <div class="status-row">
              <span class="chip ${importClass}">${item.import_enabled ? "支持文件导入" : "暂不支持文件导入"}</span>
              <span class="chip ${webClass}">${item.web_enabled ? "支持订单导出" : "无需订单导出"}</span>
            </div>
          </div>
        `;
      }).join("");
    }

    function setTaskResult(text, kind = "") {
      const el = $("taskResult");
      el.textContent = text;
      el.className = `result-box ${kind}`.trim();
    }

    function setTaskResultHtml(html, kind = "") {
      const el = $("taskResult");
      el.innerHTML = html;
      el.className = `result-box ${kind}`.trim();
    }

    function renderLocalExportCheckResult(result) {
      const counts = result.batch_counts || {};
      const inspection = result.inspection || {};
      const tables = inspection.totals?.tables || Object.fromEntries(
        Object.entries(counts).filter(([key, value]) => key !== "warnings" && Number(value || 0) > 0)
      );
      const metricCards = renderMetricCards(tables);
      const readinessHtml = renderBusinessReadiness(inspection.business_readiness || []);
      const inspectionHtml = renderImportInspection(inspection.source_inspections || []);
      const warnings = (result.warnings || []).slice(0, 5);
      const warningHtml = warnings.length
        ? `<ol class="warning-list">${warnings.map((warning) => `<li>${escapeHtml(warning.message || "需要人工复核")}</li>`).join("")}</ol>`
        : '<p>没有需要复核的项目。</p>';
      setTaskResultHtml(`
        <div class="check-summary">
          <div><strong>文件检查完成</strong> · 需复核 ${escapeHtml(String(counts.warnings || 0))} 项</div>
          <div class="metric-grid">${metricCards}</div>
          ${readinessHtml}
          ${inspectionHtml}
          <div>${warningHtml}</div>
        </div>
      `, "ok");
    }

    function renderMetricCards(tables) {
      const nonZeroCounts = Object.entries(tables || {})
        .filter(([, value]) => Number(value || 0) > 0);
      return nonZeroCounts.length
        ? nonZeroCounts.map(([table, value]) => `<div class="metric-card"><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(tableLabel(table))}</span></div>`).join("")
        : '<div class="metric-card"><strong>0</strong><span>可识别数据行</span></div>';
    }

    function tableLabel(table) {
      const labels = {
        orders: "订单",
        order_items: "订单商品",
        products: "商品",
        product_skus: "商品规格",
        refunds: "售后退款",
        reviews: "评价",
        shop_daily: "店铺概览",
        product_daily: "商品表现",
        traffic_sources: "流量来源",
        fund_flows: "资金流水",
        ad_spend: "投放消耗",
        audience_insights: "人群画像",
      };
      return labels[table] || table || "-";
    }

    function fieldLabel(field) {
      const labels = {
        order_id: "订单编号",
        product_id: "商品编号",
        sku_id: "规格编号",
        pay_amount: "支付金额",
        refund_amount: "退款金额",
        created_at: "创建时间",
        paid_at: "支付时间",
      };
      return labels[field] || "必要字段";
    }

    function renderBusinessReadiness(items) {
      if (!items.length) {
        return "";
      }
      const rows = items.map((item) => {
        const status = item.status || "blocked";
        const chipClass = status === "supported" ? "ok" : "bad";
        const missing = (item.missing || []).length ? `待补充：${item.missing.map(tableLabel).join("、")}` : "";
        return `
          <div class="readiness-panel">
            <div class="readiness-panel-header">
              <strong>${escapeHtml(item.label || item.key || "业务能力")}</strong>
              <span class="chip ${chipClass}">${escapeHtml(statusLabel(status))}</span>
            </div>
            <div class="readiness-summary">${escapeHtml(item.summary || "")}</div>
            ${missing ? `<div class="inspection-meta">${escapeHtml(missing)}</div>` : ""}
          </div>
        `;
      }).join("");
      return `<div class="readiness-panels">${rows}</div>`;
    }

    function renderImportInspection(items) {
      if (!items.length) {
        return '<div class="inspection-list"><div class="inspection-item"><strong>暂无文件明细</strong><div class="inspection-meta">当前只返回了汇总结果。</div></div></div>';
      }
      const rows = items.map((item) => {
        const table = tableLabel(item.selected_table || item.guessed_table || "未识别");
        const status = item.status || "unknown";
        const rowCounts = item.row_counts || {};
        const derived = rowCounts.derived_tables || {};
        const derivedText = Object.entries(derived)
          .filter(([, value]) => Number(value || 0) > 0)
          .map(([name, value]) => `${tableLabel(name)} ${value}`)
          .join("，");
        const missingColumns = (item.missing_required_columns || []).join("、");
        const missingValues = (item.missing_required_values || [])
          .map((entry) => `${fieldLabel(entry.field)}缺少 ${entry.count} 行`)
          .join("，");
        const matchedFieldCount = (item.field_matches || []).length;
        return `
          <div class="inspection-item">
            <div class="inspection-item-header">
              <strong>${escapeHtml(table)} · ${escapeHtml(shortFileName(item.source_file || ""))}${item.source_sheet ? ` / ${escapeHtml(item.source_sheet)}` : ""}</strong>
              <span class="chip ${status === "importable" ? "ok" : status === "needs_review" ? "" : "bad"}">${escapeHtml(statusLabel(status))}</span>
            </div>
            <div class="inspection-meta">文件行数 ${escapeHtml(String(rowCounts.raw_rows ?? 0))} · 可导入 ${escapeHtml(String(rowCounts.imported_rows ?? 0))}${derivedText ? ` · 关联数据 ${escapeHtml(derivedText)}` : ""}</div>
            ${matchedFieldCount ? `<div class="inspection-meta">已识别字段 ${escapeHtml(String(matchedFieldCount))} 个</div>` : '<div class="inspection-meta">没有匹配到可用字段。</div>'}
            ${missingColumns ? `<div class="inspection-meta">缺少必要列：${escapeHtml(missingColumns)}</div>` : ""}
            ${missingValues ? `<div class="inspection-meta">缺少必要值：${escapeHtml(missingValues)}</div>` : ""}
          </div>
        `;
      }).join("");
      return `<div class="inspection-list">${rows}</div>`;
    }

    function statusLabel(status) {
      const labels = {
        supported: "可支持",
        limited: "受限",
        blocked: "资料不足",
        importable: "可导入",
        needs_review: "需复核",
        skipped: "已跳过",
        unrecognized: "未识别",
        empty: "空表",
        queued: "排队中",
        running: "处理中",
        pending: "等待中",
        completed: "已完成",
        failed: "失败",
        unknown: "未知",
      };
      return labels[status] || status || "-";
    }

    function shortFileName(path) {
      return String(path || "").split(/[\\/]/).filter(Boolean).pop() || path || "-";
    }

    function renderTask(task) {
      if (!task) {
        $("taskStateChip").textContent = "暂无任务";
        $("taskStateChip").className = "chip";
        $("taskReportChip").textContent = "暂无报告";
        $("taskReportChip").className = "chip";
        $("taskSummary").innerHTML = "";
        renderTaskSteps({});
        return;
      }
      const stateText = task.state || task.status || "unknown";
      const isOk = stateText === "completed";
      const isBad = stateText === "failed";
      $("taskStateChip").textContent = `任务 ${statusLabel(stateText)}`;
      $("taskStateChip").className = `chip ${isOk ? "ok" : isBad ? "bad" : ""}`.trim();

      const reportId = task.result?.report?.report_id || "";
      $("taskReportChip").textContent = reportId ? `报告 ${reportId}` : "暂无报告";
      $("taskReportChip").className = `chip ${reportId ? "ok" : isBad ? "bad" : ""}`.trim();
      renderDefinitionList("taskSummary", [
        ["任务", task.task_name || task.source_type || "-"],
        ["店铺", task.shop_name_snapshot || task.shop_id || "-"],
        ["周期", `${task.date_range?.from || "-"} 至 ${task.date_range?.to || "-"}`],
        ["状态", statusLabel(stateText)],
        ["报告", reportId || "-"],
      ]);
      renderTaskSteps(task.steps || {});

      if (task.error?.message) {
        setTaskResult(task.error.message, "error");
      } else if (task.result) {
        const importInspection = task.result.import_summary?.inspection;
        if (importInspection) {
          const counts = task.result.import_summary?.batch_counts || {};
          const tables = importInspection.totals?.tables || {};
          setTaskResultHtml(`
            <div class="check-summary">
              <div><strong>任务已完成</strong> · 需复核 ${escapeHtml(String(counts.warnings || 0))} 项</div>
              <div class="metric-grid">${renderMetricCards(tables)}</div>
              ${renderBusinessReadiness(importInspection.business_readiness || [])}
              ${renderImportInspection(importInspection.source_inspections || [])}
            </div>
          `, "ok");
          return;
        }
        const parts = [
          `处理文件数：${task.result.artifact_count ?? "-"}`,
          `报告编号：${reportId || "-"}`,
        ];
        setTaskResult(parts.join("\\n"), "ok");
      } else {
        setTaskResult(task.source_type === "local_export" ? "文件正在导入、分析并生成报告。" : "任务正在执行，请保持登录状态有效。");
      }
    }

    function renderTaskSteps(steps) {
      $("taskSteps").innerHTML = Object.entries(taskStepLabels).map(([key, label]) => {
        const step = steps[key] || {};
        const status = step.status || "pending";
        const detail = step.error || step.completed_at || step.started_at || "";
        return `<div class="task-step ${escapeHtml(status)}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(statusLabel(status))}${detail ? ` · ${escapeHtml(String(detail))}` : ""}</span></div>`;
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
        setMessage("taskMessage", "扫码登录窗口仍在运行，请先关闭官方后台登录窗口，再启动订单导出。", "error");
        return;
      }
      const payload = {
        shop_id: $("collectShopId").value,
        task_name: "订单导出分析",
        source_type: "web_export",
        params: {
          shop_name: $("collectShopName").value,
          from: $("collectFrom").value,
          to: $("collectTo").value,
          types: ["orders"],
          headless: false,
        },
      };
      setMessage("taskMessage", "正在启动订单导出任务...");
      try {
        const task = await fetchJson("/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        state.activeTaskId = task.id || task.task_id;
        renderTask(task);
        selectMenuSection("statusSection");
        setMessage("taskMessage", "任务已启动，正在更新进度。", "ok");
        startTaskPolling(state.activeTaskId);
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    async function startLocalExportTask(event) {
      event.preventDefault();
      await submitLocalExportTask("/tasks", "正在导入文件...", "文件导入任务已启动，正在更新进度。", true);
    }

    async function checkLocalExportTask() {
      await submitLocalExportTask("/tasks/local-export/check", "正在检查文件...", "文件检查完成。", false);
    }

    async function submitLocalExportTask(url, pendingMessage, successMessage, shouldPoll) {
      const payload = {
        shop_id: $("collectShopId").value,
        task_name: "文件导入分析",
        source_type: "local_export",
        params: {
          mode: "local_export",
          shop_name: $("collectShopName").value,
          from: $("collectFrom").value,
          to: $("collectTo").value,
          types: [$("localExportTypes").value],
          source_dir: $("localSourceDir").value,
        },
      };
      setMessage("taskMessage", pendingMessage);
      try {
        const task = await fetchJson(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (shouldPoll) {
          state.activeTaskId = task.id || task.task_id;
          renderTask(task);
          selectMenuSection("statusSection");
          startTaskPolling(state.activeTaskId);
        } else {
          renderLocalExportCheckResult(task);
          selectMenuSection("statusSection");
        }
        setMessage("taskMessage", successMessage, "ok");
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    function selectedApiSyncEndpoints() {
      return [...document.querySelectorAll('input[name="api_sync_endpoint"]:checked')]
        .map((item) => item.value)
        .filter(Boolean);
    }

    async function startApiSyncTask(event) {
      event.preventDefault();
      const payload = {
        shop_id: $("collectShopId").value,
        shop_name: $("collectShopName").value,
        date_from: $("collectFrom").value,
        date_to: $("collectTo").value,
        endpoints: selectedApiSyncEndpoints(),
        generate_report: $("apiSyncGenerateReport").checked,
      };
      setMessage("taskMessage", "正在同步数据...");
      try {
        const result = await fetchJson("/api-sync/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        renderApiSyncResult(result);
        selectMenuSection("statusSection");
        if (result.report?.report_id) {
          await loadReportPreview(result.report.report_id);
        }
        await loadRecords();
        setMessage("taskMessage", "数据同步完成。", "ok");
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    function renderApiSyncResult(result) {
      const rows = (result.items || []).reduce((sum, item) => sum + Number(item.row_count || 0), 0);
      const reportId = result.report?.report_id || "-";
      const moduleCount = new Set((result.items || []).map((item) => item.table_hint || item.export_type || item.endpoint)).size;
      setTaskResultHtml(`
        <div class="check-summary">
          <div><strong>数据同步完成</strong></div>
          <div class="metric-grid">
            <div class="metric-card"><strong>${escapeHtml(String(moduleCount))}</strong><span>同步模块</span></div>
            <div class="metric-card"><strong>${escapeHtml(String(rows))}</strong><span>写入记录</span></div>
            <div class="metric-card"><strong>${escapeHtml(reportId)}</strong><span>分析报告</span></div>
          </div>
        </div>
      `, result.status === "completed" ? "ok" : "error");
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
      setMessage("taskMessage", "读取任务进度中...");
      try {
        const tasks = await fetchJson("/tasks");
        const latest = (tasks || [])[0];
        if (!state.activeTaskId && latest) {
          renderTask(latest);
        } else if (!state.activeTaskId) {
          renderTask(null);
        }
        setMessage("taskMessage", "任务进度已刷新。", "ok");
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
      setMessage("recordsMessage", "读取分析结果中...");
      try {
        const reports = await fetchJson("/reports");
        renderReports(reports || []);
        setMessage("recordsMessage", "分析结果已加载。", "ok");
      } catch (error) {
        setMessage("recordsMessage", error.message, "error");
      }
    }

    function renderReports(reports) {
      const el = $("reportsPanel");
      if (!reports.length) {
        el.innerHTML = '<div class="empty">暂无分析报告</div>';
        $("reportDetail").classList.add("hidden");
        return;
      }
      const rows = reports.slice(0, 20).map((report) => `
        <tr>
          <td>${escapeHtml(report.title || report.id || "-")}</td>
          <td>${escapeHtml(report.shop_name_snapshot || report.shop_id || "-")}</td>
          <td>${escapeHtml(report.date || report.created_at || "-")}</td>
          <td><button type="button" class="tab report-open" data-report-id="${escapeHtml(report.id || "")}">查看</button></td>
        </tr>
      `).join("");
      el.innerHTML = `<table><thead><tr><th>报告</th><th>店铺</th><th>日期</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`;
      document.querySelectorAll(".report-open").forEach((button) => {
        button.addEventListener("click", () => loadReportDetail(button.dataset.reportId));
      });
      const first = reports[0];
      if (first?.id) {
        loadReportDetail(first.id);
      }
    }

    async function loadReportDetail(reportId) {
      if (!reportId) return;
      try {
        const report = await fetchJson(`/reports/${encodeURIComponent(reportId)}`);
        $("reportDetail").textContent = report.content || "报告没有可展示的正文。";
        $("reportDetail").classList.remove("hidden");
      } catch (error) {
        $("reportDetail").textContent = `报告读取失败：${error.message}`;
        $("reportDetail").classList.remove("hidden");
      }
    }

    async function refreshAll() {
      setDefaultDates();
      await Promise.all([loadConfig(), loadCapabilities(), loadTasks(), loadRecords()]);
    }

    $("refreshHealth")?.addEventListener("click", loadHealth);
    $("refreshConfig")?.addEventListener("click", loadConfig);
    $("refreshWebLogin")?.addEventListener("click", loadWebLoginStatus);
    $("refreshRecords")?.addEventListener("click", loadRecords);
    $("refreshAll")?.addEventListener("click", refreshAll);
    $("openWebLogin")?.addEventListener("click", openWebLogin);
    $("orderTaskForm")?.addEventListener("submit", startOrderTask);
    $("localExportForm")?.addEventListener("submit", startLocalExportTask);
    $("checkLocalExportTask")?.addEventListener("click", checkLocalExportTask);
    $("apiSyncForm")?.addEventListener("submit", startApiSyncTask);
    $("refreshTasks")?.addEventListener("click", loadTasks);
    document.querySelectorAll(".side-nav-item").forEach((trigger) => {
      trigger.addEventListener("click", () => selectMenuSection(trigger.dataset.menuTarget));
    });
    $("clearSensitive")?.addEventListener("click", () => {
      $("appSecret").value = "";
      $("accessToken").value = "";
      setMessage("configMessage", "密钥输入框已清空；保存时会保留旧值。");
    });

    selectMenuSection("apiSyncSection");
    refreshAll();
  </script>
</body>
</html>
"""
