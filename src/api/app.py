from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from api import collector_jobs, diagnostics
from api.repository import LocalRepository
from api.task_runner import TaskRunnerError
from api.task_runner import cancel_runtime_task
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


@app.on_event("startup")
def start_diagnostic_cleanup() -> None:
    diagnostics.start_diagnostic_cleanup_thread()

ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
DEFAULT_WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
WECHAT_STORE_BASIC_INFO_PATH = "/channels/ec/basics/info/get"
ACCESS_TOKEN_SOURCE = "stable_token"
ADMIN_AUTH_REALM = "WeChat Store Admin"
WEB_LOGIN_URL = "https://store.weixin.qq.com/"
WEB_LOGIN_CDP_URL = "http://127.0.0.1:9333"
WEB_LOGIN_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "auth" / "open_wechat_store_login.mjs"
WEB_LOGIN_CHECK_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "auth" / "check_wechat_store_login.mjs"
WEB_LOGIN_PROFILE_DIR = PROJECT_ROOT / "data" / "browser-profile"
WEB_LOGIN_SCREENSHOT_PATH = PROJECT_ROOT / "data" / "web-login-screenshot.png"
EXPORT_VERIFICATION_SCREENSHOT_PATH = PROJECT_ROOT / "data" / "export-verification-screenshot.png"
EXPORT_VERIFICATION_STATUS_PATH = PROJECT_ROOT / "data" / "export-verification-status.json"
WEB_LOGIN_QR_STALE_SECONDS = 120
PLAYWRIGHT_NODE_MODULE_DIR = PROJECT_ROOT / "node_modules" / "playwright"
TASK_ARTIFACT_DOWNLOAD_ROOT = PROJECT_ROOT / "data" / "raw"
TASK_ARTIFACT_DOWNLOAD_SUFFIXES = {".csv", ".xls", ".xlsx", ".zip"}
PAGE_EXPORT_TARGET_GROUPS: dict[str, tuple[str, ...]] = {
    "product_list": ("product_list",),
    "orders": ("orders",),
    "fund_flows": ("fund_flows",),
    "transactions": ("transactions",),
    "product_data": (
        "product_core_conversion",
        "product_traffic_funnel",
        "product_detail",
    ),
    "compass_buyer_profile": ("compass_buyer_profile",),
}
_web_login_process: Any = None
_web_login_started_at: Optional[datetime] = None
_web_login_auth_cache: dict[str, Any] | None = None
_web_login_auth_checked_at = 0.0
_web_login_auth_lock = threading.Lock()

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


@app.middleware("http")
async def require_admin_basic_auth(request: Request, call_next: Any) -> Any:
    auth_enabled = False
    authenticated = True
    if request.url.path != "/health":
        env_values = parse_env_file(ENV_LOCAL_PATH)
        auth_enabled = admin_auth_enabled(env_values)
        authenticated = admin_basic_auth_valid(request.headers.get("authorization", ""), env_values)
    request.state.admin_auth_enabled = auth_enabled
    request.state.admin_authenticated = authenticated
    if auth_enabled and not authenticated:
        return Response(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{ADMIN_AUTH_REALM}"'},
        )
    return await call_next(request)


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


class ApiConnectRequest(BaseModel):
    credential_id: Optional[str] = None
    app_secret: Optional[str] = None


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


class CollectorJobRequest(BaseModel):
    shop_id: Optional[str] = None
    shop_name: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    types: list[str] = Field(default_factory=lambda: ["orders"])
    headless: bool = False


class CollectorHeartbeatRequest(BaseModel):
    collector_id: str
    status: Optional[str] = None
    version: Optional[str] = None
    message: Optional[str] = None


class CollectorClaimRequest(BaseModel):
    collector_id: str


class CollectorUploadFile(BaseModel):
    path: str
    content_base64: str


class CollectorCompleteRequest(BaseModel):
    collector_id: str
    status: str = "completed"
    message: Optional[str] = None
    files: list[CollectorUploadFile] = Field(default_factory=list)


def response(status: str, message: str, data: Any) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "data": data,
    }


def repository() -> LocalRepository:
    return LocalRepository()


def admin_auth_enabled(env_values: Mapping[str, str]) -> bool:
    return bool(clean_config_value(env_values.get("WECHAT_STORE_ADMIN_PASSWORD", "")))


def admin_basic_auth_valid(header: str, env_values: Mapping[str, str]) -> bool:
    expected_password = clean_config_value(env_values.get("WECHAT_STORE_ADMIN_PASSWORD", ""))
    if not expected_password:
        return True
    expected_user = clean_config_value(env_values.get("WECHAT_STORE_ADMIN_USER", "")) or "admin"
    if not header.casefold().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, password = decoded.split(":", 1)
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(password, expected_password)


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def apply_page_export_target_selection(request: dict[str, Any]) -> dict[str, Any]:
    """Derive collector targets from the six UI groups when that selection is supplied."""
    params = request.get("params")
    if not isinstance(params, Mapping) or clean_config_value(params.get("mode")) != "export_only":
        return request
    if "target_groups" not in params:
        return request

    raw_groups = params.get("target_groups")
    if not isinstance(raw_groups, list):
        raise TaskRunnerError("页面导出勾选项必须是数组。")

    selected_groups = list(
        dict.fromkeys(clean_config_value(item) for item in raw_groups if clean_config_value(item))
    )
    if not selected_groups:
        raise TaskRunnerError("页面导出至少需要勾选一个数据模块。")

    unsupported_groups = [group for group in selected_groups if group not in PAGE_EXPORT_TARGET_GROUPS]
    if unsupported_groups:
        raise TaskRunnerError(f"页面导出包含未知勾选项：{'、'.join(unsupported_groups)}。")

    selected_targets = list(
        dict.fromkeys(
            target
            for group in selected_groups
            for target in PAGE_EXPORT_TARGET_GROUPS[group]
        )
    )
    normalized_request = dict(request)
    normalized_params = dict(params)
    normalized_params["target_groups"] = selected_groups
    normalized_params["targets"] = selected_targets
    normalized_request["params"] = normalized_params
    return normalized_request


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


def task_record(task_id: str) -> dict[str, Any] | None:
    return get_runtime_task(task_id) or repository().get_task(task_id)


def task_download_record(task_id: str) -> dict[str, Any] | None:
    runtime_task = get_runtime_task(task_id)
    if runtime_task is not None:
        return runtime_task
    return repository().get_api_task_run_download_record(task_id)


def _download_path_value(value: Any, *, relative_only: bool = False) -> Path:
    text = clean_config_value(value)
    if not text or len(text) > 4096 or "\\" in text or any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise PermissionError("artifact path text is invalid")
    path = Path(text)
    if relative_only and path.is_absolute():
        raise PermissionError("relative artifact path is absolute")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PermissionError("artifact path has unsafe components")
    return path


def _ensure_no_symlink(root: Path, target: Path) -> None:
    if root.is_symlink():
        raise PermissionError("download root is a symlink")
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path is outside expected root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("symlinked artifact paths are not allowed")


def _resolve_artifact_candidate(
    value: Any,
    *,
    source_dir: Path,
    source_dir_path: Path,
    relative_only: bool = False,
) -> Path:
    candidate = _download_path_value(value, relative_only=relative_only)
    if not candidate.is_absolute():
        candidate = source_dir / candidate
        lexical_root = source_dir
    else:
        lexical_root = next(
            (
                root
                for root in (source_dir_path, source_dir)
                if candidate == root or root in candidate.parents
            ),
            None,
        )
        if lexical_root is None:
            raise PermissionError("artifact is outside task source directory")
    _ensure_no_symlink(lexical_root, candidate)
    resolved_candidate = candidate.resolve(strict=True)
    if resolved_candidate != source_dir and source_dir not in resolved_candidate.parents:
        raise PermissionError("artifact is outside task source directory")
    return resolved_candidate


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_task_artifact_path(task: Mapping[str, Any], artifact_index: int) -> Path:
    task_state = clean_config_value(task.get("state") or task.get("status")).casefold()
    if task_state not in {"completed", "failed", "cancelled"}:
        raise FileNotFoundError("task is not terminal")
    result = task.get("result")
    result = result if isinstance(result, Mapping) else {}
    if clean_config_value(result.get("mode")) != "export_only":
        raise FileNotFoundError("task does not expose export-only artifacts")
    artifacts = result.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, list) else []
    if artifact_index < 0 or artifact_index >= len(artifacts):
        raise FileNotFoundError("artifact index is out of range")

    artifact = artifacts[artifact_index]
    if not isinstance(artifact, Mapping):
        raise FileNotFoundError("artifact metadata is invalid")
    artifact_status = clean_config_value(artifact.get("status")).casefold()
    if artifact_status != "completed":
        raise FileNotFoundError("artifact is not completed")
    artifact_kind = clean_config_value(artifact.get("source_kind") or artifact.get("source_type")).casefold()
    if artifact_kind not in {"export_file", "page_snapshot", "export_bundle"}:
        raise FileNotFoundError("artifact is not a downloadable export file")

    source_value = clean_config_value(result.get("source_dir") or task.get("source_dir"))
    if not source_value:
        metadata_value = clean_config_value(result.get("metadata_path") or task.get("metadata_path"))
        source_value = str(Path(metadata_value).parent) if metadata_value else ""
    if not source_value:
        raise FileNotFoundError("task source directory is missing")

    raw_root_path = TASK_ARTIFACT_DOWNLOAD_ROOT.absolute()
    raw_root = raw_root_path.resolve(strict=True)
    source_dir_path = _download_path_value(source_value)
    if not source_dir_path.is_absolute():
        source_dir_path = PROJECT_ROOT / source_dir_path
    try:
        source_dir_path.relative_to(raw_root_path)
    except ValueError as exc:
        raise PermissionError("task source directory is outside raw root") from exc
    _ensure_no_symlink(raw_root_path, source_dir_path)
    source_dir = source_dir_path.resolve(strict=True)
    if raw_root not in source_dir.parents:
        raise PermissionError("task source directory is outside raw root")
    if not source_dir.is_dir():
        raise FileNotFoundError("task source directory is not a directory")

    relative_value = clean_config_value(artifact.get("relative_path"))
    saved_value = clean_config_value(
        artifact.get("saved_path")
        or artifact.get("path")
        or artifact.get("file_path")
    )
    if not relative_value and not saved_value:
        raise FileNotFoundError("artifact path is missing")
    relative_path = (
        _resolve_artifact_candidate(
            relative_value,
            source_dir=source_dir,
            source_dir_path=source_dir_path,
            relative_only=True,
        )
        if relative_value
        else None
    )
    saved_path = (
        _resolve_artifact_candidate(
            saved_value,
            source_dir=source_dir,
            source_dir_path=source_dir_path,
        )
        if saved_value
        else None
    )
    if relative_path is not None and saved_path is not None and relative_path != saved_path:
        raise PermissionError("artifact paths do not match")
    artifact_path = relative_path or saved_path
    if artifact_path is None:
        raise FileNotFoundError("artifact path is missing")

    if artifact_path != raw_root and raw_root not in artifact_path.parents:
        raise PermissionError("artifact is outside raw root")
    if artifact_path != source_dir and source_dir not in artifact_path.parents:
        raise PermissionError("artifact is outside task source directory")
    if not artifact_path.is_file() or artifact_path.suffix.casefold() not in TASK_ARTIFACT_DOWNLOAD_SUFFIXES:
        raise FileNotFoundError("artifact is not a supported file")
    size_bytes = artifact_path.stat().st_size
    if size_bytes <= 0:
        raise FileNotFoundError("artifact is empty")
    expected_size = artifact.get("size_bytes")
    if expected_size not in (None, ""):
        try:
            normalized_size = int(expected_size)
        except (TypeError, ValueError) as exc:
            raise PermissionError("artifact size metadata is invalid") from exc
        if normalized_size != size_bytes:
            raise PermissionError("artifact size does not match metadata")
    expected_sha256 = clean_config_value(artifact.get("sha256")).casefold()
    if expected_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise PermissionError("artifact hash metadata is invalid")
        if not hmac.compare_digest(_sha256_path(artifact_path), expected_sha256):
            raise PermissionError("artifact hash does not match metadata")
    return artifact_path


def sync_collector_import_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    synced: list[dict[str, Any]] = []
    repo = repository()
    runtime_tasks = list_runtime_tasks()
    persisted_tasks: list[dict[str, Any]] | None = None
    for job in jobs:
        task_id = str(job.get("import_task_id") or "")
        if job.get("status") == "importing" and task_id:
            task = get_runtime_task(task_id) or repo.get_task(task_id)
            if task is None:
                if persisted_tasks is None:
                    persisted_tasks = repo.list_tasks()
                task = _task_for_collector_source_dir(job, [*runtime_tasks, *persisted_tasks])
            task_state = str((task or {}).get("state") or (task or {}).get("status") or "")
            if task and task_state in {"completed", "failed"}:
                try:
                    job = collector_jobs.sync_import_task(str(job["job_id"]), task)
                except ValueError:
                    pass
        synced.append(job)
    return synced


def _task_for_collector_source_dir(job: Mapping[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    source_dir = str(job.get("source_dir") or "")
    if not source_dir:
        return None
    candidates = [
        task
        for task in tasks
        if str(task.get("source_dir") or "") == source_dir
        and task.get("source_type") == "local_export"
        and str(task.get("state") or task.get("status") or "") in {"completed", "failed"}
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.get("completed_at") or item.get("updated_at") or ""), reverse=True)[0]


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    return HTMLResponse(
        render_admin_ui(),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(render_web_login_ui())


@app.get("/health")
def health() -> dict[str, Any]:
    return response("ok", "API is ready.", repository().health())


@app.get("/diagnostics/status")
def diagnostics_status() -> dict[str, Any]:
    return response("ok", "Diagnostic retention status loaded.", diagnostics.diagnostic_cleanup_status())


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


@app.post("/api-config/connect")
def connect_api_config(payload: ApiConnectRequest) -> dict[str, Any]:
    result = connect_api_credentials(payload)
    return response(result["status"], result["message"], result["data"])


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


@app.post("/collector/jobs")
def create_collector_job(payload: CollectorJobRequest) -> dict[str, Any]:
    try:
        job = collector_jobs.create_job(model_to_dict(payload))
    except ValueError as exc:
        return response("error", str(exc), {"request": model_to_dict(payload)})
    return response("ok", "本地采集任务已创建，等待客户电脑采集助手领取。", job)


@app.get("/collector/jobs")
def list_collector_jobs(limit: int = 50) -> dict[str, Any]:
    return response("ok", "Collector jobs loaded.", sync_collector_import_jobs(collector_jobs.list_jobs(limit=limit)))


@app.post("/collector/heartbeat")
def collector_heartbeat(payload: CollectorHeartbeatRequest) -> dict[str, Any]:
    try:
        data = collector_jobs.heartbeat(payload.collector_id, model_to_dict(payload))
    except ValueError as exc:
        return response("error", str(exc), model_to_dict(payload))
    return response("ok", "Collector heartbeat saved.", data)


@app.post("/collector/jobs/claim")
def claim_collector_job(payload: CollectorClaimRequest) -> dict[str, Any]:
    try:
        job = collector_jobs.claim_job(payload.collector_id)
    except ValueError as exc:
        return response("error", str(exc), model_to_dict(payload))
    return response("ok", "Collector job claimed." if job else "No pending collector job.", job)


@app.post("/collector/jobs/{job_id}/complete")
def complete_collector_job(job_id: str, payload: CollectorCompleteRequest) -> dict[str, Any]:
    try:
        result = collector_jobs.complete_job(job_id, model_to_dict(payload))
    except ValueError as exc:
        return response("error", str(exc), {"job_id": job_id})
    source_dir = result.get("source_dir")
    if not source_dir:
        return response("ok", "本地采集任务已结束。", result)
    try:
        task = start_web_export_task(
            {
                "shop_id": result["job"]["shop_id"],
                "task_name": "本地采集助手订单导入",
                "source_type": "local_export",
                "params": {
                    "mode": "local_export",
                    "shop_name": result["job"].get("shop_name"),
                    "from": result["job"]["date_from"],
                    "to": result["job"]["date_to"],
                    "types": result["job"].get("types") or ["orders"],
                    "source_dir": source_dir,
                },
            }
        )
    except TaskRunnerError as exc:
        result["job"] = collector_jobs.mark_import_error(job_id, str(exc))
        return response("ok", "本地采集结果已上传，但导入分析任务暂未启动。", result)
    result["import_task"] = task
    result["job"] = collector_jobs.attach_import_task(job_id, task)
    return response("ok", "本地采集结果已上传，导入分析任务已启动。", result)


@app.post("/web-login/open")
def open_web_login() -> dict[str, Any]:
    result = start_web_login_browser()
    if result["status"] == "error":
        return response("error", result["message"], result["data"])
    return response("ok", result["message"], result["data"])


@app.post("/web-login/close")
def close_web_login() -> dict[str, Any]:
    return response("ok", "扫码登录页已收起。", stop_web_login_browser())


@app.get("/web-login/status")
def web_login_status(fresh: bool = False) -> dict[str, Any]:
    return response(
        "ok",
        "Web login status loaded.",
        web_login_status_data(force_auth_check=fresh),
    )


@app.get("/web-login/export-verification-status")
def web_login_export_verification_status() -> dict[str, Any]:
    return response("ok", "Export verification status loaded.", export_verification_status_data())


@app.get("/web-login/screenshot")
def web_login_screenshot() -> Any:
    if not WEB_LOGIN_SCREENSHOT_PATH.is_file():
        return Response("Login screenshot is not ready.", status_code=404)
    return FileResponse(WEB_LOGIN_SCREENSHOT_PATH, media_type="image/png")


@app.get("/web-login/export-verification-screenshot")
def web_login_export_verification_screenshot(challenge_id: str | None = None) -> Any:
    verification = export_verification_status_data()
    if not verification["required"] or not verification["screenshot_ready"]:
        return Response("Export verification screenshot is not ready.", status_code=404)
    if challenge_id and challenge_id != verification["challenge_id"]:
        return Response("Export verification challenge has changed.", status_code=404)
    return FileResponse(
        EXPORT_VERIFICATION_SCREENSHOT_PATH,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/shops")
def shops() -> dict[str, Any]:
    return response("ok", "Shops loaded.", repository().list_shops())


@app.get("/tasks")
def tasks() -> dict[str, Any]:
    return response("ok", "Tasks loaded.", merge_runtime_and_persisted_tasks())


@app.get("/tasks/{task_id}")
def task_detail(task_id: str) -> dict[str, Any]:
    task = task_record(task_id)
    if task is None:
        return response("not_found", "Task not found.", None)
    return response("ok", "Task loaded.", task)


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> Any:
    try:
        task = cancel_runtime_task(task_id)
    except TaskRunnerError as exc:
        message = str(exc)
        status_code = 404 if "任务不存在" in message else 409
        return JSONResponse(
            response("error", message, {"task_id": task_id}),
            status_code=status_code,
        )
    return response("ok", "任务已取消；取消前生成的文件会继续保留。", task)


@app.get("/tasks/{task_id}/artifacts/{artifact_index}/download")
def download_task_artifact(request: Request, task_id: str, artifact_index: int) -> Any:
    if not (
        getattr(request.state, "admin_auth_enabled", False)
        and getattr(request.state, "admin_authenticated", False)
    ):
        return Response(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{ADMIN_AUTH_REALM}"'},
        )
    if (
        not task_id
        or len(task_id) > 255
        or "/" in task_id
        or "\\" in task_id
        or any(ord(character) < 32 or ord(character) == 127 for character in task_id)
    ):
        return Response("Task not found.", status_code=404)
    task = task_download_record(task_id)
    if task is None:
        return Response("Task not found.", status_code=404)
    try:
        artifact_path = resolve_task_artifact_path(task, artifact_index)
    except (FileNotFoundError, OSError, PermissionError, ValueError):
        return Response("Export file is unavailable.", status_code=404)
    return FileResponse(
        artifact_path,
        filename=artifact_path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/tasks")
def create_task(payload: TaskRequest) -> dict[str, Any]:
    try:
        request = apply_page_export_target_selection(model_to_dict(payload))
    except TaskRunnerError as exc:
        return response("error", str(exc), {"request": model_to_dict(payload)})
    if task_requires_web_login(request):
        login_status = web_login_status_data(force_auth_check=True)
        if not login_status["cdp_available"]:
            return response(
                "error",
                "扫码登录浏览器未运行，请先打开/刷新扫码二维码并完成扫码，再启动页面表格导出。",
                login_status,
            )
        if not login_status["authenticated"]:
            return response(
                "error",
                login_status["login_state_message"],
                login_status,
            )
    try:
        task = start_web_export_task(request)
    except TaskRunnerError as exc:
        return response("error", str(exc), {"request": request})
    return response("ok", "真实采集任务已启动，请在页面等待进度完成。", task)


@app.post("/tasks/check")
def check_task_request(payload: TaskRequest) -> dict[str, Any]:
    try:
        request = apply_page_export_target_selection(model_to_dict(payload))
        spec = normalize_web_export_payload(request)
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


def connect_api_credentials(payload: ApiConnectRequest) -> dict[str, Any]:
    existing_values = parse_env_file(ENV_LOCAL_PATH)
    credential_id = clean_config_value(payload.credential_id)
    supplied_secret = clean_config_value(payload.app_secret)
    existing_app_id = clean_config_value(existing_values.get(API_CONFIG_FIELD_TO_KEY["app_id"], ""))
    existing_shop_id = clean_config_value(existing_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""))
    existing_secret = clean_config_value(existing_values.get(API_CONFIG_FIELD_TO_KEY["app_secret"], ""))
    previous_credential_id = existing_app_id or existing_shop_id

    if not credential_id:
        return {
            "status": "error",
            "message": "请填写微信小店 AppID。",
            "data": {"missing": {"credential_id": True, "app_secret": not bool(existing_secret)}},
        }

    credential_changed = bool(previous_credential_id and previous_credential_id != credential_id)
    if not supplied_secret and (credential_changed or not existing_secret):
        return {
            "status": "error",
            "message": "请填写当前 AppID 对应的 AppSecret。",
            "data": {"missing": {"credential_id": False, "app_secret": True}},
        }

    updates = {
        API_CONFIG_FIELD_TO_KEY["app_id"]: credential_id,
        API_CONFIG_FIELD_TO_KEY["app_secret"]: supplied_secret or existing_secret,
    }
    if credential_changed or not existing_shop_id or not existing_app_id:
        updates[API_CONFIG_FIELD_TO_KEY["shop_id"]] = credential_id
    if credential_changed:
        updates[API_CONFIG_FIELD_TO_KEY["shop_name"]] = ""
    if credential_changed or supplied_secret:
        updates.update({key: "" for key in ACCESS_TOKEN_ENV_KEYS})

    write_env_updates(ENV_LOCAL_PATH, updates)

    token_result = fetch_and_store_access_token()
    result_data = {
        "credential_id": credential_id,
        "credentials_saved": True,
        "token": token_result["data"],
        "config": read_api_config(),
    }
    return {
        "status": token_result["status"],
        "message": token_result["message"],
        "data": result_data,
    }


def fetch_and_store_access_token() -> dict[str, Any]:
    env_values = parse_env_file(ENV_LOCAL_PATH)
    configured_app_id = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["app_id"], ""))
    fallback_shop_id = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""))
    app_id = configured_app_id or fallback_shop_id
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

    if not configured_app_id:
        write_env_updates(
            ENV_LOCAL_PATH,
            {API_CONFIG_FIELD_TO_KEY["app_id"]: app_id},
        )

    try:
        token_payload = request_stable_access_token(base_url=base_url, app_id=app_id, app_secret=app_secret)
    except Exception:
        return {
            "status": "error",
            "message": "接口授权获取失败，请检查网络后重试。",
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
    shop_info = fetch_and_store_shop_info(
        base_url=base_url,
        access_token=access_token,
        credential_id=app_id,
    )
    message = (
        "接口授权和店铺信息已自动获取。"
        if shop_info.get("loaded")
        else "接口授权已获取；店铺信息暂未自动获取，可稍后重试。"
    )

    return {
        "status": "ok",
        "message": message,
        "data": {
            "configured": True,
            "expires_in": token_updates["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_IN"],
            "fetched_at": token_updates["WECHAT_STORE_ACCESS_TOKEN_FETCHED_AT"],
            "expires_at": token_updates["WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT"],
            "source": token_updates["WECHAT_STORE_ACCESS_TOKEN_SOURCE"],
            "errcode": errcode,
            "errmsg": errmsg,
            "shop_info": shop_info,
        },
    }


def fetch_and_store_shop_info(
    *,
    base_url: str,
    access_token: str,
    credential_id: str,
) -> dict[str, Any]:
    try:
        payload = request_store_basic_info(base_url=base_url, access_token=access_token)
    except Exception:
        return {
            "loaded": False,
            "message": "店铺信息接口暂时不可用，请稍后重试。",
        }

    errcode = payload.get("errcode")
    errmsg = clean_config_value(payload.get("errmsg"))
    if errcode not in (None, 0, "0"):
        return {
            "loaded": False,
            "errcode": errcode,
            "message": errmsg or "微信未返回店铺基本信息。",
        }

    info = payload.get("info")
    info = info if isinstance(info, Mapping) else {}
    shop_name = clean_config_value(info.get("nickname"))
    official_shop_id = clean_config_value(info.get("username"))
    if not shop_name and not official_shop_id:
        return {
            "loaded": False,
            "message": "接口授权有效，但微信未返回店铺名称或店铺原始 ID。",
        }

    current_values = parse_env_file(ENV_LOCAL_PATH)
    current_shop_id = clean_config_value(current_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""))
    updates: dict[str, str] = {}
    if shop_name:
        updates[API_CONFIG_FIELD_TO_KEY["shop_name"]] = shop_name
    if official_shop_id and (not current_shop_id or current_shop_id == credential_id):
        updates[API_CONFIG_FIELD_TO_KEY["shop_id"]] = official_shop_id
    elif not current_shop_id:
        updates[API_CONFIG_FIELD_TO_KEY["shop_id"]] = credential_id
    if updates:
        write_env_updates(ENV_LOCAL_PATH, updates)

    saved_values = parse_env_file(ENV_LOCAL_PATH)
    return {
        "loaded": True,
        "shop_id": clean_config_value(saved_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], "")),
        "shop_name": clean_config_value(saved_values.get(API_CONFIG_FIELD_TO_KEY["shop_name"], "")),
        "official_shop_id": official_shop_id or None,
        "status": clean_config_value(info.get("status")) or None,
        "subject_type": clean_config_value(info.get("subject_type")) or None,
        "message": "店铺基本信息已自动获取。",
    }


def run_configured_api_sync(payload: Mapping[str, Any]) -> dict[str, Any]:
    env_values = parse_env_file(ENV_LOCAL_PATH)
    access_token = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], ""))
    if not access_token or access_token_needs_refresh(env_values):
        token_result = fetch_and_store_access_token()
        if token_result["status"] == "error":
            return token_result
        env_values = parse_env_file(ENV_LOCAL_PATH)
        access_token = clean_config_value(env_values.get(API_CONFIG_FIELD_TO_KEY["access_token"], ""))

    api_base_url = clean_api_base_url(env_values.get(API_CONFIG_FIELD_TO_KEY["api_base_url"], "") or DEFAULT_API_BASE_URL)
    shop_name = clean_config_value(payload.get("shop_name") or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_name"], ""))
    if not shop_name and access_token:
        credential_id = clean_config_value(
            env_values.get(API_CONFIG_FIELD_TO_KEY["app_id"], "")
            or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], "")
        )
        fetch_and_store_shop_info(
            base_url=api_base_url,
            access_token=access_token,
            credential_id=credential_id,
        )
        env_values = parse_env_file(ENV_LOCAL_PATH)
        shop_name = clean_config_value(payload.get("shop_name") or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_name"], ""))

    shop_id = clean_config_value(payload.get("shop_id") or env_values.get(API_CONFIG_FIELD_TO_KEY["shop_id"], ""))
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


def access_token_needs_refresh(env_values: Mapping[str, str]) -> bool:
    expires_at = clean_config_value(env_values.get("WECHAT_STORE_ACCESS_TOKEN_EXPIRES_AT", ""))
    if not expires_at:
        return False
    try:
        expires_at_dt = datetime.fromisoformat(expires_at.removesuffix("Z"))
    except ValueError:
        return True
    return expires_at_dt <= datetime.utcnow() + timedelta(minutes=5)


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
    global _web_login_process, _web_login_started_at

    existing_process = current_web_login_process()
    if existing_process is None and web_login_cdp_available():
        return {
            "status": "ok",
            "message": "扫码登录页已在运行。",
            "data": {
                **web_login_status_data(),
                "already_running": True,
            },
        }

    if existing_process is not None:
        _stop_process(existing_process)

    dependency_error = web_login_dependency_error()
    if dependency_error is not None:
        return dependency_error

    WEB_LOGIN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    WEB_LOGIN_SCREENSHOT_PATH.unlink(missing_ok=True)
    clear_web_login_auth_cache()
    try:
        _web_login_process = subprocess.Popen(
            ["node", str(WEB_LOGIN_SCRIPT_PATH)],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _web_login_started_at = datetime.utcnow()
    except OSError as exc:
        _web_login_process = None
        _web_login_started_at = None
        return {
            "status": "error",
            "message": f"扫码登录窗口启动失败：{exc}",
            "data": web_login_status_data(),
        }

    return {
        "status": "ok",
        "message": "扫码登录页已打开，请在页面截图中扫码登录。",
        "data": {
            **web_login_status_data(),
            "already_running": False,
        },
    }


def stop_web_login_browser() -> dict[str, Any]:
    process = current_web_login_process()
    was_running = process is not None
    if process is not None:
        _stop_process(process)
    else:
        _stop_untracked_web_login_processes()
    return {**web_login_status_data(), "was_running": was_running}


def _stop_process(process: Any) -> None:
    global _web_login_process, _web_login_started_at
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    _web_login_process = None
    _web_login_started_at = None
    clear_web_login_auth_cache()


def _stop_untracked_web_login_processes() -> None:
    try:
        subprocess.run(
            ["pkill", "-f", str(WEB_LOGIN_SCRIPT_PATH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass
    clear_web_login_auth_cache()


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
    global _web_login_process, _web_login_started_at
    if _web_login_process is not None and _web_login_process.poll() is not None:
        _web_login_process = None
        _web_login_started_at = None
    return _web_login_process


def export_verification_status_data() -> dict[str, Any]:
    try:
        parsed = json.loads(EXPORT_VERIFICATION_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, Mapping):
        parsed = {}

    challenge_id = clean_config_value(parsed.get("challenge_id"))
    target_key = clean_config_value(parsed.get("target_key"))
    target_label = clean_config_value(parsed.get("target_label"))
    control_label = clean_config_value(parsed.get("control_label"))
    started_at = clean_config_value(parsed.get("started_at"))
    timeout_ms = parse_int(parsed.get("timeout_ms"), 0)
    elapsed_seconds: int | None = None
    remaining_seconds: int | None = None
    if started_at:
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00")).replace(tzinfo=None)
            elapsed_seconds = max(0, int((datetime.utcnow() - started).total_seconds()))
        except ValueError:
            elapsed_seconds = None
    if timeout_ms > 0 and elapsed_seconds is not None:
        remaining_seconds = max(0, int(timeout_ms / 1000) - elapsed_seconds)

    required = bool(parsed.get("required")) and bool(challenge_id)
    if remaining_seconds == 0:
        required = False
    screenshot_ready = required and EXPORT_VERIFICATION_SCREENSHOT_PATH.is_file()
    return {
        "required": required,
        "challenge_id": challenge_id,
        "target_key": target_key,
        "target_label": target_label,
        "control_label": control_label,
        "started_at": started_at,
        "timeout_ms": timeout_ms,
        "elapsed_seconds": elapsed_seconds,
        "remaining_seconds": remaining_seconds,
        "screenshot_ready": screenshot_ready,
        "screenshot_url": "/web-login/export-verification-screenshot",
        "message": (
            f"微信要求扫码确认“{target_label}”导出，请使用当前二维码完成验证。"
            if required and target_label
            else "微信要求扫码确认本次导出，请使用当前二维码完成验证。"
            if required
            else "当前没有待处理的导出扫码验证。"
        ),
    }


def web_login_status_data(*, force_auth_check: bool = False) -> dict[str, Any]:
    process = current_web_login_process()
    process_running = process is not None and process.poll() is None
    elapsed_seconds = int((datetime.utcnow() - _web_login_started_at).total_seconds()) if process_running and _web_login_started_at else None
    screenshot_age_seconds = web_login_screenshot_age_seconds()
    cdp_available = web_login_cdp_available()
    auth_state = web_login_auth_state(cdp_available=cdp_available, force=force_auth_check)
    verification = export_verification_status_data()
    return {
        "profile_dir": str(WEB_LOGIN_PROFILE_DIR),
        "profile_dir_exists": WEB_LOGIN_PROFILE_DIR.exists(),
        "process_running": process_running,
        "cdp_available": cdp_available,
        "login_browser_ready": process_running or cdp_available,
        "authenticated": bool(auth_state.get("authenticated")),
        "logged_in": bool(auth_state.get("authenticated")),
        "scan_required": auth_state.get("state") == "login_required",
        "export_ready": bool(auth_state.get("authenticated")),
        "login_state": auth_state.get("state") or "unknown",
        "login_state_message": auth_state.get("message") or "暂时无法确认微信小店登录状态，请刷新后重试。",
        "login_page_url": auth_state.get("url"),
        "login_page_title": auth_state.get("title"),
        "pid": process.pid if process_running else None,
        "login_url": WEB_LOGIN_URL,
        "screenshot_ready": WEB_LOGIN_SCREENSHOT_PATH.is_file(),
        "screenshot_url": "/web-login/screenshot",
        "screenshot_age_seconds": screenshot_age_seconds,
        "qr_maybe_expired": not bool(auth_state.get("authenticated")) and bool(
            (elapsed_seconds is not None and elapsed_seconds >= WEB_LOGIN_QR_STALE_SECONDS)
            or (screenshot_age_seconds is not None and screenshot_age_seconds >= WEB_LOGIN_QR_STALE_SECONDS)
            or (WEB_LOGIN_SCREENSHOT_PATH.is_file() and not (process_running or cdp_available))
        ),
        "qr_stale_seconds": WEB_LOGIN_QR_STALE_SECONDS,
        "login_elapsed_seconds": elapsed_seconds,
        "login_started_at": format_utc_datetime(_web_login_started_at) if process_running and _web_login_started_at else "",
        "export_verification": verification,
    }


def web_login_auth_state(
    *,
    cdp_available: bool | None = None,
    force: bool = False,
    max_age_seconds: float = 5.0,
) -> dict[str, Any]:
    global _web_login_auth_cache, _web_login_auth_checked_at

    is_available = web_login_cdp_available() if cdp_available is None else cdp_available
    if not is_available:
        return {
            "state": "cdp_unavailable",
            "authenticated": False,
            "url": None,
            "title": None,
            "message": "扫码登录浏览器未运行，请先打开扫码二维码。",
        }

    now = time.monotonic()
    if not force and _web_login_auth_cache is not None and now - _web_login_auth_checked_at <= max_age_seconds:
        return dict(_web_login_auth_cache)

    with _web_login_auth_lock:
        now = time.monotonic()
        if not force and _web_login_auth_cache is not None and now - _web_login_auth_checked_at <= max_age_seconds:
            return dict(_web_login_auth_cache)

        result = _run_web_login_auth_probe()
        _web_login_auth_cache = result
        _web_login_auth_checked_at = time.monotonic()
        return dict(result)


def _run_web_login_auth_probe() -> dict[str, Any]:
    if not WEB_LOGIN_CHECK_SCRIPT_PATH.is_file():
        return {
            "state": "unknown",
            "authenticated": False,
            "url": None,
            "title": None,
            "message": "缺少微信小店登录状态检查脚本，暂时不能启动导出。",
        }

    try:
        completed = subprocess.run(
            [
                "node",
                str(WEB_LOGIN_CHECK_SCRIPT_PATH),
                "--cdp-url",
                WEB_LOGIN_CDP_URL,
            ],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "state": "unknown",
            "authenticated": False,
            "url": None,
            "title": None,
            "message": "暂时无法确认微信小店登录状态，请刷新登录状态后重试。",
        }

    try:
        parsed = json.loads(completed.stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        parsed = {}
    if not isinstance(parsed, Mapping):
        parsed = {}

    state = str(parsed.get("state") or "unknown")
    if state not in {"logged_in", "login_required", "unknown", "cdp_unavailable"}:
        state = "unknown"
    default_messages = {
        "logged_in": "微信小店后台已登录，可以启动页面表格导出。",
        "login_required": "微信小店后台仍停留在二维码页，请先用微信扫码并确认进入小店后台。",
        "cdp_unavailable": "扫码登录浏览器未运行，请先打开扫码二维码。",
        "unknown": "暂时无法确认微信小店登录状态，请刷新登录状态后重试。",
    }
    return {
        "state": state,
        "authenticated": state == "logged_in" and bool(parsed.get("authenticated")),
        "url": parsed.get("url"),
        "title": parsed.get("title"),
        "message": default_messages[state],
    }


def clear_web_login_auth_cache() -> None:
    global _web_login_auth_cache, _web_login_auth_checked_at
    with _web_login_auth_lock:
        _web_login_auth_cache = None
        _web_login_auth_checked_at = 0.0


def task_requires_web_login(payload: Mapping[str, Any]) -> bool:
    params = payload.get("params")
    params = params if isinstance(params, Mapping) else {}
    source_type = str(payload.get("source_type") or "web_export")
    mode = str(params.get("mode") or "")
    return source_type != "local_export" and mode != "local_export"


def web_login_cdp_available() -> bool:
    try:
        with urllib.request.urlopen(f"{WEB_LOGIN_CDP_URL}/json/version", timeout=1) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def web_login_screenshot_age_seconds() -> int | None:
    try:
        modified_at = datetime.utcfromtimestamp(WEB_LOGIN_SCREENSHOT_PATH.stat().st_mtime)
    except OSError:
        return None
    return max(0, int((datetime.utcnow() - modified_at).total_seconds()))


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


def request_store_basic_info(base_url: str, access_token: str, timeout: int = 15) -> dict[str, Any]:
    query = urllib.parse.urlencode({"access_token": access_token})
    url = f"{clean_api_base_url(base_url)}{WECHAT_STORE_BASIC_INFO_PATH}?{query}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response_obj:
            response_body = response_obj.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")

    if not response_body.strip():
        return {}
    parsed = json.loads(response_body)
    if not isinstance(parsed, dict):
        raise ValueError("微信店铺信息接口返回了非对象 JSON。")
    return parsed


def write_access_token_env(path: Path, token_updates: dict[str, str]) -> None:
    write_env_updates(path, token_updates, ACCESS_TOKEN_ENV_KEYS)


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
    write_env_updates(path, updates, API_CONFIG_KEYS)


def write_env_updates(
    path: Path,
    updates: Mapping[str, str],
    ordered_keys: tuple[str, ...] = (),
) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output_lines: list[str] = []
    written_keys: set[str] = set()

    for line in lines:
        key, _ = split_env_line(line)
        if key in updates:
            if key not in written_keys:
                output_lines.append(f"{key}={format_env_value(str(updates[key]))}")
            written_keys.add(key)
            continue
        output_lines.append(line)

    for key, value in updates.items():
        if key not in written_keys and key not in ordered_keys:
            output_lines.append(f"{key}={format_env_value(str(value))}")

    for key in ordered_keys:
        if key not in written_keys:
            output_lines.append(f"{key}={format_env_value(str(updates.get(key, '')))}")

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o600
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write("\n".join(output_lines).rstrip() + "\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


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
      --bg: #f6f8fa;
      --panel: #ffffff;
      --panel-soft: #f7f9fa;
      --text: #1f2329;
      --muted: #7b8493;
      --line: #e5e8eb;
      --accent: #07c160;
      --accent-soft: #e8f8ef;
      --accent-text: #04783a;
      --accent-strong: #04833e;
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
    .panel-header > .button-row { margin-top: 0; }
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
    button:disabled,
    button:disabled:hover {
      opacity: 0.68;
      cursor: wait;
    }
    .message {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
    }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    .qr-expiry-alert {
      border: 1px solid var(--warn-line);
      border-radius: 7px;
      background: var(--warn-bg);
      color: #7a4f01;
      padding: 9px 10px;
      font-size: 13px;
      font-weight: 700;
    }
    .qr-expiry-alert[hidden] { display: none; }
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
    .screenshot-box {
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 10px;
    }
    .screenshot-box img {
      width: 100%;
      max-height: 520px;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
    }
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
        <p>在服务器浏览器中打开微信小店官方后台，请使用页面截图扫码登录。</p>
      </div>
      <a class="button-link" href="/">返回管理台</a>
    </header>

    <section class="panel" aria-labelledby="loginTitle">
      <div class="panel-header">
        <h2 id="loginTitle">后台登录页</h2>
        <button type="button" id="refreshStatus">刷新登录状态</button>
      </div>
      <div class="panel-body">
        <div class="notice">
          <strong>这个页面只负责打开微信小店官方后台</strong>
          <p>系统不会向你索要微信账号、密码，也不会保存微信密码。扫码完成并确认进入小店后台后，系统会保留服务器浏览器登录态；再回到管理台启动页面表格导出。</p>
        </div>

        <ol class="steps" aria-label="扫码登录步骤">
          <li><span class="step-number">1</span><span>点击“打开/刷新扫码二维码”。</span></li>
          <li><span class="step-number">2</span><span>等待下方截图出现二维码后用微信扫码。</span></li>
          <li><span class="step-number">3</span><span>二维码变灰或提示失效时，再点一次刷新。</span></li>
          <li><span class="step-number">4</span><span>等待登录状态显示“已进入小店后台”，再回到管理台启动页面表格导出，不要手动关闭扫码页。</span></li>
        </ol>

        <div class="status-row">
          <span id="processStatus" class="chip">扫码窗口未读取</span>
          <span id="authStatus" class="chip">后台登录未读取</span>
          <span id="profileStatus" class="chip">浏览器配置未读取</span>
        </div>

        <dl id="statusList">
          <dt>窗口进程</dt><dd>未读取</dd>
          <dt>登录状态</dt><dd>未读取</dd>
          <dt>官方后台</dt><dd>未读取</dd>
          <dt>浏览器配置</dt><dd>未读取</dd>
        </dl>

        <div class="screenshot-box">
          <strong>扫码截图</strong>
          <p>服务器不会把浏览器窗口弹到你电脑上；二维码会显示在这里。二维码变灰表示已过期。</p>
          <div id="qrExpiryAlert" class="qr-expiry-alert" hidden>二维码可能已过期，请点击“刷新扫码二维码”生成新的二维码。</div>
          <img id="loginScreenshot" data-screenshot-url="/web-login/screenshot" alt="微信小店扫码登录截图" />
        </div>

        <div class="button-row">
          <button type="button" id="openLogin" class="primary">打开/刷新扫码二维码</button>
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
        const requestError = new Error(payload.message || `${url} 请求失败`);
        requestError.data = payload.data;
        throw requestError;
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
      const verification = data.export_verification || {};
      setChip("processStatus", data.process_running ? "扫码页运行中" : "扫码页未运行", Boolean(data.process_running));
      setChip(
        "authStatus",
        verification.required ? "等待导出确认" : data.authenticated ? "已进入小店后台" : "等待微信扫码",
        Boolean(data.authenticated) && !verification.required
      );
      setChip("profileStatus", data.profile_dir_exists ? "浏览器配置已创建" : "浏览器配置未创建", Boolean(data.profile_dir_exists));
      $("statusList").innerHTML = [
        ["页面进程", data.process_running ? `运行中 pid=${data.pid}` : "未运行"],
        ["登录状态", data.login_state_message || (data.authenticated ? "已登录" : "等待扫码")],
        ["导出验证", verification.required ? "本次导出等待微信扫码确认" : "当前无需确认"],
        ["官方后台", data.login_url || loginUrlFallback],
        ["浏览器配置", data.profile_dir_exists ? data.profile_dir : "未创建"],
        ["扫码截图", data.qr_maybe_expired ? "二维码可能已过期，请刷新" : (data.screenshot_ready ? "已生成，变灰请刷新二维码" : "等待生成")],
      ].map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
      $("openLogin").textContent = data.authenticated ? "已登录，如需换账号可刷新" : data.process_running ? "刷新扫码二维码" : "打开扫码二维码";
      $("qrExpiryAlert").hidden = data.authenticated || !data.qr_maybe_expired;
      $("urlFallback").textContent = data.login_url || loginUrlFallback;
      const screenshotUrl = verification.required
        ? `${verification.screenshot_url || "/web-login/export-verification-screenshot"}?challenge_id=${encodeURIComponent(verification.challenge_id || "")}`
        : data.screenshot_url || $("loginScreenshot").dataset.screenshotUrl;
      const screenshotReady = verification.required ? verification.screenshot_ready : data.screenshot_ready;
      const separator = screenshotUrl.includes("?") ? "&" : "?";
      $("loginScreenshot").src = screenshotReady ? `${screenshotUrl}${separator}t=${Date.now()}` : "";
    }

    async function loadStatus() {
      setMessage("读取登录状态中...");
      try {
        const data = await fetchJson("/web-login/status");
        renderStatus(data);
        setMessage(
          data.export_verification?.required
            ? "微信要求扫码确认本次导出，请扫描当前二维码并在手机上确认。"
            : data.authenticated
            ? "微信小店后台已登录，可以返回管理台启动页面表格导出。"
            : data.login_state === "login_required"
              ? "当前仍是二维码页，请用微信扫码并在手机上确认登录。"
              : data.login_state_message || "登录状态已刷新。",
          data.authenticated ? "ok" : ""
        );
      } catch (error) {
        setMessage(error.message, "error");
      }
    }

    async function openLogin() {
      setMessage("正在刷新扫码二维码...");
      try {
        const data = await fetchJson("/web-login/open", { method: "POST" });
        renderStatus(data);
        setMessage(
          data.authenticated ? "微信小店后台已经登录。" : "扫码二维码已刷新，等待截图生成后扫码。",
          "ok"
        );
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
    setInterval(loadStatus, 3000);
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
  <title>微信小店数据助手</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f8fa;
      --panel: #ffffff;
      --panel-soft: #f7f9fa;
      --text: #1f2329;
      --muted: #7b8493;
      --line: #e5e8eb;
      --accent: #07c160;
      --accent-soft: #e8f8ef;
      --accent-text: #04783a;
      --accent-strong: #04833e;
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
      line-height: 1.5;
    }
    .app-shell {
      width: min(1400px, 100%);
      margin: 0 auto;
      padding: 24px 28px 40px;
      display: grid;
      grid-template-columns: 220px minmax(0, 1fr);
      gap: 28px;
      align-items: start;
    }
    .side-nav {
      position: sticky;
      top: 20px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      gap: 16px;
      align-self: start;
      min-width: 0;
      max-height: calc(100vh - 40px);
      padding: 18px 12px 14px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      box-shadow: 0 8px 30px rgba(31, 35, 41, 0.04);
    }
    .side-nav-header {
      display: grid;
      gap: 4px;
      padding: 0 8px 4px;
    }
    .brand-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-mark {
      flex: 0 0 auto;
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 10px;
      background: var(--accent-strong);
      color: #fff;
      box-shadow: 0 6px 16px rgba(4, 131, 62, 0.18);
    }
    .brand-mark svg {
      width: 20px;
      height: 20px;
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
      gap: 3px;
    }
    .nav-group {
      display: grid;
      gap: 3px;
    }
    .nav-group + .nav-group {
      margin-top: 10px;
    }
    .nav-group-label {
      padding: 0 10px 4px;
      color: #a0a7b2;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .side-nav-item {
      width: 100%;
      min-height: 40px;
      justify-content: flex-start;
      display: grid;
      gap: 2px;
      padding: 8px 10px;
      border-color: transparent;
      background: transparent;
      text-align: left;
      font-weight: 700;
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
      background: var(--panel-soft);
    }
    .side-nav-item.active {
      border-color: transparent;
      background: var(--accent-soft);
      color: var(--accent-text);
    }
    .side-nav-item.active small {
      color: #406b5d;
    }
    .side-nav-footer {
      display: grid;
      gap: 8px;
      padding: 10px 8px 0;
      border-top: 1px solid var(--line);
    }
    .side-nav-footer a {
      color: var(--muted);
      font-size: 12px;
    }
    .main-content {
      min-width: 0;
    }
    .workspace-surface {
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      box-shadow: 0 12px 36px rgba(31, 35, 41, 0.055);
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin: 2px 0 18px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 26px;
      line-height: 1.2;
      letter-spacing: -0.02em;
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
      min-height: 40px;
      padding: 9px 14px;
      border: 1px solid var(--accent-strong);
      border-radius: 8px;
      background: var(--accent-strong);
      color: #ffffff;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }
    .button-link:hover {
      background: #036f34;
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
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(31, 35, 41, 0.035);
      margin-bottom: 16px;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }
    .panel-heading {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .panel-heading p {
      font-size: 12px;
    }
    .panel-body { padding: 18px; }
    .context-panel .panel-body { padding: 16px 18px; }
    .workflow-panel {
      margin: 0;
      padding: 20px 24px 22px;
      border: 0;
      border-radius: 0;
      background: linear-gradient(180deg, #fbfcfc 0%, #f8faf9 100%);
      box-shadow: none;
      border-bottom: 1px solid var(--line);
    }
    .workflow-head {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    .workflow-head h2 {
      margin-bottom: 3px;
    }
    .workflow-head p {
      font-size: 12px;
    }
    .workflow-rail {
      position: relative;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
    }
    .workflow-rail::before {
      content: "";
      position: absolute;
      top: 16px;
      left: 12.5%;
      right: 12.5%;
      height: 2px;
      background: #dfe5e2;
    }
    .workflow-step {
      position: relative;
      z-index: 1;
      display: grid;
      justify-items: center;
      gap: 7px;
      min-width: 0;
      padding: 0 8px;
      border: 0;
      background: transparent;
      text-align: center;
    }
    .workflow-step:hover {
      background: transparent;
    }
    .workflow-number {
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 2px solid #dfe5e2;
      border-radius: 50%;
      background: #fff;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      transition: 0.18s ease;
    }
    .workflow-step:hover .workflow-number {
      border-color: var(--accent);
      color: var(--accent-text);
    }
    .workflow-step.completed .workflow-number {
      border-color: #a8dfbf;
      background: var(--accent-soft);
      color: var(--accent-text);
    }
    .workflow-step.active .workflow-number {
      border-color: var(--accent-strong);
      background: var(--accent-strong);
      color: #fff;
      box-shadow: 0 0 0 5px rgba(7, 193, 96, 0.11);
    }
    .workflow-step.active .workflow-copy strong {
      color: var(--accent-text);
    }
    .workflow-copy {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .workflow-copy strong {
      color: var(--text);
      font-size: 13px;
    }
    .workflow-copy small {
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
    }
    .context-layout {
      display: grid;
      grid-template-columns: minmax(240px, 1.15fr) minmax(150px, 0.7fr) minmax(150px, 0.7fr);
      gap: 14px;
      align-items: end;
    }
    .context-shop {
      min-width: 0;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-soft);
    }
    .compact-summary {
      grid-template-columns: 64px minmax(0, 1fr);
      gap: 3px 8px;
    }
    .compact-summary dt,
    .compact-summary dd {
      line-height: 1.4;
    }
    .workspace-surface .context-panel {
      display: grid;
      grid-template-columns: 230px minmax(0, 1fr);
      align-items: center;
      margin: 0;
      border: 0;
      border-radius: 0;
      box-shadow: none;
      border-bottom: 1px solid var(--line);
    }
    .workspace-surface .context-panel .panel-header {
      padding: 18px 8px 18px 24px;
      border: 0;
    }
    .workspace-surface .context-panel .panel-body {
      padding: 14px 24px 14px 8px;
    }
    .workspace-surface .menu-section {
      margin: 0;
      border: 0;
      border-radius: 0;
      box-shadow: none;
    }
    .workspace-surface .menu-section .panel-header {
      padding: 20px 24px;
    }
    .workspace-surface .menu-section .panel-body {
      padding: 22px 24px 26px;
    }
    .connection-layout,
    .export-layout {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(300px, 0.85fr);
      gap: 16px;
      align-items: start;
    }
    .operation-block,
    .status-block {
      min-width: 0;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
    }
    .status-block {
      background: var(--panel-soft);
    }
    .block-heading {
      display: grid;
      gap: 3px;
      margin-bottom: 14px;
    }
    .block-heading strong {
      color: var(--text);
      font-size: 14px;
    }
    .block-heading span {
      color: var(--muted);
      font-size: 12px;
    }
    .status-block .status-row:last-child,
    .operation-block .button-row:last-child {
      margin-bottom: 0;
    }
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
      border-radius: 8px;
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      min-height: 42px;
      font: inherit;
      font-size: 13px;
      outline: none;
    }
    input:focus,
    select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(7, 193, 96, 0.12);
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
      border-radius: 8px;
      background: #ffffff;
      color: #243044;
      min-height: 40px;
      padding: 9px 13px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: #f8fafc; }
    button.primary {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #ffffff;
    }
    button.primary:hover { background: #036f34; }
    button.danger {
      border-color: #e3aaa1;
      background: #fff4f2;
      color: var(--danger);
    }
    button.danger:hover { background: #ffe8e4; }
    button:disabled,
    button:disabled:hover {
      opacity: 0.68;
      cursor: wait;
    }
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
      position: relative;
      display: grid;
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 0;
      padding-top: 2px;
    }
    .task-steps::before {
      content: "";
      position: absolute;
      top: 14px;
      left: 10%;
      right: 10%;
      height: 2px;
      background: #e2e7e4;
    }
    .task-step {
      position: relative;
      z-index: 1;
      border: 0;
      border-radius: 0;
      background: transparent;
      padding: 28px 8px 0;
      min-height: 66px;
      text-align: center;
    }
    .task-step::before {
      content: "";
      position: absolute;
      top: 5px;
      left: 50%;
      width: 18px;
      height: 18px;
      transform: translateX(-50%);
      border: 4px solid #fff;
      border-radius: 50%;
      background: #cbd2d8;
      box-shadow: 0 0 0 1px #cbd2d8;
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
      background: transparent;
    }
    .task-step.completed {
      background: transparent;
    }
    .task-step.failed {
      background: transparent;
    }
    .task-step.running::before {
      background: var(--accent);
      box-shadow: 0 0 0 1px var(--accent), 0 0 0 5px rgba(7, 193, 96, 0.12);
    }
    .task-step.completed::before {
      background: var(--accent-strong);
      box-shadow: 0 0 0 1px var(--accent-strong);
    }
    .task-step.failed::before {
      background: var(--danger);
      box-shadow: 0 0 0 1px var(--danger);
    }
    .task-step.cancelled::before {
      background: #98a2b3;
      box-shadow: 0 0 0 1px #98a2b3;
    }
    .task-progress {
      display: grid;
      gap: 6px;
    }
    .progress-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: #344054;
      font-size: 12px;
      font-weight: 700;
    }
    .progress-track {
      height: 9px;
      overflow: hidden;
      border-radius: 999px;
      background: #edf1f5;
    }
    .progress-fill {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: var(--accent-strong);
      transition: width 0.2s ease;
    }
    .progress-fill.failed { background: var(--danger); }
    .progress-fill.completed { background: var(--ok); }
    .progress-fill.cancelled { background: #98a2b3; }
    .history-heading {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      color: var(--text);
      font-size: 13px;
      font-weight: 750;
    }
    .history-heading span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .task-history-current td {
      background: #f0fbf3;
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
    body.modal-open {
      overflow: hidden;
    }
    .completion-modal {
      position: fixed;
      z-index: 90;
      inset: 0;
      display: grid;
      place-items: center;
      padding: 24px;
    }
    .completion-modal[hidden] {
      display: none;
    }
    .completion-modal-backdrop {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      min-height: 0;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: rgba(18, 23, 28, 0.48);
      cursor: default;
    }
    .completion-modal-card {
      position: relative;
      width: min(520px, 100%);
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #fff;
      box-shadow: 0 24px 80px rgba(18, 23, 28, 0.24);
    }
    .completion-modal-close-icon {
      position: absolute;
      z-index: 1;
      top: 14px;
      right: 14px;
      width: 36px;
      min-height: 36px;
      padding: 0;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
      font-size: 22px;
      font-weight: 500;
      line-height: 1;
    }
    .completion-modal-close-icon:hover {
      background: var(--panel-soft);
      color: var(--text);
    }
    .completion-modal-hero {
      display: grid;
      grid-template-columns: 48px minmax(0, 1fr);
      gap: 14px;
      align-items: center;
      padding: 24px 58px 18px 24px;
      border-bottom: 1px solid var(--line);
      background: #f4fbf7;
    }
    .completion-modal-card.partial .completion-modal-hero,
    .completion-modal-card.warning .completion-modal-hero {
      background: #fff9ea;
    }
    .completion-modal-card.danger .completion-modal-hero {
      background: #fff4f2;
    }
    .completion-modal-icon {
      width: 48px;
      height: 48px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: var(--accent-soft);
      color: var(--accent-strong);
    }
    .completion-modal-card.partial .completion-modal-icon,
    .completion-modal-card.warning .completion-modal-icon {
      background: #fff0c2;
      color: #996a00;
    }
    .completion-modal-card.danger .completion-modal-icon {
      background: #ffe1dc;
      color: var(--danger);
    }
    .completion-modal-icon svg {
      width: 25px;
      height: 25px;
    }
    .completion-modal-hero h2 {
      margin: 0 0 4px;
      font-size: 20px;
      line-height: 1.25;
    }
    .completion-modal-hero p {
      font-size: 13px;
    }
    .completion-modal-body {
      display: grid;
      gap: 14px;
      padding: 20px 24px 24px;
    }
    .completion-modal-metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .completion-modal-metric {
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-soft);
    }
    .completion-modal-metric strong {
      display: block;
      margin-bottom: 2px;
      color: var(--text);
      font-size: 22px;
      line-height: 1.2;
    }
    .completion-modal-metric span {
      color: var(--muted);
      font-size: 12px;
    }
    .completion-modal-message {
      color: #344054;
      font-size: 13px;
    }
    .completion-modal-warning {
      padding: 10px 12px;
      border: 1px solid var(--warn-line);
      border-radius: 8px;
      background: var(--warn-bg);
      color: #7a4f01;
      font-size: 12px;
    }
    .completion-modal-warning[hidden],
    .completion-modal-actions > [hidden] {
      display: none;
    }
    .completion-modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
      padding-top: 2px;
    }
    .completion-modal-card.verification .completion-modal-hero {
      background: var(--info-bg);
    }
    .completion-modal-card.verification .completion-modal-icon {
      background: #dceeff;
      color: #1769aa;
    }
    .verification-qr-frame {
      display: grid;
      place-items: center;
      min-height: 260px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-soft);
      padding: 10px;
    }
    .verification-qr-image {
      display: block;
      width: 100%;
      max-height: 440px;
      object-fit: contain;
      border-radius: 8px;
      background: #fff;
    }
    .verification-qr-image[hidden] {
      display: none;
    }
    .verification-status {
      color: #344054;
      font-size: 12px;
      text-align: center;
    }
    .verification-target {
      display: grid;
      gap: 3px;
      padding: 13px 15px;
      border: 1px solid #9ecaf0;
      border-radius: 10px;
      background: #edf7ff;
      color: #173b5c;
    }
    .verification-target span {
      color: #47647e;
      font-size: 12px;
    }
    .verification-target strong {
      color: #0f548c;
      font-size: 18px;
      line-height: 1.35;
    }
    .verification-target small {
      color: #47647e;
      font-size: 12px;
    }
    .verification-target small[hidden] {
      display: none;
    }
    .check-summary {
      display: grid;
      gap: 10px;
      color: #243044;
    }
    .bundle-download {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 14px;
      border: 1px solid #a9d9bc;
      border-radius: 10px;
      background: #f0fbf3;
    }
    .bundle-download-copy {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .bundle-download-copy strong { color: var(--accent-strong); }
    .export-file-details {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
    }
    .export-file-details summary {
      padding: 11px 13px;
      color: #344054;
      font-size: 12px;
      font-weight: 750;
      cursor: pointer;
    }
    .export-file-details-body {
      padding: 0 13px 13px;
      border-top: 1px solid var(--line);
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
      gap: 18px;
    }
    .capability-group {
      display: grid;
      gap: 10px;
    }
    .capability-group + .capability-group {
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }
    .capability-group-head {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px;
    }
    .capability-group-heading {
      display: grid;
      gap: 2px;
    }
    .capability-group-heading strong {
      color: var(--text);
      font-size: 14px;
    }
    .capability-group-heading span,
    .capability-count {
      color: var(--muted);
      font-size: 12px;
    }
    .capability-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 10px;
    }
    .capability-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #ffffff;
      padding: 12px 13px;
      display: grid;
      gap: 9px;
    }
    .capability-item.direct {
      border-color: #c5e8d3;
      background: #fbfefc;
    }
    .capability-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .capability-title strong {
      color: var(--text);
      font-size: 13px;
      line-height: 1.35;
    }
    .capability-state {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 650;
      white-space: nowrap;
    }
    .capability-state::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #a8b0ba;
    }
    .capability-state.ok {
      color: var(--ok);
    }
    .capability-state.ok::before {
      background: var(--accent);
    }
    .capability-method {
      display: flex;
      align-items: baseline;
      gap: 8px;
    }
    .capability-method span {
      color: var(--muted);
      font-size: 11px;
    }
    .capability-method strong {
      color: #344054;
      font-size: 12px;
    }
    .capability-notes {
      color: var(--muted);
      font-size: 11px;
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
      border-radius: 10px;
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
      background: var(--panel-soft);
      color: #475467;
      font-weight: 750;
    }
    tr:last-child td { border-bottom: 0; }
    tbody tr:hover td { background: #fbfcfc; }
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
    .mobile-topbar,
    .mobile-bottom-nav,
    .nav-scrim {
      display: none;
    }
    @media (max-width: 900px) {
      body {
        padding-bottom: 72px;
      }
      body.nav-open {
        overflow: hidden;
      }
      .app-shell {
        display: block;
        padding: 14px 14px 24px;
      }
      .side-nav {
        position: fixed;
        z-index: 40;
        top: 0;
        bottom: 0;
        left: 0;
        width: min(82vw, 300px);
        max-height: none;
        gap: 16px;
        padding: 22px 14px;
        border-width: 0 1px 0 0;
        border-radius: 0 16px 16px 0;
        transform: translateX(-104%);
        transition: transform 0.2s ease;
        box-shadow: 16px 0 40px rgba(31, 35, 41, 0.14);
      }
      body.nav-open .side-nav {
        transform: translateX(0);
      }
      .side-nav-footer {
        display: grid;
      }
      .nav-scrim {
        position: fixed;
        z-index: 30;
        inset: 0;
        width: 100%;
        height: 100%;
        min-height: 0;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: rgba(18, 23, 28, 0.34);
      }
      body.nav-open .nav-scrim {
        display: block;
      }
      .mobile-topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 20px;
      }
      .mobile-brand {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 15px;
        font-weight: 800;
      }
      .mobile-brand .brand-mark {
        width: 28px;
        height: 28px;
        border-radius: 8px;
      }
      .mobile-brand .brand-mark svg {
        width: 17px;
        height: 17px;
      }
      .mobile-menu-button {
        width: 40px;
        min-height: 40px;
        padding: 0;
      }
      .mobile-menu-button svg {
        width: 20px;
        height: 20px;
      }
      header {
        display: grid;
        gap: 14px;
        margin-bottom: 16px;
      }
      h1 { font-size: 23px; }
      .top-actions { display: none; }
      .workspace-surface { border-radius: 14px; }
      .workflow-panel { padding: 18px 16px; }
      .workflow-head { align-items: flex-start; margin-bottom: 16px; }
      .workflow-head > p { display: none; }
      .workflow-rail {
        grid-template-columns: 1fr;
      }
      .workflow-rail::before {
        top: 16px;
        bottom: 18px;
        left: 16px;
        right: auto;
        width: 2px;
        height: auto;
      }
      .workflow-step {
        grid-template-columns: 34px minmax(0, 1fr);
        justify-items: start;
        align-items: start;
        gap: 12px;
        min-height: 58px;
        padding: 0;
        text-align: left;
      }
      .workflow-step:last-child { min-height: 34px; }
      .workflow-copy { padding-top: 1px; }
      .workflow-copy small { display: block; }
      .workflow-copy strong { font-size: 13px; }
      .context-layout,
      .connection-layout,
      .export-layout,
      .grid,
      .menu-grid,
      .notice-grid,
      .form-grid { grid-template-columns: 1fr; }
      .workspace-surface .context-panel {
        grid-template-columns: 1fr;
      }
      .workspace-surface .context-panel .panel-header {
        padding: 18px 18px 8px;
      }
      .workspace-surface .context-panel .panel-body {
        padding: 8px 18px 18px;
      }
      .workspace-surface .menu-section .panel-header {
        padding: 18px;
      }
      .workspace-surface .menu-section .panel-body {
        padding: 18px;
      }
      .operation-block,
      .status-block {
        padding: 16px;
      }
      .context-shop { order: -1; }
      .task-steps {
        grid-template-columns: 1fr;
        gap: 0;
      }
      .task-steps::before {
        top: 16px;
        bottom: 16px;
        left: 10px;
        right: auto;
        width: 2px;
        height: auto;
      }
      .task-step {
        min-height: 52px;
        padding: 5px 8px 12px 34px;
        text-align: left;
      }
      .task-step::before {
        top: 8px;
        left: 10px;
      }
      .task-step strong { margin-bottom: 2px; }
      .full { grid-column: auto; }
      dl { grid-template-columns: 100px minmax(0, 1fr); }
      .compact-summary { grid-template-columns: 64px minmax(0, 1fr); }
      .panel-header button {
        flex: 0 0 auto;
        min-width: 88px;
        white-space: nowrap;
      }
      .mobile-bottom-nav {
        position: fixed;
        z-index: 20;
        left: 0;
        right: 0;
        bottom: 0;
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        padding: 7px 8px calc(7px + env(safe-area-inset-bottom));
        border-top: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.96);
        backdrop-filter: blur(14px);
      }
      .mobile-nav-item {
        display: grid;
        justify-items: center;
        gap: 2px;
        min-height: 48px;
        padding: 5px 2px;
        border-color: transparent;
        background: transparent;
        color: var(--muted);
        font-size: 11px;
      }
      .mobile-nav-item::before {
        content: "";
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: transparent;
      }
      .mobile-nav-item.active {
        color: var(--accent-text);
      }
      .mobile-nav-item.active::before {
        background: var(--accent);
      }
      .mobile-nav-item:hover { background: transparent; }
      .button-link {
        max-width: 100%;
        white-space: normal;
        overflow-wrap: anywhere;
        text-align: center;
      }
      .button-row .primary {
        flex: 1 1 100%;
        min-height: 44px;
      }
      .completion-modal {
        align-items: end;
        padding: 12px;
      }
      .completion-modal-card {
        width: 100%;
        max-height: calc(100vh - 24px);
        overflow: auto;
        border-radius: 16px;
      }
      .completion-modal-hero {
        grid-template-columns: 42px minmax(0, 1fr);
        padding: 20px 50px 16px 18px;
      }
      .completion-modal-icon {
        width: 42px;
        height: 42px;
      }
      .completion-modal-hero h2 {
        font-size: 18px;
      }
      .completion-modal-body {
        padding: 18px;
      }
      .completion-modal-actions {
        display: grid;
        grid-template-columns: 1fr;
      }
      .completion-modal-actions > * {
        width: 100%;
        min-height: 44px;
      }
      input,
      select {
        font-size: 16px;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
      }
    }
  </style>
</head>
<body>
  <button type="button" id="navScrim" class="nav-scrim" aria-label="关闭功能菜单"></button>
  <main class="app-shell">
    <aside id="sideNavigation" class="side-nav" aria-label="功能菜单">
      <div class="side-nav-header">
        <div class="brand-row">
          <span class="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M6 16v-5m6 5V7m6 9v-8M4 19h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </span>
          <div>
            <div class="side-nav-title">微信小店数据助手</div>
            <div class="side-nav-subtitle">经营数据中心</div>
          </div>
        </div>
      </div>
      <nav class="side-nav-items">
        <div class="nav-group">
          <div class="nav-group-label">开始</div>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="authSection" aria-controls="authSection">
            <span>连接店铺</span>
            <small>授权状态与店铺信息</small>
          </button>
        </div>
        <div class="nav-group">
          <div class="nav-group-label">获取数据</div>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="webOrderSection" aria-controls="webOrderSection">
            <span>页面表格导出</span>
            <small>扫码后下载原始文件</small>
          </button>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="apiSyncSection" aria-controls="apiSyncSection">
            <span>数据同步</span>
            <small>通过官方接口获取</small>
          </button>
        </div>
        <div class="nav-group">
          <div class="nav-group-label">查看结果</div>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="statusSection" aria-controls="statusSection">
            <span>任务与下载</span>
            <small>进度、结果和文件</small>
          </button>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="recordsSection" aria-controls="recordsSection">
            <span>报告中心</span>
            <small>经营分析结果</small>
          </button>
        </div>
        <div class="nav-group">
          <div class="nav-group-label">其他工具</div>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="localExportSection" aria-controls="localExportSection">
            <span>文件导入</span>
            <small>Excel / CSV</small>
          </button>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="collectorSection" aria-controls="collectorSection">
            <span>本地采集助手</span>
            <small>客户电脑采集</small>
          </button>
          <button type="button" class="side-nav-item menu-trigger" data-menu-target="capabilitySection" aria-controls="capabilitySection">
            <span>数据范围</span>
            <small>可用数据模块</small>
          </button>
        </div>
      </nav>
      <div class="side-nav-footer">
        <a href="/login">打开扫码登录页</a>
      </div>
    </aside>

    <section class="main-content">
      <div class="mobile-topbar">
        <button type="button" id="mobileMenuButton" class="mobile-menu-button" aria-controls="sideNavigation" aria-expanded="false" aria-label="打开功能菜单">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
        <div class="mobile-brand">
          <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M6 16v-5m6 5V7m6 9v-8M4 19h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></span>
          <span>微信小店数据助手</span>
        </div>
        <button type="button" id="mobileRefreshAll" aria-label="刷新页面数据">刷新</button>
      </div>
      <header>
        <div>
          <h1>数据采集工作台</h1>
          <p>按步骤连接店铺、获取数据并下载结果；需要时再生成分析报告。</p>
        </div>
        <div class="top-actions">
          <a class="button-link" href="/login">扫码登录</a>
          <button type="button" id="refreshAll">刷新全部</button>
        </div>
      </header>

      <div class="workspace-surface">
      <section class="workflow-panel" aria-labelledby="workflowTitle">
        <div class="workflow-head">
          <div>
            <h2 id="workflowTitle">完成一次数据导出</h2>
            <p>第一次使用按 1–4 步操作，熟悉后可直接从左侧进入对应功能。</p>
          </div>
          <p>页面导出只下载文件，不会自动分析。</p>
        </div>
        <div class="workflow-rail" aria-label="数据导出流程">
          <button type="button" class="workflow-step workflow-jump" data-menu-target="authSection" data-flow-step="connect" aria-controls="authSection">
            <span class="workflow-number">1</span>
            <span class="workflow-copy"><strong>连接店铺</strong><small>填写 AppID 和密钥</small></span>
          </button>
          <button type="button" class="workflow-step workflow-jump" data-menu-target="webOrderSection" data-flow-step="login" aria-controls="webOrderSection">
            <span class="workflow-number">2</span>
            <span class="workflow-copy"><strong>扫码登录</strong><small>登录微信小店后台</small></span>
          </button>
          <button type="button" class="workflow-step workflow-jump" data-menu-target="webOrderSection" data-flow-step="export" aria-controls="webOrderSection">
            <span class="workflow-number">3</span>
            <span class="workflow-copy"><strong>导出数据</strong><small>校验登录并启动任务</small></span>
          </button>
          <button type="button" class="workflow-step workflow-jump" data-menu-target="statusSection" data-flow-step="download" aria-controls="statusSection">
            <span class="workflow-number">4</span>
            <span class="workflow-copy"><strong>下载文件</strong><small>查看任务结果并下载</small></span>
          </button>
        </div>
      </section>

      <section class="panel records context-panel" aria-labelledby="commonTaskTitle">
        <div class="panel-header">
          <div class="panel-heading">
            <h2 id="commonTaskTitle">本次任务范围</h2>
            <p>店铺信息自动读取；日期供同步、导入和任务归档使用。</p>
          </div>
        </div>
        <div class="panel-body">
          <div class="context-layout">
            <input id="collectShopId" name="shop_id" type="hidden">
            <input id="collectShopName" name="shop_name" type="hidden">
            <div class="context-shop">
              <dl id="shopInfoSummary" class="compact-summary">
                <dt>店铺名称</dt><dd>未读取</dd>
                <dt>店铺 ID</dt><dd>未读取</dd>
              </dl>
            </div>
            <label>
              开始日期
              <input id="collectFrom" name="from" type="date" required>
            </label>
            <label>
              结束日期
              <input id="collectTo" name="to" type="date" required>
            </label>
            <div class="full" role="status" aria-live="polite">
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
            <strong>扫码登录用于页面表格导出</strong>
            <p>这里勾选的数据模块只控制官方接口同步，不会影响“页面表格导出”。数据同步需要服务端接口授权；微信小店后台首页没有这个授权按钮。</p>
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
          <div class="panel-heading">
            <h2 id="authTitle">连接店铺</h2>
            <p>接口配置完成后，店铺与授权状态会自动读取。</p>
          </div>
          <button type="button" id="refreshConfig">刷新配置</button>
        </div>
        <div class="panel-body stack">
          <div class="notice">
            <strong>只需填写 AppID 和 AppSecret</strong>
            <p>系统会自动获取接口授权、有效期、店铺名称和店铺原始 ID。接口密钥保存后不会回显。</p>
          </div>
          <div class="connection-layout">
            <div class="operation-block">
              <div class="block-heading">
                <strong>填写接口信息</strong>
                <span>只需要微信小店 AppID 和 AppSecret。</span>
              </div>
              <form id="configForm" class="form-grid">
                <label>
                  微信小店 AppID
                  <input id="credentialId" name="credential_id" autocomplete="off" placeholder="例如 wxf32b82e92ba04239" required>
                </label>
                <label>
                  微信小店 AppSecret
                  <input id="appSecret" name="app_secret" type="password" autocomplete="off" placeholder="首次连接必填；已连接后留空可复用">
                </label>
                <input id="shopId" name="shop_id" type="hidden">
                <input id="shopName" name="shop_name" type="hidden">
                <input id="apiBaseUrl" type="hidden">
                <input id="rawArchiveDir" type="hidden">
                <input id="accessToken" type="hidden">
                <input id="syncDryRun" class="hidden" type="checkbox">
                <div class="button-row full">
                  <button type="submit" id="saveAndFetchToken" class="primary">保存并连接微信小店</button>
                </div>
              </form>
              <span id="configMessage" class="message" role="status" aria-live="polite"></span>
            </div>
            <aside class="status-block" aria-label="当前连接状态">
              <div class="block-heading">
                <strong>当前连接状态</strong>
                <span>保存成功后会自动更新店铺与授权信息。</span>
              </div>
              <div class="status-row">
                <span id="appSecretStatus" class="chip">接口密钥未读取</span>
                <span id="accessTokenStatus" class="chip">接口授权未读取</span>
              </div>
              <dl id="configSummary"></dl>
              <div id="tokenResult" class="result-box">接口授权状态未读取。</div>
            </aside>
          </div>
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
              <input id="localSourceDir" name="source_dir" autocomplete="off" placeholder="例如 ~/Downloads/微信小店导出">
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
          <div class="panel-heading">
            <h2 id="orderTaskTitle">微信小店页面表格导出</h2>
            <p>先确认扫码登录，再启动只导出任务。</p>
          </div>
        </div>
        <div class="panel-body stack">
          <div class="notice">
            <strong>本轮只导出，不导入、不分析、不生成报告</strong>
            <p>系统会先把上方日期应用到支持日期筛选的页面，再按所选模块导出；日期无法可靠应用时会明确失败，不会把全部数据冒充所选周期。微信可能对不同模块分别要求扫码，系统会在每次验证时重新弹出二维码。</p>
          </div>
          <div class="export-layout">
            <div class="status-block">
              <div class="block-heading">
                <strong>扫码登录状态</strong>
                <span>页面导出会复用独立扫码浏览器。</span>
              </div>
              <div class="status-row">
                <span id="webLoginProcessStatus" class="chip">扫码窗口未读取</span>
                <span id="webLoginAuthStatus" class="chip">后台登录未读取</span>
                <span id="webLoginProfileStatus" class="chip">Profile 未读取</span>
              </div>
              <dl id="webLoginStatusList"></dl>
              <span id="webLoginMessage" class="message" role="status" aria-live="polite"></span>
              <div class="button-row">
                <button type="button" id="refreshWebLogin">刷新登录状态</button>
                <a class="button-link" href="/login">去扫码登录</a>
              </div>
            </div>
            <div class="operation-block">
              <div class="block-heading">
                <strong>启动页面导出</strong>
                <span>按微信小店实际页面选择数据，登录校验通过后才创建任务。</span>
              </div>
              <form id="orderTaskForm" class="form-grid" autocomplete="off">
                <label class="full">
                  本次导出数据（6 类）
                  <div class="module-grid">
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="product_list" checked>商品管理 &gt; 商品列表（商品数据）</label>
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="orders" checked>订单明细</label>
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="fund_flows" checked>资金流水</label>
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="transactions" checked>交易数据（含页面内多张表）</label>
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="product_data" checked>店铺数据 &gt; 商品数据（3 张表）</label>
                    <label class="module-option"><input type="checkbox" name="page_export_module" value="compass_buyer_profile" checked>电商罗盘 &gt; 买家人群特征（页面快照）</label>
                  </div>
                  <span class="hint">6 个业务组会展开为 8 个采集页面；买家人群特征暂无微信原生下载按钮，保存页面可见数据 CSV 快照。</span>
                </label>
                <div class="button-row full">
                  <button type="submit" id="startOrderTask" class="primary">校验登录并启动导出</button>
                  <span id="exportActionStatus" class="chip">等待操作</span>
                  <span class="hint">只执行本区域勾选的数据；未勾选项不会打开或导出。</span>
                </div>
                <span id="exportActionMessage" class="message full" role="status" aria-live="polite">上方日期会应用到支持筛选的页面；点击后先校验登录，再按勾选顺序导出。</span>
              </form>
            </div>
          </div>
        </div>
      </section>

      <section id="collectorSection" class="panel records menu-section" aria-labelledby="collectorTitle">
        <div class="panel-header">
          <h2 id="collectorTitle">本地采集助手</h2>
          <button type="button" id="refreshCollectorJobs">刷新任务</button>
        </div>
        <div class="panel-body stack">
          <div class="notice">
            <strong>本地可见订单兜底（仅原始文件）</strong>
            <p>采集助手只上传客户电脑当前可见区域；未完成日期筛选和分页校验前，不会自动导入分析。</p>
          </div>
          <div class="button-row">
            <button type="button" id="createCollectorJob" class="primary">创建订单采集任务</button>
            <span class="hint">使用上方任务信息里的店铺和日期。</span>
          </div>
          <pre id="collectorCommand" class="result-box">正在生成当前服务器命令...</pre>
          <span class="hint">运行前请设置 WECHAT_STORE_ADMIN_USER 和 WECHAT_STORE_ADMIN_PASSWORD。</span>
          <span id="collectorMessage" class="message"></span>
          <div id="collectorJobsPanel" class="table-wrap"></div>
        </div>
      </section>

      <section id="capabilitySection" class="panel records menu-section" aria-labelledby="capabilityTitle">
        <div class="panel-header">
          <div class="panel-heading">
            <h2 id="capabilityTitle">数据范围</h2>
            <p>按获取方式查看哪些数据可直接导出，哪些数据需要上传文件。</p>
          </div>
        </div>
        <div class="panel-body stack">
          <div id="capabilityMessage" class="message">读取数据范围中...</div>
          <div id="capabilitiesPanel" class="capability-grid"></div>
        </div>
      </section>

      <section id="statusSection" class="panel records menu-section" aria-labelledby="statusTitle">
        <div class="panel-header">
          <div class="panel-heading">
            <h2 id="statusTitle">任务与下载</h2>
            <p>查看执行进度、失败原因和已导出的原始文件。</p>
          </div>
          <div class="button-row">
            <button type="button" id="cancelActiveTask" class="danger" hidden>取消任务</button>
            <button type="button" id="refreshTasks">刷新进度</button>
          </div>
        </div>
        <div class="panel-body stack">
          <div class="status-row">
            <span id="taskStateChip" class="chip">暂无任务</span>
            <span id="taskReportChip" class="chip">暂无报告</span>
          </div>
          <div id="taskProgress" class="task-progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
            <div class="progress-head">
              <span id="taskProgressLabel">暂无进度</span>
              <span id="taskProgressPercent">0%</span>
            </div>
            <div class="progress-track"><div id="taskProgressBar" class="progress-fill"></div></div>
          </div>
          <dl id="taskSummary"></dl>
          <div id="taskSteps" class="task-steps"></div>
          <div id="taskResult" class="result-box">暂无任务结果。</div>
          <div class="history-heading">最近导出任务 <span>保留最近 20 条，可回看每次执行结果</span></div>
          <div id="taskHistoryPanel" class="table-wrap"></div>
          <div id="reportPreview" class="result-box report-preview hidden"></div>
        </div>
      </section>

      <section id="recordsSection" class="panel records menu-section" aria-labelledby="recordsTitle">
        <div class="panel-header">
          <div class="panel-heading">
            <h2 id="recordsTitle">报告中心</h2>
            <p>仅展示已完成分析任务生成的经营报告。</p>
          </div>
          <button type="button" id="refreshRecords">刷新报告</button>
        </div>
        <div class="panel-body">
          <div id="recordsMessage" class="message">读取分析结果中...</div>
          <div id="reportsPanel" class="table-wrap"></div>
          <div id="reportDetail" class="result-box report-preview hidden"></div>
        </div>
      </section>
      </div>
    </section>
  </main>

  <nav class="mobile-bottom-nav" aria-label="移动端核心功能">
    <button type="button" class="mobile-nav-item menu-trigger" data-menu-target="authSection" aria-controls="authSection">工作台</button>
    <button type="button" class="mobile-nav-item menu-trigger" data-menu-target="webOrderSection" aria-controls="webOrderSection">采集</button>
    <button type="button" class="mobile-nav-item menu-trigger" data-menu-target="statusSection" aria-controls="statusSection">任务</button>
    <button type="button" class="mobile-nav-item menu-trigger" data-menu-target="recordsSection" aria-controls="recordsSection">分析</button>
  </nav>

  <div id="exportCompletionModal" class="completion-modal" role="dialog" aria-modal="true" aria-labelledby="exportCompletionTitle" aria-describedby="exportCompletionMessage" hidden>
    <button type="button" class="completion-modal-backdrop" data-completion-close aria-label="关闭导出结果弹窗"></button>
    <section id="exportCompletionCard" class="completion-modal-card" tabindex="-1">
      <button type="button" id="exportCompletionCloseIcon" class="completion-modal-close-icon" aria-label="关闭">×</button>
      <div class="completion-modal-hero">
        <span id="exportCompletionIcon" class="completion-modal-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M7 12.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </span>
        <div>
          <h2 id="exportCompletionTitle">导出完成</h2>
          <p id="exportCompletionSubtitle">任务已结束，可以查看结果和下载文件。</p>
        </div>
      </div>
      <div class="completion-modal-body">
        <div class="completion-modal-metrics">
          <div class="completion-modal-metric"><strong id="exportCompletionFileCount">0</strong><span>可下载文件</span></div>
          <div class="completion-modal-metric"><strong id="exportCompletionFailureCount">0</strong><span>失败页面</span></div>
        </div>
        <p id="exportCompletionMessage" class="completion-modal-message"></p>
        <div id="exportCompletionWarning" class="completion-modal-warning" hidden></div>
        <div class="completion-modal-actions">
          <button type="button" id="exportCompletionDetails">查看任务详情</button>
          <a id="exportCompletionDownload" class="button-link" href="#" download>立即下载文件</a>
          <button type="button" id="exportCompletionClose">关闭</button>
        </div>
      </div>
    </section>
  </div>

  <div id="exportVerificationModal" class="completion-modal" role="dialog" aria-modal="true" aria-labelledby="exportVerificationTitle" aria-describedby="exportVerificationMessage" hidden>
    <button type="button" class="completion-modal-backdrop" data-verification-close aria-label="暂时关闭导出验证弹窗"></button>
    <section id="exportVerificationCard" class="completion-modal-card verification" tabindex="-1">
      <button type="button" id="exportVerificationCloseIcon" class="completion-modal-close-icon" aria-label="暂时关闭">×</button>
      <div class="completion-modal-hero">
        <span class="completion-modal-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM15 14h2v2h-2zM18 14h2v6h-6v-2h4zM14 17h2v3h-2z" fill="currentColor"/></svg>
        </span>
        <div>
          <h2 id="exportVerificationTitle">需要扫码确认本次导出</h2>
          <p>这是微信针对当前数据模块发起的安全验证。</p>
        </div>
      </div>
      <div class="completion-modal-body">
        <div class="verification-target">
          <span>当前需要验证</span>
          <strong id="exportVerificationTarget">当前数据表</strong>
          <small id="exportVerificationControl" hidden></small>
        </div>
        <div class="notice warn">
          <strong>请使用微信扫描下方二维码</strong>
          <p id="exportVerificationMessage">扫码并在手机端确认后，任务会自动继续；后续模块可能再次要求验证。</p>
        </div>
        <div class="verification-qr-frame">
          <img id="exportVerificationImage" class="verification-qr-image" alt="当前导出安全验证二维码" hidden>
          <span id="exportVerificationImagePending" class="hint">二维码正在生成，请稍候...</span>
        </div>
        <p id="exportVerificationStatus" class="verification-status" role="status" aria-live="polite">等待二维码。</p>
        <div class="completion-modal-actions">
          <button type="button" id="refreshExportVerification">刷新二维码</button>
          <a class="button-link" href="/login" target="_blank" rel="noopener">打开独立扫码页</a>
          <button type="button" id="exportVerificationClose">暂时关闭</button>
        </div>
      </div>
    </section>
  </div>

  <script>
    const state = {
      activeTaskId: null,
      currentTask: null,
      activeSection: "authSection",
      taskPoller: null,
      cancellingTaskId: "",
      capabilities: [],
      webLoginReady: false,
      completionWatchTaskIds: new Set(),
      completionModalShownTaskIds: new Set(),
      completionModalTaskId: "",
      completionModalPreviousFocus: null,
      exportVerificationChallengeId: "",
      exportVerificationDismissedChallengeId: "",
      exportVerificationData: null,
      exportVerificationPreviousFocus: null,
    };
    const $ = (id) => document.getElementById(id);
    const taskStepLabels = {
      collect: "获取数据",
      import_metadata: "登记任务",
      import_files: "整理数据",
      analyze: "计算指标",
      report: "生成报告",
    };
    const pageExportTargetGroups = Object.freeze({
      product_list: ["product_list"],
      orders: ["orders"],
      fund_flows: ["fund_flows"],
      transactions: ["transactions"],
      product_data: ["product_core_conversion", "product_traffic_funnel", "product_detail"],
      compass_buyer_profile: ["compass_buyer_profile"],
    });

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok || payload.status === "error") {
        const requestError = new Error(payload.message || `${url} 请求失败`);
        requestError.data = payload.data;
        throw requestError;
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

    function setMobileNavigationOpen(open, restoreFocus = false) {
      document.body.classList.toggle("nav-open", open);
      $("mobileMenuButton")?.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open && restoreFocus) $("mobileMenuButton")?.focus();
    }

    function updateWorkflowState(targetId) {
      const sectionSteps = {
        authSection: 0,
        webOrderSection: state.webLoginReady ? 2 : 1,
        apiSyncSection: 2,
        localExportSection: 2,
        collectorSection: 2,
        capabilitySection: 2,
        statusSection: 3,
        recordsSection: 3,
      };
      const activeIndex = sectionSteps[targetId] ?? 0;
      document.querySelectorAll(".workflow-step").forEach((step, index) => {
        step.classList.toggle("active", index === activeIndex);
        step.classList.toggle("completed", index < activeIndex);
        step.setAttribute("aria-current", index === activeIndex ? "step" : "false");
      });
    }

    function selectMenuSection(targetId = "authSection") {
      const sections = document.querySelectorAll(".menu-section");
      const triggers = document.querySelectorAll(".menu-trigger");
      const target = $(targetId) ? targetId : "authSection";
      sections.forEach((section) => {
        section.classList.toggle("menu-hidden", section.id !== target);
      });
      triggers.forEach((trigger) => {
        const isActive = trigger.dataset.menuTarget === target;
        trigger.classList.toggle("active", isActive);
        trigger.setAttribute("aria-current", isActive ? "page" : "false");
      });
      state.activeSection = target;
      updateWorkflowState(target);
      setMobileNavigationOpen(false);
    }

    function navigateToMenuSection(targetId) {
      selectMenuSection(targetId);
      const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
      document.querySelector(".workspace-surface")?.scrollIntoView({ behavior, block: "start" });
    }

    async function loadConfig() {
      if (!$("configSummary")) return;
      setMessage("configMessage", "读取接口配置中...");
      try {
        const data = await fetchJson("/api-config");
        if ($("credentialId")) $("credentialId").value = data.values.app_id || data.values.shop_id || "";
        if ($("shopId")) $("shopId").value = data.values.shop_id || "";
        if ($("shopName")) $("shopName").value = data.values.shop_name || "";
        if ($("apiBaseUrl")) $("apiBaseUrl").value = data.values.api_base_url || "";
        if ($("rawArchiveDir")) $("rawArchiveDir").value = data.values.raw_archive_dir || "";
        if ($("syncDryRun")) $("syncDryRun").checked = Boolean(data.values.sync_dry_run);
        if ($("appSecret")) $("appSecret").value = "";
        if ($("accessToken")) $("accessToken").value = "";
        $("collectShopId").value = data.values.shop_id || "";
        $("collectShopName").value = data.values.shop_name || "";
        renderDefinitionList("shopInfoSummary", [
          ["店铺名称", data.values.shop_name || "未配置"],
          ["店铺 ID", data.values.shop_id || "未配置"],
        ]);

        statusChip("appSecretStatus", "接口密钥", data.secrets.app_secret.configured);
        statusChip("accessTokenStatus", "接口授权", data.secrets.access_token.configured);
        const tokenStatus = data.secrets.access_token || {};
        renderDefinitionList("configSummary", [
          ["微信小店 AppID", data.values.app_id || "未填写"],
          ["店铺名称", data.values.shop_name || "授权后自动获取"],
          ["店铺原始 ID", data.values.shop_id || "授权后自动获取"],
          ["授权状态", tokenStatus.configured ? "已获取" : "未获取"],
          ["有效期至", tokenStatus.expires_at || "未获取"],
        ]);
        setTokenResult(
          tokenStatus.configured
            ? `接口授权已保存；有效期至 ${tokenStatus.expires_at || "未读取"}。发起同步时系统会按需刷新授权。`
            : "尚未获取接口授权，请保存并连接微信小店。",
          tokenStatus.configured ? "ok" : ""
        );
        setMessage("configMessage", "接口配置已刷新。", "ok");
      } catch (error) {
        setMessage("configMessage", error.message, "error");
      }
    }

    function buildConnectPayload() {
      return {
        credential_id: $("credentialId").value,
        app_secret: $("appSecret").value,
      };
    }

    function setTokenResult(text, kind = "") {
      const el = $("tokenResult");
      if (!el) return;
      el.textContent = text;
      el.className = `result-box ${kind}`.trim();
    }

    async function saveAndFetchToken(event) {
      event?.preventDefault();
      const button = $("saveAndFetchToken");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "正在连接微信小店...";
      setMessage("configMessage", "正在保存 AppID 和密钥，并自动获取授权与店铺信息...");
      setTokenResult("正在连接微信小店...");
      try {
        const data = await fetchJson("/api-config/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildConnectPayload()),
        });
        $("appSecret").value = "";
        await loadConfig();
        const token = data.token || {};
        const shopInfo = token.shop_info || {};
        const expiresText = token.expires_at ? `授权有效期至 ${token.expires_at}` : "授权有效期未返回";
        const shopText = shopInfo.loaded
          ? `已自动识别店铺“${shopInfo.shop_name || "未命名店铺"}”`
          : shopInfo.message || "店铺信息暂未自动获取";
        setMessage(
          "configMessage",
          shopInfo.loaded
            ? "微信小店连接成功，其余配置已自动完成。"
            : "接口授权已获取，店铺信息暂未自动完成。",
          shopInfo.loaded ? "ok" : ""
        );
        setTokenResult(`${shopText}；${expiresText}。`, shopInfo.loaded ? "ok" : "");
      } catch (error) {
        setMessage("configMessage", error.message, "error");
        setTokenResult(error.message, "error");
        await loadConfig();
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = "保存并连接微信小店";
      }
    }

    function renderWebLoginStatus(data) {
      const verification = data.export_verification || {};
      statusChip("webLoginProcessStatus", "登录浏览器", Boolean(data.login_browser_ready));
      statusChip(
        "webLoginAuthStatus",
        verification.required ? "等待导出确认" : data.authenticated ? "后台已登录" : "后台待扫码",
        Boolean(data.authenticated) && !verification.required
      );
      statusChip("webLoginProfileStatus", "Profile", Boolean(data.profile_dir_exists));
      renderDefinitionList("webLoginStatusList", [
        ["登录状态", data.login_state_message || (data.authenticated ? "已登录" : "等待扫码")],
        ["导出验证", verification.required ? "本次导出等待扫码确认" : "当前无需确认"],
        ["登录浏览器", data.process_running ? `运行中 pid=${data.pid}` : data.login_browser_ready ? "已连接" : "未运行"],
        ["扫码页面", data.login_url],
      ]);
    }

    function setExportActionState(text, kind = "") {
      const chip = $("exportActionStatus");
      if (!chip) return;
      chip.textContent = text;
      chip.className = `chip ${kind === "ok" ? "ok" : kind === "error" ? "bad" : ""}`.trim();
    }

    async function loadWebLoginStatus(fresh = false) {
      if (!$("webLoginStatusList")) return;
      setMessage("webLoginMessage", "读取 /web-login/status 中...");
      try {
        const statusUrl = fresh ? "/web-login/status?fresh=1" : "/web-login/status";
        const data = await fetchJson(statusUrl, fresh ? { cache: "no-store" } : undefined);
        state.webLoginReady = Boolean(data.authenticated);
        renderWebLoginStatus(data);
        updateWorkflowState(state.activeSection);
        setMessage(
          "webLoginMessage",
          data.export_verification?.required
            ? "微信要求扫码确认本次导出，二维码已在任务页面弹出。"
            : data.authenticated
            ? "微信小店后台已登录，可以启动页面表格导出。"
            : data.login_state === "login_required"
              ? "当前仍是二维码页，请先用微信扫码并在手机上确认登录。"
              : data.login_state_message || "扫码登录状态已加载。",
          data.authenticated ? "ok" : "error"
        );
        if (!$("startOrderTask")?.disabled) {
          setExportActionState(data.authenticated ? "登录校验通过" : "需要扫码", data.authenticated ? "ok" : "error");
        }
      } catch (error) {
        setMessage("webLoginMessage", error.message, "error");
        if (!$("startOrderTask")?.disabled) {
          setExportActionState("校验失败", "error");
        }
      }
    }

    async function loadCapabilities() {
      setMessage("capabilityMessage", "读取数据范围中...");
      try {
        const capabilities = await fetchJson("/capabilities");
        state.capabilities = capabilities || [];
        renderCapabilities(state.capabilities);
        renderLocalExportTypeOptions(state.capabilities);
        const pageExportCount = state.capabilities.filter((item) => item.page_export_status === "verified").length;
        const importCount = state.capabilities.filter((item) => item.import_enabled).length;
        setMessage(
          "capabilityMessage",
          `共 ${state.capabilities.length} 类数据 · ${pageExportCount} 类可直接页面导出 · ${importCount} 类支持文件导入`,
          "ok"
        );
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
      const directExport = capabilities.filter((item) => item.page_export_status === "verified");
      const fileImport = capabilities.filter((item) => item.page_export_status !== "verified");
      const renderCard = (item, direct) => `
        <article class="capability-item ${direct ? "direct" : ""}">
          <div class="capability-title">
            <strong>${escapeHtml(item.label || item.export_type)}</strong>
            <span class="capability-state ${direct ? "ok" : ""}">${direct ? "已验证" : item.import_enabled ? "可导入" : "暂不可用"}</span>
          </div>
          <div class="capability-method">
            <span>获取方式</span>
            <strong>${direct ? item.import_enabled ? "页面导出 · 文件导入" : "页面导出" : item.import_enabled ? "文件导入" : "暂未开放"}</strong>
          </div>
          <p class="capability-notes">${escapeHtml(
            direct
              ? "已完成真实导出验证，任务结束后可直接下载原始文件。"
              : item.import_enabled
                ? "当前页面未发现可用导出按钮；如已有 Excel/CSV，可通过文件导入使用。"
                : "当前还没有可用的数据获取方式。"
          )}</p>
        </article>
      `;
      const renderGroup = (title, description, items, direct) => items.length ? `
        <section class="capability-group" aria-label="${escapeHtml(title)}">
          <div class="capability-group-head">
            <div class="capability-group-heading">
              <strong>${escapeHtml(title)}</strong>
              <span>${escapeHtml(description)}</span>
            </div>
            <span class="capability-count">${items.length} 类</span>
          </div>
          <div class="capability-list">${items.map((item) => renderCard(item, direct)).join("")}</div>
        </section>
      ` : "";
      el.innerHTML = [
        renderGroup("可直接页面导出", "扫码登录后由系统自动下载原始文件", directExport, true),
        renderGroup("通过文件导入", "页面暂无可用导出按钮时，上传已有 Excel / CSV", fileImport, false),
      ].join("");
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
      updateTaskProgress(100, "文件检查完成", "completed");
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
        uploaded: "已上传",
        importing: "导入中",
        completed: "已完成",
        success: "成功",
        partial: "部分完成",
        failed: "失败",
        error: "失败",
        cancelled: "已取消",
        unknown: "未知",
      };
      return labels[status] || status || "-";
    }

    function shortFileName(path) {
      return String(path || "").split(/[\\/]/).filter(Boolean).pop() || path || "-";
    }

    function renderTask(task) {
      state.currentTask = task || null;
      syncCancelTaskButton(task);
      if (!task) {
        $("taskStateChip").textContent = "暂无任务";
        $("taskStateChip").className = "chip";
        $("taskReportChip").textContent = "暂无报告";
        $("taskReportChip").className = "chip";
        $("taskSummary").innerHTML = "";
        renderTaskSteps({});
        renderTaskProgress(null);
        setTaskResult("暂无任务结果。");
        $("reportPreview").classList.add("hidden");
        $("reportPreview").textContent = "";
        return;
      }
      const stateText = task.state || task.status || "unknown";
      const isExportOnly = task.mode === "export_only" || task.result?.mode === "export_only";
      const exportOutcome = task.result?.outcome || "";
      const isOk = stateText === "completed" && (!isExportOnly || exportOutcome === "exported");
      const isBad = stateText === "failed";
      const stateLabel = isExportOnly && stateText === "completed"
        ? exportOutcome === "no_files"
          ? "完成（无文件）"
          : exportOutcome === "partial"
            ? "完成（部分失败）"
            : "完成"
        : statusLabel(stateText);
      $("taskStateChip").textContent = `任务 ${stateLabel}`;
      $("taskStateChip").className = `chip ${isOk ? "ok" : isBad ? "bad" : ""}`.trim();

      const reportId = task.result?.report?.report_id || "";
      $("taskReportChip").textContent = isExportOnly ? "仅导出，不分析" : reportId ? `报告 ${reportId}` : "暂无报告";
      $("taskReportChip").className = `chip ${(isExportOnly && exportOutcome === "exported") || reportId ? "ok" : isBad ? "bad" : ""}`.trim();
      renderDefinitionList("taskSummary", [
        ["任务", task.task_name || task.source_type || "-"],
        ["店铺", task.shop_name_snapshot || task.shop_id || "-"],
        [isExportOnly ? "归档日期（页面未自动筛选）" : "周期", `${task.date_range?.from || "-"} 至 ${task.date_range?.to || "-"}`],
        ["状态", statusLabel(stateText)],
        ["执行时间", task.started_at || task.created_at || "-"],
        [stateText === "cancelled" ? "取消时间" : "完成时间", task.cancelled_at || task.completed_at || task.finished_at || "-"],
        [isExportOnly ? "原始文件" : "报告", isExportOnly ? `${task.result?.artifact_count ?? 0} 个` : reportId || "-"],
      ]);
      renderTaskSteps(taskStepsForDisplay(task));
      renderTaskProgress(task);

      if (stateText === "cancelled") {
        renderCancelledTaskResult(task);
        return;
      }

      if (task.result?.mode === "export_only") {
        renderExportOnlyResult(task, task.result);
        return;
      }
      if (task.error?.message) {
        setTaskResult(conciseTaskErrorMessage(task.error.message), "error");
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
        setTaskResult(
          isExportOnly
            ? "正在逐页查找可导出的原始表格，请保持扫码登录页运行。"
            : task.source_type === "local_export"
              ? "文件正在导入、分析并生成报告。"
              : "任务正在执行，请保持登录状态有效。"
        );
      }
    }

    function conciseTaskErrorMessage(value) {
      const text = String(value || "").trim();
      if (/LOGIN_REQUIRED|仍停留在扫码[/]登录页|仍停留在二维码页|需要扫码登录/.test(text)) {
        return "微信小店后台尚未登录。请先进入“扫码登录”，用微信扫码并确认进入小店后台，再重新启动页面表格导出。";
      }
      if (text.length <= 500) return text;
      const structuredMessage = [...text.matchAll(/\"message\":\"([^\"]+)\"/g)].at(-1)?.[1];
      return structuredMessage || `${text.slice(0, 500)}...`;
    }

    function taskStepsForDisplay(task) {
      const steps = Object.fromEntries(
        Object.entries(task?.steps || {}).map(([key, value]) => [key, { ...(value || {}) }])
      );
      const isExportOnlyFailure = (task?.mode === "export_only" || task?.result?.mode === "export_only")
        && (task?.state === "failed" || task?.status === "failed");
      if (isExportOnlyFailure) {
        ["import_metadata", "import_files", "analyze", "report"].forEach((key) => {
          if (!steps[key] || steps[key].status === "pending") {
            steps[key] = {
              ...(steps[key] || {}),
              status: "skipped",
              error: "仅导出任务在获取数据前置检查阶段结束，后续步骤不会执行。",
            };
          }
        });
      }
      return steps;
    }

    function exportArtifactFileName(artifact, fallbackIndex) {
      const value = artifact?.original_filename
        || artifact?.filename
        || artifact?.relative_path
        || artifact?.saved_path
        || `导出文件 ${fallbackIndex + 1}`;
      return String(value).split(/[\\/]/).filter(Boolean).at(-1) || `导出文件 ${fallbackIndex + 1}`;
    }

    function formatFileSize(sizeBytes) {
      const bytes = Number(sizeBytes);
      if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function dedupeExportArtifacts(artifacts) {
      const seen = new Set();
      return (Array.isArray(artifacts) ? artifacts : []).map((artifact, originalIndex) => ({
        artifact: artifact || {},
        originalIndex,
      })).filter(({ artifact }) => {
        const sha256 = String(artifact.sha256 || "").trim().toLowerCase();
        const sizeBytes = Number(artifact.size_bytes);
        if (!sha256 || !Number.isFinite(sizeBytes)) return true;
        const key = `${sha256}:${sizeBytes}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }

    function isExportBundleArtifact(artifact) {
      return String(artifact?.source_kind || artifact?.source_type || "").toLowerCase() === "export_bundle";
    }

    function isExportOnlyTask(task) {
      return task?.mode === "export_only"
        || task?.result?.mode === "export_only"
        || task?.spec?.mode === "export_only";
    }

    function isTerminalTask(task) {
      return ["completed", "failed", "cancelled"].includes(task?.state || task?.status);
    }

    function syncModalOpenState() {
      const completionOpen = $("exportCompletionModal") && !$("exportCompletionModal").hidden;
      const verificationOpen = $("exportVerificationModal") && !$("exportVerificationModal").hidden;
      document.body.classList.toggle("modal-open", Boolean(completionOpen || verificationOpen));
    }

    function closeExportCompletionModal() {
      const modal = $("exportCompletionModal");
      if (!modal || modal.hidden) return;
      modal.hidden = true;
      syncModalOpenState();
      const previousFocus = state.completionModalPreviousFocus;
      state.completionModalPreviousFocus = null;
      if (previousFocus && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
    }

    function closeExportVerificationModal({ dismissed = false } = {}) {
      const modal = $("exportVerificationModal");
      if (!modal || modal.hidden) return;
      if (dismissed && state.exportVerificationChallengeId) {
        state.exportVerificationDismissedChallengeId = state.exportVerificationChallengeId;
      }
      modal.hidden = true;
      syncModalOpenState();
      const previousFocus = state.exportVerificationPreviousFocus;
      state.exportVerificationPreviousFocus = null;
      if (previousFocus && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
    }

    function refreshExportVerificationImage(verification = state.exportVerificationData) {
      const image = $("exportVerificationImage");
      const pending = $("exportVerificationImagePending");
      if (!image || !pending || !verification) return;
      if (!verification.screenshot_ready || !verification.challenge_id) {
        image.hidden = true;
        image.removeAttribute("src");
        pending.hidden = false;
        return;
      }
      const screenshotUrl = verification.screenshot_url || "/web-login/export-verification-screenshot";
      const separator = screenshotUrl.includes("?") ? "&" : "?";
      image.src = `${screenshotUrl}${separator}challenge_id=${encodeURIComponent(verification.challenge_id)}&t=${Date.now()}`;
      image.hidden = false;
      pending.hidden = true;
    }

    function showExportVerificationModal(verification) {
      const modal = $("exportVerificationModal");
      if (!modal || !verification?.challenge_id) return;
      state.exportVerificationData = verification;
      state.exportVerificationChallengeId = verification.challenge_id;
      const targetLabel = String(verification.target_label || "").trim() || "当前数据表";
      const controlLabel = String(verification.control_label || "").trim();
      $("exportVerificationTitle").textContent = `需要扫码确认：${targetLabel}`;
      $("exportVerificationTarget").textContent = targetLabel;
      $("exportVerificationControl").textContent = controlLabel ? `触发操作：${controlLabel}` : "";
      $("exportVerificationControl").hidden = !controlLabel;
      $("exportVerificationMessage").textContent = `请完成“${targetLabel}”的扫码确认；验证通过后任务会自动继续，后续模块可能再次要求验证。`;
      const remaining = Number(verification.remaining_seconds);
      $("exportVerificationStatus").textContent = Number.isFinite(remaining)
        ? `二维码有效时间约剩 ${Math.max(0, remaining)} 秒，扫码完成后任务会自动继续。`
        : "二维码正在实时刷新，扫码完成后任务会自动继续。";
      refreshExportVerificationImage(verification);
      if (modal.hidden) {
        state.exportVerificationPreviousFocus = document.activeElement;
        modal.hidden = false;
        syncModalOpenState();
        window.requestAnimationFrame(() => $("exportVerificationCard")?.focus());
      }
    }

    async function pollExportVerificationStatus({ forceClose = false } = {}) {
      if (forceClose) {
        closeExportVerificationModal();
        state.exportVerificationChallengeId = "";
        state.exportVerificationDismissedChallengeId = "";
        state.exportVerificationData = null;
        return;
      }
      try {
        const verification = await fetchJson("/web-login/export-verification-status", { cache: "no-store" });
        if (!verification?.required || !verification.challenge_id) {
          closeExportVerificationModal();
          state.exportVerificationChallengeId = "";
          state.exportVerificationDismissedChallengeId = "";
          state.exportVerificationData = verification || null;
          return;
        }
        const isNewChallenge = verification.challenge_id !== state.exportVerificationChallengeId;
        if (isNewChallenge) {
          state.exportVerificationChallengeId = verification.challenge_id;
          state.exportVerificationDismissedChallengeId = "";
        }
        state.exportVerificationData = verification;
        if (state.exportVerificationDismissedChallengeId !== verification.challenge_id) {
          showExportVerificationModal(verification);
        }
      } catch (_error) {
        // Task polling remains authoritative; a temporary QR status failure
        // must not stop the export task or replace its real error message.
      }
    }

    function exportCompletionPresentation(task) {
      const result = task?.result || {};
      const pages = Array.isArray(result.pages) ? result.pages : [];
      const allArtifacts = dedupeExportArtifacts(result.artifacts || []);
      const bundleArtifact = allArtifacts.find(({ artifact }) => isExportBundleArtifact(artifact)) || null;
      const uniqueArtifacts = allArtifacts.filter(({ artifact }) => !isExportBundleArtifact(artifact));
      const recordedFileCount = Number(result.artifact_count || 0);
      const fileCount = uniqueArtifacts.length;
      const artifactListMissing = Number.isFinite(recordedFileCount) && recordedFileCount > 0 && fileCount === 0;
      const pageFailureCount = pages.filter((page) => (page?.status || "") === "failed").length;
      const recordedFailureCount = Number(result.failed_count || 0);
      const failureCount = Math.max(pageFailureCount, Number.isFinite(recordedFailureCount) ? recordedFailureCount : 0);
      const skippedCount = Number(result.skipped_count || pages.filter((page) => page?.status === "skipped").length || 0);
      const stateText = task?.state || task?.status || "unknown";
      const hasFiles = fileCount > 0;
      const hasFailures = stateText === "failed" || failureCount > 0;
      let tone = "success";
      let title = "导出完成";
      let subtitle = "文件已经生成，可以立即下载。";
      let message = `本次共生成 ${fileCount} 个可下载文件。`;
      let warning = "";

      if (hasFiles && hasFailures) {
        tone = "partial";
        title = "部分导出完成";
        subtitle = "已有文件可下载，部分页面需要处理。";
        message = `已生成 ${fileCount} 个文件，另有 ${failureCount} 个页面导出失败。`;
        warning = "失败原因和逐页结果已保留，可进入任务详情查看。";
      } else if (!hasFiles && hasFailures) {
        tone = "danger";
        title = "导出失败";
        subtitle = "本次没有生成可下载文件。";
        message = conciseTaskErrorMessage(task?.error?.message || "导出任务未生成文件，请查看任务详情中的失败原因。");
        warning = "0 个文件不计为成功；请根据失败页面原因重新操作。";
      } else if (!hasFiles) {
        tone = "warning";
        title = "导出结束，但没有文件";
        subtitle = "任务已结束，页面未生成可下载文件。";
        message = artifactListMissing
          ? `任务记录显示生成了 ${recordedFileCount} 个文件，但可下载清单缺失。`
          : skippedCount > 0
            ? `${skippedCount} 个页面没有发现可用的导出控件。`
            : "本次任务没有返回可下载文件。";
        warning = artifactListMissing
          ? "请刷新任务；若仍未显示，需要检查任务 metadata。"
          : "请进入任务详情查看逐页跳过原因，这不会显示为导出成功。";
      } else if (skippedCount > 0) {
        message = `已生成 ${fileCount} 个文件；另有 ${skippedCount} 个页面没有可用导出项。`;
      }

      return {
        tone,
        title,
        subtitle,
        message,
        warning,
        fileCount,
        failureCount,
        uniqueArtifacts,
        bundleArtifact,
      };
    }

    function showExportCompletionModal(task) {
      const modal = $("exportCompletionModal");
      const taskId = taskIdentity(task);
      if (!modal || !taskId) return;
      const presentation = exportCompletionPresentation(task);
      const card = $("exportCompletionCard");
      card.className = `completion-modal-card ${presentation.tone}`;
      $("exportCompletionIcon").innerHTML = ["danger", "warning"].includes(presentation.tone)
        ? '<svg viewBox="0 0 24 24"><path d="M12 7v6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="17" r="1.2" fill="currentColor"/></svg>'
        : '<svg viewBox="0 0 24 24"><path d="M7 12.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      $("exportCompletionTitle").textContent = presentation.title;
      $("exportCompletionSubtitle").textContent = presentation.subtitle;
      $("exportCompletionFileCount").textContent = String(presentation.fileCount);
      $("exportCompletionFailureCount").textContent = String(presentation.failureCount);
      $("exportCompletionMessage").textContent = presentation.message;

      const warning = $("exportCompletionWarning");
      warning.textContent = presentation.warning;
      warning.hidden = !presentation.warning;

      const download = $("exportCompletionDownload");
      const firstArtifact = presentation.bundleArtifact || presentation.uniqueArtifacts[0];
      if (firstArtifact) {
        download.href = `/tasks/${encodeURIComponent(taskId)}/artifacts/${firstArtifact.originalIndex}/download`;
        download.setAttribute("download", exportArtifactFileName(firstArtifact.artifact, firstArtifact.originalIndex));
        download.textContent = presentation.bundleArtifact ? "一键下载全部 ZIP" : "立即下载文件";
        download.hidden = false;
      } else {
        download.removeAttribute("href");
        download.hidden = true;
      }

      state.completionModalTaskId = taskId;
      state.completionModalPreviousFocus = document.activeElement;
      modal.hidden = false;
      syncModalOpenState();
      window.requestAnimationFrame(() => {
        (firstArtifact ? download : $("exportCompletionDetails"))?.focus();
      });
    }

    function maybeShowExportCompletion(task) {
      const taskId = taskIdentity(task);
      if (!taskId || !isTerminalTask(task) || !isExportOnlyTask(task)) return;
      if ((task?.state || task?.status) === "cancelled") return;
      if (!state.completionWatchTaskIds.has(taskId) || state.completionModalShownTaskIds.has(taskId)) return;
      state.completionWatchTaskIds.delete(taskId);
      state.completionModalShownTaskIds.add(taskId);
      showExportCompletionModal(task);
    }

    function renderExportArtifactDownloads(task, artifacts) {
      const taskId = taskIdentity(task);
      const allArtifacts = dedupeExportArtifacts(artifacts);
      const bundleArtifact = allArtifacts.find(({ artifact }) => isExportBundleArtifact(artifact)) || null;
      const uniqueArtifacts = allArtifacts.filter(({ artifact }) => !isExportBundleArtifact(artifact));
      if (!taskId || (uniqueArtifacts.length === 0 && !bundleArtifact)) return "";
      const links = uniqueArtifacts.map(({ artifact, originalIndex }) => {
        const name = exportArtifactFileName(artifact, originalIndex);
        const moduleLabel = artifact.page_label || artifact.page_name || artifact.module || artifact.export_type || "页面导出";
        const href = `/tasks/${encodeURIComponent(taskId)}/artifacts/${originalIndex}/download`;
        return `
          <div class="button-row">
            <a class="button-link" href="${href}" download>下载 ${escapeHtml(name)}</a>
            <span class="hint">${escapeHtml(String(moduleLabel))} · ${escapeHtml(formatFileSize(artifact.size_bytes))}</span>
          </div>
        `;
      }).join("");
      const rawIndividualCount = (Array.isArray(artifacts) ? artifacts : [])
        .filter((artifact) => !isExportBundleArtifact(artifact)).length;
      const duplicateCount = Math.max(0, rawIndividualCount - uniqueArtifacts.length);
      const bundleDownload = bundleArtifact ? `
        <div class="bundle-download">
          <div class="bundle-download-copy">
            <strong>全部文件已整理为 1 个压缩包</strong>
            <span class="hint">${escapeHtml(exportArtifactFileName(bundleArtifact.artifact, bundleArtifact.originalIndex))} · ${escapeHtml(formatFileSize(bundleArtifact.artifact.size_bytes))}</span>
          </div>
          <a class="button-link primary" href="/tasks/${encodeURIComponent(taskId)}/artifacts/${bundleArtifact.originalIndex}/download" download>一键下载全部 ZIP</a>
        </div>
      ` : "";
      const individualDownloads = uniqueArtifacts.length ? `
        <details class="export-file-details" ${bundleArtifact ? "" : "open"}>
          <summary>查看单个文件（${uniqueArtifacts.length} 个）</summary>
          <div class="export-file-details-body">${links}</div>
        </details>
      ` : "";
      return `
        ${bundleDownload}
        ${individualDownloads}
        ${duplicateCount ? `<span class="hint">已自动隐藏 ${duplicateCount} 个完全重复文件。</span>` : ""}
      `;
    }

    function renderExportOnlyResult(task, result) {
      const pages = Array.isArray(result.pages) ? result.pages : [];
      const artifacts = Array.isArray(result.artifacts) ? result.artifacts : [];
      const uniqueArtifacts = dedupeExportArtifacts(artifacts)
        .filter(({ artifact }) => !isExportBundleArtifact(artifact));
      const taskErrorNotice = task?.error?.message
        ? `<div class="notice warn"><strong>任务执行中出现错误</strong><p>${escapeHtml(conciseTaskErrorMessage(task.error.message))}</p></div>`
        : "";
      const rows = pages.map((item) => {
        const status = item.status || "unknown";
        const pageName = item.page_name || item.label || item.menu_path || item.type || item.target || "未命名页面";
        const files = item.exported_filenames || item.files || item.downloads || [];
        const fileText = Array.isArray(files)
          ? files.map((file) => typeof file === "string" ? file : file?.filename || file?.saved_path || "").filter(Boolean).join("、")
          : String(files || "");
        const detailedFailure = (Array.isArray(item.failures) ? item.failures : [])
          .map((failure) => failure?.error?.message || failure?.message || "")
          .filter(Boolean)
          .join("；");
        const reason = detailedFailure || item.reason || item.failure_reason || item.skipped_reason || item.message || item.error?.message || item.error || "";
        return `<tr><td>${escapeHtml(pageName)}</td><td>${escapeHtml(statusLabel(status))}</td><td>${escapeHtml(fileText || "-")}</td><td>${escapeHtml(reason || "-")}</td></tr>`;
      }).join("");
      const downloadSection = renderExportArtifactDownloads(task, artifacts);
      const missingArtifactNotice = !downloadSection && Number(result.artifact_count || 0) > 0
        ? '<div class="notice"><strong>任务记录显示已有文件，但可下载清单缺失</strong><p>请刷新任务；若仍未显示，需要检查任务 metadata。</p></div>'
        : !downloadSection
          ? '<div class="notice"><strong>本次没有生成可下载文件</strong><p>请查看下方跳过和失败原因。</p></div>'
          : "";
      setTaskResultHtml(`
        <div class="check-summary">
          <div><strong>页面表格导出已结束</strong> · 可下载文件 ${escapeHtml(String(uniqueArtifacts.length))} 个</div>
          <div class="metric-grid">
            ${renderMetricCards({
              成功: result.success_count ?? 0,
              跳过: result.skipped_count ?? 0,
              失败: result.failed_count ?? 0,
              可下载: uniqueArtifacts.length,
            })}
          </div>
          <p>${escapeHtml(result.notice || "未执行入库、分析和报告。")}</p>
          ${result.bundle_error?.message ? `<div class="notice warn"><strong>压缩包生成失败</strong><p>${escapeHtml(result.bundle_error.message)}</p></div>` : ""}
          ${taskErrorNotice}
          ${downloadSection || missingArtifactNotice}
          ${rows ? `<div class="table-wrap"><table><thead><tr><th>页面</th><th>状态</th><th>文件</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">未返回页面级清单，请查看任务 metadata。</div>'}
        </div>
      `, (task?.state || task?.status) === "failed" && uniqueArtifacts.length === 0 ? "error" : result.failed_count > 0 || uniqueArtifacts.length === 0 ? "" : "ok");
    }

    function renderCancelledTaskResult(task) {
      const result = task?.result || {};
      const artifacts = Array.isArray(result.artifacts) ? result.artifacts : [];
      const downloadSection = isExportOnlyTask(task) ? renderExportArtifactDownloads(task, artifacts) : "";
      setTaskResultHtml(`
        <div class="check-summary">
          <div><strong>任务已取消</strong></div>
          <p>${escapeHtml(result.notice || "任务已由用户取消；未执行的后续步骤已停止。")}</p>
          ${downloadSection || '<div class="notice"><strong>没有可下载文件</strong><p>取消前尚未登记可下载文件；采集日志仍会按保留策略保存。</p></div>'}
        </div>
      `);
    }

    function renderTaskSteps(steps) {
      $("taskSteps").innerHTML = Object.entries(taskStepLabels).map(([key, label]) => {
        const step = steps[key] || {};
        const status = step.status || "pending";
        const detail = step.error ? conciseTaskErrorMessage(step.error) : step.completed_at || step.started_at || "";
        return `<div class="task-step ${escapeHtml(status)}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(statusLabel(status))}${detail ? ` · ${escapeHtml(String(detail))}` : ""}</span></div>`;
      }).join("");
    }

    function taskProgressValue(task) {
      if (!task) {
        return { percent: 0, label: "暂无进度", state: "" };
      }
      const stateText = task.state || task.status || "unknown";
      const steps = taskStepsForDisplay(task);
      const entries = Object.entries(taskStepLabels);
      if (stateText === "completed") {
        return { percent: 100, label: "任务已完成", state: "completed" };
      }
      let units = 0;
      let activeLabel = "";
      entries.forEach(([key, label], index) => {
        const status = steps[key]?.status || "";
        if (["completed", "skipped"].includes(status)) {
          units = Math.max(units, index + 1);
        } else if (["running", "importing", "uploaded", "queued", "failed", "cancelled"].includes(status)) {
          units = Math.max(units, index + 0.5);
          activeLabel = activeLabel || label;
        }
      });
      if (stateText === "cancelled") {
        return { percent: 100, label: "任务已取消", state: "cancelled" };
      }
      if (stateText === "failed") {
        return {
          percent: Math.max(8, Math.min(100, Math.round((units / entries.length) * 100))),
          label: `任务失败${activeLabel ? `：${activeLabel}` : ""}`,
          state: "failed",
        };
      }
      if (["queued", "pending"].includes(stateText)) {
        return { percent: Math.max(5, Math.round((units / entries.length) * 100)), label: "任务排队中", state: stateText };
      }
      const percent = Math.max(8, Math.min(98, Math.round((units / entries.length) * 100) || 8));
      return { percent, label: activeLabel ? `正在${activeLabel}` : "任务处理中", state: stateText };
    }

    function renderTaskProgress(task) {
      const progress = taskProgressValue(task);
      updateTaskProgress(progress.percent, progress.label, progress.state);
    }

    function updateTaskProgress(percent, label, stateText = "") {
      const fill = $("taskProgressBar");
      const safePercent = Math.max(0, Math.min(100, Number(percent || 0)));
      $("taskProgressLabel").textContent = label || "暂无进度";
      $("taskProgressPercent").textContent = `${Math.round(safePercent)}%`;
      $("taskProgress").setAttribute("aria-valuenow", String(Math.round(safePercent)));
      fill.style.width = `${safePercent}%`;
      fill.className = `progress-fill ${stateText === "failed" ? "failed" : stateText === "completed" ? "completed" : stateText === "cancelled" ? "cancelled" : ""}`.trim();
    }

    function taskIdentity(task) {
      return String(task?.id || task?.task_id || task?.collection_task_id || "");
    }

    function syncCancelTaskButton(task) {
      const button = $("cancelActiveTask");
      if (!button) return;
      const taskId = taskIdentity(task);
      const stateText = task?.state || task?.status || "";
      const canCancel = Boolean(
        taskId
        && isExportOnlyTask(task)
        && task?.can_cancel === true
        && ["queued", "running"].includes(stateText)
      );
      button.hidden = !canCancel;
      button.dataset.taskId = canCancel ? taskId : "";
      const isCancelling = canCancel && state.cancellingTaskId === taskId;
      button.disabled = isCancelling;
      button.toggleAttribute("aria-busy", isCancelling);
      button.textContent = isCancelling ? "正在取消..." : "取消任务";
    }

    async function cancelActiveTask() {
      const task = state.currentTask;
      const taskId = taskIdentity(task);
      const canCancel = Boolean(
        taskId
        && isExportOnlyTask(task)
        && task?.can_cancel === true
        && ["queued", "running"].includes(task?.state || task?.status)
      );
      if (!canCancel) {
        syncCancelTaskButton(task);
        setMessage("taskMessage", "当前任务已经结束，无法取消。", "error");
        return;
      }
      if (!window.confirm("确定取消当前导出任务吗？取消前已经生成的文件会保留。")) return;

      state.cancellingTaskId = taskId;
      syncCancelTaskButton(task);
      setMessage("taskMessage", "正在停止当前导出进程，请稍候...");
      try {
        const cancelled = await fetchJson(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
          method: "POST",
        });
        state.completionWatchTaskIds.delete(taskId);
        await pollExportVerificationStatus({ forceClose: true });
        if (state.taskPoller) {
          clearInterval(state.taskPoller);
          state.taskPoller = null;
        }
        state.activeTaskId = taskId;
        renderTask(cancelled);
        await loadTasks();
        setExportActionState("任务已取消");
        setMessage("taskMessage", "导出任务已取消；取消前已经生成并登记的文件仍可下载。", "ok");
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
        const refreshed = await loadTaskDetail(taskId);
        if (taskIsLive(refreshed) && !state.taskPoller) {
          startTaskPolling(taskId);
        }
      } finally {
        state.cancellingTaskId = "";
        syncCancelTaskButton(state.currentTask);
      }
    }

    function taskDateRangeText(task) {
      const from = task?.date_range?.from || task?.date_from || task?.params?.from || "-";
      const to = task?.date_range?.to || task?.date_to || task?.params?.to || "-";
      return `${from} 至 ${to}`;
    }

    function taskExecutionTimeText(task) {
      const startedAt = task?.started_at || task?.created_at || "-";
      const endedAt = task?.completed_at || task?.finished_at || "";
      return endedAt && endedAt !== startedAt ? `${startedAt} 至 ${endedAt}` : startedAt;
    }

    function taskSourceLabel(sourceType) {
      const labels = {
        web_export: "页面表格导出",
        local_export: "文件导入",
        api_sync: "数据同步",
        manual: "手动任务",
      };
      return labels[sourceType] || sourceType || "任务";
    }

    function findTaskById(tasks, taskId) {
      const target = String(taskId || "");
      return (tasks || []).find((task) => taskIdentity(task) === target);
    }

    function taskIsLive(task) {
      return ["queued", "pending", "running"].includes(task?.state || task?.status);
    }

    function existingTaskIdFromError(message) {
      const text = String(message || "");
      const marker = text.includes("已有任务正在运行：") ? "已有任务正在运行：" : text.includes("已有任务正在运行:") ? "已有任务正在运行:" : "";
      if (!marker) return "";
      const rest = text.slice(text.indexOf(marker) + marker.length).trim();
      const end = rest.indexOf("。");
      return (end >= 0 ? rest.slice(0, end) : rest).trim();
    }

    async function openExistingTaskProgress(taskId, fallbackMessage = "已有任务正在运行，已切换到任务进度。") {
      if (!taskId) return false;
      state.activeTaskId = taskId;
      selectMenuSection("statusSection");
      const task = await loadTaskDetail(taskId);
      setMessage("taskMessage", fallbackMessage, "ok");
      if (taskIsLive(task)) {
        startTaskPolling(taskId);
      }
      return Boolean(task);
    }

    function renderTaskHistory(tasks) {
      const el = $("taskHistoryPanel");
      if (!el) return;
      const visibleTasks = (tasks || []).slice(0, 20);
      if (!visibleTasks.length) {
        el.innerHTML = '<div class="empty">暂无导出任务</div>';
        return;
      }
      const rows = visibleTasks.map((task) => {
        const id = taskIdentity(task);
        const isCurrent = state.activeTaskId && id === String(state.activeTaskId);
        return `
          <tr class="${isCurrent ? "task-history-current" : ""}">
            <td>${escapeHtml(task.task_name || taskSourceLabel(task.source_type))}</td>
            <td>${escapeHtml(statusLabel(task.state || task.status || "unknown"))}</td>
            <td>${escapeHtml(task.shop_name_snapshot || task.shop_name || task.shop_id || "-")}</td>
            <td>${escapeHtml(taskDateRangeText(task))}</td>
            <td>${escapeHtml(taskExecutionTimeText(task))}</td>
            <td><button type="button" class="tab task-open" data-task-id="${escapeHtml(id)}">查看</button></td>
          </tr>
        `;
      }).join("");
      el.innerHTML = `<table><thead><tr><th>任务</th><th>状态</th><th>店铺</th><th>周期</th><th>执行时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`;
      document.querySelectorAll(".task-open").forEach((button) => {
        button.addEventListener("click", async () => {
          const taskId = button.dataset.taskId;
          const task = findTaskById(visibleTasks, taskId);
          state.activeTaskId = taskId;
          if (task) {
            renderTask(task);
          }
          selectMenuSection("statusSection");
          const detail = await loadTaskDetail(taskId);
          const stateText = detail?.state || detail?.status || task?.state || task?.status;
          if (["queued", "pending", "running"].includes(stateText)) {
            startTaskPolling(taskId);
          }
        });
      });
    }

    function setDefaultDates() {
      const now = new Date();
      const end = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      const start = new Date(end.getTime() - 6 * 24 * 60 * 60 * 1000);
      $("collectFrom").value = $("collectFrom").value || start.toISOString().slice(0, 10);
      $("collectTo").value = $("collectTo").value || end.toISOString().slice(0, 10);
    }

    function resetOrderTaskButton(button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = "校验登录并启动导出";
    }

    function selectedPageExportSelection(form) {
      const selectedGroups = [...new FormData(form).getAll("page_export_module")]
        .map((value) => String(value || "").trim())
        .filter((group) => Object.hasOwn(pageExportTargetGroups, group));
      const groups = [...new Set(selectedGroups)];
      const targets = [...new Set(groups.flatMap((group) => pageExportTargetGroups[group]))];
      return { groups, targets };
    }

    function selectedPageExportTargets() {
      return selectedPageExportSelection($("orderTaskForm")).targets;
    }

    async function startOrderTask(event) {
      event.preventDefault();
      const form = event.currentTarget || $("orderTaskForm");
      const button = $("startOrderTask");
      const selection = selectedPageExportSelection(form);
      const targets = selection.targets;
      if (!targets.length) {
        setExportActionState("请选择模块", "error");
        setMessage("exportActionMessage", "请至少选择一个页面导出模块。", "error");
        setMessage("taskMessage", "请至少选择一个页面导出模块。", "error");
        return;
      }
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "正在校验登录状态...";
      setExportActionState("正在校验");
      setMessage("exportActionMessage", "正在实时校验微信小店后台登录状态；校验通过后系统才会创建导出任务...");
      setMessage("taskMessage", "正在确认微信小店后台登录状态...");
      const payload = {
        shop_id: $("collectShopId").value,
        task_name: "微信小店页面表格导出",
        source_type: "web_export",
        params: {
          mode: "export_only",
          shop_name: $("collectShopName").value,
          from: $("collectFrom").value,
          to: $("collectTo").value,
          types: ["visible_tables"],
          target_groups: selection.groups,
          targets,
        },
      };
      try {
        const task = await fetchJson("/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        button.textContent = "导出任务已创建";
        state.activeTaskId = task.id || task.task_id;
        renderTask(task);
        setExportActionState("任务已启动", "ok");
        setMessage("exportActionMessage", `导出任务已创建：${state.activeTaskId}，正在进入任务进度。`, "ok");
        selectMenuSection("statusSection");
        setMessage("taskMessage", "页面表格导出已启动，正在更新进度。", "ok");
        startTaskPolling(state.activeTaskId);
      } catch (error) {
        const loginStatus = error.data;
        if (loginStatus?.login_state || loginStatus?.scan_required) {
          renderWebLoginStatus(loginStatus);
          const message = loginStatus.login_state_message || error.message || "微信小店后台尚未登录。";
          setExportActionState("需要扫码", "error");
          setMessage("webLoginMessage", message, "error");
          setMessage("exportActionMessage", `${message} 未创建导出任务，请点击“去扫码登录”。`, "error");
          setMessage("taskMessage", message, "error");
          selectMenuSection("webOrderSection");
          return;
        }
        const activeTaskId = existingTaskIdFromError(error.message);
        if (activeTaskId) {
          await openExistingTaskProgress(activeTaskId, "已有页面导出任务正在运行，已切换到任务进度。");
          return;
        }
        setExportActionState("启动失败", "error");
        setMessage("exportActionMessage", `${error.message} 未创建新的导出任务。`, "error");
        setMessage("taskMessage", error.message, "error");
      } finally {
        resetOrderTaskButton(button);
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

    function validateSyncRange() {
      const dateFrom = $("collectFrom").value;
      const dateTo = $("collectTo").value;
      if (!dateFrom || !dateTo) {
        setMessage("taskMessage", "请选择开始日期和结束日期。", "error");
        (!dateFrom ? $("collectFrom") : $("collectTo")).focus();
        return null;
      }
      if (dateFrom > dateTo) {
        setMessage("taskMessage", "开始日期不能晚于结束日期。", "error");
        $("collectFrom").focus();
        return null;
      }
      return { date_from: dateFrom, date_to: dateTo };
    }

    function resetCurrentApiSyncView(payload) {
      $("taskStateChip").textContent = "任务 处理中";
      $("taskStateChip").className = "chip";
      $("taskReportChip").textContent = payload.generate_report ? "本次报告生成中" : "本次不生成报告";
      $("taskReportChip").className = "chip";
      renderDefinitionList("taskSummary", [
        ["任务", "数据同步"],
        ["店铺", payload.shop_name || payload.shop_id || "-"],
        ["周期", `${payload.date_from} 至 ${payload.date_to}`],
        ["状态", "处理中"],
        ["报告", payload.generate_report ? "生成中" : "未选择生成"],
      ]);
      renderTaskSteps(Object.fromEntries(Object.keys(taskStepLabels).map((key, index) => [key, { status: index === 0 ? "running" : "pending" }])));
      updateTaskProgress(10, "正在获取数据", "running");
      setTaskResult("正在按当前日期范围同步数据。");
      $("reportPreview").classList.add("hidden");
      $("reportPreview").textContent = "";
      $("reportDetail").classList.add("hidden");
      $("reportDetail").textContent = "";
      $("reportsPanel").innerHTML = `<div class="empty">${payload.generate_report ? "本次报告生成后会自动显示。" : "本次未选择生成报告，报告中心仍保留历史报告。"}</div>`;
    }

    function markDateRangeChanged() {
      const dateFrom = $("collectFrom").value;
      const dateTo = $("collectTo").value;
      if (!dateFrom || !dateTo) return;
      $("taskStateChip").textContent = "待同步";
      $("taskStateChip").className = "chip";
      $("taskReportChip").textContent = "待生成本次报告";
      $("taskReportChip").className = "chip";
      renderDefinitionList("taskSummary", [
        ["任务", "数据同步"],
        ["店铺", $("collectShopName").value || $("collectShopId").value || "-"],
        ["周期", `${dateFrom} 至 ${dateTo}`],
        ["状态", "待同步"],
        ["报告", "-"],
      ]);
      renderTaskSteps({});
      updateTaskProgress(0, "待同步", "");
      setTaskResult("已调整同步周期，点击开始数据同步。");
      $("reportPreview").classList.add("hidden");
    }

    async function startApiSyncTask(event) {
      event.preventDefault();
      const range = validateSyncRange();
      if (!range) return;
      const payload = {
        shop_id: $("collectShopId").value,
        shop_name: $("collectShopName").value,
        ...range,
        endpoints: selectedApiSyncEndpoints(),
        generate_report: $("apiSyncGenerateReport").checked,
      };
      const startButton = $("startApiSyncTask");
      startButton.disabled = true;
      resetCurrentApiSyncView(payload);
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
        await loadRecords(result.report?.report_id || null);
        setMessage("taskMessage", "数据同步完成。", "ok");
      } catch (error) {
        $("taskStateChip").textContent = "任务 失败";
        $("taskStateChip").className = "chip bad";
        $("taskReportChip").textContent = "暂无报告";
        $("taskReportChip").className = "chip bad";
        renderTaskSteps(Object.fromEntries(Object.keys(taskStepLabels).map((key) => [key, { status: "failed" }])));
        updateTaskProgress(10, "同步失败", "failed");
        setTaskResult(error.message, "error");
        setMessage("taskMessage", error.message, "error");
      } finally {
        startButton.disabled = false;
      }
    }

    function renderApiSyncResult(result) {
      const rows = (result.items || []).reduce((sum, item) => sum + Number(item.row_count || 0), 0);
      const reportId = result.report?.report_id || "-";
      const moduleCount = new Set((result.items || []).map((item) => item.table_hint || item.export_type || item.endpoint)).size;
      const isOk = result.status === "completed";
      $("taskStateChip").textContent = `任务 ${statusLabel(result.status || "unknown")}`;
      $("taskStateChip").className = `chip ${isOk ? "ok" : "bad"}`;
      $("taskReportChip").textContent = result.report?.report_id ? `报告 ${result.report.report_id}` : "暂无报告";
      $("taskReportChip").className = `chip ${result.report?.report_id ? "ok" : ""}`.trim();
      renderDefinitionList("taskSummary", [
        ["任务", "数据同步"],
        ["店铺", $("collectShopName").value || $("collectShopId").value || "-"],
        ["周期", `${$("collectFrom").value || "-"} 至 ${$("collectTo").value || "-"}`],
        ["状态", statusLabel(result.status || "unknown")],
        ["报告", result.report?.report_id || "-"],
      ]);
      renderTaskSteps(Object.fromEntries(Object.keys(taskStepLabels).map((key) => [key, { status: isOk ? "completed" : "failed" }])));
      updateTaskProgress(isOk ? 100 : 10, isOk ? "数据同步完成" : "数据同步失败", isOk ? "completed" : "failed");
      setTaskResultHtml(`
        <div class="check-summary">
          <div><strong>数据同步完成</strong></div>
          <div class="metric-grid">
            <div class="metric-card"><strong>${escapeHtml(String(moduleCount))}</strong><span>同步模块</span></div>
            <div class="metric-card"><strong>${escapeHtml(String(rows))}</strong><span>写入记录</span></div>
            <div class="metric-card"><strong>${escapeHtml(reportId)}</strong><span>分析报告</span></div>
          </div>
        </div>
      `, isOk ? "ok" : "error");
    }

    function startTaskPolling(taskId) {
      if (state.taskPoller) {
        clearInterval(state.taskPoller);
      }
      if (taskId) {
        state.completionWatchTaskIds.add(String(taskId));
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
        const terminal = isTerminalTask(task);
        await pollExportVerificationStatus({ forceClose: terminal });
        if (terminal) {
          clearInterval(state.taskPoller);
          state.taskPoller = null;
          await loadTasks();
          await loadRecords();
          if (task.result?.report?.report_id) {
            await loadReportPreview(task.result.report.report_id);
          }
          maybeShowExportCompletion(task);
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

    async function loadTaskDetail(taskId) {
      if (!taskId) return null;
      try {
        const task = await fetchJson(`/tasks/${encodeURIComponent(taskId)}`);
        renderTask(task);
        if (task.result?.report?.report_id) {
          await loadReportPreview(task.result.report.report_id);
        }
        return task;
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
        return null;
      }
    }

    async function loadTasks() {
      setMessage("taskMessage", "读取任务进度中...");
      try {
        const tasks = await fetchJson("/tasks");
        const taskList = tasks || [];
        renderTaskHistory(taskList);
        const selected = state.activeTaskId ? findTaskById(taskList, state.activeTaskId) : null;
        const latest = taskList[0];
        let visibleTask = null;
        if (selected) {
          renderTask(selected);
          visibleTask = selected;
        } else if (latest) {
          state.activeTaskId = taskIdentity(latest);
          renderTask(latest);
          visibleTask = latest;
        } else {
          state.activeTaskId = null;
          renderTask(null);
        }
        if (taskIsLive(visibleTask) && !state.taskPoller) {
          startTaskPolling(taskIdentity(visibleTask));
        }
        setMessage("taskMessage", "任务进度已刷新。", "ok");
      } catch (error) {
        setMessage("taskMessage", error.message, "error");
      }
    }

    async function createCollectorJob() {
      const range = validateSyncRange();
      if (!range) return;
      const payload = {
        shop_id: $("collectShopId").value,
        shop_name: $("collectShopName").value,
        date_from: range.date_from,
        date_to: range.date_to,
        types: ["orders"],
        headless: false,
      };
      setMessage("collectorMessage", "正在创建本地采集任务...");
      try {
        const job = await fetchJson("/collector/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        setMessage("collectorMessage", `任务已创建：${job.job_id}`, "ok");
        await loadCollectorJobs();
      } catch (error) {
        setMessage("collectorMessage", error.message, "error");
      }
    }

    async function loadCollectorJobs() {
      if (!$("collectorJobsPanel")) return;
      try {
        const jobs = await fetchJson("/collector/jobs?limit=20");
        renderCollectorJobs(jobs || []);
      } catch (error) {
        setMessage("collectorMessage", error.message, "error");
      }
    }

    function renderCollectorJobs(jobs) {
      const el = $("collectorJobsPanel");
      if (!jobs.length) {
        el.innerHTML = '<div class="empty">暂无本地采集任务</div>';
        return;
      }
      const rows = jobs.map((job) => `
        <tr>
          <td>${escapeHtml(job.job_id || "-")}</td>
          <td>${escapeHtml(statusLabel(job.status || "pending"))}</td>
          <td>${escapeHtml(job.shop_name || job.shop_id || "-")}</td>
          <td>${escapeHtml(`${job.date_from || "-"} 至 ${job.date_to || "-"}`)}</td>
          <td>${escapeHtml(job.import_task_id || job.message || job.import_error || "-")}</td>
        </tr>
      `).join("");
      el.innerHTML = `<table><thead><tr><th>任务</th><th>状态</th><th>店铺</th><th>周期</th><th>结果</th></tr></thead><tbody>${rows}</tbody></table>`;
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

    async function loadRecords(focusReportId) {
      setMessage("recordsMessage", "读取分析结果中...");
      try {
        const reports = await fetchJson("/reports");
        renderReports(reports || [], focusReportId);
        setMessage("recordsMessage", "分析结果已加载。", "ok");
      } catch (error) {
        setMessage("recordsMessage", error.message, "error");
      }
    }

    function renderReports(reports, focusReportId) {
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
      const first = focusReportId === null ? null : focusReportId ? reports.find((report) => report.id === focusReportId) : reports[0];
      if (first?.id) {
        loadReportDetail(first.id);
      } else {
        $("reportDetail").classList.add("hidden");
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
      $("collectorCommand").textContent = `python3 scripts/collector/local_client.py --server ${window.location.origin} --once`;
      await Promise.all([loadConfig(), loadWebLoginStatus(), loadCapabilities(), loadTasks(), loadRecords(), loadCollectorJobs()]);
    }

    $("configForm")?.addEventListener("submit", saveAndFetchToken);
    $("refreshConfig")?.addEventListener("click", loadConfig);
    $("refreshWebLogin")?.addEventListener("click", () => loadWebLoginStatus(true));
    $("refreshRecords")?.addEventListener("click", loadRecords);
    $("refreshAll")?.addEventListener("click", refreshAll);
    $("mobileRefreshAll")?.addEventListener("click", refreshAll);
    $("orderTaskForm")?.addEventListener("submit", startOrderTask);
    $("localExportForm")?.addEventListener("submit", startLocalExportTask);
    $("checkLocalExportTask")?.addEventListener("click", checkLocalExportTask);
    $("apiSyncForm")?.addEventListener("submit", startApiSyncTask);
    $("createCollectorJob")?.addEventListener("click", createCollectorJob);
    $("refreshCollectorJobs")?.addEventListener("click", loadCollectorJobs);
    $("collectFrom")?.addEventListener("change", markDateRangeChanged);
    $("collectTo")?.addEventListener("change", markDateRangeChanged);
    $("refreshTasks")?.addEventListener("click", loadTasks);
    $("cancelActiveTask")?.addEventListener("click", cancelActiveTask);
    document.querySelectorAll(".menu-trigger, .workflow-jump").forEach((trigger) => {
      trigger.addEventListener("click", () => navigateToMenuSection(trigger.dataset.menuTarget));
    });
    $("mobileMenuButton")?.addEventListener("click", () => {
      setMobileNavigationOpen(!document.body.classList.contains("nav-open"));
    });
    $("navScrim")?.addEventListener("click", () => setMobileNavigationOpen(false, true));
    $("exportCompletionClose")?.addEventListener("click", closeExportCompletionModal);
    $("exportCompletionCloseIcon")?.addEventListener("click", closeExportCompletionModal);
    document.querySelectorAll("[data-completion-close]").forEach((button) => {
      button.addEventListener("click", closeExportCompletionModal);
    });
    $("exportVerificationClose")?.addEventListener("click", () => closeExportVerificationModal({ dismissed: true }));
    $("exportVerificationCloseIcon")?.addEventListener("click", () => closeExportVerificationModal({ dismissed: true }));
    document.querySelectorAll("[data-verification-close]").forEach((button) => {
      button.addEventListener("click", () => closeExportVerificationModal({ dismissed: true }));
    });
    $("refreshExportVerification")?.addEventListener("click", () => {
      refreshExportVerificationImage();
      pollExportVerificationStatus();
    });
    $("exportVerificationImage")?.addEventListener("error", () => {
      $("exportVerificationImage").hidden = true;
      $("exportVerificationImagePending").hidden = false;
      $("exportVerificationStatus").textContent = "二维码仍在生成，系统会自动刷新；也可以点击“刷新二维码”。";
    });
    $("exportCompletionDetails")?.addEventListener("click", async () => {
      const taskId = state.completionModalTaskId;
      closeExportCompletionModal();
      if (!taskId) return;
      state.activeTaskId = taskId;
      selectMenuSection("statusSection");
      await loadTaskDetail(taskId);
      window.requestAnimationFrame(() => {
        $("taskResult")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("exportVerificationModal")?.hidden) {
        closeExportVerificationModal({ dismissed: true });
        return;
      }
      if (!$("exportCompletionModal")?.hidden) {
        closeExportCompletionModal();
        return;
      }
      setMobileNavigationOpen(false, true);
    });

    selectMenuSection("authSection");
    refreshAll();
  </script>
</body>
</html>
"""
