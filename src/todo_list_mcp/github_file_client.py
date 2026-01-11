"""Lightweight GitHub file client for CRUD and move operations.

This module is a small, self-contained helper for interacting with the GitHub
Contents API. It offers a straightforward interface to create, read, update,
delete, and move files in a repository. Authentication uses a personal access
token (PAT) supplied directly or via the `GITHUB_TOKEN` environment variable.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator


class GitHubFileClientSettings(BaseModel):
    """Settings for GitHubFileClient."""

    owner: str = Field("", description="GitHub repository owner")
    repo: str = Field("", description="GitHub repository name")
    token: str = Field("", description="GitHub API token")
    default_branch: str = Field("main", description="Default branch name")
    base_url: str = Field("https://api.github.com", description="GitHub API base URL")
    timeout_seconds: float = Field(15.0, description="HTTP client timeout in seconds")
    user_agent: str = Field("todo-list-mcp-github-file-client/0.1", description="User-Agent header for HTTP requests")

    @field_validator("token")
    @classmethod
    def validate_github_token(cls, v: str | None) -> str | None:
        if not v:
            raise ValueError(
                "GitHub token is required"
            )
        return v

    @model_validator(mode="after")
    def validate_github_repo_info(self) -> "GitHubFileClientSettings":
        if not self.owner or not self.repo:
            raise ValueError(
                "Both owner and repo are required in settings"
            )
        return self


@dataclass(frozen=True)
class FileContent:
    path: str
    sha: str
    content: str
    download_url: Optional[str]


class GitHubFileClient:
    def __init__(
        self,
        settings: "GitHubFileClientSettings",
    ) -> None:
        self.owner = settings.owner
        self.repo = settings.repo
        self.default_branch = settings.default_branch
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": settings.user_agent,
            },
        )

        logger.info(
            "Initialized GitHubFileClient",
            extra={
                "owner": self.owner,
                "repo": self.repo,
                "default_branch": self.default_branch,
                "base_url": settings.base_url,
                "timeout_seconds": settings.timeout_seconds,
            },
        )

    def close(self) -> None:
        self._client.close()
        logger.debug(
            "Closed GitHub HTTP client", extra={"owner": self.owner, "repo": self.repo}
        )

    def __enter__(self) -> "GitHubFileClient":
        return self

    def __exit__(self, *_) -> None:  # type: ignore[override]
        self.close()

    # Public API

    def create_file(
        self,
        path: str,
        content: str,
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> FileContent:
        branch_name = branch or self.default_branch
        result = self._put_contents(
            self.owner,
            self.repo,
            path,
            content,
            message or f"Create {path}",
            branch_name,
            sha=None,
        )
        logger.info(
            "Created file",
            extra={
                "action": "create",
                "path": path,
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "sha": result.sha,
            },
        )
        return result

    def create_files(
        self,
        files: Sequence[Tuple[str, str]] | Dict[str, str],
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> List[FileContent]:
        """Create multiple files in a single commit using the Git Data API.

        The GitHub Contents API cannot batch writes; using the Git Data API
        reduces round-trips and rate-limit usage by committing all files at once.
        """

        branch_name = branch or self.default_branch
        items = self._normalize_kv_pairs(files)
        if not items:
            return []

        tree_entries: List[Dict[str, Any]] = []
        created_files: List[FileContent] = []

        for path, content in items:
            blob_sha = self._create_blob(content)
            tree_entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )
            created_files.append(
                FileContent(
                    path=path,
                    sha=blob_sha,
                    content=content,
                    download_url=self._raw_download_url(path, branch_name),
                )
            )

        commit_message = message or f"Create {len(tree_entries)} file(s)"
        commit_sha = self._commit_tree(tree_entries, commit_message, branch_name)

        logger.info(
            "Created multiple files",
            extra={
                "action": "create_batch",
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "file_count": len(tree_entries),
                "commit_sha": commit_sha,
            },
        )

        return created_files

    def read_file(
        self,
        path: str,
        *,
        branch: Optional[str] = None,
    ) -> FileContent:
        branch_name = branch or self.default_branch
        response = self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            params={"ref": branch_name},
        )
        if response.get("type") != "file":
            raise RuntimeError(f"Path is not a file: {path}")
        encoding = response.get("encoding")
        if encoding != "base64":
            raise RuntimeError(f"Unexpected encoding for {path}: {encoding}")
        raw_content = response.get("content", "")
        decoded_bytes = base64.b64decode(raw_content)
        decoded_content = decoded_bytes.decode("utf-8")
        return FileContent(
            path=response["path"],
            sha=response["sha"],
            content=decoded_content,
            download_url=response.get("download_url"),
        )

    def read_directory_files(
        self,
        directory: str,
        *,
        branch: Optional[str] = None,
    ) -> List[FileContent]:
        branch_name = branch or self.default_branch
        normalized_dir = directory.strip("/")
        expression = (
            f"{branch_name}:{normalized_dir}" if normalized_dir else f"{branch_name}:"
        )

        data = self._graphql_query(
            query=(
                """
                query ($owner: String!, $repo: String!, $expr: String!) {
                  repository(owner: $owner, name: $repo) {
                    object(expression: $expr) {
                      ... on Tree {
                        entries {
                          name
                          path
                          type
                          object {
                            ... on Blob {
                              oid
                              text
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """
            ),
            variables={
                "owner": self.owner,
                "repo": self.repo,
                "expr": expression,
            },
        )

        repository = data.get("repository") or {}
        tree = repository.get("object") or {}
        entries = tree.get("entries")
        if entries is None:
            raise RuntimeError(f"Path is not a directory: {directory}")

        files: List[FileContent] = []
        for entry in entries:
            if entry.get("type") != "blob":
                continue
            blob = entry.get("object") or {}
            text_content = blob.get("text")
            if text_content is None:
                continue
            file_path = entry.get("path", "")
            files.append(
                FileContent(
                    path=file_path,
                    sha=blob.get("oid", ""),
                    content=text_content,
                    download_url=self._raw_download_url(file_path, branch_name),
                )
            )

        logger.info(
            "Read directory files",
            extra={
                "action": "read_directory",
                "directory": directory,
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "file_count": len(files),
            },
        )

        return files

    def update_file(
        self,
        path: str,
        content: str,
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
    ) -> FileContent:
        branch_name = branch or self.default_branch
        sha_to_use = sha or self._get_sha(path, branch_name)
        result = self._put_contents(
            self.owner,
            self.repo,
            path,
            content,
            message or f"Update {path}",
            branch_name,
            sha=sha_to_use,
        )
        logger.info(
            "Updated file",
            extra={
                "action": "update",
                "path": path,
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "sha": result.sha,
            },
        )
        return result

    def update_files(
        self,
        files: Sequence[Tuple[str, str]] | Dict[str, str],
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> List[FileContent]:
        """Update multiple files in a single commit using the Git Data API."""

        branch_name = branch or self.default_branch
        items = self._normalize_kv_pairs(files)
        if not items:
            return []

        tree_entries: List[Dict[str, Any]] = []
        updated_files: List[FileContent] = []

        for path, content in items:
            blob_sha = self._create_blob(content)
            tree_entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )
            updated_files.append(
                FileContent(
                    path=path,
                    sha=blob_sha,
                    content=content,
                    download_url=self._raw_download_url(path, branch_name),
                )
            )

        commit_message = message or f"Update {len(tree_entries)} file(s)"
        commit_sha = self._commit_tree(tree_entries, commit_message, branch_name)

        logger.info(
            "Updated multiple files",
            extra={
                "action": "update_batch",
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "file_count": len(tree_entries),
                "commit_sha": commit_sha,
            },
        )

        return updated_files

    def delete_file(
        self,
        path: str,
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
    ) -> str:
        branch_name = branch or self.default_branch
        sha_to_use = sha or self._get_sha(path, branch_name)
        body = {
            "message": message or f"Delete {path}",
            "branch": branch_name,
            "sha": sha_to_use,
        }
        data = self._request(
            "DELETE",
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            json=body,
        )
        commit = data.get("commit", {})
        logger.info(
            "Deleted file",
            extra={
                "action": "delete",
                "path": path,
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "sha": sha_to_use,
                "commit_sha": commit.get("sha", ""),
            },
        )
        return commit.get("sha", "")

    def move_file(
        self,
        source_path: str,
        target_path: str,
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> FileContent:
        branch_name = branch or self.default_branch
        if source_path == target_path:
            logger.info(
                "Move skipped; source and target are identical",
                extra={
                    "action": "move",
                    "from": source_path,
                    "to": target_path,
                    "branch": branch_name,
                    "owner": self.owner,
                    "repo": self.repo,
                },
            )
            return self.read_file(source_path, branch=branch_name)

        source = self.read_file(source_path, branch=branch_name)
        commit_message = message or f"Move {source_path} -> {target_path}"

        # First write to the target path using the existing content/sha.
        moved = self._put_contents(
            self.owner,
            self.repo,
            target_path,
            source.content,
            commit_message,
            branch_name,
            sha=source.sha,
        )

        # Then remove the old path to ensure it no longer exists.
        delete_message = f"{commit_message} (remove source)"
        try:
            self.delete_file(
                source_path,
                message=delete_message,
                branch=branch_name,
                sha=source.sha,
            )
            deleted = True
        except RuntimeError as exc:
            detail = str(exc)
            if "404" in detail or "Not Found" in detail:
                # If GitHub treated the PUT as a rename, the source may already be gone.
                deleted = True
                logger.info(
                    "Source already absent after move",
                    extra={
                        "action": "move",
                        "from": source_path,
                        "to": target_path,
                        "branch": branch_name,
                        "owner": self.owner,
                        "repo": self.repo,
                    },
                )
            else:
                logger.error(
                    "Failed to delete source after move",
                    extra={
                        "action": "move",
                        "from": source_path,
                        "to": target_path,
                        "branch": branch_name,
                        "owner": self.owner,
                        "repo": self.repo,
                        "error": detail,
                    },
                )
                raise

        logger.info(
            "Moved file",
            extra={
                "action": "move",
                "from": source_path,
                "to": target_path,
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "sha": moved.sha,
                "deleted_source": deleted,
            },
        )
        return moved

    def move_files(
        self,
        moves: Sequence[Tuple[str, str]],
        *,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> List[FileContent]:
        """Move multiple files in a single commit.

        Uses a single tree/commit to minimize GitHub API calls. Sources are
        fetched via a single GraphQL query when possible.
        """

        branch_name = branch or self.default_branch
        if not moves:
            return []

        normalized_moves = [(src, tgt) for src, tgt in moves]
        sources = [src for src, _ in normalized_moves]
        source_lookup = self._read_files_bulk(sources, branch_name)

        tree_entries: List[Dict[str, Any]] = []
        moved_files: List[FileContent] = []

        for source_path, target_path in normalized_moves:
            if source_path == target_path:
                # No-op, but preserve return contract.
                moved_files.append(source_lookup[source_path])
                continue

            source_content = source_lookup[source_path]
            blob_sha = self._create_blob(source_content.content)

            tree_entries.append(
                {
                    "path": target_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )
            tree_entries.append(
                {
                    "path": source_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                }
            )

            moved_files.append(
                FileContent(
                    path=target_path,
                    sha=blob_sha,
                    content=source_content.content,
                    download_url=self._raw_download_url(target_path, branch_name),
                )
            )

        commit_message = message or f"Move {len(moved_files)} file(s)"
        commit_sha = self._commit_tree(tree_entries, commit_message, branch_name)

        logger.info(
            "Moved multiple files",
            extra={
                "action": "move_batch",
                "branch": branch_name,
                "owner": self.owner,
                "repo": self.repo,
                "move_count": len(moved_files),
                "commit_sha": commit_sha,
            },
        )

        return moved_files

    # Internal helpers

    def _get_sha(self, path: str, branch: str) -> str:
        file_info = self.read_file(path, branch=branch)
        return file_info.sha

    def _put_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        *,
        sha: Optional[str],
    ) -> FileContent:
        content_bytes = content.encode("utf-8")
        encoded = base64.b64encode(content_bytes).decode("ascii")
        body: Dict[str, Any] = {
            "message": message,
            "branch": branch,
            "content": encoded,
        }
        if sha:
            body["sha"] = sha
        data = self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body)
        content_info = data.get("content", {})
        return FileContent(
            path=content_info.get("path", path),
            sha=content_info.get("sha", ""),
            content=content,
            download_url=content_info.get("download_url"),
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            response = self._client.request(method, url, params=params, json=json)
            response.raise_for_status()
            logger.debug(
                "GitHub request succeeded",
                extra={
                    "method": method,
                    "url": url,
                    "owner": self.owner,
                    "repo": self.repo,
                    "status": response.status_code,
                },
            )
            return response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = exc.response.text
            logger.error(
                "GitHub API status error",
                extra={
                    "method": method,
                    "url": url,
                    "owner": self.owner,
                    "repo": self.repo,
                    "status": status_code,
                    "detail": detail,
                },
            )
            raise RuntimeError(
                f"GitHub API request failed ({status_code}) for {url}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "GitHub API transport error",
                extra={
                    "method": method,
                    "url": url,
                    "owner": self.owner,
                    "repo": self.repo,
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"GitHub API request failed for {url}: {exc}") from exc

    def _graphql_query(
        self,
        *,
        query: str,
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            response = self._client.post(
                "/graphql", json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                logger.error(
                    "GitHub GraphQL reported errors",
                    extra={
                        "owner": self.owner,
                        "repo": self.repo,
                        "errors": payload.get("errors"),
                    },
                )
                raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
            logger.debug(
                "GitHub GraphQL request succeeded",
                extra={
                    "owner": self.owner,
                    "repo": self.repo,
                    "status": response.status_code,
                },
            )
            return payload.get("data", {})
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = exc.response.text
            logger.error(
                "GitHub GraphQL status error",
                extra={
                    "owner": self.owner,
                    "repo": self.repo,
                    "status": status_code,
                    "detail": detail,
                },
            )
            raise RuntimeError(
                f"GitHub GraphQL request failed ({status_code}): {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "GitHub GraphQL transport error",
                extra={
                    "owner": self.owner,
                    "repo": self.repo,
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"GitHub GraphQL request failed: {exc}") from exc

    def _read_files_bulk(
        self, paths: Iterable[str], branch: str
    ) -> Dict[str, FileContent]:
        """Fetch multiple file contents with a single GraphQL query."""

        path_list = list(dict.fromkeys(p.strip("/") for p in paths))
        if not path_list:
            return {}

        aliases = [f"p{i}" for i in range(len(path_list))]

        def _escape(expr: str) -> str:
            return expr.replace("\\", "\\\\").replace('"', '\\"')

        expressions = {
            alias: _escape(f"{branch}:{path}") if path else _escape(f"{branch}:")
            for alias, path in zip(aliases, path_list)
        }

        selection = "\n".join(
            f'  {alias}: object(expression: "{expr}") {{\n'
            "    ... on Blob {\n"
            "      oid\n"
            "      text\n"
            "      byteSize\n"
            "    }\n"
            "  }\n"
            for alias, expr in expressions.items()
        )

        query = (
            "query ($owner: String!, $repo: String!) {\n"
            "  repository(owner: $owner, name: $repo) {\n"
            f"{selection}"
            "  }\n"
            "}\n"
        )

        data = self._graphql_query(
            query=query,
            variables={"owner": self.owner, "repo": self.repo},
        )

        repository = data.get("repository") or {}
        results: Dict[str, FileContent] = {}

        for alias, path in zip(aliases, path_list):
            node = repository.get(alias)
            if not node:
                raise RuntimeError(f"Path is not a file: {path}")
            text_content = node.get("text")
            if text_content is None:
                raise RuntimeError(f"Path is not a blob: {path}")
            results[path] = FileContent(
                path=path,
                sha=node.get("oid", ""),
                content=text_content,
                download_url=self._raw_download_url(path, branch),
            )

        return results

    def _create_blob(self, content: str) -> str:
        data = self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/git/blobs",
            json={"content": content, "encoding": "utf-8"},
        )
        return data.get("sha", "")

    def _commit_tree(
        self, tree_entries: List[Dict[str, Any]], message: str, branch: str
    ) -> str:
        if not tree_entries:
            raise ValueError("Tree entries are required to create a commit")

        def _commit_against_head(head_sha: str) -> str:
            base_commit = self._request(
                "GET", f"/repos/{self.owner}/{self.repo}/git/commits/{head_sha}"
            )
            base_tree_sha = (base_commit.get("tree") or {}).get("sha")
            if not base_tree_sha:
                raise RuntimeError(f"Unable to resolve base tree for branch {branch}")

            tree = self._request(
                "POST",
                f"/repos/{self.owner}/{self.repo}/git/trees",
                json={"base_tree": base_tree_sha, "tree": tree_entries},
            )

            commit = self._request(
                "POST",
                f"/repos/{self.owner}/{self.repo}/git/commits",
                json={
                    "message": message,
                    "tree": tree.get("sha"),
                    "parents": [head_sha],
                },
            )

            commit_sha = commit.get("sha", "")
            self._request(
                "PATCH",
                f"/repos/{self.owner}/{self.repo}/git/refs/heads/{branch}",
                json={"sha": commit_sha},
            )
            return commit_sha

        def _head_sha() -> str:
            ref = self._request(
                "GET", f"/repos/{self.owner}/{self.repo}/git/ref/heads/{branch}"
            )
            head = (ref.get("object") or {}).get("sha")
            if not head:
                raise RuntimeError(f"Unable to resolve base commit for branch {branch}")
            return head

        attempts = 0
        last_error: Optional[Exception] = None
        while attempts < 2:
            attempts += 1
            head_sha = _head_sha()
            try:
                return _commit_against_head(head_sha)
            except RuntimeError as exc:
                last_error = exc
                detail = str(exc).lower()
                if "fast forward" in detail and attempts < 2:
                    logger.info(
                        "Ref out-of-date; retrying commit with latest head",
                        extra={"branch": branch, "attempt": attempts},
                    )
                    continue
                raise
        # Should not reach here; surface last error for clarity.
        if last_error:
            raise last_error
        raise RuntimeError("Failed to commit tree for unknown reasons")

    @staticmethod
    def _normalize_kv_pairs(
        pairs: Sequence[Tuple[str, str]] | Dict[str, str],
    ) -> List[Tuple[str, str]]:
        if isinstance(pairs, dict):
            return list(pairs.items())
        return list(pairs)

    def _raw_download_url(self, path: str, branch: str) -> str:
        normalized_path = path.lstrip("/")
        return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{branch}/{normalized_path}"


## Example usage (for testing purposes)
if __name__ == "__main__":
    import sys
    import uuid

    from todo_list_mcp.settings import get_settings

    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <level>{message}</level> | {extra}",
        level="DEBUG",
    )

    with GitHubFileClient(settings=get_settings().github_file_client_settings) as client:
        task_id = str(uuid.uuid4())
        task_path = f"task_{task_id}.md"
        archive_path = f"archive/task_{task_id}.md"

        created = client.create_file(task_path, "Task details")
        print(f"Created: {created.path} @ {created.sha[:8]}")

        fetched = client.read_file(task_path)
        print(f"Read: {fetched.path} -> {fetched.content}")

        updated = client.update_file(task_path, "Updated task details")
        print(f"Updated: {updated.path} @ {updated.sha[:8]}")

        moved = client.move_file(task_path, archive_path)
        print(f"Moved to: {moved.path} @ {moved.sha[:8]}")

        archive_files = client.read_directory_files("archive")
        print("Archive directory contents:")
        for file_content in archive_files:
            print(
                f"- {file_content.path} @ {file_content.sha[:8]} @ {file_content.content[:20]}"
            )

        task_id_a = str(uuid.uuid4())
        task_id_b = str(uuid.uuid4())
        batch_files = {
            f"{task_id_a}.txt": "Batch A",
            f"{task_id_b}.txt": "Batch B",
        }
        batch_created = client.create_files(batch_files)
        print("Batch created:")
        for file_content in batch_created:
            print(f"- {file_content.path} @ {file_content.sha[:8]}")

        batch_updates = {
            f"{task_id_a}.txt": "Batch A updated",
            f"{task_id_b}.txt": "Batch B updated",
        }
        batch_updated = client.update_files(batch_updates)
        print("Batch updated:")
        for file_content in batch_updated:
            print(f"- {file_content.path} @ {file_content.sha[:8]}")

        batch_moves = [
            (f"{task_id_a}.txt", f"archive/{task_id_a}.txt"),
            (f"{task_id_b}.txt", f"archive/{task_id_b}.txt"),
        ]
        batch_moved = client.move_files(batch_moves)
        print("Batch moved:")
        for file_content in batch_moved:
            print(f"- {file_content.path} @ {file_content.sha[:8]}")
