# Publishing to PyPI

This project uses [UV](https://docs.astral.sh/uv/) as the package manager and GitHub Actions for automated publishing to PyPI.

## Prerequisites

1. **PyPI Account**: Create an account at [https://pypi.org/](https://pypi.org/)
2. **Trusted Publishing**: Configure trusted publishing (no API tokens needed!) at [https://pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/)
   - Add a new publisher with:
     - PyPI Project Name: `todo-list-mcp`
     - Owner: `l0kifs`
     - Repository name: `todo-list-mcp`
     - Workflow name: `publish-to-pypi.yml`
     - Environment name: (leave blank)

## Automated Publishing (Recommended)

The project is configured to automatically publish to PyPI when a new GitHub release is created:

1. **Update version** in `pyproject.toml`:
   ```toml
   version = "0.2.0"  # Update to your new version
   ```

2. **Update version** in `src/todo_list_mcp/settings.py`:
   ```python
   app_version: str = Field(default="0.2.0", description="Application version")
  # Update to your new version
   ```

1. **Update CHANGELOG.md** with the new version changes and link for release.

2. **Commit and push** your changes:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.2.0"
   git push
   ```

3. **Create a GitHub release**:
   
   Using GitHub CLI (recommended):
   ```bash
   gh release create v0.2.0 \
     --title "v0.2.0 - Release Title" \
     --notes "Release notes here..."
   ```
   
   Or using GitHub web interface:
   - Go to [https://github.com/l0kifs/todo-list-mcp/releases/new](https://github.com/l0kifs/todo-list-mcp/releases/new)
   - Create a new tag (e.g., `v0.2.0`)
   - Add release title and description
   - Click "Publish release"
   
   To verify the release:
   ```bash
   gh release view v0.2.0
   ```

4. **GitHub Actions will automatically**:
   - Build the package using UV
   - Publish to PyPI using trusted publishing
   - You can monitor the progress in the Actions tab

## Manual Publishing

If you need to publish manually:

1. **Install UV** (if not already installed):
   ```bash
   pip install uv
   ```

2. **Build the package**:
   ```bash
   uv build
   ```
   This creates distribution files in the `dist/` directory.

3. **Publish using UV** (requires PyPI API token):
   ```bash
   uv publish
   ```
   Or use `twine`:
   ```bash
   pip install twine
   twine upload dist/*
   ```

## Testing on TestPyPI

Before publishing to the main PyPI, you can test on TestPyPI:

1. Configure trusted publishing for TestPyPI at [https://test.pypi.org/manage/account/publishing/](https://test.pypi.org/manage/account/publishing/)

2. Manually trigger the workflow or modify the workflow to publish to TestPyPI:
   ```bash
   uv publish --index-url https://test.pypi.org/legacy/
   ```

3. Test installation:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ jira-cli
   ```

## Best Practices

1. **Always create tags on `main` branch** - Never tag on `develop` or feature branches
2. **Merge develop to main before tagging** - Ensure all changes are in main
3. **Test on TestPyPI first** (optional but recommended for major releases)
4. **Use semantic versioning** (MAJOR.MINOR.PATCH)
5. **Analyze changes** in repository between last release and current state
6. **Update CHANGELOG.md** with all changes before release
7. **Test build locally** before pushing tags
8. **Keep credentials secure** - use project-specific tokens
9. **Test installation** from PyPI after publishing
10. **Create GitHub Release** after successful publish
11. **Monitor PyPI stats** and user feedback
