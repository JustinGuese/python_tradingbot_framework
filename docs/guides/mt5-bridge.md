# MetaTrader 5 Bridge Guide

> [!WARNING]
> **DISCLAIMER:** MetaTrader 5 execution interacts with live or demo financial broker accounts. Always verify order routing, volume limits, and symbol mapping against demo accounts before enabling live capital.

The **MT5 Bridge** provides a self-hosted bridge enabling Linux-based trading bots and copiers to communicate with the Windows-native MetaTrader 5 (MT5) client. It runs MetaTrader 5 inside Wine alongside an `mt5linux` RPyC server within the Kubernetes cluster, allowing our Python services to use standard `MetaTrader5` API calls remotely.

---

## Architecture & Components

The MT5 bridge deployment consists of four Kubernetes resources deployed together:

1. **Deployment (`deployment-mt5-bridge.yaml`)**:
   - Single-replica (`replicas: 1`) with `strategy: Recreate`. Wine's terminal state cannot be safely mounted concurrently by multiple pods.
   - Runs Wine, MT5 terminal, and the `mt5linux` RPyC daemon.
2. **ClusterIP Service (`service-mt5-bridge.yaml`)**:
   - Exposes **only** the RPyC port (default `8001`) internally to the cluster.
   - No external Ingress or LoadBalancer is configured.
3. **NetworkPolicy (`networkpolicy-mt5-bridge.yaml`)**:
   - **Crucial Security Boundary:** RPyC is an unauthenticated protocol that allows remote Python execution.
   - Restricts ingress on port `8001` strictly to pods labelled `app.kubernetes.io/component=livetrade`. All unauthorized cluster traffic is blocked.
4. **PersistentVolumeClaim (`pvc-mt5-bridge.yaml`)**:
   - Mounts persistent storage to `/config` (default `5Gi`).
   - Persists the Wine prefix (`/config/.wine`), downloaded terminal files, server list, and logged-in account sessions across pod restarts.

```
+-------------------------------------------------------------+
| Kubernetes Cluster                                          |
|                                                             |
|  +---------------------------+                              |
|  | Livetrade Copier Pod      |                              |
|  | (app.kubernetes.io/       |                              |
|  |  component=livetrade)     |                              |
|  +-------------+-------------+                              |
|                |                                            |
|                | TCP 8001 (RPyC)                            |
|                v                                            |
|  +---------------------------+                              |
|  | NetworkPolicy Ingress Gate|                              |
|  +-------------+-------------+                              |
|                |                                            |
|                v                                            |
|  +---------------------------+     +---------------------+  |
|  | MT5 Bridge Pod            |<--->| PVC (/config)       |  |
|  | - Wine + MetaTrader 5     |     | - .wine prefix      |  |
|  | - mt5linux RPyC (0.0.0.0) |     | - Terminal session  |  |
|  | - KasmVNC (3000, unserviced)    +---------------------+  |
|  +---------------------------+                              |
+-------------------------------------------------------------+
```

---

## Container Image & Custom Build

- **Image**: `registry.gitlab.com/datafortress/tradingbot25/mt5-bridge:main`
- Configured via `.Values.mt5Bridge.image`.

### Why a Custom Image?
MetaTrader 5 (build 4400+) includes an internal anti-debugger check that fails under stock Wine 10.0 (present in upstream third-party base images like `gmag11/metatrader5_vnc:2.3`), causing MT5 to exit immediately or display a "debugger found" dialog. Testing confirmed that **Wine Staging 11.0** (`winehq-staging=11.0.0~bookworm-1`) satisfies the anti-debug check and runs MT5 terminal without issue (login and equity reads verified).

Additionally:
- Python on the Wine side requires `numpy<2` to avoid binary ABI incompatibilities with the MetaTrader 5 Python package.
- Python on the Linux bridge side requires `mt5linux==0.1.9` because `mt5linux>=1.1.x` introduced Python 3.12-specific f-string syntax that crashes under Debian Bookworm's Python 3.11 with a `SyntaxError`.
- Wine environment variables require `WINEDLLOVERRIDES=mscoree,mshtml=d` to disable unused Mono/Gecko installer popups.

Rather than running brittle startup initContainers that patch user site packages at pod boot, our custom bridge image bakes Wine Staging 11, the required Wine overrides, and patched dependencies into `registry.gitlab.com/datafortress/tradingbot25/mt5-bridge:main`.

### How to Bump Pinned Wine
The bridge image is defined in `docker/mt5-bridge/Dockerfile` and built via the `.gitlab-ci.yml` `build-mt5-bridge` pipeline job. WineHQ staging packages are explicitly pinned to Debian bookworm release versions.

To upgrade Wine in the future:
1. Inspect available package versions from the WineHQ Bookworm repository:
   ```bash
   apt-cache madison winehq-staging
   ```
2. In `docker/mt5-bridge/Dockerfile`, update the pinned versions for `winehq-staging`, `wine-staging`, `wine-staging-amd64`, and `wine-staging-i386`.
3. Test locally with a Docker build:
   ```bash
   docker build -t mt5-bridge:test docker/mt5-bridge
   ```
4. Push the branch to trigger GitLab CI to build and publish the new `:main` image tag.

---

## Account Login & Session Management

The bridge supports two complementary login workflows:

### 1. Programmatic Login via Livetrade Runner (Headless)
When the MT5 copier job runs:
1. It connects to the bridge via RPyC: `client = connect_rpyc(host="mt5-bridge", port=8001)`.
2. It calls `client.initialize()` and `client.login(login=int(login), password=password, server=server)`.
3. The credentials are provided to the CronJob pod through Kubernetes secrets (`tradingbot-secrets`), keeping credentials out of persistent disk storage wherever possible.

### 2. Manual GUI Login / Inspection via VNC
For brokers requiring initial interactive login, license agreement acceptance, or visual verification of chart data:
1. Establish a port-forward directly to the bridge pod for port 3000:
   ```bash
   kubectl -n tradingbots-2025 port-forward deployment/mt5-bridge 3000:3000
   ```
2. Open `http://localhost:3000` in your web browser.
3. Access the KasmVNC desktop, open MetaTrader 5, and log in to the account (`File -> Login to Trade Account`).
4. Because `/config` is backed by the PVC, session credentials, certificate state, and broker server configurations persist permanently across container restarts.

### 3. Headless Terminal Inspection via VNC Screenshot (`xwd`)
To inspect the terminal display without opening an interactive browser session (e.g. to quickly verify whether MT5 is stuck on a dialog, anti-debugger prompt, or modal message), capture an instant headless screenshot of the virtual X display (`:0`) using `xwd`:

```bash
# Capture virtual display screenshot from the container and convert locally with ImageMagick:
kubectl -n tradingbots-2025 exec deployment/mt5-bridge -c mt5-bridge -- xwd -root -silent -display :0 | convert xwd:- /tmp/mt5_terminal.png

# Or save the raw XWD screen dump directly:
kubectl -n tradingbots-2025 exec deployment/mt5-bridge -c mt5-bridge -- xwd -root -silent -display :0 > /tmp/mt5_terminal.xwd
```

---

## Health Checks & Diagnostics

### Kubernetes Probes (Startup & Readiness)
Because MT5 first boot initializes the Wine prefix and starts the terminal before launching the RPyC server on port `8001`, the Deployment includes:
- **`startupProbe`**: TCP socket probe on port 8001 with a long initial delay (`initialDelaySeconds: 30`, `periodSeconds: 10`, `failureThreshold: 30`). This gives the container up to 5 minutes to complete Wine setup and launch MT5 without premature pod restarts.
- **`readinessProbe`**: TCP socket probe on port 8001 (`periodSeconds: 10`, `failureThreshold: 3`) ensuring only bridge pods with active RPyC listeners receive livetrade traffic.

### Check Pod & PVC Status
```bash
# Check pod state
kubectl -n tradingbots-2025 get pods -l app=mt5-bridge

# Check PVC attachment
kubectl -n tradingbots-2025 get pvc -l app.kubernetes.io/managed-by=Helm

# View bridge initialization and mt5linux logs
kubectl -n tradingbots-2025 logs -l app=mt5-bridge -f
```

### Smoke Test RPyC Connectivity
To verify that the RPyC server is answering requests:

```bash
# Forward RPyC port locally
kubectl -n tradingbots-2025 port-forward svc/mt5-bridge 8001:8001
```

In a Python environment with `mt5linux`:
```python
from mt5linux import MetaTrader5

mt5 = MetaTrader5(host="localhost", port=8001)
assert mt5.initialize(), "Failed to initialize MT5 client"
terminal = mt5.terminal_info()
print("Terminal connected:", terminal)
print("Version:", mt5.version())
mt5.shutdown()
```

---

## Fallback Strategy: MetaApi Cloud

Self-hosted Wine environments have known operational risks:
- Wine display server or terminal crashes during long uptime periods.
- Automatic MT5 terminal updates requiring manual intervention.
- Memory leaks in prolonged wine server emulation.

If production smoke tests demonstrate that the self-hosted Wine bridge cannot reliably maintain account connections or recover from cluster maintenance events, the architectural fallback is **MetaApi Cloud** ([https://metaapi.cloud](https://metaapi.cloud)).

Because the copier framework abstracts broker interactions behind the `MT5Client` Protocol interface (`tradingbot/livetrade/mt5/client.py`), swapping from `mt5linux` RPyC to MetaApi cloud requires:
1. Implementing a `MetaApiClient` conforming to `MT5Client`.
2. Directing copier calls to MetaApi REST/WebSocket endpoints.
3. No changes to `MT5Broker` reconciliation, order sizing, or copier logic.

---

## Helm Chart Configuration Reference

All bridge settings reside under `mt5Bridge` in `values.yaml` (default is disabled and omitted).

| Parameter | Description | Default |
|---|---|---|
| `mt5Bridge.enabled` | Whether to deploy the MT5 bridge | `false` |
| `mt5Bridge.name` | Deployment, Service, and NetworkPolicy resource name | `mt5-bridge` |
| `mt5Bridge.image` | Container image repository and pinned tag | `registry.gitlab.com/datafortress/tradingbot25/mt5-bridge:main` |
| `mt5Bridge.imagePullPolicy` | Pull policy for the bridge container | `Always` |
| `mt5Bridge.imagePullSecrets` | Pull secrets for container registry | `[{name: "regcred"}]` (or inherits `.Values.imagePullSecrets`) |
| `mt5Bridge.port` | RPyC service and container port | `8001` |
| `mt5Bridge.pvcName` | PersistentVolumeClaim name for `/config` | `mt5-bridge-config` |
| `mt5Bridge.pvcSize` | Storage requested for MT5 Wine installation & data | `5Gi` |
| `mt5Bridge.storageClassName` | Optional storage class override | `""` (cluster default) |
| `mt5Bridge.resources` | Container resource requests and limits | `requests: {cpu: 100m, memory: 512Mi}`, `limits: {cpu: 2000m, memory: 4Gi}` |
| `mt5Bridge.env` | Additional custom environment variables | `[]` |
| `mt5Bridge.startupProbe` | Optional custom startup probe override (defaults to TCP 8001 with 30s initial delay) | `{}` |
| `mt5Bridge.readinessProbe` | Optional custom readiness probe override (defaults to TCP 8001) | `{}` |

### Rendering Template Verification
```bash
# Verify nothing renders when mt5Bridge is absent or disabled:
helm template t ./helm/tradingbots | grep -c "mt5"
# Result: 0

# Verify full manifest renders when mt5Bridge is enabled:
helm template t ./helm/tradingbots --set mt5Bridge.enabled=true
```
