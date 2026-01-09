# Documentation

This directory contains the source files for the MkDocs documentation site.

## Building the Documentation

### Install Dependencies

```bash
uv sync --extra docs
```

### Serve Locally

Start the development server:

```bash
mkdocs serve
```

The documentation will be available at `http://127.0.0.1:8000`

### Build Static Site

Build the static HTML files:

```bash
mkdocs build
```

The output will be in the `site/` directory.

## Deploying to GitHub Pages

### Automatic Deployment

If you have GitHub Actions set up, the documentation can be automatically deployed on push to main.

### Manual Deployment

Deploy to GitHub Pages:

```bash
mkdocs gh-deploy --force
```

This will:
1. Build the documentation
2. Push the `site/` directory to the `gh-pages` branch
3. Make it available at `https://yourusername.github.io/tradingbot25/`

## Documentation Structure

- `index.md` - Homepage
- `getting-started/` - Installation and quick start guides
- `architecture/` - System design and architecture
- `api/` - Auto-generated API documentation from docstrings
- `deployment/` - Kubernetes and Helm deployment guides
- `guides/` - In-depth tutorials
- `examples/` - Example bot implementations

## Adding New Documentation

1. Create a new markdown file in the appropriate directory
2. Add it to the `nav` section in `mkdocs.yml`
3. Build and test locally with `mkdocs serve`

## API Documentation

API documentation is automatically generated from docstrings using `mkdocstrings`. The API pages use the `::: module.Class` syntax to include auto-generated documentation.
