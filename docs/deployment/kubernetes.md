# Kubernetes Deployment

## Prerequisites

- Kubernetes cluster (1.20+)
- kubectl configured
- Helm 3.x installed

## Namespace

Create a namespace:

```bash
kubectl create namespace tradingbots-2025
```

## Secrets

Create PostgreSQL secret:

```bash
kubectl create secret generic psqlcreds \
  --from-literal=POSTGRES_URI="postgresql://user:password@host:5432/database" \
  -n tradingbots-2025
```

## Deploy

```bash
helm upgrade --install tradingbots \
      ./helm/tradingbots \
      --create-namespace \
      --namespace tradingbots-2025
```

## Verify

Check CronJobs:

```bash
kubectl get cronjobs -n tradingbots-2025
```

Check recent jobs:

```bash
kubectl get jobs -n tradingbots-2025
```

## Monitoring

View bot logs:

```bash
kubectl logs -n tradingbots-2025 job/<job-name>
```

## Next Steps

- [Helm Charts](helm.md) - Configuration details
