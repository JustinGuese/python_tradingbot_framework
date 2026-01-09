# Deployment Overview

The trading bot system is designed to run on Kubernetes using Helm charts.

## Architecture

- **Kubernetes CronJobs**: Each bot runs as a separate CronJob
- **Helm Charts**: Manages deployment configuration
- **PostgreSQL**: Stores bot state, trades, and historical data
- **Docker**: All bots run from the same container image

## Quick Deploy

```bash
helm upgrade --install tradingbots \
      ./helm/tradingbots \
      --create-namespace \
      --namespace tradingbots-2025
```

## Next Steps

- [Helm Charts](helm.md) - Configuration details
- [Kubernetes](kubernetes.md) - Cluster setup
