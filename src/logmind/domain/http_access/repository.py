"""Read-only GitLab repository cache and bounded code evidence retrieval."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.http_access.site_config import GitRepositoryConfig

logger = get_logger(__name__)

_ALLOWED_BRANCHES = {"main", "master"}
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_SAFE_ENV_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,119}$")
_SAFE_SEARCH_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$-]{3,120}$")


class RepositoryError(RuntimeError):
    pass


@dataclass(slots=True)
class CodeEvidence:
    commit_sha: str = ""
    previous_commit_sha: str = ""
    matched_files: list[str] = field(default_factory=list)
    snippets: list[dict] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    confidence: str = "none"

    def to_dict(self, *, include_source: bool) -> dict:
        snippets = (
            self.snippets
            if include_source
            else [
                {
                    "file": item.get("file", ""),
                    "line": item.get("line", 0),
                    "symbol": item.get("symbol", ""),
                }
                for item in self.snippets
            ]
        )
        return {
            "commit_sha": self.commit_sha,
            "previous_commit_sha": self.previous_commit_sha,
            "matched_files": self.matched_files[:10],
            "snippets": snippets[:5],
            "changed_files": self.changed_files[:20],
            "confidence": self.confidence,
        }


class GitRepositoryService:
    def __init__(self, runner=None):
        self._runner = runner or self._run

    @staticmethod
    def validate(repository: GitRepositoryConfig) -> None:
        parsed = urlsplit(repository.clone_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RepositoryError("clone_url must be credential-free HTTPS")
        if repository.default_branch not in _ALLOWED_BRANCHES:
            raise RepositoryError("only main/master branches are allowed")
        if repository.credential_ref and not _SAFE_ENV_REF_RE.fullmatch(repository.credential_ref):
            raise RepositoryError("credential_ref must be an environment secret name")

    def cache_path(self, repository: GitRepositoryConfig) -> Path:
        settings = get_settings()
        root = Path(settings.http_access_repo_cache_dir).resolve()
        digest = hashlib.sha256(f"{repository.tenant_id}|{repository.id}".encode()).hexdigest()[:24]
        path = (root / f"{digest}.git").resolve()
        if root != path.parent:
            raise RepositoryError("invalid repository cache path")
        return path

    async def test_connection(self, repository: GitRepositoryConfig) -> dict:
        self.validate(repository)
        output = await self._git_remote(
            repository,
            ["ls-remote", "--symref", repository.clone_url, "HEAD"],
        )
        branch = ""
        for line in output.splitlines():
            match = re.match(r"ref: refs/heads/([^\s]+)\s+HEAD", line)
            if match:
                branch = match.group(1)
                break
        if branch not in _ALLOWED_BRANCHES:
            raise RepositoryError("remote default branch must be main or master")
        return {"ok": True, "default_branch": branch}

    async def sync(self, repository: GitRepositoryConfig) -> dict:
        self.validate(repository)
        remote = await self.test_connection(repository)
        repository.default_branch = remote["default_branch"]
        cache = self.cache_path(repository)
        cache.parent.mkdir(parents=True, exist_ok=True)
        depth = get_settings().http_access_repo_shallow_depth
        if not cache.exists():
            await self._git_remote(
                repository,
                [
                    "clone",
                    "--bare",
                    "--filter=blob:none",
                    "--no-recurse-submodules",
                    f"--depth={depth}",
                    "--single-branch",
                    f"--branch={repository.default_branch}",
                    repository.clone_url,
                    str(cache),
                ],
            )
            await self._runner(
                ["git", "-C", str(cache), "config", "core.hooksPath", "/dev/null"], {}
            )
        else:
            await self._git_remote(
                repository,
                [
                    "-C",
                    str(cache),
                    "fetch",
                    "--prune",
                    "--no-tags",
                    f"--depth={depth}",
                    "origin",
                    f"+refs/heads/{repository.default_branch}:refs/heads/{repository.default_branch}",
                ],
            )
        commit = (
            await self._runner(
                ["git", "-C", str(cache), "rev-parse", f"refs/heads/{repository.default_branch}"],
                self._safe_env(),
            )
        ).strip()
        return {
            "commit_sha": commit,
            "cache_size_bytes": _directory_size(cache),
            "synced_at": datetime.now(UTC),
        }

    async def ensure_commit(self, repository: GitRepositoryConfig, commit_sha: str) -> None:
        if not _SHA_RE.fullmatch(commit_sha):
            raise RepositoryError("invalid commit SHA")
        cache = self.cache_path(repository)
        if not cache.exists():
            await self.sync(repository)
        if await self._has_commit(cache, commit_sha) and await self._is_ancestor(
            cache, commit_sha, repository.default_branch
        ):
            return
        # First ask for the exact CI-provided object, then progressively deepen
        # only the allowed default branch. A SHA is accepted for diagnosis only
        # after Git proves it is an ancestor of main/master.
        with suppress(RepositoryError):
            await self._git_remote(
                repository,
                ["-C", str(cache), "fetch", "--no-tags", "--depth=1", "origin", commit_sha],
            )
        for deepen in (200, 800, 4000, 15000):
            if await self._has_commit(cache, commit_sha) and await self._is_ancestor(
                cache, commit_sha, repository.default_branch
            ):
                return
            await self._git_remote(
                repository,
                [
                    "-C",
                    str(cache),
                    "fetch",
                    "--no-tags",
                    f"--deepen={deepen}",
                    "origin",
                    f"+refs/heads/{repository.default_branch}:refs/heads/{repository.default_branch}",
                ],
            )
        if not await self._has_commit(cache, commit_sha) or not await self._is_ancestor(
            cache, commit_sha, repository.default_branch
        ):
            raise RepositoryError(
                "deployed commit is not reachable from allowed main/master history"
            )

    async def locate(
        self,
        repository: GitRepositoryConfig,
        *,
        commit_sha: str,
        previous_commit_sha: str = "",
        route_key: str = "",
        stack_symbols: list[str] | None = None,
    ) -> CodeEvidence:
        await self.ensure_commit(repository, commit_sha)
        cache = self.cache_path(repository)
        tokens = _search_tokens(route_key, stack_symbols or [])
        matches: list[tuple[str, int, str]] = []
        for token in tokens[:8]:
            try:
                output = await self._runner(
                    [
                        "git",
                        "-C",
                        str(cache),
                        "grep",
                        "-n",
                        "-I",
                        "-i",
                        "--fixed-strings",
                        token,
                        commit_sha,
                        "--",
                        "*.cs",
                        "*.java",
                    ],
                    self._safe_env(),
                )
            except RepositoryError:
                continue
            for line in output.splitlines():
                match = re.match(r"[^:]+:([^:]+):(\d+):(.*)", line)
                if match:
                    matches.append((match.group(1), int(match.group(2)), match.group(3).strip()))
            if matches:
                break
        unique: list[tuple[str, int, str]] = []
        seen: set[tuple[str, int]] = set()
        for item in matches:
            key = (item[0], item[1])
            if key not in seen:
                seen.add(key)
                unique.append(item)
            if len(unique) >= 5:
                break
        snippets = []
        for filename, line_number, symbol in unique:
            content = await self._bounded_file(cache, commit_sha, filename)
            lines = content.splitlines()
            start = max(0, line_number - 8)
            end = min(len(lines), line_number + 12)
            snippets.append(
                {
                    "file": filename,
                    "line": line_number,
                    "symbol": symbol[:300],
                    "content": "\n".join(lines[start:end])[:8000],
                }
            )
        changed_files: list[str] = []
        if previous_commit_sha and _SHA_RE.fullmatch(previous_commit_sha):
            try:
                await self.ensure_commit(repository, previous_commit_sha)
                output = await self._runner(
                    [
                        "git",
                        "-C",
                        str(cache),
                        "diff",
                        "--name-only",
                        previous_commit_sha,
                        commit_sha,
                        "--",
                        "*.cs",
                        "*.java",
                    ],
                    self._safe_env(),
                )
                changed_files = [line.strip() for line in output.splitlines() if line.strip()][:50]
            except RepositoryError:
                changed_files = []
        confidence = (
            "high"
            if any(item[0] in changed_files for item in unique)
            else "medium"
            if unique
            else "none"
        )
        return CodeEvidence(
            commit_sha=commit_sha,
            previous_commit_sha=previous_commit_sha,
            matched_files=[item[0] for item in unique],
            snippets=snippets,
            changed_files=changed_files,
            confidence=confidence,
        )

    async def _bounded_file(self, cache: Path, commit: str, filename: str) -> str:
        if filename.startswith("/") or ".." in Path(filename).parts:
            raise RepositoryError("unsafe repository path")
        output = await self._runner(
            ["git", "-C", str(cache), "show", f"{commit}:{filename}"],
            self._safe_env(),
        )
        return output[:200_000]

    async def _has_commit(self, cache: Path, commit: str) -> bool:
        try:
            await self._runner(
                ["git", "-C", str(cache), "cat-file", "-e", f"{commit}^{{commit}}"],
                self._safe_env(),
            )
            return True
        except RepositoryError:
            return False

    async def _is_ancestor(self, cache: Path, commit: str, branch: str) -> bool:
        try:
            await self._runner(
                [
                    "git",
                    "-C",
                    str(cache),
                    "merge-base",
                    "--is-ancestor",
                    commit,
                    f"refs/heads/{branch}",
                ],
                self._safe_env(),
            )
            return True
        except RepositoryError:
            return False

    async def _git_remote(self, repository: GitRepositoryConfig, args: list[str]) -> str:
        env = self._safe_env()
        command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null"]
        credential = (
            os.environ.get(repository.credential_ref, "") if repository.credential_ref else ""
        )
        if credential:
            encoded = base64.b64encode(credential.encode()).decode()
            # Keep credentials out of process arguments and application logs.
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {encoded}",
                }
            )
        command.extend(args)
        return await self._runner(command, env)

    @staticmethod
    def _safe_env() -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LANG": "C.UTF-8",
        }
        ca_file = get_settings().http_access_git_ca_file.strip()
        if ca_file:
            ca_path = Path(ca_file).resolve()
            if not ca_path.is_file():
                raise RepositoryError("configured GitLab CA file is unavailable")
            env["GIT_SSL_CAINFO"] = str(ca_path)
        return env

    @staticmethod
    async def _run(command: list[str], env: dict[str, str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RepositoryError("git command timed out") from exc
        if len(stdout) > 2_000_000 or len(stderr) > 200_000:
            raise RepositoryError("git command output exceeded safety limit")
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            error = re.sub(r"https://[^/@\s]+@", "https://***@", error)
            raise RepositoryError(error.strip()[:500] or "git command failed")
        return stdout.decode("utf-8", errors="replace")


def _search_tokens(route_key: str, stack_symbols: list[str]) -> list[str]:
    tokens = [
        value.strip() for value in stack_symbols if _SAFE_SEARCH_TOKEN_RE.fullmatch(value.strip())
    ]
    path = route_key.partition(" ")[2]
    tokens.extend(
        segment for segment in reversed(path.split("/")) if _SAFE_SEARCH_TOKEN_RE.fullmatch(segment)
    )
    return list(dict.fromkeys(tokens))


def _directory_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


git_repository_service = GitRepositoryService()
