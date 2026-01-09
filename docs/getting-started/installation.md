# Installation

## System Requirements

- Python 3.12 or higher
- PostgreSQL database
- Kubernetes cluster (for production deployment)

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

1. Create a PostgreSQL database:

```sql
CREATE DATABASE tradingbot;
```

2. Set the connection string:

```bash
export POSTGRES_URI="postgresql://user:password@host:5432/tradingbot"
```

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

- [Quick Start Guide](quick-start.md)
- [Creating a Bot](creating-a-bot.md)
