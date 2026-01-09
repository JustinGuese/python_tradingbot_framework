# Helm Charts

The system uses Helm charts for Kubernetes deployment.

## Chart Structure

```
helm/tradingbots/
├── Chart.yaml              # Chart metadata
├── values.yaml             # Bot configurations
└── templates/
    ├── cronjob.yaml        # Generates CronJobs for each bot
    ├── postgresql-secret.yaml
    ├── postgresql-deployment.yaml
    └── postgresql-service.yaml
```

## Configuration

Edit `helm/tradingbots/values.yaml` to configure:

- Bot schedules (cron format)
- Resource limits
- Database settings
- Image repository and tags

## Adding a Bot

Add to `values.yaml`:

```yaml
bots:
  - name: mynewbot
    schedule: "*/5 * * * 1-5"  # Every 5 minutes, Mon-Fri
```

The CronJob is automatically generated from the template.

## Schedule Format

Standard cron syntax:
- `*/5 * * * 1-5` - Every 5 minutes, Monday-Friday
- `0 17 * * 1-5` - Daily at 5:00 PM, Monday-Friday
- `0 17 * * 2` - Weekly on Tuesday at 5:00 PM

## Deployment

```bash
helm upgrade --install tradingbots \
      ./helm/tradingbots \
      --create-namespace \
      --namespace tradingbots-2025
```

## Next Steps

- [Kubernetes Deployment](kubernetes.md) - Cluster setup
