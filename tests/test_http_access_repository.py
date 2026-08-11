import asyncio

import pytest

from logmind.domain.http_access.repository import (
    CodeEvidence,
    GitRepositoryService,
    RepositoryError,
)
from logmind.domain.http_access.site_config import GitRepositoryConfig


def _repository(**overrides):
    values = {
        "id": "repo-1",
        "tenant_id": "tenant-1",
        "name": "account",
        "clone_url": "https://gitlab.example.cn/account/service.git",
        "default_branch": "main",
        "credential_ref": "LOGMIND_GIT_ACCOUNT",
    }
    values.update(overrides)
    return GitRepositoryConfig(**values)


@pytest.mark.parametrize(
    ("url", "branch", "credential_ref"),
    [
        ("http://gitlab.example.cn/a.git", "main", "LOGMIND_GIT_TOKEN"),
        ("https://user:token@gitlab.example.cn/a.git", "main", "LOGMIND_GIT_TOKEN"),
        ("https://gitlab.example.cn/a.git?private_token=x", "main", "LOGMIND_GIT_TOKEN"),
        ("https://gitlab.example.cn/a.git", "develop", "LOGMIND_GIT_TOKEN"),
        ("https://gitlab.example.cn/a.git", "main", "unsafe-ref"),
    ],
)
def test_repository_rejects_unsafe_connection_configuration(url, branch, credential_ref):
    repository = _repository(
        clone_url=url,
        default_branch=branch,
        credential_ref=credential_ref,
    )
    with pytest.raises(RepositoryError):
        GitRepositoryService.validate(repository)


def test_git_credential_is_passed_only_in_environment(monkeypatch):
    calls = []

    async def runner(command, env):
        calls.append((command, env))
        return "ref: refs/heads/main\tHEAD\n"

    monkeypatch.setenv("LOGMIND_GIT_ACCOUNT", "deploy-user:super-secret-token")
    service = GitRepositoryService(runner=runner)

    result = asyncio.run(service.test_connection(_repository()))

    assert result == {"ok": True, "default_branch": "main"}
    command, env = calls[0]
    assert "super-secret-token" not in " ".join(command)
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_code_evidence_hides_source_until_security_setting_allows_it():
    evidence = CodeEvidence(
        commit_sha="a" * 40,
        matched_files=["Controllers/PayController.cs"],
        snippets=[
            {
                "file": "Controllers/PayController.cs",
                "line": 42,
                "symbol": "CreateOrder",
                "content": "private string secret = value;",
            }
        ],
        confidence="medium",
    )

    safe = evidence.to_dict(include_source=False)
    allowed = evidence.to_dict(include_source=True)

    assert "content" not in safe["snippets"][0]
    assert "private string" in allowed["snippets"][0]["content"]


@pytest.mark.parametrize(
    ("route", "filename", "source_line"),
    [
        (
            "GET /Advertisement/GetImage/{id}",
            "Controllers/AdvertisementController.cs",
            '[HttpGet("GetImage/{id}")] public IActionResult GetImage() {',
        ),
        (
            "POST /api/order/create",
            "src/main/java/OrderController.java",
            '@PostMapping("/create") public Order create() {',
        ),
    ],
)
def test_locate_maps_csharp_and_java_routes_to_changed_methods(
    tmp_path, route, filename, source_line
):
    sha = "a" * 40
    previous = "b" * 40

    async def runner(command, _env):
        if "grep" in command:
            return f"{sha}:{filename}:12:{source_line}\n"
        if "show" in command:
            return "\n".join(["line"] * 11 + [source_line] + ["line"] * 20)
        if "diff" in command:
            return f"{filename}\n"
        return ""

    service = GitRepositoryService(runner=runner)

    async def ensure_commit(_repository, _commit):
        return None

    service.ensure_commit = ensure_commit
    service.cache_path = lambda _repository: tmp_path / "repo.git"

    evidence = asyncio.run(
        service.locate(
            _repository(),
            commit_sha=sha,
            previous_commit_sha=previous,
            route_key=route,
        )
    )

    assert evidence.confidence == "high"
    assert evidence.matched_files == [filename]
    assert evidence.changed_files == [filename]
    assert source_line in evidence.snippets[0]["content"]
