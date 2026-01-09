FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev

COPY tradingbot/ /app/