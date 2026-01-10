"""Lightweight GitHub file client for CRUD and move operations.

This module is a small, self-contained helper for interacting with the GitHub
Contents API. It offers a straightforward interface to create, read, update,
delete, and move files in a repository. Authentication uses a personal access
token (PAT) supplied directly or via the `GITHUB_TOKEN` environment variable.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger


@dataclass(frozen=True)
class FileContent:
    path: str
    sha: str
    content: str
    download_url: Optional[str]


class GitHubFileClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        *,
        default_branch: str = "main",
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 15.0,
        user_agent: str = "todo-list-mcp-github-file-client/0.1",
    ) -> None:
        token_to_use = token or os.environ.get("GITHUB_TOKEN")
        if not token_to_use:
            raise ValueError("GitHub token is required; set GITHUB_TOKEN or pass token")

        if not owner or not repo:
            raise ValueError("Both owner and repo are required")

        self.owner = owner
        self.repo = repo
        self.default_branch = default_branch
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {token_to_use}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": user_agent,
            },
        )

        logger.info(
            "Initialized GitHubFileClient",
            extra={
                "owner": owner,
                "repo": repo,
                "default_branch": default_branch,
                "base_url": base_url,
                "timeout_seconds": timeout_seconds,
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

    task_id = str(uuid.uuid4())

    with GitHubFileClient(
        owner=get_settings().github_repo_owner,
        repo=get_settings().github_repo_name,
        token=get_settings().github_api_token,
    ) as client:
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
            print(f"- {file_content.path} @ {file_content.sha[:8]} @ {file_content.content[:20]}")
