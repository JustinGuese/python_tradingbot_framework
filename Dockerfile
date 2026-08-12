FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev

# Copy the package AS a package, preserving the `tradingbot/` directory.
#
# This previously did `COPY tradingbot/ /app/`, flattening `utils/` and
# `livetrade/` to the image root. That is what forced every module to import
# itself rootlessly (`from utils.botclass import ...`) while the tests used the
# `tradingbot.` root — two importable names for one module, hence two SQLAlchemy
# declarative registries and two Bot classes in any process that loaded both.
#
# With PYTHONPATH=/app the package resolves as `tradingbot.*`, so entry points
# are invoked as `python -m tradingbot.<name>` (see helm/tradingbots/templates).
# `symbol_map.json` is loaded __file__-relative, so it travels with the package.
#
# Deliberately NOT `COPY . /app`: that would drop the 1.7 GB host .venv over the
# one uv just built (its shebangs point at the host's home directory) and bake
# .env secrets into a registry image. See .dockerignore.
COPY tradingbot/ /app/tradingbot/
