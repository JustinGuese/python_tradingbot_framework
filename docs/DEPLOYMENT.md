# Documentation Deployment

This guide explains how to set up automatic deployment of MkDocs documentation to GitHub Pages.

## Automatic Deployment with GitHub Actions

The documentation is automatically built and deployed on every push to the `main` or `master` branch.

### Initial Setup

1. **Enable GitHub Pages**:
   - Go to your repository on GitHub
   - Navigate to **Settings** → **Pages**
   - Under **Source**, select **GitHub Actions**
   - Save the changes

2. **Push to trigger deployment**:
   ```bash
   git push origin main
   ```

3. **Verify deployment**:
   - Go to **Actions** tab in your repository
   - You should see "Deploy Documentation" workflow running
   - Once complete, your docs will be available at:
     `https://your-username.github.io/tradingbot25/`

### Manual Deployment

You can also trigger deployment manually:

1. Go to **Actions** tab
2. Select **Deploy Documentation** workflow
3. Click **Run workflow**
4. Select branch and click **Run workflow**

## Workflow Details

The GitHub Actions workflow (`.github/workflows/docs.yml`) does the following:

1. **Checks out** the repository
2. **Sets up Python 3.12** and `uv`
3. **Installs dependencies** including MkDocs and plugins
4. **Builds documentation** using `mkdocs build`
5. **Deploys to GitHub Pages** automatically

## Troubleshooting

### Documentation not appearing

1. **Check GitHub Pages settings**:
   - Ensure "GitHub Actions" is selected as the source
   - Check that the workflow has completed successfully

2. **Check workflow logs**:
   - Go to **Actions** tab
   - Click on the latest workflow run
   - Review logs for any errors

3. **Common issues**:
   - **Build failures**: Check that all dependencies are in `pyproject.toml` under `[project.optional-dependencies.docs]`
   - **Import errors**: Ensure `tradingbot` module can be imported (check Python path)
   - **Missing files**: Verify all markdown files referenced in `mkdocs.yml` exist

### Update repository URL

If your repository URL changes, update `mkdocs.yml`:

```yaml
repo_name: tradingbot25
repo_url: https://github.com/your-username/tradingbot25
```

## Local Testing

Before pushing, test the build locally:

```bash
# Install dependencies
uv sync --extra docs

# Build documentation
uv run mkdocs build

# Check for errors
# Output should be in ./site directory
```

## Custom Domain (Optional)

To use a custom domain:

1. Add a `CNAME` file to `docs/` directory:
   ```
   docs.yourdomain.com
   ```

2. Configure DNS:
   - Add a CNAME record pointing to `your-username.github.io`

3. Update GitHub Pages settings:
   - Go to **Settings** → **Pages**
   - Enter your custom domain

## Next Steps

- [MkDocs Documentation](https://www.mkdocs.org/)
- [Material Theme](https://squidfunk.github.io/mkdocs-material/)
- [GitHub Pages Documentation](https://docs.github.com/en/pages)
