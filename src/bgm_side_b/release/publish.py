"""Transactional, manual gh-pages publication of an existing Pages candidate."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from bgm_side_b import __version__
from bgm_side_b.build.paths import PathResolver
from bgm_side_b.build.profiles import pages_profile
from bgm_side_b.build.templates import TemplateRenderer
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.release.candidate import (
    read_data_generation,
    read_pages_build_marker,
    read_pages_build_snapshot,
)
from bgm_side_b.release.history import (
    history_entry,
    next_release_version,
    unreleased_changes,
    write_history,
)
from bgm_side_b.release.manifest import (
    ManifestError,
    build_snapshot_manifest,
    candidate_content_hash,
    index_candidate,
    manifest_json,
    validate_manifest_payload,
)
from bgm_side_b.release.snapshot import (
    diff_snapshots,
    read_snapshot,
    write_snapshot,
)
from bgm_side_b.release.validation import validate_release_payload


class PublishError(RuntimeError):
    """Raised for a refused or failed publish transaction without stack leakage."""


@dataclass(frozen=True)
class PublishRun:
    """Safe outcome facts for CLI output and tests."""

    dry_run: bool
    release_version: str
    report_path: Path
    published: bool
    remote_commit: str | None
    local_mirror_updated: bool = False
    local_state_updated: bool = False
    warnings: tuple[str, ...] = ()


class Publisher:
    """Validate, stage, and publish a Pages candidate without rebuilding it."""

    def __init__(
        self, project_root: Path, reporter: ProgressReporter | None = None
    ) -> None:
        self.root = project_root.resolve()
        self.workspace = self.root / "workspace"
        self.candidate = self.root / "dist" / "pages"
        self.profile = pages_profile()
        self.reporter = reporter or NullProgressReporter()

    def publish(
        self,
        *,
        dry_run: bool = False,
        remote: str = "origin",
        branch: str = "gh-pages",
    ) -> PublishRun:
        """Publish one fully verified snapshot, or simulate it without Git writes."""
        self._stage(1, "正在检查 main 与干净工作树")
        self._stage(2, "正在检查远端与发布分支")
        self._preconditions(dry_run=dry_run, remote=remote, branch=branch)
        self._stage(3, "正在读取 Pages build marker")
        marker = read_pages_build_marker(self.workspace)
        self._validate_marker(marker)
        self._stage(4, "正在验证 build-bound 事实快照")
        build_snapshot = self._validate_build_snapshot(marker)
        self._stage(5, "正在验证 data generation")
        self._validate_data_generation(marker)
        self._stage(6, "正在验证 Pages candidate index")
        self._validate_candidate_tree(marker)
        self._stage(7, "正在读取远端 gh-pages")
        remote_release, remote_history = self._remote_state(
            remote, branch, required=True
        )
        previous_version = (
            str(remote_release["release_version"]) if remote_release else None
        )
        changes, system_changes, snapshot = self._changes(build_snapshot)
        if (
            remote_release
            and remote_release.get("app_version") != __version__
            and not system_changes
        ):
            raise PublishError("app version changed but CHANGELOG Unreleased is empty")
        rules_hash = str(snapshot["rules_hash"])
        candidate_hash = str(marker["business_content_hash"])
        if remote_release and self._has_no_changes(
            remote_release, candidate_hash, rules_hash, system_changes
        ):
            self.reporter.complete(
                stage="summary", message="无需发布｜候选与远端一致"
            )
            raise PublishError("Pages candidate has no publishable changes")
        release_system_changes = (
            system_changes
            if not remote_release
            or remote_release.get("system_changelog_hash")
            != _system_hash(system_changes)
            else ()
        )
        version = next_release_version(previous_version)
        self._stage(8, "正在计算 tentative 资料版本", counters={"版本": version})
        self._stage(9, "正在比较系统与资料变化")
        staging = self._staging_directory()
        try:
            self._stage(10, "正在创建 release staging")
            shutil.copytree(self.candidate, staging, dirs_exist_ok=True)
            release, manifest, history = self._assemble_release(
                staging,
                version,
                marker,
                changes,
                release_system_changes,
                remote_history,
                candidate_hash,
                rules_hash,
                _system_hash(system_changes),
            )
            self._stage(
                11,
                "snapshot manifest 已生成",
                counters={
                    "文件": manifest.payload()["entry_count"],
                    "字节": manifest.payload()["total_bytes"],
                },
            )
            self._stage(12, "updates 页面已生成")
            self._stage(13, "正在执行 release staging 安全扫描")
            self._validate_staging(staging, manifest, release)
            if dry_run:
                self._stage(14, "正在写入 dry-run 报告")
                report = self._write_report(
                    dry_run=True,
                    release=release,
                    files=manifest.payload()["entry_count"],
                    bytes=manifest.payload()["total_bytes"],
                    push_result="not-published",
                )
                self.reporter.complete(
                    stage="summary",
                    message=f"dry run only：{version} 未发布",
                    counters={
                        "文件": manifest.payload()["entry_count"],
                        "字节": manifest.payload()["total_bytes"],
                    },
                )
                return PublishRun(True, version, report, False, None)
            self.reporter.stage(
                stage="publish-worktree", message="正在创建临时 worktree"
            )
            remote_commit = self._publish_tree(staging, remote, branch, version)
            self.reporter.stage(
                stage="publish-state", message="正在登记本地 release 状态"
            )
            local_mirror_updated, local_state_updated, warnings = self._record_success(
                snapshot, history, staging
            )
            self._stage(14, "正在写入发布报告")
            report = self._write_report(
                dry_run=False,
                release=release,
                files=manifest.payload()["entry_count"],
                bytes=manifest.payload()["total_bytes"],
                push_result="success",
                remote_commit=remote_commit,
                local_mirror_updated=local_mirror_updated,
                local_state_updated=local_state_updated,
                warnings=warnings,
            )
            self.reporter.complete(
                stage="summary",
                message=(
                    "发布成功｜远端已更新｜本地镜像更新失败（可恢复）"
                    if "local-mirror-update-failed" in warnings
                    else "发布成功｜远端已更新"
                ),
                counters={
                    "文件": manifest.payload()["entry_count"],
                    "字节": manifest.payload()["total_bytes"],
                },
            )
            return PublishRun(
                False,
                version,
                report,
                True,
                remote_commit,
                local_mirror_updated,
                local_state_updated,
                tuple(warnings),
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _stage(
        self,
        completed: int,
        message: str,
        *,
        counters: dict[str, str | int] | None = None,
    ) -> None:
        self.reporter.stage(
            stage="publish",
            message=message,
            completed=completed,
            total=14,
            counters=counters,
        )

    def _preconditions(self, *, dry_run: bool, remote: str, branch: str) -> None:
        if self._git("rev-parse", "--abbrev-ref", "HEAD") != "main":
            raise PublishError("publish must run from main")
        dirty = [
            line
            for line in self._git_lines("status", "--porcelain")
            if not _temporary_plan(line)
        ]
        if dirty:
            raise PublishError("publish requires a clean working tree")
        if not _safe_branch(branch):
            raise PublishError("publish branch is invalid")
        if remote == "origin" and branch != "gh-pages":
            raise PublishError("origin publish may target gh-pages only")
        if not dry_run and remote == "origin":
            url = self._git("remote", "get-url", "origin")
            if not _allowed_origin(url):
                raise PublishError("origin does not match the allowed repository")
            self.reporter.stage(
                stage="origin-main", message="正在读取远端 origin/main"
            )
            with self.reporter.activity(
                stage="origin-main", message="等待 Git 远端 main"
            ):
                self._run_git("fetch", "origin", "main")
            if self._git("rev-parse", "HEAD") != self._git("rev-parse", "origin/main"):
                raise PublishError("main must be pushed before publishing Pages")

    def _validate_marker(self, marker: dict[str, object]) -> None:
        if marker.get("profile") != "pages":
            raise PublishError("Pages build marker has the wrong profile")
        if marker.get("source_commit") in (None, "unavailable"):
            raise PublishError("Pages build marker has no usable source commit")
        if marker.get("source_commit") != self._git("rev-parse", "HEAD"):
            raise PublishError("Pages build marker is stale")

    def _validate_build_snapshot(
        self, marker: dict[str, object]
    ) -> dict[str, object]:
        try:
            build_snapshot = read_pages_build_snapshot(self.workspace)
        except ValueError as error:
            raise PublishError(str(error)) from error
        if (
            build_snapshot.get("candidate_id") != marker.get("candidate_id")
            or build_snapshot.get("facts_snapshot_hash")
            != marker.get("facts_snapshot_hash")
            or build_snapshot.get("source_commit") != marker.get("source_commit")
        ):
            raise PublishError("Pages build marker and facts snapshot disagree")
        return build_snapshot

    def _validate_data_generation(self, marker: dict[str, object]) -> None:
        try:
            generation = read_data_generation(self.workspace)
        except ValueError as error:
            raise PublishError(str(error)) from error
        if generation > int(marker.get("data_generation", -1)):
            raise PublishError("facts changed since the Pages build; rebuild Pages")

    def _validate_candidate_tree(self, marker: dict[str, object]) -> None:
        try:
            entries = index_candidate(self.candidate, self.profile.deployment_path)
        except ManifestError as error:
            raise PublishError(str(error)) from error
        actual_hash = candidate_content_hash(entries)
        if marker.get("business_content_hash") != actual_hash:
            release_path = self.candidate / "release.json"
            try:
                mirrored = json.loads(release_path.read_text("utf-8"))
                validate_release_payload(mirrored)
            except (OSError, json.JSONDecodeError, ManifestError) as error:
                raise PublishError(
                    "Pages candidate differs from its successful build marker"
                ) from error
            if (
                mirrored.get("candidate_content_hash")
                != marker.get("business_content_hash")
                or mirrored.get("content_hash") != actual_hash
            ):
                raise PublishError(
                    "Pages candidate differs from its successful build marker"
                )
        required = (
            "manifest.webmanifest",
            "sw.js",
            "settings/index.html",
            "updates/index.html",
            "offline.html",
        )
        if not all((self.candidate / item).is_file() for item in required):
            raise PublishError("Pages candidate lacks its PWA shell")
        _scan_public_tree(self.candidate)

    def _remote_state(
        self, remote: str, branch: str, *, required: bool
    ) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
        if not required:
            return None, []
        with self.reporter.activity(
            stage="remote-release", message="等待 Git 远端 gh-pages"
        ):
            result = self._run_git(
                "fetch",
                remote,
                f"{branch}:refs/remotes/{remote}/{branch}",
                check=False,
            )
        if result.returncode:
            message = (result.stderr + result.stdout).lower()
            if "couldn't find remote ref" in message or "not our ref" in message:
                return None, []
            raise PublishError("could not fetch the publish branch")
        reference = f"{remote}/{branch}"
        release = _git_json(
            self._run_git("show", f"{reference}:release.json", check=False)
        )
        history = _git_json(
            self._run_git("show", f"{reference}:release-history.json", check=False),
            fallback=[],
        )
        if release is not None:
            try:
                validate_release_payload(release)
            except ManifestError as error:
                raise PublishError("remote release metadata is invalid") from error
        if not isinstance(history, list) or not all(
            isinstance(item, dict) for item in history
        ):
            raise PublishError("remote release history is invalid")
        return release, history

    def _changes(
        self, build_snapshot: dict[str, object]
    ) -> tuple[dict[str, object], tuple[str, ...], dict[str, object]]:
        snapshot = build_snapshot.get("facts")
        if not isinstance(snapshot, dict):
            raise PublishError("Pages facts snapshot is invalid; rebuild Pages")
        previous = read_snapshot(self.workspace / "releases" / "current-snapshot.json")
        return (
            diff_snapshots(previous, snapshot),
            unreleased_changes(self.root / "CHANGELOG.md"),
            snapshot,
        )

    def _has_no_changes(
        self,
        previous: dict[str, object],
        candidate_hash: str,
        rules_hash: str,
        system_changes: tuple[str, ...],
    ) -> bool:
        return (
            previous.get("candidate_content_hash") == candidate_hash
            and previous.get("app_version") == __version__
            and previous.get("rules_hash") == rules_hash
            and previous.get("system_changelog_hash") == _system_hash(system_changes)
        )

    def _staging_directory(self) -> Path:
        destination = self.workspace / "tmp" / f"publish-{uuid.uuid4().hex}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def _assemble_release(
        self,
        staging: Path,
        version: str,
        marker: dict[str, object],
        changes: dict[str, object],
        system_changes: tuple[str, ...],
        remote_history: list[dict[str, object]],
        candidate_hash: str,
        rules_hash: str,
        system_changelog_hash: str,
    ) -> tuple[dict[str, object], object, list[dict[str, object]]]:
        change_kind = _change_kind(changes, system_changes)
        history = list(remote_history)
        release_for_page = {
            "release_version": version,
            "app_version": __version__,
            "change_kind": change_kind,
            "system": system_changes,
            "data": _change_lines(changes),
            "history": history,
        }
        _render_updates(self.root, staging, release_for_page)
        (staging / ".nojekyll").write_text("", encoding="utf-8")
        entries = index_candidate(staging, self.profile.deployment_path)
        manifest = build_snapshot_manifest(
            entries,
            release_version=version,
            app_version=__version__,
            deployment_path=self.profile.deployment_path,
        )
        manifest_text = manifest_json(manifest)
        (staging / "snapshot-manifest.json").write_bytes(manifest_text.encode())
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        release = {
            "schema": 1,
            "release_version": version,
            "app_version": __version__,
            "generated_at": now,
            "published_at": now,
            "quarter_count": int(marker["quarter_count"]),
            "subject_count": int(marker["subject_count"]),
            "total_bytes": manifest.payload()["total_bytes"],
            "latest_quarter": _latest_quarter(staging),
            "content_hash": manifest.content_hash,
            "candidate_content_hash": candidate_hash,
            "rules_hash": rules_hash,
            "system_changelog_hash": system_changelog_hash,
            "manifest_url": "snapshot-manifest.json",
            "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
            "change_kind": change_kind,
            "summary": {"system": list(system_changes), "data": _change_lines(changes)},
        }
        validate_release_payload(release)
        entry = history_entry(
            release_version=version,
            app_version=__version__,
            content_hash=manifest.content_hash,
            quarter_count=int(marker["quarter_count"]),
            subject_count=int(marker["subject_count"]),
            total_bytes=int(manifest.payload()["total_bytes"]),
            change_kind=change_kind,
            system_summary=system_changes,
            data_summary=changes,
            commit_sha=str(marker["source_commit"]),
        )
        history.insert(0, entry)
        (staging / "release.json").write_text(
            _json(release), encoding="utf-8", newline="\n"
        )
        (staging / "release-history.json").write_text(
            _json(history), encoding="utf-8", newline="\n"
        )
        return release, manifest, history

    def _validate_staging(
        self, staging: Path, manifest: object, release: dict[str, object]
    ) -> None:
        _scan_public_tree(staging)
        payload = json.loads((staging / "snapshot-manifest.json").read_text("utf-8"))
        try:
            validate_manifest_payload(payload)
            validate_release_payload(release)
        except ManifestError as error:
            raise PublishError("release staging validation failed") from error
        if payload["content_hash"] != release["content_hash"]:
            raise PublishError("release and manifest hashes disagree")

    def _publish_tree(
        self, staging: Path, remote: str, branch: str, version: str
    ) -> str:
        temporary = Path(tempfile.mkdtemp(prefix="bgm-side-b-pages-"))
        try:
            self.reporter.stage(
                stage="publish-worktree", message="正在创建临时 worktree"
            )
            fetched = self._run_git(
                "fetch",
                remote,
                f"{branch}:refs/remotes/{remote}/{branch}",
                check=False,
            )
            if fetched.returncode:
                message = (fetched.stderr + fetched.stdout).lower()
                if (
                    "couldn't find remote ref" not in message
                    and "not our ref" not in message
                ):
                    raise PublishError("could not fetch the publish branch")
                self._run_git("worktree", "add", "--detach", str(temporary), "HEAD")
                self._run_git("-C", str(temporary), "checkout", "--orphan", branch)
            else:
                self._run_git(
                    "worktree", "add", "--detach", str(temporary), f"{remote}/{branch}"
                )
            _replace_worktree_contents(temporary, staging)
            self._run_git("-C", str(temporary), "add", "--all")
            self.reporter.stage(
                stage="publish-commit", message="正在创建 release commit"
            )
            self._run_git("-C", str(temporary), "commit", "-m", f"release: {version}")
            self.reporter.stage(
                stage="publish-push",
                message=f"即将推送 release {version} 到 {remote}/{branch}",
            )
            with self.reporter.activity(
                stage="publish-push", message="等待 GitHub 接收 gh-pages push"
            ):
                self._run_git("-C", str(temporary), "push", remote, f"HEAD:{branch}")
            return self._git("-C", str(temporary), "rev-parse", "HEAD")
        except KeyboardInterrupt as error:
            self._report_interrupted_push(remote, branch, version)
            raise PublishError(
                "publish was interrupted; remote result needs manual verification"
            ) from error
        except subprocess.CalledProcessError as error:
            self.reporter.error(
                stage="publish-push",
                message="发布失败｜远端旧版本保持不变｜未登记本次 release",
            )
            raise PublishError("publish Git transaction failed") from error
        finally:
            self.reporter.stage(
                stage="publish-cleanup", message="正在清理临时 worktree"
            )
            self._run_git("worktree", "remove", "--force", str(temporary), check=False)
            shutil.rmtree(temporary, ignore_errors=True)

    def _report_interrupted_push(self, remote: str, branch: str, version: str) -> None:
        """Re-read the branch once without making a second publication attempt."""
        result = self._run_git(
            "fetch", remote, f"{branch}:refs/remotes/{remote}/{branch}", check=False
        )
        if result.returncode:
            self.reporter.warning(
                stage="publish-push", message="远端结果不确定，需要人工检查"
            )
            return
        release = _git_json(
            self._run_git(
                "show", f"{remote}/{branch}:release.json", check=False
            )
        )
        if release and release.get("release_version") == version:
            self.reporter.warning(
                stage="publish-push",
                message="远端 release 已存在；本地状态尚未登记",
            )
            return
        self.reporter.warning(
            stage="publish-push", message="远端未确认本次 release，需要人工检查"
        )

    def _record_success(
        self,
        snapshot: dict[str, object],
        history: list[dict[str, object]],
        staging: Path,
    ) -> tuple[bool, bool, list[str]]:
        warnings: list[str] = []
        try:
            write_snapshot(
                self.workspace / "releases" / "current-snapshot.json", snapshot
            )
            write_history(self.workspace / "releases" / "history.json", history)
            local_state_updated = True
        except OSError:
            local_state_updated = False
            warnings.append("local-state-update-failed")
        mirror = self.root / "dist" / "pages"
        backup = self.root / "dist" / ".staging" / f"pages-published-{uuid.uuid4().hex}"
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            mirror.replace(backup)
            shutil.copytree(staging, mirror)
        except OSError:
            if backup.exists() and not mirror.exists():
                backup.replace(mirror)
            local_mirror_updated = False
            warnings.append("local-mirror-update-failed")
        else:
            local_mirror_updated = True
            shutil.rmtree(backup, ignore_errors=True)
        return local_mirror_updated, local_state_updated, warnings

    def _write_report(
        self,
        *,
        dry_run: bool,
        release: dict[str, object],
        files: object,
        bytes: object,
        push_result: str,
        remote_commit: str | None = None,
        local_mirror_updated: bool | None = None,
        local_state_updated: bool | None = None,
        warnings: list[str] | None = None,
    ) -> Path:
        payload = {
            "dry_run": dry_run,
            "release": release["release_version"],
            "app_version": release["app_version"],
            "source_commit": self._git("rev-parse", "HEAD"),
            "content_hash": release["content_hash"],
            "files": files,
            "bytes": bytes,
            "change_kind": release["change_kind"],
            "push_result": push_result,
            "remote_commit": remote_commit,
            "success": push_result in {"success", "not-published"},
            "remote_published": push_result == "success",
            "local_mirror_updated": local_mirror_updated,
            "local_state_updated": local_state_updated,
            "warnings": warnings or [],
        }
        reports = self.workspace / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = reports / f"publish-{release['release_version']}-{timestamp}.json"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=reports, delete=False
        ) as stream:
            stream.write(_json(payload))
            temporary = stream.name
        os.replace(temporary, destination)
        return destination

    def _git(self, *args: str) -> str:
        return self._run_git(*args).stdout.strip()

    def _git_lines(self, *args: str) -> list[str]:
        return [line for line in self._git(*args).splitlines() if line]

    def _run_git(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=check,
        )


def _render_updates(root: Path, staging: Path, release: dict[str, object]) -> None:
    assets = {
        path.name: f"assets/{path.name}"
        for path in (staging / "assets").iterdir()
        if path.is_file()
    }
    stylesheet = next(
        value
        for name, value in assets.items()
        if name.startswith("site.") and name.endswith(".css")
    )
    archive_js = next(
        value
        for name, value in assets.items()
        if name.startswith("site.") and name.endswith(".js")
    )
    controller = next(
        value for name, value in assets.items() if name.startswith("pwa-controller.")
    )
    ui = next(value for name, value in assets.items() if name.startswith("pwa-ui."))
    favicon = next(
        value for name, value in assets.items() if name.startswith("favicon.")
    )
    resolver = PathResolver(pages_profile())
    document = "updates/index.html"
    rendered = TemplateRenderer(root / "templates").render_reference_page(
        "updates.html",
        stylesheet_href=resolver.asset(document, stylesheet),
        script_href=resolver.asset(document, archive_js),
        favicon_href=resolver.asset(document, favicon),
        manifest_href=resolver.href(document, "manifest.webmanifest"),
        apple_touch_icon_href=resolver.href(document, "icons/icon-192.png"),
        pwa_controller_href=resolver.asset(document, controller),
        pwa_ui_href=resolver.asset(document, ui),
        home_href=resolver.href(document, "index.html"),
        settings_href=resolver.href(document, "settings/index.html"),
        updates_href="./",
        app_version=__version__,
        release=release,
        history=tuple(release["history"]),
    )
    (staging / document).write_text(rendered, encoding="utf-8", newline="\n")


def _change_kind(changes: dict[str, object], system: tuple[str, ...]) -> str:
    data = changes.get("kind") != "data" or any(
        value
        for key, value in changes.items()
        if key not in {"kind", "failure_summary"}
    )
    return "both" if data and system else "system" if system else "data"


def _change_lines(changes: dict[str, object]) -> list[str]:
    if changes.get("kind") == "initial_snapshot":
        return ["首次完整资料快照"]
    labels = {
        "subjects_added": "新增作品",
        "subjects_removed": "移除作品",
        "subjects_updated": "更新作品",
        "covers_changed": "更新封面",
    }
    return [
        f"{label} {changes[key]}" for key, label in labels.items() if changes.get(key)
    ] or ["资料无结构化变化"]


def _latest_quarter(staging: Path) -> str | None:
    values = [path.parent.name for path in (staging / "quarters").glob("*/index.html")]
    return max(values) if values else None


def _replace_worktree_contents(worktree: Path, staging: Path) -> None:
    for path in worktree.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in staging.iterdir():
        target = worktree / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)


def _scan_public_tree(root: Path) -> None:
    forbidden = (
        "api.bgm.tv",
        "authorization:",
        "bangumi-side-b.sqlite3",
        "C:\\Users\\",
        "token=",
    )
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {
            ".html",
            ".css",
            ".js",
            ".json",
            ".webmanifest",
        }:
            content = path.read_text(encoding="utf-8")
            if any(value.lower() in content.lower() for value in forbidden):
                raise PublishError("Pages tree contains forbidden content")
    if (root / "media" / "characters").exists():
        raise PublishError("Pages tree contains character media")


def _git_json(
    result: subprocess.CompletedProcess[str], fallback: object = None
) -> object:
    if result.returncode:
        return fallback
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return fallback


def _json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    )


def _system_hash(changes: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(changes).encode()).hexdigest()


def _safe_branch(branch: str) -> bool:
    if not branch or any(value in branch for value in (":", "^", "~", "..", "@{", " ")):
        return False
    return all(character.isalnum() or character in "._/-" for character in branch)


def _allowed_origin(value: str) -> bool:
    if value.startswith("git@github.com:"):
        return value.removeprefix("git@github.com:") == (
            "MyKr-YSteinsK/bangumi-side-b.git"
        )
    parsed = urlparse(value)
    return (
        parsed.scheme in {"https", "ssh"}
        and parsed.hostname == "github.com"
        and parsed.path == "/MyKr-YSteinsK/bangumi-side-b.git"
        and not parsed.query
        and not parsed.fragment
    )


def _temporary_plan(line: str) -> bool:
    return line[3:].replace("\\", "/").startswith("docs/Bangumi-Side-B-Codex-Plan-")
