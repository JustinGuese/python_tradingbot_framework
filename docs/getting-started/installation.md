# Installation

## System Requirements

- Python 3.12 or higher
- PostgreSQL database (see [Quick Start](quick-start.md) for local setup)
- Kubernetes cluster (for production deployment - see [Deployment Guide](../deployment/overview.md))

## Install Dependencies

The project uses `uv` for dependency management:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv sync
```

## Optional Dependencies

### Development Tools

```bash
uv sync --extra dev
```

### Documentation

```bash
uv sync --extra docs
```

## Database Setup

For local development, see the [Quick Start Guide](quick-start.md) which covers:
- Running PostgreSQL with Docker
- Setting the `POSTGRES_URI` environment variable

For production deployment with Kubernetes, see the [Deployment Guide](../deployment/overview.md).

The database tables will be created automatically on first run using SQLAlchemy.

## Verify Installation

Test your installation:

```python
from tradingbot.utils.botclass import Bot
from tradingbot.utils.db import get_db_session

# Test database connection
with get_db_session() as session:
    print("Database connection successful!")

# Test bot creation
bot = Bot("TestBot", "QQQ")
print("Bot created successfully!")
```

## Docker (Optional)

If you prefer Docker, build the image:

```bash
docker build -t tradingbot:latest .
```

## Next Steps

- [Quick Start Guide](quick-start.md) - Get your first bot running locally
- [Creating a Bot](creating-a-bot.md) - Learn how to create custom bots
- [Deployment Guide](../deployment/overview.md) - Deploy to Kubernetes
