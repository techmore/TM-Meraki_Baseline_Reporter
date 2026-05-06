# TM UniFi Baseline Runner

`./unifi/run.sh` is a separate UniFi/Ubiquiti reporting pipeline. It does not
modify or call the Meraki runner.

## API Modes

- `site-manager`: uses the official cloud Site Manager API at `https://api.ui.com/v1`.
- `network`: uses the local UniFi Network Application Integration API under
  `/proxy/network/integration/v1`, or the remote connector form under
  `https://api.ui.com/v1/connector/consoles/{consoleId}/network/integration/v1`.
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

# Remote Network Application connector
# This usually requires an API key from the UniFi account/API key area with
# access to the console. A local Network Integrations key may return 401 here.
UNIFI_NETWORK_CONSOLE_ID=58D...:123
UNIFI_NETWORK_API_KEY=...
```

For multiple saved customer/site entries, add numbered profile variables:

```sh
UNIFI_SITE1_NAME=First Campus
UNIFI_SITE1_API_KEY=...
UNIFI_SITE1_CONSOLE_ID=58D...:123
UNIFI_SITE1_SITE_ID=default

UNIFI_SITE2_NAME=Second Campus
UNIFI_SITE2_API_KEY=...
UNIFI_SITE2_BASE_URL=https://192.168.10.1
UNIFI_SITE2_SITE_ID=default
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
./unifi/run.sh --mode network --console-id 58D...:123 --site-id default --no-open
./unifi/run.sh --all-sites --no-open
./unifi/run.sh --all-sites --profile site1 --no-open
./unifi/run.sh --report-only --keep-html --no-open
./unifi/run.sh --health-check
```

Outputs are written to:

- `unifi/backups/latest/` for raw JSON backups
- `unifi/reports/latest/` for `report.pdf`, `report.html`, and inventory data

When `--all-sites` is used, outputs are separated by saved profile:

- `unifi/backups/sites/site1/`
- `unifi/reports/sites/site1/`
