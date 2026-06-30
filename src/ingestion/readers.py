from __future__ import annotations

import json
import hashlib
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".zip"}
SUPPORTED_WORKBOOK_EXTENSIONS = {".csv", ".xlsx", ".xls"}
ARTIFACT_MANIFEST_FILENAME = "artifacts-manifest.json"
EXPORT_FILE_SOURCE_KIND = "export_file"


def iter_source_files(source_dir: str | Path) -> list[Path]:
    root = Path(source_dir)
    if not root.exists():
        raise FileNotFoundError(f"source-dir not found: {root}")
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and not path.name.startswith("~$")
    ]
    return files


def read_workbook_tables(path: Path) -> Iterable[tuple[str | None, pd.DataFrame]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield None, pd.read_csv(path, dtype=object)
        return
    if suffix in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=None, dtype=object)
        for sheet_name, frame in sheets.items():
            yield str(sheet_name), frame
        return
    if suffix == ".zip":
        yield from read_zip_workbook_tables(path)
        return
    raise ValueError(f"Unsupported file extension: {path}")


def read_zip_workbook_tables(path: Path) -> Iterable[tuple[str | None, pd.DataFrame]]:
    with zipfile.ZipFile(path) as archive:
        members = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            and not Path(member.filename).name.startswith("~$")
            and Path(member.filename).suffix.lower() in SUPPORTED_WORKBOOK_EXTENSIONS
        ]
        if not members:
            raise ValueError(f"Zip archive contains no supported workbook files: {path}")

        with tempfile.TemporaryDirectory(prefix="wechat_zip_import_") as temp_dir:
            temp_root = Path(temp_dir)
            for index, member in enumerate(members):
                suffix = Path(member.filename).suffix.lower()
                extracted_path = temp_root / f"member_{index}{suffix}"
                with archive.open(member) as source, extracted_path.open("wb") as target:
                    target.write(source.read())

                for sheet_name, frame in read_workbook_tables(extracted_path):
                    member_name = Path(member.filename).name
                    display_name = member_name if sheet_name is None else f"{member_name}:{sheet_name}"
                    yield display_name, frame


@dataclass(frozen=True)
class ArtifactRecord:
    manifest_path: Path
    manifest_task_id: str | None
    artifact_index: int
    data: Mapping[str, Any]

    @property
    def saved_path(self) -> str:
        value = self.data.get("saved_path")
        return "" if value is None else str(value)

    @property
    def original_filename(self) -> str:
        value = self.data.get("original_filename")
        return "" if value is None else str(value)

    @property
    def table_hint(self) -> str:
        value = self.data.get("table_hint")
        return "" if value is None else str(value).strip()

    @property
    def export_type(self) -> str:
        value = self.data.get("export_type")
        return "" if value is None else str(value).strip()

    @property
    def shop_id(self) -> str:
        value = self.data.get("shop_id")
        return "" if value is None else str(value).strip()

    @property
    def sha256(self) -> str:
        value = self.data.get("sha256")
        return "" if value is None else str(value).strip().lower()

    @property
    def size_bytes(self) -> int | None:
        value = self.data.get("size_bytes")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class ArtifactMatch:
    artifact: ArtifactRecord
    match_type: str
    score: int


class ArtifactManifestIndex:
    def __init__(self, source_dir: str | Path, artifacts: Iterable[ArtifactRecord]) -> None:
        self.source_root = _safe_resolve(Path(source_dir))
        self.artifacts = tuple(artifacts)

    def match_file(self, source_file: str | Path) -> tuple[ArtifactMatch | None, list[dict[str, Any]]]:
        path = Path(source_file)
        source_abs = _safe_resolve(path)
        matches: list[ArtifactMatch] = []
        for artifact in self.artifacts:
            score, match_type = _score_artifact_match(
                artifact=artifact,
                source_path=path,
                source_abs=source_abs,
                source_root=self.source_root,
            )
            if score > 0:
                matches.append(ArtifactMatch(artifact=artifact, match_type=match_type, score=score))

        if not matches:
            return None, [
                {
                    "code": "manifest_artifact_not_matched",
                    "source_file": str(source_file),
                    "message": "已读取 artifacts-manifest.json，但未匹配到该源文件的 artifact",
                }
            ]

        matches.sort(key=lambda match: match.score, reverse=True)
        best_score = matches[0].score
        best_matches = [match for match in matches if match.score == best_score]
        if len(best_matches) == 1:
            return best_matches[0], []

        hints = {match.artifact.table_hint for match in best_matches}
        saved_paths = [match.artifact.saved_path for match in best_matches]
        warning = {
            "code": "manifest_artifact_ambiguous",
            "source_file": str(source_file),
            "match_type": best_matches[0].match_type,
            "candidate_saved_paths_json": json.dumps(saved_paths, ensure_ascii=False),
            "message": "源文件匹配到多个同分 artifact，回退启发式猜表",
        }
        if len(hints) == 1 and best_score >= 90:
            warning["message"] = "源文件匹配到多个同分 artifact，table_hint 一致，使用第一个匹配"
            return best_matches[0], [warning]
        return None, [warning]

    def validate_match(
        self,
        match: ArtifactMatch,
        source_file: str | Path,
        *,
        shop_id: str,
        task_id: str | None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        artifact = match.artifact
        source_path = Path(source_file)
        warnings: list[dict[str, Any]] = []
        base = {
            "source_file": str(source_file),
            "manifest_path": str(artifact.manifest_path),
            "artifact_index": artifact.artifact_index,
            "match_type": match.match_type,
        }

        if artifact.shop_id and artifact.shop_id != shop_id:
            warnings.append(
                {
                    **base,
                    "code": "artifact_shop_mismatch",
                    "artifact_shop_id": artifact.shop_id,
                    "import_shop_id": shop_id,
                    "message": "artifact shop_id 与导入 shop_id 不一致，已跳过该文件",
                }
            )
            return False, warnings

        if task_id and artifact.manifest_task_id and artifact.manifest_task_id != task_id:
            warnings.append(
                {
                    **base,
                    "code": "artifact_task_mismatch",
                    "artifact_task_id": artifact.manifest_task_id,
                    "import_task_id": task_id,
                    "message": "manifest task_id 与导入 task_id 不一致，已跳过该文件",
                }
            )
            return False, warnings

        try:
            stats = source_path.stat()
        except OSError as error:
            warnings.append(
                {
                    **base,
                    "code": "artifact_file_missing",
                    "message": f"artifact 源文件不存在或不可读取，已跳过该文件: {error}",
                }
            )
            return False, warnings

        if artifact.size_bytes is not None and artifact.size_bytes != stats.st_size:
            warnings.append(
                {
                    **base,
                    "code": "artifact_size_mismatch",
                    "artifact_size_bytes": artifact.size_bytes,
                    "actual_size_bytes": stats.st_size,
                    "message": "artifact size_bytes 与当前文件大小不一致，已跳过该文件",
                }
            )
            return False, warnings

        if artifact.sha256:
            actual_sha256 = sha256_file(source_path)
            if actual_sha256 != artifact.sha256:
                warnings.append(
                    {
                        **base,
                        "code": "artifact_hash_mismatch",
                        "artifact_sha256": artifact.sha256,
                        "actual_sha256": actual_sha256,
                        "message": "artifact sha256 与当前文件不一致，已跳过该文件",
                    }
                )
                return False, warnings

        return True, warnings


def load_artifact_manifest_index(source_dir: str | Path) -> tuple[ArtifactManifestIndex | None, list[dict[str, Any]]]:
    manifest_paths = find_artifact_manifest_paths(source_dir)
    if not manifest_paths:
        return None, []

    artifacts: list[ArtifactRecord] = []
    warnings: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            warnings.append(
                {
                    "code": "invalid_artifact_manifest",
                    "manifest_path": str(manifest_path),
                    "message": f"无法读取 artifacts-manifest.json，回退启发式猜表: {error}",
                }
            )
            continue

        manifest_task_id = _text(manifest.get("task_id")) if isinstance(manifest, Mapping) else None
        raw_artifacts = manifest.get("artifacts") if isinstance(manifest, Mapping) else None
        if not isinstance(raw_artifacts, list):
            warnings.append(
                {
                    "code": "invalid_artifact_manifest",
                    "manifest_path": str(manifest_path),
                    "message": "artifacts-manifest.json 缺少 artifacts 数组，回退启发式猜表",
                }
            )
            continue

        if not raw_artifacts:
            warnings.append(
                {
                    "code": "manifest_no_completed_export_files",
                    "manifest_path": str(manifest_path),
                    "message": "artifacts-manifest.json 没有可导入的 completed export_file artifact",
                }
            )
            continue

        for index, raw_artifact in enumerate(raw_artifacts):
            if not isinstance(raw_artifact, Mapping):
                warnings.append(
                    {
                        "code": "invalid_manifest_artifact",
                        "manifest_path": str(manifest_path),
                        "artifact_index": index,
                        "message": "manifest artifact 不是对象，已跳过",
                    }
                )
                continue
            if not _is_export_file_artifact(raw_artifact):
                warnings.append(
                    {
                        "code": "unsupported_source_kind",
                        "manifest_path": str(manifest_path),
                        "artifact_index": index,
                        "source_kind": _text(raw_artifact.get("source_kind")) or "",
                        "source_type": _text(raw_artifact.get("source_type")) or "",
                        "message": "V1 只导入 export_file artifact，已跳过该 artifact",
                    }
                )
                continue
            if _artifact_status(raw_artifact) != "completed":
                warnings.append(
                    {
                        "code": "artifact_not_completed",
                        "manifest_path": str(manifest_path),
                        "artifact_index": index,
                        "status": _artifact_status(raw_artifact),
                        "message": "artifact 状态不是 completed，已跳过",
                    }
                )
                continue
            artifacts.append(ArtifactRecord(manifest_path=manifest_path, manifest_task_id=manifest_task_id, artifact_index=index, data=raw_artifact))

    if not artifacts:
        return None, warnings
    return ArtifactManifestIndex(source_dir, artifacts), warnings


def find_artifact_manifest_paths(source_dir: str | Path) -> list[Path]:
    root = Path(source_dir)
    source_root = root if root.is_dir() else root.parent
    candidates: list[Path] = []

    if source_root.exists():
        candidates.extend(sorted(source_root.rglob(ARTIFACT_MANIFEST_FILENAME)))

    nearby_roots = _nearby_manifest_roots(source_root)
    candidates.extend(parent / ARTIFACT_MANIFEST_FILENAME for parent in nearby_roots)

    seen: set[str] = set()
    manifest_paths: list[Path] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        key = str(_safe_resolve(candidate))
        if key in seen:
            continue
        seen.add(key)
        manifest_paths.append(candidate)
    return manifest_paths


def _is_export_file_artifact(artifact: Mapping[str, Any]) -> bool:
    values = {
        str(value).strip().casefold()
        for value in (artifact.get("source_kind"), artifact.get("source_type"))
        if str(value or "").strip()
    }
    return not values or values == {EXPORT_FILE_SOURCE_KIND}


def _artifact_status(artifact: Mapping[str, Any]) -> str:
    return (_text(artifact.get("status")) or "completed").casefold()


def _nearby_manifest_roots(source_root: Path) -> tuple[Path, ...]:
    roots = (source_root, source_root.parent, source_root.parent.parent)
    seen: set[str] = set()
    result: list[Path] = []
    for root in roots:
        key = str(_safe_resolve(root))
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return tuple(result)


def _score_artifact_match(
    *,
    artifact: ArtifactRecord,
    source_path: Path,
    source_abs: Path,
    source_root: Path,
) -> tuple[int, str]:
    saved_path = artifact.saved_path
    if saved_path:
        saved = Path(saved_path).expanduser()
        candidate_paths: list[Path] = []
        if saved.is_absolute():
            candidate_paths.append(saved)
        else:
            candidate_paths.extend((artifact.manifest_path.parent / saved, source_root / saved))
        if any(_safe_resolve(candidate) == source_abs for candidate in candidate_paths):
            return 100, "path_exact"

        source_relative = _relative_to(source_abs, source_root)
        if source_relative and _normalize_path_text(saved_path) == _normalize_path_text(str(source_relative)):
            return 90, "relative_path"

        if _path_suffix_matches(source_abs, saved_path):
            return 80, "path_suffix"

        if Path(saved_path).name and _same_filename(Path(saved_path).name, source_path.name):
            return 30, "filename"

    if artifact.original_filename and _same_filename(artifact.original_filename, source_path.name):
        return 25, "original_filename"

    return 0, ""


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _path_suffix_matches(source_abs: Path, artifact_path: str) -> bool:
    normalized_artifact = _normalize_path_text(artifact_path)
    if not normalized_artifact or normalized_artifact in {".", ".."}:
        return False
    normalized_source = _normalize_path_text(str(source_abs))
    return normalized_source == normalized_artifact or normalized_source.endswith(f"/{normalized_artifact}")


def _same_filename(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _normalize_path_text(path: str) -> str:
    return path.replace("\\", "/").strip().rstrip("/").casefold()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
