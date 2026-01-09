# Deployment Overview

The trading bot system supports two deployment approaches:

1. **Kubernetes with Helm** (Production) - Deploys PostgreSQL and bots as CronJobs
2. **Local Development** - Run PostgreSQL locally, execute bots manually

## Deployment Options

### Option 1: Kubernetes with Helm (Production)

Deploy the entire system to Kubernetes, including PostgreSQL and all trading bots as scheduled CronJobs.

**Benefits**:
- Automated scheduling via Kubernetes CronJobs
- PostgreSQL deployed in-cluster
- Centralized configuration via Helm
- Production-ready with resource limits and monitoring

**Prerequisites**:
- Kubernetes cluster (1.20+)
- `kubectl` configured
- Helm 3.x installed

**Quick Deploy**:

```bash
# 1. Create namespace
kubectl create namespace tradingbots-2025

# 2. Create secrets from .env file
kubectl create secret generic tradingbot-secrets \
  --from-env-file=.env \
  --namespace=tradingbots-2025

# 3. Deploy with Helm
helm upgrade --install tradingbots \
  ./helm/tradingbots \
  --create-namespace \
  --namespace tradingbots-2025
```

**What Gets Deployed**:
- PostgreSQL instance (if `postgresql.enabled: true` in `values.yaml`)
- Trading bot CronJobs (one per bot in `values.yaml`)
- Visualization dashboard (if `visualization.enabled: true`)
- All configured with secrets and environment variables

See [Kubernetes Deployment](kubernetes.md) and [Helm Charts](helm.md) for detailed configuration.

### Option 2: Local Development

Run PostgreSQL locally and execute bots manually with `bot.run()`.

**Benefits**:
- Fast iteration and testing
- No Kubernetes cluster required
- Easy debugging and development

**Setup**:

1. **Run PostgreSQL with Docker**:
```bash
docker run -d --name postgres-tradingbot \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=tradingbot \
  -p 5432:5432 \
  postgres:17-alpine
```

2. **Set Environment Variable**:
```bash
export POSTGRES_URI="postgresql://postgres:yourpassword@localhost:5432/tradingbot"
```

3. **Run Your Bot**:
```python
from tradingbot.utils.botclass import Bot

bot = MyBot()
bot.run()  # Executes one iteration
```

See the [Quick Start Guide](../getting-started/quick-start.md) for complete local development workflow.

## PostgreSQL Deployment

### Kubernetes (via Helm)

PostgreSQL is automatically deployed when `postgresql.enabled: true` in `helm/tradingbots/values.yaml`:

```yaml
postgresql:
  enabled: true
  image:
    repository: postgres
    tag: 17-alpine
  database: postgres
  service:
    name: psql-service
    port: 5432
```

The Helm chart creates:
- PostgreSQL Deployment
- PostgreSQL Service (accessible at `psql-service:5432`)
- Persistent storage (configured in `values.yaml`)

**Connection String**: `postgresql://postgres:${POSTGRES_PASSWORD}@psql-service:5432/postgres`

### Local (Docker)

For local development, use the Docker command shown in Option 2 above.

## Bot Deployment

### Kubernetes (CronJobs)

Bots are deployed as Kubernetes CronJobs with configurable schedules:

```yaml
# helm/tradingbots/values.yaml
bots:
  - name: mybot
    schedule: "*/5 * * * 1-5"  # Every 5 minutes, Mon-Fri
```

Each bot runs automatically on its schedule. See [Helm Charts](helm.md) for configuration details.

### Local (Manual Execution)

Run bots manually with `bot.run()`:

```python
bot = MyBot()
bot.run()  # Execute one iteration
```

For scheduled execution locally, use cron or a task scheduler.

## Configuration

### Secrets Management

All secrets (database passwords, API keys) are stored in a single Kubernetes secret:

```bash
# Create .env file with:
# POSTGRES_PASSWORD=yourpassword
# POSTGRES_URI=postgresql://postgres:yourpassword@psql-service:5432/postgres
# OPENROUTER_API_KEY=yourkey
# BASIC_AUTH_PASSWORD=yourpassword

# Create secret
kubectl create secret generic tradingbot-secrets \
  --from-env-file=.env \
  --namespace=tradingbots-2025
```

**Security**: Never commit `.env` files to version control. Add to `.gitignore`.

### Environment Variables

Bots receive environment variables from Kubernetes secrets:

```yaml
env:
  - name: POSTGRES_URI
    valueFrom:
      secretKeyRef:
        name: tradingbot-secrets
        key: POSTGRES_URI
```

## Monitoring

### Kubernetes

View bot execution logs:

```bash
# List CronJobs
kubectl get cronjobs -n tradingbots-2025

# List recent jobs
kubectl get jobs -n tradingbots-2025

# View logs
kubectl logs -n tradingbots-2025 job/<job-name>
```

### Local

Check database for run logs:

```python
from tradingbot.utils.db import get_db_session, RunLog

with get_db_session() as session:
    logs = session.query(RunLog).filter(
        RunLog.bot_name == "MyBot"
    ).order_by(RunLog.timestamp.desc()).limit(10).all()
    
    for log in logs:
        print(f"{log.timestamp}: {log.success} - {log.result}")
```

## Next Steps

- **Kubernetes Setup**: See [Kubernetes Deployment](kubernetes.md) for cluster setup
- **Helm Configuration**: See [Helm Charts](helm.md) for bot scheduling and configuration
- **Local Development**: See [Quick Start Guide](../getting-started/quick-start.md) for local setup
- **Creating Bots**: See [Creating a Bot](../getting-started/creating-a-bot.md) for bot development
