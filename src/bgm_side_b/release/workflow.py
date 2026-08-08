"""Local release readiness checks and explicit prepare/publish orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath

from bgm_side_b import __version__
from bgm_side_b.audit import AuditResult, ReleaseDataAuditor
from bgm_side_b.build.builder import ArchiveBuilder, BuildError
from bgm_side_b.build.output import AtomicOutput, OutputError
from bgm_side_b.build.profiles import pages_profile
from bgm_side_b.config import load_rules
from bgm_side_b.legacy_database import Database
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.release.candidate import (
    data_generation_is_dirty,
    read_data_generation,
    read_pages_build_marker,
)
from bgm_side_b.release.manifest import (
    ManifestError,
    candidate_content_hash,
    index_candidate,
)
from bgm_side_b.release.publish import Publisher, PublishError, PublishRun


class WorkflowError(RuntimeError):
    """Raised for a refused release workflow with a concise Chinese remedy."""


@dataclass(frozen=True)
class LocalStatus:
    """Fast, non-mutating facts used by ``status`` and ``doctor``."""

    source_version: str
    package_version: str
    branch: str
    head: str | None
    worktree_clean: bool
    data_status: str
    data_generation: int | None
    quarters: tuple[str, ...]
    pages_build: str
    pages_candidate: str
    pending_promotion: str | None
    prepared_release_status: str
    prepared_release_version: str | None
    marker: dict[str, object] | None

    def render_status(self) -> str:
        """Render the compact, local-only status command."""
        lines = [
            f"程序版本      {self.source_version}",
            f"资料状态      {self.data_status}",
            f"Pages build   {self.pages_build}",
            "Prepared release  "
            + _prepared_display(
                self.prepared_release_status, self.prepared_release_version
            ),
            f"工作树        {'clean' if self.worktree_clean else 'dirty'}",
            "",
            "下一步：",
            self.next_step(),
        ]
        return "\n".join(lines)

    def next_step(self) -> str:
        """Choose one primary action without consulting a remote."""
        if self.branch != "main":
            return "git switch main"
        if not self.worktree_clean:
            return "git status"
        if self.pending_promotion is not None:
            return f"bgmb promote {self.pending_promotion}"
        if self.data_status != "clean":
            return "bgmb sync 2026 4"
        if self.prepared_release_status == "stale":
            return "bgmb release prepare"
        if self.prepared_release_status == "invalid":
            return "处理无效 prepared state 后运行：bgmb release prepare"
        if self.prepared_release_status == "consumed":
            return "bgmb release prepare"
        if self.prepared_release_status == "valid_local":
            return "确认 main 已 push 后运行：\nbgmb release publish"
        return "bgmb release prepare"


@dataclass(frozen=True)
class DoctorResult:
    """The combined local and optional remote diagnostic report."""

    local: LocalStatus
    audit: AuditResult
    sqlite_status: str
    origin_main: str
    gh_pages: str
    remote_app_version: str | None
    remote_release_version: str | None
    local_only: bool
    prepared_release_status: str

    def render(self) -> str:
        """Render a stable, path-free Chinese environment report."""
        package = self.local.package_version
        if package != "未安装" and package != self.local.source_version:
            package = f"{package}（与源码不一致）"
        lines = [
            "Bangumi Side B 环境检查",
            "",
            "项目根目录       OK",
            f"Python           {sys.version.split()[0]}",
            f"源码程序版本     {self.local.source_version}",
            f"包元数据版本     {package}",
            f"Git 分支         {self.local.branch}",
            f"工作树           {'clean' if self.local.worktree_clean else 'dirty'}",
            f"origin/main      {self.origin_main}",
            f"SQLite           {self.sqlite_status}",
            f"资料代次         {self.local.data_status}",
            f"当前季度         {', '.join(self.local.quarters)}",
            f"作品             {self.audit.subject_count}",
            f"Pages build      {self.local.pages_build}",
            f"Pages candidate  {self.local.pages_candidate}",
            f"Pending promotion {self.local.pending_promotion or 'none'}",
            "Prepared release  "
            + _prepared_display(
                self.prepared_release_status, self.local.prepared_release_version
            ),
            f"gh-pages         {self.gh_pages}",
            f"线上程序版本     {self.remote_app_version or '-'}",
            f"线上资料版本     {self.remote_release_version or '-'}",
            "",
            "结论：",
            self.conclusion(),
        ]
        return "\n".join(lines)

    def conclusion(self) -> str:
        """State the one conclusion without treating package metadata as authority."""
        if not self.audit.passed:
            return "资料审计未通过，请先运行 bgmb audit 查看详情"
        if self.local.branch != "main":
            return "请切换到 main 后重新检查"
        if not self.local.worktree_clean:
            return "工作树不干净，请提交或处理本地改动后重新检查"
        if self.local.pending_promotion is not None:
            return f"请先运行 bgmb promote {self.local.pending_promotion}"
        if self.local.data_status != "clean":
            return "资料状态不是 clean，请完成同步后重新构建"
        if self.prepared_release_status == "stale":
            return "prepared release 已失效，请重新运行：bgmb release prepare"
        if self.prepared_release_status == "invalid":
            return "prepared release 状态无效，请处理后重新运行：bgmb release prepare"
        if self.prepared_release_status == "consumed":
            return (
                "上一次发布已成功，但本地 prepared state 清理失败；"
                "请重新运行：bgmb release prepare"
            )
        if self.local.pages_build != "fresh" or self.local.pages_candidate != "OK":
            return "请先运行 bgmb release prepare"
        if self.local_only:
            if self.prepared_release_status == "valid_local":
                return "prepared release 本地有效；请运行 bgmb doctor 检查远端状态"
            return "本地检查完成；请运行 bgmb doctor 检查远端状态"
        if self.prepared_release_status == "valid_local":
            if self.origin_main != "synchronized":
                return "请先：git push origin main\n然后：bgmb release publish"
            if self.gh_pages != "reachable":
                return "无法确认 gh-pages，请检查网络或远端后重试"
        if self.prepared_release_status == "publishable":
            return "prepared release 有效，可以执行：\nbgmb release publish"
        if self.origin_main != "synchronized":
            return "main 尚未与 origin/main 同步，暂不能发布"
        if self.gh_pages != "reachable":
            return "无法确认 gh-pages，请检查网络或远端后重试"
        return "可以准备发布"


@dataclass(frozen=True)
class PreparedRelease:
    """The output of a successful release preparation."""

    state_path: Path
    release_version: str
    report_path: Path


def local_status(project_root: Path) -> LocalStatus:
    """Return local release facts without fetching or changing any state."""
    root = project_root.resolve()
    settings, _, _ = load_rules(root / "config")
    branch = _git_value(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    head = _git_value(root, "rev-parse", "HEAD")
    worktree_clean = not _worktree_changes(root)
    data_status, generation = _data_status(root / "workspace")
    marker, pages_build, candidate = _pages_status(root, head, generation, data_status)
    prepared_status, prepared_version = _prepared_local_status(
        root, head, generation, data_status, marker
    )
    try:
        package = distribution_version("bgm-side-b")
    except PackageNotFoundError:
        package = "未安装"
    try:
        pending = AtomicOutput(
            root / "dist", workspace_directory=root / "workspace"
        ).pending_profile()
    except OutputError:
        pending = "invalid"
    return LocalStatus(
        source_version=__version__,
        package_version=package,
        branch=branch,
        head=head,
        worktree_clean=worktree_clean,
        data_status=data_status,
        data_generation=generation,
        quarters=settings.scope.release_quarters,
        pages_build=pages_build,
        pages_candidate=candidate,
        pending_promotion=pending,
        prepared_release_status=prepared_status,
        prepared_release_version=prepared_version,
        marker=marker,
    )


def doctor(project_root: Path, *, local_only: bool = False) -> DoctorResult:
    """Inspect release readiness; only the non-local form refreshes Git refs."""
    root = project_root.resolve()
    settings, _, _ = load_rules(root / "config")
    local = local_status(root)
    audit = ReleaseDataAuditor(root, settings).audit()
    sqlite_status = _sqlite_status(audit)
    if local_only:
        return DoctorResult(
            local,
            audit,
            sqlite_status,
            "未检查",
            "未检查",
            None,
            None,
            True,
            local.prepared_release_status,
        )
    origin_main = _origin_main_status(root)
    gh_pages, remote_app, remote_release = _gh_pages_status(root)
    prepared_status = _prepared_remote_status(root, local, origin_main, gh_pages)
    return DoctorResult(
        local,
        audit,
        sqlite_status,
        origin_main,
        gh_pages,
        remote_app,
        remote_release,
        False,
        prepared_status,
    )


def prepare_release(
    project_root: Path, reporter: ProgressReporter | None = None
) -> PreparedRelease:
    """Audit, rebuild Pages, dry-run publication, and bind the resulting state."""
    root = project_root.resolve()
    active_reporter = reporter or NullProgressReporter()
    active_reporter.start(stage="release-preflight", message="正在执行本地发布预检")
    local = local_status(root)
    _require_prepare_preflight(local)
    settings, tags, sources = load_rules(root / "config")
    active_reporter.stage(stage="release-audit", message="正在执行资料审计")
    audit = ReleaseDataAuditor(root, settings).audit()
    if not audit.passed:
        raise WorkflowError("资料审计未通过，请先运行 bgmb audit 查看详情")
    database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    active_reporter.stage(stage="release-build", message="正在构建 Pages 候选目录")
    try:
        ArchiveBuilder(
            root, database, settings, tags, sources, reporter=active_reporter
        ).build(None, target="pages")
    except (BuildError, ValueError) as error:
        raise WorkflowError(f"Pages 构建失败：{error}") from error
    active_reporter.stage(stage="release-dry-run", message="正在执行发布 dry-run")
    try:
        run = Publisher(root, active_reporter).publish(dry_run=True)
    except (PublishError, ValueError) as error:
        raise WorkflowError(f"发布 dry-run 失败：{error}") from error
    marker = _require_marker(root)
    candidate_hash = _candidate_hash(root)
    remote_commit = _remote_commit(root, "origin", "gh-pages")
    report = _project_relative(root, run.report_path)
    payload = {
        "schema": 1,
        "source_commit": marker["source_commit"],
        "app_version": __version__,
        "data_generation": marker["data_generation"],
        "pages_candidate_id": marker["candidate_id"],
        "candidate_content_hash": candidate_hash,
        "remote_gh_pages_commit": remote_commit,
        "tentative_release_version": run.release_version,
        "prepared_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dry_run_report": report,
    }
    destination = root / "workspace" / "state" / "prepared-release.json"
    _write_prepared(destination, payload)
    active_reporter.complete(
        stage="prepared-release",
        message="发布准备完成；尚未推送或发布",
        counters={"版本": run.release_version},
    )
    return PreparedRelease(destination, run.release_version, run.report_path)


def publish_prepared_release(
    project_root: Path, reporter: ProgressReporter | None = None
) -> PublishRun:
    """Publish only a still-bound prepared state after refreshing remote safety data."""
    root = project_root.resolve()
    active_reporter = reporter or NullProgressReporter()
    active_reporter.start(
        stage="release-preflight", message="正在验证 prepared release"
    )
    prepared = _read_prepared(root / "workspace" / "state" / "prepared-release.json")
    if _is_prepared_consumed(root, prepared):
        raise WorkflowError(
            "prepared release 已经发布；请重新运行：bgmb release prepare"
        )
    _validate_prepared_local(root, prepared)
    active_reporter.stage(stage="origin-main", message="正在确认 origin/main")
    if _origin_main_status(root) != "synchronized":
        raise _invalid_prepared()
    active_reporter.stage(stage="remote-release", message="正在确认 gh-pages")
    try:
        Publisher(root, active_reporter)._remote_state(
            "origin", "gh-pages", required=True
        )
    except PublishError as error:
        raise WorkflowError(
            f"无法确认 gh-pages；{_invalid_prepared_message()}"
        ) from error
    if (
        _remote_commit(root, "origin", "gh-pages")
        != prepared["remote_gh_pages_commit"]
    ):
        raise _invalid_prepared()
    try:
        run = Publisher(root, active_reporter).publish()
    except PublishError as error:
        raise WorkflowError(f"发布失败：{error}") from error
    if not run.published:
        return run
    return _consume_prepared_state(root, prepared, run, active_reporter)


def _require_prepare_preflight(status: LocalStatus) -> None:
    if status.branch != "main":
        raise WorkflowError("release prepare 必须在 main 分支执行")
    if not status.worktree_clean:
        raise WorkflowError("release prepare 需要干净的工作树")
    if status.data_status != "clean":
        raise WorkflowError("资料状态不是 clean，请完成同步后再准备发布")
    if status.pending_promotion is not None:
        raise WorkflowError(
            f"存在 pending promotion，请先运行 bgmb promote {status.pending_promotion}"
        )


def _validate_prepared_local(root: Path, prepared: dict[str, object]) -> None:
    status = local_status(root)
    if (
        status.branch != "main"
        or status.head != prepared["source_commit"]
        or __version__ != prepared["app_version"]
        or status.data_generation != prepared["data_generation"]
        or status.data_status != "clean"
        or not status.worktree_clean
        or status.pending_promotion is not None
    ):
        raise _invalid_prepared()
    marker = _require_marker(root)
    if (
        marker["candidate_id"] != prepared["pages_candidate_id"]
        or marker["source_commit"] != prepared["source_commit"]
        or marker["app_version"] != prepared["app_version"]
        or marker["data_generation"] != prepared["data_generation"]
        or _candidate_hash(root) != prepared["candidate_content_hash"]
    ):
        raise _invalid_prepared()


def _prepared_local_status(
    root: Path,
    head: str | None,
    generation: int | None,
    data_status: str,
    marker: dict[str, object] | None,
) -> tuple[str, str | None]:
    """Classify prepared state only from local facts without fetching a remote."""
    path = root / "workspace" / "state" / "prepared-release.json"
    if not path.is_file():
        return "none", None
    try:
        prepared = _read_prepared(path)
    except WorkflowError:
        return "invalid", None
    version = str(prepared["tentative_release_version"])
    if _is_prepared_consumed(root, prepared):
        return "consumed", version
    if (
        data_status != "clean"
        or head != prepared["source_commit"]
        or __version__ != prepared["app_version"]
        or generation != prepared["data_generation"]
        or marker is None
        or marker.get("candidate_id") != prepared["pages_candidate_id"]
        or marker.get("source_commit") != prepared["source_commit"]
        or marker.get("app_version") != prepared["app_version"]
        or marker.get("data_generation") != prepared["data_generation"]
    ):
        return "stale", version
    try:
        current_hash = _candidate_hash(root)
    except WorkflowError:
        return "stale", version
    if current_hash != prepared["candidate_content_hash"]:
        return "stale", version
    return "valid_local", version


def _prepared_remote_status(
    root: Path, local: LocalStatus, origin_main: str, gh_pages: str
) -> str:
    """Extend a local prepared-state result only after doctor refreshed Git refs."""
    if local.prepared_release_status != "valid_local":
        return local.prepared_release_status
    try:
        prepared = _read_prepared(
            root / "workspace" / "state" / "prepared-release.json"
        )
    except WorkflowError:
        return "invalid"
    if gh_pages == "reachable" and (
        _remote_commit(root, "origin", "gh-pages")
        != prepared["remote_gh_pages_commit"]
    ):
        return "stale"
    if origin_main == "synchronized" and gh_pages == "reachable":
        return "publishable"
    return "valid_local"


def _prepared_display(status: str, version: str | None) -> str:
    """Render known prepared-state values without failing on future variants."""
    labels = {
        "none": "none",
        "valid_local": "本地有效",
        "publishable": "可发布",
        "stale": "已失效",
        "invalid": "状态无效",
        "consumed": "已消费",
    }
    label = labels.get(status, status)
    return f"{version}（{label}）" if version is not None else label


def _pages_status(
    root: Path, head: str | None, generation: int | None, data_status: str
) -> tuple[dict[str, object] | None, str, str]:
    try:
        marker = read_pages_build_marker(root / "workspace")
    except ValueError:
        return None, "missing", "missing"
    if (
        marker.get("profile") != "pages"
        or marker.get("source_commit") != head
        or marker.get("app_version") != __version__
        or marker.get("data_generation") != generation
        or data_status != "clean"
    ):
        return marker, "stale", "stale"
    try:
        Publisher(root)._validate_candidate_tree(marker)
    except PublishError:
        return marker, "fresh", "stale"
    return marker, "fresh", "OK"


def _data_status(workspace: Path) -> tuple[str, int | None]:
    try:
        generation = read_data_generation(workspace)
        return ("dirty" if data_generation_is_dirty(workspace) else "clean"), generation
    except ValueError:
        return "invalid", None


def _sqlite_status(audit: AuditResult) -> str:
    blocking = {"workspace", "schema", "integrity", "foreign_keys"}
    return (
        "OK"
        if not blocking.intersection(failure.check for failure in audit.failures)
        else "错误"
    )


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


def _gh_pages_status(root: Path) -> tuple[str, str | None, str | None]:
    try:
        release, _ = Publisher(root)._remote_state("origin", "gh-pages", required=True)
    except PublishError:
        return "unreachable", None, None
    if release is None:
        return "reachable", None, None
    app = release.get("app_version")
    published = release.get("release_version")
    return (
        "reachable",
        app if isinstance(app, str) else None,
        published if isinstance(published, str) else None,
    )


def _require_marker(root: Path) -> dict[str, object]:
    try:
        marker = read_pages_build_marker(root / "workspace")
    except ValueError as error:
        raise _invalid_prepared() from error
    required = ("candidate_id", "source_commit", "app_version", "data_generation")
    if not all(key in marker for key in required):
        raise _invalid_prepared()
    return marker


def _candidate_hash(root: Path) -> str:
    try:
        entries = index_candidate(
            root / "dist" / "pages", pages_profile().deployment_path
        )
    except ManifestError as error:
        raise _invalid_prepared() from error
    return candidate_content_hash(entries)


def _read_prepared(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _invalid_prepared() from error
    required = {
        "schema",
        "source_commit",
        "app_version",
        "data_generation",
        "pages_candidate_id",
        "candidate_content_hash",
        "remote_gh_pages_commit",
        "tentative_release_version",
        "prepared_at",
        "dry_run_report",
    }
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or not required.issubset(value)
    ):
        raise _invalid_prepared()
    string_keys = required - {"schema", "data_generation", "remote_gh_pages_commit"}
    if not all(isinstance(value[key], str) and value[key] for key in string_keys):
        raise _invalid_prepared()
    if not isinstance(value["data_generation"], int) or value["data_generation"] < 0:
        raise _invalid_prepared()
    if value["remote_gh_pages_commit"] is not None and not isinstance(
        value["remote_gh_pages_commit"], str
    ):
        raise _invalid_prepared()
    report = PurePosixPath(str(value["dry_run_report"]))
    if report.is_absolute() or ".." in report.parts or not report.parts:
        raise _invalid_prepared()
    return value


def _write_prepared(destination: Path, payload: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        stream.write("\n")
        temporary = stream.name
    os.replace(temporary, destination)


def _consume_prepared_state(
    root: Path,
    prepared: dict[str, object],
    run: PublishRun,
    reporter: ProgressReporter,
) -> PublishRun:
    """Remove a successfully published prepared state without hiding local failure."""
    state_path = root / "workspace" / "state" / "prepared-release.json"
    try:
        state_path.unlink()
    except OSError:
        try:
            _write_consumed_prepared(root, prepared, run)
        except OSError:
            reporter.warning(
                stage="prepared-release",
                message=(
                    "远端发布已成功，但本地 prepared state 清理和标记均失败；"
                    "请勿重复发布"
                ),
            )
            warning = "prepared-state-cleanup-and-marker-failed"
        else:
            reporter.warning(
                stage="prepared-release",
                message=(
                    "远端发布已成功，但本地 prepared state 清理失败；"
                    "已标记为已消费"
                ),
            )
            warning = "prepared-state-cleanup-failed"
        return replace(run, warnings=(*run.warnings, warning))
    return run


def _write_consumed_prepared(
    root: Path, prepared: dict[str, object], run: PublishRun
) -> None:
    destination = root / "workspace" / "state" / "prepared-release-consumed.json"
    payload = {
        "schema": 1,
        "prepared_fingerprint": _prepared_fingerprint(prepared),
        "release_version": run.release_version,
        "remote_commit": run.remote_commit,
    }
    _write_prepared(destination, payload)


def _is_prepared_consumed(root: Path, prepared: dict[str, object]) -> bool:
    """Recognize a consumed marker or the matching locally published release."""
    path = root / "workspace" / "state" / "prepared-release-consumed.json"
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        value = None
    if (
        isinstance(value, dict)
        and value.get("schema") == 1
        and value.get("prepared_fingerprint") == _prepared_fingerprint(prepared)
    ):
        return True
    try:
        release = json.loads(
            (root / "dist" / "pages" / "release.json").read_text("utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(release, dict)
        and release.get("release_version") == prepared["tentative_release_version"]
        and release.get("candidate_content_hash")
        == prepared["candidate_content_hash"]
    )


def _prepared_fingerprint(prepared: dict[str, object]) -> str:
    encoded = json.dumps(
        prepared, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise WorkflowError("dry-run 报告路径不在项目目录内") from error


def _remote_commit(root: Path, remote: str, branch: str) -> str | None:
    return _git_value(root, "rev-parse", "--verify", f"{remote}/{branch}")


def _worktree_changes(root: Path) -> list[str]:
    result = _run_git(root, "status", "--porcelain", check=False)
    if result.returncode:
        return ["git-error"]
    return [line for line in result.stdout.splitlines() if not _temporary_plan(line)]


def _temporary_plan(line: str) -> bool:
    return line[3:].replace("\\", "/").startswith("docs/Bangumi-Side-B-Codex-Plan-")


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
    return WorkflowError(_invalid_prepared_message())


def _invalid_prepared_message() -> str:
    return "prepared release 已失效，请重新运行：bgmb release prepare"
