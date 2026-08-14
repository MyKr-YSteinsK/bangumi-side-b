"""Local release readiness and the explicit unified-site workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath

from bgm_side_b import __version__
from bgm_side_b.archive_config import load_archive_sync_settings
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database, DatabaseError
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.release.site_candidate import (
    CandidateIdentity,
    SiteCandidate,
    SiteCandidateError,
    validate_build_state,
    validate_site,
)
from bgm_side_b.release.site_publish import (
    SitePublishError,
    SitePublishRun,
    UnifiedPublisher,
    validate_release_origin,
)
from bgm_side_b.release.unified_audit import (
    UnifiedAuditResult,
    UnifiedReleaseAuditor,
)


class WorkflowError(RuntimeError):
    """Raised for a refused release operation with an actionable remedy."""


_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_CONTENT_SHA = re.compile(r"[0-9a-f]{64}")
_PUBLIC_QUARTER = re.compile(r"\d{4}-(?:01|04|07|10)")
_BUILD_STATE_SCHEMA = 1
_STATUS_REQUIRED_FILES = (
    "index.html",
    "archive/index.html",
    "settings/index.html",
    "assets/app.css",
    "assets/app.js",
    "assets/pwa.js",
    "manifest.webmanifest",
    "sw.js",
    "data/archive-index.json",
    "data/pwa-shell.json",
)


@dataclass(frozen=True)
class LocalStatus:
    """Fast local-only release facts; no network access is performed."""

    source_version: str
    package_version: str
    branch: str
    head: str | None
    worktree_clean: bool
    sqlite_status: str
    site_status: str
    site_candidate_hash: str | None
    artifact_count: int | None
    total_bytes: int | None
    public_quarters: tuple[str, ...]
    prepared_release_status: str
    prepared_source_commit: str | None

    def render_status(self) -> str:
        lines = [
            f"程序版本      {self.source_version}",
            f"SQLite        {self.sqlite_status}",
            f"正式站点      {self.site_status}",
            f"候选哈希      {self.site_candidate_hash or '-'}",
            f"公开季度      {', '.join(self.public_quarters) or 'none'}",
            "Prepared release  " + self.prepared_release_status,
            f"工作树        {'clean' if self.worktree_clean else 'dirty'}",
            "",
            "下一步：",
            self.next_step(),
        ]
        return "\n".join(lines)

    def next_step(self) -> str:
        if self.branch != "main":
            return "git switch main"
        if not self.worktree_clean:
            return "git status"
        if self.sqlite_status != "OK":
            return "bgmb sync ..."
        if self.site_status != "valid":
            return "bgmb build --all"
        if self.prepared_release_status in {"none", "stale", "invalid"}:
            return "bgmb release prepare"
        if self.prepared_release_status == "valid_local":
            return "git push origin main"
        return "bgmb release prepare"


@dataclass(frozen=True)
class DoctorResult:
    """Combined local and optional remote unified-site diagnostics."""

    local: LocalStatus
    audit: UnifiedAuditResult
    origin_main: str
    gh_pages: str
    gh_pages_commit: str | None
    local_only: bool
    prepared_release_status: str

    def render(self) -> str:
        lines = [
            "Bangumi Side B 环境检查",
            "",
            "项目根目录       OK",
            f"Python           {sys.version.split()[0]}",
            f"源码程序版本     {self.local.source_version}",
            f"包元数据版本     {self.local.package_version}",
            f"Git 分支         {self.local.branch}",
            f"工作树           {'clean' if self.local.worktree_clean else 'dirty'}",
            f"origin/main      {self.origin_main}",
            f"SQLite           {self.local.sqlite_status}",
            f"正式站点         {self.local.site_status}",
            f"公开季度         {', '.join(self.local.public_quarters) or 'none'}",
            f"资料作品         {self.audit.subject_count}",
            f"Prepared release  {self.prepared_release_status}",
            f"gh-pages         {self.gh_pages}",
            "",
            "结论：",
            self.conclusion(),
        ]
        return "\n".join(lines)

    def conclusion(self) -> str:
        if not self.audit.passed:
            return "资料审计未通过，请先运行 bgmb audit 或处理 REVIEW"
        if self.local.branch != "main":
            return "请切换到 main 后重新检查"
        if not self.local.worktree_clean:
            return "工作树不干净，请提交或处理本地改动后重新检查"
        if self.local.site_status != "valid":
            return "请先运行 bgmb build --all"
        if self.prepared_release_status in {"none", "stale", "invalid"}:
            return "请运行：bgmb release prepare"
        if self.local_only:
            return "本地检查完成；请运行 bgmb doctor 检查远端状态"
        if self.origin_main != "synchronized":
            return "请先：git push origin main"
        if self.gh_pages not in {"reachable", "missing"}:
            return "无法确认 gh-pages，请检查网络或远端后重试"
        return "可以执行：bgmb release publish"


@dataclass(frozen=True)
class PreparedRelease:
    state_path: Path
    release_version: str
    report_path: Path


def local_status(project_root: Path) -> LocalStatus:
    """Compute status from local Git, SQLite, site, and prepared state only."""
    return _compute_local_status(project_root.resolve())


def _compute_local_status(
    root: Path, *, audit: UnifiedAuditResult | None = None
) -> LocalStatus:
    branch = _git_value(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    head = _git_value(root, "rev-parse", "HEAD")
    clean = not _worktree_changes(root)
    if audit is not None:
        blocking = {"workspace", "schema"}
        sqlite_status = (
            "错误"
            if any(failure.check in blocking for failure in audit.failures)
            else "OK"
        )
    else:
        sqlite_status = _quick_sqlite_status(root)
    candidate, site_status = _quick_site_status(root, head)
    prepared_status, prepared_source = _prepared_local_status(root, head, candidate)
    try:
        package = distribution_version("bgm-side-b")
    except PackageNotFoundError:
        package = "未安装"
    return LocalStatus(
        source_version=__version__,
        package_version=package,
        branch=branch,
        head=head,
        worktree_clean=clean,
        sqlite_status=sqlite_status,
        site_status=site_status,
        site_candidate_hash=(
            None if candidate is None else candidate.identity.content_hash
        ),
        artifact_count=None if candidate is None else candidate.identity.artifact_count,
        total_bytes=None if candidate is None else candidate.identity.total_bytes,
        public_quarters=() if candidate is None else candidate.public_quarters,
        prepared_release_status=prepared_status,
        prepared_source_commit=prepared_source,
    )


def doctor(project_root: Path, *, local_only: bool = False) -> DoctorResult:
    """Inspect local facts and, unless local-only, refresh the two remote refs."""
    root = project_root.resolve()
    settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
    audit = UnifiedReleaseAuditor(root, settings).audit()
    local = _compute_local_status(root, audit=audit)
    if local_only:
        return DoctorResult(
            local, audit, "未检查", "未检查", None, True, local.prepared_release_status
        )
    origin_main = _origin_main_status(root)
    gh_pages, gh_commit = _gh_pages_status(root)
    prepared = local.prepared_release_status
    if prepared == "valid_local" and gh_pages in {"reachable", "missing"}:
        payload = _read_prepared_or_none(root)
        if payload is not None and payload.get("remote_gh_pages_commit") != gh_commit:
            prepared = "stale"
    return DoctorResult(local, audit, origin_main, gh_pages, gh_commit, False, prepared)


def prepare_release(
    project_root: Path, reporter: ProgressReporter | None = None
) -> PreparedRelease:
    """Build, validate, dry-run, and bind the exact current ``dist/site`` tree."""
    root = project_root.resolve()
    active = reporter or NullProgressReporter()
    active.start(stage="release-preflight", message="正在执行本地发布预检")
    _require_prepare_preflight(root)
    settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
    tags = load_tag_rules(root / "config" / "allowed-tags.toml")
    active.stage(stage="release-audit", message="正在执行统一资料审计")
    audit = UnifiedReleaseAuditor(root, settings).audit()
    if not audit.passed:
        raise WorkflowError("资料审计未通过，请先处理 REVIEW 或同步状态")
    active.stage(stage="release-build", message="正在离线收敛 dist/site")
    try:
        from bgm_side_b.build.site_builder import UnifiedSiteBuilder

        build_run = UnifiedSiteBuilder(
            root,
            Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3"),
            tags,
            workspace_directory=root / "workspace",
            reporter=active,
            excluded_subject_ids=settings.all_excluded_subject_ids,
        ).build()
    except Exception as error:  # builder normalizes product errors below
        if isinstance(error, KeyboardInterrupt):
            raise
        raise WorkflowError(f"统一站点构建失败：{error}") from error
    del build_run
    active.stage(stage="release-validate", message="正在验证正式站点候选")
    candidate = _validated_candidate(root)
    public_review = set(candidate.public_quarters) & set(audit.review_quarters)
    if public_review:
        raise WorkflowError("formal site exposes a quarter with unresolved REVIEW")
    active.stage(stage="release-dry-run", message="正在执行发布 dry-run")
    try:
        run = UnifiedPublisher(root, active).publish(dry_run=True)
    except SitePublishError as error:
        raise WorkflowError(f"发布 dry-run 失败：{error}") from error
    payload = {
        "schema": 2,
        "source_commit": candidate.identity.source_commit,
        "app_version": __version__,
        "candidate_content_hash": candidate.identity.content_hash,
        "artifact_count": candidate.identity.artifact_count,
        "total_bytes": candidate.identity.total_bytes,
        "remote_gh_pages_commit": _remote_commit(root, "origin", "gh-pages"),
        "prepared_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dry_run_report": _project_relative(root, run.report_path),
        "public_quarters": list(candidate.public_quarters),
        "build_state_schema": 1,
    }
    destination = root / "workspace" / "state" / "prepared-release.json"
    _write_json(destination, payload)
    active.complete(stage="prepared-release", message="发布准备完成；尚未推送或发布")
    return PreparedRelease(destination, run.release_version, run.report_path)


def publish_prepared_release(
    project_root: Path, reporter: ProgressReporter | None = None
) -> SitePublishRun:
    """Publish only a prepared site whose source, tree, and remote are unchanged."""
    root = project_root.resolve()
    active = reporter or NullProgressReporter()
    active.start(stage="release-preflight", message="正在验证 prepared release")
    try:
        validate_release_origin(root)
    except SitePublishError as error:
        raise WorkflowError(str(error)) from error
    prepared = _read_prepared(root)
    _validate_prepared_local(root, prepared)
    if _origin_main_status(root) != "synchronized":
        raise _invalid_prepared()
    remote_commit = _remote_commit(root, "origin", "gh-pages")
    if remote_commit != prepared["remote_gh_pages_commit"]:
        raise _invalid_prepared()
    try:
        run = UnifiedPublisher(root, active).publish(
            remote="origin",
            branch="gh-pages",
            expected_remote_commit=remote_commit,
            expected_content_hash=str(prepared["candidate_content_hash"]),
        )
    except SitePublishError as error:
        raise WorkflowError(f"发布失败：{error}") from error
    if run.published:
        try:
            (root / "workspace" / "state" / "prepared-release.json").unlink()
        except OSError:
            warning = "remote published but local prepared state cleanup failed"
            active.warning(stage="prepared-cleanup", message=warning)
            run = replace(run, warnings=(*run.warnings, warning))
    return run


def _require_prepare_preflight(root: Path) -> None:
    branch = _git_value(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise WorkflowError("release prepare 必须在 main 分支执行")
    if _worktree_changes(root):
        raise WorkflowError("release prepare 需要干净的工作树")
    if _git_value(root, "rev-parse", "HEAD") is None:
        raise WorkflowError("无法确认 HEAD")
    if not (root / "workspace" / "data" / "bangumi-side-b.sqlite3").is_file():
        raise WorkflowError("workspace SQLite database is missing")
    try:
        load_archive_sync_settings(root / "config" / "bangumi.toml")
    except (OSError, ValueError) as error:
        raise WorkflowError(f"配置不可读：{error}") from error


def _validated_candidate(root: Path) -> SiteCandidate:
    head = _git_value(root, "rev-parse", "HEAD")
    if head is None:
        raise WorkflowError("无法确认 HEAD")
    try:
        validate_build_state(root / "dist" / "site", root / "workspace")
        return validate_site(root / "dist" / "site", source_commit=head)
    except SiteCandidateError as error:
        raise WorkflowError(str(error)) from error


def _site_status(root: Path, head: str | None) -> tuple[SiteCandidate | None, str]:
    if head is None or not (root / "dist" / "site").is_dir():
        return None, "missing"
    try:
        validate_build_state(root / "dist" / "site", root / "workspace")
        candidate = validate_site(root / "dist" / "site", source_commit=head)
    except SiteCandidateError:
        return None, "stale"
    return candidate, "valid"


def _quick_sqlite_status(root: Path) -> str:
    database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    if not database.path.is_file():
        return "错误"
    try:
        connection = database.connect()
    except (DatabaseError, OSError, sqlite3.Error, ValueError):
        return "错误"
    connection.close()
    return "OK"


def _quick_site_status(
    root: Path, head: str | None
) -> tuple[SiteCandidate | None, str]:
    site = root / "dist" / "site"
    if head is None or not site.is_dir():
        return None, "missing"
    try:
        payload = json.loads(
            (root / "workspace" / "build-state.json").read_text("utf-8")
        )
        if not isinstance(payload, dict):
            raise ValueError
        artifacts = payload["artifacts"]
        sizes = payload["artifact_sizes"]
        if payload.get("schema") != _BUILD_STATE_SCHEMA:
            raise ValueError
        if not isinstance(artifacts, dict) or not isinstance(sizes, dict):
            raise ValueError
        normalized = {
            str(relative): (str(digest), int(sizes[relative]))
            for relative, digest in artifacts.items()
            if (
                isinstance(relative, str)
                and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest)
                and isinstance(sizes.get(relative), int)
                and sizes[relative] >= 0
            )
        }
        if len(normalized) != len(artifacts) or any(
            relative not in normalized
            or not (site / PurePosixPath(relative)).is_file()
            or (site / PurePosixPath(relative)).stat().st_size
            != normalized[relative][1]
            for relative in _STATUS_REQUIRED_FILES
        ):
            raise ValueError
        archive = json.loads(
            (site / "data" / "archive-index.json").read_text("utf-8")
        )
        quarter_rows = archive.get("quarters") if isinstance(archive, dict) else None
        if not isinstance(quarter_rows, list):
            raise ValueError
        quarters = tuple(
            sorted(
                {
                    str(item["quarter"])
                    for item in quarter_rows
                    if isinstance(item, dict)
                    and re.fullmatch(_PUBLIC_QUARTER, str(item.get("quarter", "")))
                    and all(
                        relative in normalized
                        for relative in (
                            f"{item['quarter']}/index.html",
                            f"data/quarters/{item['quarter']}.json",
                            f"data/offline/{item['quarter']}.json",
                        )
                    )
                }
            )
        )
        if not quarters:
            raise ValueError
        identity_hash = hashlib.sha256()
        for relative, (digest, size) in sorted(normalized.items()):
            identity_hash.update(relative.encode("utf-8"))
            identity_hash.update(b"\0")
            identity_hash.update(digest.encode("ascii"))
            identity_hash.update(b"\0")
            identity_hash.update(str(size).encode("ascii"))
            identity_hash.update(b"\n")
        candidate = SiteCandidate(
            CandidateIdentity(
                1,
                head,
                len(normalized),
                sum(size for _, size in normalized.values()),
                identity_hash.hexdigest(),
            ),
            quarters,
            tuple(sorted(normalized)),
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None, "stale"
    return candidate, "valid"


def _prepared_local_status(
    root: Path, head: str | None, candidate: SiteCandidate | None
) -> tuple[str, str | None]:
    path = root / "workspace" / "state" / "prepared-release.json"
    if not path.is_file():
        return "none", None
    try:
        payload = _read_prepared(root)
    except WorkflowError:
        return "invalid", None
    source = str(payload["source_commit"])
    if candidate is None or head != source:
        return "stale", source
    if (
        candidate.identity.content_hash != payload["candidate_content_hash"]
        or candidate.identity.artifact_count != payload["artifact_count"]
        or candidate.identity.total_bytes != payload["total_bytes"]
        or tuple(payload["public_quarters"]) != candidate.public_quarters
    ):
        return "stale", source
    return "valid_local", source


def _validate_prepared_local(root: Path, prepared: dict[str, object]) -> None:
    if (
        _git_value(root, "rev-parse", "--abbrev-ref", "HEAD") != "main"
        or _git_value(root, "rev-parse", "HEAD") != prepared["source_commit"]
    ):
        raise _invalid_prepared()
    if _worktree_changes(root):
        raise _invalid_prepared()
    candidate = _validated_candidate(root)
    if (
        candidate.identity.content_hash != prepared["candidate_content_hash"]
        or candidate.identity.artifact_count != prepared["artifact_count"]
        or candidate.identity.total_bytes != prepared["total_bytes"]
        or list(candidate.public_quarters) != prepared["public_quarters"]
        or __version__ != prepared["app_version"]
    ):
        raise _invalid_prepared()


def _read_prepared(root: Path) -> dict[str, object]:
    path = root / "workspace" / "state" / "prepared-release.json"
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _invalid_prepared() from error
    required = {
        "schema",
        "source_commit",
        "app_version",
        "candidate_content_hash",
        "artifact_count",
        "total_bytes",
        "remote_gh_pages_commit",
        "prepared_at",
        "dry_run_report",
        "public_quarters",
        "build_state_schema",
    }
    if (
        not isinstance(value, dict)
        or value.get("schema") != 2
        or not required.issubset(value)
    ):
        raise _invalid_prepared()
    if not isinstance(value["source_commit"], str) or not _GIT_SHA.fullmatch(
        value["source_commit"]
    ):
        raise _invalid_prepared()
    if not isinstance(value["app_version"], str) or not value["app_version"]:
        raise _invalid_prepared()
    if not isinstance(value["candidate_content_hash"], str) or not (
        _CONTENT_SHA.fullmatch(value["candidate_content_hash"])
    ):
        raise _invalid_prepared()
    if (
        not isinstance(value["artifact_count"], int)
        or isinstance(value["artifact_count"], bool)
        or value["artifact_count"] <= 0
    ):
        raise _invalid_prepared()
    if (
        not isinstance(value["total_bytes"], int)
        or isinstance(value["total_bytes"], bool)
        or value["total_bytes"] <= 0
    ):
        raise _invalid_prepared()
    remote_commit = value["remote_gh_pages_commit"]
    if remote_commit is not None and (
        not isinstance(remote_commit, str) or not _GIT_SHA.fullmatch(remote_commit)
    ):
        raise _invalid_prepared()
    if not isinstance(value["prepared_at"], str) or not value["prepared_at"]:
        raise _invalid_prepared()
    report_value = value["dry_run_report"]
    if not isinstance(report_value, str) or not report_value:
        raise _invalid_prepared()
    report = PurePosixPath(report_value)
    if (
        report.is_absolute()
        or ".." in report.parts
        or not report.parts
        or "\\" in report_value
        or ":" in report.parts[0]
    ):
        raise _invalid_prepared()
    quarters = value["public_quarters"]
    if (
        not isinstance(quarters, list)
        or any(
            not isinstance(quarter, str) or _PUBLIC_QUARTER.fullmatch(quarter) is None
            for quarter in quarters
        )
        or len(quarters) != len(set(quarters))
        or quarters != sorted(quarters)
    ):
        raise _invalid_prepared()
    build_state_schema = value["build_state_schema"]
    if (
        not isinstance(build_state_schema, int)
        or isinstance(build_state_schema, bool)
        or build_state_schema != _BUILD_STATE_SCHEMA
    ):
        raise _invalid_prepared()
    return value


def _read_prepared_or_none(root: Path) -> dict[str, object] | None:
    try:
        return _read_prepared(root)
    except WorkflowError:
        return None


def _write_json(destination: Path, payload: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)


def _origin_main_status(root: Path) -> str:
    fetched = _run_git(root, "fetch", "origin", "main", check=False)
    if fetched.returncode:
        return "unreachable"
    counts = _git_value(
        root, "rev-list", "--left-right", "--count", "HEAD...origin/main"
    )
    if counts is None:
        return "unreachable"
    try:
        ahead, behind = (int(value) for value in counts.split())
    except ValueError:
        return "unreachable"
    if ahead == behind == 0:
        return "synchronized"
    if ahead and behind:
        return "diverged"
    return "ahead" if ahead else "behind"


def _gh_pages_status(root: Path) -> tuple[str, str | None]:
    try:
        commit = UnifiedPublisher(root).remote_commit("origin", "gh-pages")
    except SitePublishError:
        return "unreachable", None
    return ("missing" if commit is None else "reachable"), commit


def _remote_commit(root: Path, remote: str, branch: str) -> str | None:
    try:
        return UnifiedPublisher(root).remote_commit(remote, branch)
    except SitePublishError as error:
        raise WorkflowError(str(error)) from error


def _project_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise WorkflowError("报告路径不在项目目录内") from error


def _worktree_changes(root: Path) -> list[str]:
    result = _run_git(root, "status", "--porcelain", check=False)
    if result.returncode:
        return ["git-error"]
    return result.stdout.splitlines()


def _git_value(root: Path, *args: str) -> str | None:
    result = _run_git(root, *args, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _run_git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def _invalid_prepared() -> WorkflowError:
    return WorkflowError("prepared release 已失效，请重新运行：bgmb release prepare")
