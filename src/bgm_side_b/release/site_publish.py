"""Exact ``dist/site`` publication through an ordinary gh-pages push."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from bgm_side_b import __version__
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.release.site_candidate import (
    CandidateIdentity,
    SiteCandidateError,
    validate_site,
)


class SitePublishError(RuntimeError):
    """Raised when exact-site publication cannot be proven safe."""


_OFFICIAL_ORIGIN = "github.com/mykr-ysteinsk/bangumi-side-b"
_RELEASE_COMMIT = re.compile(
    r"^release: (?P<date>\d{4}\.\d{2}\.\d{2})\.(?P<serial>[1-9]\d*) "
    r"\[source (?P<source>[0-9a-f]{12})\]$"
)


def validate_release_origin(project_root: Path, remote: str = "origin") -> str:
    """Require a configured remote to be this project's official GitHub origin."""
    root = project_root.resolve()
    result = subprocess.run(
        ["git", "config", "--get", f"remote.{remote}.url"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SitePublishError(f"{remote} remote is not configured for this project")
    value = result.stdout.strip()
    if _normalise_origin(value) != _OFFICIAL_ORIGIN:
        raise SitePublishError("release publish requires the official project origin")
    return value


def _normalise_origin(value: str) -> str | None:
    """Return a canonical official-origin key for the supported GitHub URL forms."""
    text = value.strip()
    if not text:
        return None
    if text.startswith("git@"):
        if ":" not in text or "/" not in text:
            return None
        user_host, path = text.split(":", 1)
        if user_host.casefold() != "git@github.com" or path.count("/") != 1:
            return None
        owner, repository = path.split("/", 1)
    else:
        parsed = urlsplit(text)
        if parsed.scheme not in {"https", "ssh"}:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            return None
        if parsed.query or parsed.fragment or port is not None:
            return None
        if parsed.scheme == "https" and parsed.username is not None:
            return None
        if parsed.scheme == "ssh" and parsed.username != "git":
            return None
        path = parsed.path.strip("/")
        if path.count("/") != 1:
            return None
        owner, repository = path.split("/", 1)
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository or "." in repository or "/" in repository:
        return None
    return "github.com/" + f"{owner}/{repository}".casefold()


@dataclass(frozen=True)
class SitePublishRun:
    dry_run: bool
    release_version: str
    report_path: Path
    published: bool
    remote_commit: str | None
    warnings: tuple[str, ...] = ()


class UnifiedPublisher:
    """Publish one validated site tree without rebuilding or synchronising."""

    def __init__(
        self, project_root: Path, reporter: ProgressReporter | None = None
    ) -> None:
        self.root = project_root.resolve()
        self.workspace = self.root / "workspace"
        self.site = self.root / "dist" / "site"
        self.reporter = reporter or NullProgressReporter()

    def publish(
        self,
        *,
        dry_run: bool = False,
        remote: str = "origin",
        branch: str = "gh-pages",
        expected_remote_commit: str | None = None,
        expected_content_hash: str | None = None,
    ) -> SitePublishRun:
        if branch != "gh-pages":
            raise SitePublishError("gh-pages only publication is supported")
        self.reporter.start(stage="site-validate", message="正在验证 dist/site")
        candidate = self._candidate()
        if expected_content_hash is not None and (
            candidate.identity.content_hash != expected_content_hash
        ):
            raise SitePublishError("dist/site changed; run release prepare again")
        self.reporter.stage(stage="remote-state", message="正在读取远端 gh-pages")
        remote_commit = self.remote_commit(remote, branch)
        if expected_remote_commit is not None and (
            remote_commit != expected_remote_commit
        ):
            raise SitePublishError("gh-pages changed; run release prepare again")
        release_version = self._release_version(remote, branch)
        staging = Path(tempfile.mkdtemp(prefix="bgmb-release-"))
        try:
            shutil.copytree(self.site, staging / "site")
            if dry_run:
                report = self._write_report(
                    candidate.identity,
                    release_version,
                    remote,
                    branch,
                    dry_run=True,
                    remote_commit=remote_commit,
                )
                self.reporter.complete(
                    stage="summary", message="仅 dry-run：未修改远端 gh-pages"
                )
                return SitePublishRun(True, release_version, report, False, None)
            self.reporter.stage(
                stage="publish-worktree", message="正在创建临时发布 worktree"
            )
            pushed = self._publish_tree(
                staging / "site",
                remote,
                branch,
                remote_commit,
                release_version,
                candidate.identity.source_commit,
            )
            report = self._write_report(
                candidate.identity,
                release_version,
                remote,
                branch,
                dry_run=False,
                remote_commit=pushed,
            )
            self.reporter.complete(stage="summary", message="发布成功｜远端已更新")
            return SitePublishRun(False, release_version, report, True, pushed)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def candidate(self, *, source_commit: str = ""):
        """Return the current validated site candidate for workflow binding."""
        try:
            return validate_site(self.site, source_commit=source_commit)
        except SiteCandidateError as error:
            raise SitePublishError(str(error)) from error

    def remote_commit(
        self, remote: str = "origin", branch: str = "gh-pages"
    ) -> str | None:
        result = self._git("ls-remote", "--heads", remote, branch, check=False)
        if result.returncode:
            raise SitePublishError("cannot read remote gh-pages")
        line = next((line for line in result.stdout.splitlines() if line), None)
        if line is None:
            return None
        value = line.split()[0]
        return value if len(value) == 40 else None

    def _candidate(self):
        try:
            return validate_site(self.site, source_commit=self._head())
        except SiteCandidateError as error:
            raise SitePublishError(str(error)) from error

    def _publish_tree(
        self,
        site: Path,
        remote: str,
        branch: str,
        expected_remote_commit: str | None,
        release_version: str,
        source_commit: str,
    ) -> str:
        current_remote = self.remote_commit(remote, branch)
        if current_remote != expected_remote_commit:
            raise SitePublishError("gh-pages changed during publication")
        worktree = Path(tempfile.mkdtemp(prefix="bgmb-pages-worktree-"))
        try:
            self._git("init", "-q", str(worktree))
            self._git("remote", "add", remote, self._remote_url(remote), cwd=worktree)
            if expected_remote_commit is not None:
                self._git("fetch", "-q", remote, branch, cwd=worktree)
                self._git(
                    "checkout", "-q", "-B", branch, f"{remote}/{branch}", cwd=worktree
                )
            else:
                self._git("checkout", "-q", "--orphan", branch, cwd=worktree)
            for child in worktree.iterdir():
                if child.name != ".git":
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            for child in site.iterdir():
                destination = worktree / child.name
                if child.is_dir():
                    shutil.copytree(child, destination)
                else:
                    shutil.copy2(child, destination)
            self._git("-c", "core.autocrlf=false", "add", "-A", cwd=worktree)
            status = self._git("status", "--porcelain", cwd=worktree).stdout
            if not status.strip():
                raise SitePublishError("dist/site has no publishable changes")
            self._git(
                "-c",
                "user.name=Bangumi Side B Release",
                "-c",
                "user.email=release@localhost.invalid",
                "commit",
                "-m",
                f"release: {release_version} [source {source_commit[:12]}]",
                cwd=worktree,
            )
            release_commit = self._git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
                raise SitePublishError("release commit could not be identified")
            self._git("push", remote, f"HEAD:{branch}", cwd=worktree)
            remote_after = self.remote_commit(remote, branch)
            if remote_after != release_commit:
                raise SitePublishError(
                    "release commit was pushed but gh-pages advanced "
                    "before confirmation"
                )
            return release_commit
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "git publication failed").strip()
            raise SitePublishError(detail) from error
        finally:
            shutil.rmtree(worktree, ignore_errors=True)

    def _remote_url(self, remote: str) -> str:
        result = self._git("config", "--get", f"remote.{remote}.url")
        value = result.stdout.strip()
        if not value:
            raise SitePublishError("remote is not configured")
        return value

    def _release_version(self, remote: str, branch: str) -> str:
        today = datetime.now(UTC).strftime("%Y.%m.%d")
        message = self._remote_commit_message(remote, branch)
        if message is None:
            return f"{today}.1"
        first_line = message.splitlines()[0].strip() if message.splitlines() else ""
        match = _RELEASE_COMMIT.fullmatch(first_line)
        if match is None:
            return f"{today}.1"
        try:
            release_date = datetime.strptime(match["date"], "%Y.%m.%d").date()
        except ValueError:
            return f"{today}.1"
        if release_date.strftime("%Y.%m.%d") != today:
            return f"{today}.1"
        return f"{today}.{int(match['serial']) + 1}"

    def _remote_commit_message(self, remote: str, branch: str) -> str | None:
        commit = self.remote_commit(remote, branch)
        if commit is None:
            return None
        temporary = Path(tempfile.mkdtemp(prefix="bgmb-remote-read-"))
        try:
            repository = temporary / "remote.git"
            self._git("init", "-q", "--bare", str(repository))
            self._git("remote", "add", remote, self._remote_url(remote), cwd=repository)
            self._git(
                "fetch",
                "-q",
                "--depth=1",
                remote,
                f"refs/heads/{branch}:refs/heads/{branch}",
                cwd=repository,
            )
            result = self._git(
                "log", "-1", "--format=%B", f"refs/heads/{branch}", cwd=repository
            )
            return result.stdout
        except subprocess.CalledProcessError as error:
            detail = (
                error.stderr or error.stdout or "cannot read remote commit message"
            ).strip()
            raise SitePublishError(detail) from error
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _write_report(
        self,
        identity: CandidateIdentity,
        release_version: str,
        remote: str,
        branch: str,
        *,
        dry_run: bool,
        remote_commit: str | None,
    ) -> Path:
        destination = self.workspace / "reports" / "release-publish.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "app_version": __version__,
            "release_version": release_version,
            "source_commit": identity.source_commit,
            "candidate_content_hash": identity.content_hash,
            "artifact_count": identity.artifact_count,
            "total_bytes": identity.total_bytes,
            "remote": remote,
            "branch": branch,
            "remote_commit": remote_commit,
            "dry_run": dry_run,
            "published": not dry_run,
        }
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination

    def _head(self) -> str:
        result = self._git("rev-parse", "HEAD")
        value = result.stdout.strip()
        if len(value) != 40:
            raise SitePublishError("HEAD is unknown")
        return value

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=check,
        )
