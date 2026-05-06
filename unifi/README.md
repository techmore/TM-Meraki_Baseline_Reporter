# TM UniFi Baseline Runner

`./unifi/run.sh` is a separate UniFi/Ubiquiti reporting pipeline. It does not
modify or call the Meraki runner.

## API Modes

- `site-manager`: uses the official cloud Site Manager API at `https://api.ui.com/v1`.
- `network`: uses the local UniFi Network Application Integration API under
  `/proxy/network/integration/v1`.
- `both`: collects both surfaces.
- `auto`: default. Uses the configured surface(s).

## Configuration

Use exported environment variables, root `.env`, or `unifi/.env`.

```sh
# Cloud Site Manager API
UNIFI_SITE_MANAGER_API_KEY=...

# Local Network Application API
UNIFI_NETWORK_BASE_URL=https://192.168.1.1
UNIFI_NETWORK_API_KEY=...
UNIFI_VERIFY_SSL=0
```

For the local Network Application API, create an API key in UniFi Network under
Settings > Control Plane > Integrations. Ubiquiti says the local Network API
documentation is specific to the installed Network version, so the collector
saves endpoint errors instead of failing the whole run when an endpoint is not
available on a given controller.

## Commands

```sh
./unifi/run.sh
./unifi/run.sh --mode network --no-open
./unifi/run.sh --report-only --keep-html --no-open
./unifi/run.sh --health-check
```

Outputs are written to:

- `unifi/backups/latest/` for raw JSON backups
- `unifi/reports/latest/` for `report.pdf`, `report.html`, and inventory data

