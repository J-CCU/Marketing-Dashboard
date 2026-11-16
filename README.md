# CCU Marketing Dashboard

A one-file desktop app for CashCards that pulls marketing and sales data and builds a dashboard you open in your browser. The data comes from Shopify, Google Ads, Meta Ads and Klaviyo, and every pull is read only. The dashboard is a single offline HTML file with all data and charts embedded.

## Features

### Data it pulls (read only)

- **Shopify:** every order on every sales channel, refunds, product costs, and for online-store orders the first and last visit before purchase (referrer, landing page, UTM tags and ad-click ids). It also pulls online-store sessions and the shopping funnel from Shopify analytics (ShopifyQL).
- **Google Ads:** daily spend, impressions, clicks and conversions per campaign; budgets; impression share lost to budget and to rank; and ad-click (gclid) to campaign matching.
- **Meta Ads:** daily spend, impressions and link clicks per ad account.
- **Klaviyo:** flows and campaigns with recipients, opens, clicks, bounces, unsubscribes, spam complaints and attributed orders.

### Dashboard sections

- **Overview:** What to do next, Month pacing, Needs attention, Highlights and How the money moves.
- **Sales and traffic:** Net revenue by sales channel, Shopping funnel, Sessions by traffic source, Where visitors come from and Landing pages.
- **Click attribution:** revenue and orders by first/last click channel, Campaigns and sources, and first-click to last-click journeys.
- **Ad and email performance:** the Google Ads funnel, campaigns, and spend vs. credited revenue; Meta Ads; and Email, covering campaigns and flows.
- **Products:** Products, Product types, Rising and falling, and Discount codes.
- **Sales history:** Sales by channel, including the last 12 months.
- **Customers:** Customer retention and win-back, covering cohorts, repeat rate and refunds, and customer value by acquisition source.

### Other behaviour

- **Time windows:** last 7, 28 or 90 days, any calendar month, or a custom range within the loaded data. Windows use complete days in the store's time zone.
- **CSV exports:** every pull also saves CSV exports, plus a JSON file that lets you rebuild the dashboard later without calling any API.
- **Secrets stay out:** no key or token from `config.yaml` is written into the exports or the dashboard.
- **Partial failures:** if Google Ads, Meta Ads, Klaviyo or Shopify analytics fails, that source is skipped and logged, and the dashboard is still built. Shopify orders are required.

## Requirements

- **Prebuilt apps:** no Python needed.
  - Windows: `CCU Marketing Dashboard.exe`
  - macOS: `CCU Marketing Dashboard.app.zip`
- **From source:** Python 3 with tkinter. The standard library is enough and no extra packages are needed. The prebuilt apps bundle Python 3.11.
- **On macOS from source:** HTTPS needs certificates. The app uses `certifi` if it is installed. Otherwise it exports the Mac keychain's trusted roots to `~/Library/Caches/ccu_marketing_roots.pem`.
- **API access:** see [Configuration](#configuration) for the credentials each source needs.

## How to run

### Prebuilt app

1. Put `config.yaml` in the same folder as the app (see [Configuration](#configuration)).
2. Open the app:
   - Windows: double-click `CCU Marketing Dashboard.exe`.
   - macOS: unzip `CCU Marketing Dashboard.app.zip`, then open `CCU Marketing Dashboard.app`.
3. Check the **Config:** line in the window. If it says config.yaml was not found, click **Choose config...** and select your `config.yaml`.
4. Click **Start**. Progress shows in the log box, and the finished dashboard opens in your browser.

Buttons:

| Button | What it does |
|---|---|
| Start | Pull all sources, save exports and build the dashboard |
| Choose config... | Pick a `config.yaml` / `config.yml` |
| Rebuild from saved export... | Build a dashboard from an earlier export folder, with no API calls |
| Open folder | Open the app folder |

If Windows SmartScreen or macOS Gatekeeper blocks the app, allow it in the system security/privacy settings ("Run anyway" / "Open anyway") and open it again.

### From source

With no options, the app opens the same window as the prebuilt app.

**Windows**

```
py "CCU Marketing Dashboard.py"
```

**macOS**

```
python3 "CCU Marketing Dashboard.py"
```

Command line, with no window:

```
py "CCU Marketing Dashboard.py" --pull [--config path\to\config.yaml] [--out DIR] [--no-open]
py "CCU Marketing Dashboard.py" --folder <saved export folder> [--out DIR] [--no-open]
```

| Option | Meaning |
|---|---|
| `--pull` | Pull every source and build the dashboard |
| `--folder DIR` | Rebuild from a saved export with no API calls. `DIR` can be one export folder, or a folder containing several (the newest is used). |
| `--config PATH` | Path to `config.yaml` (default: found automatically, see below) |
| `--out DIR` | Where to write the dashboard HTML (default `dashboards/`) |
| `--no-open` | Do not open the dashboard in the browser |

On macOS, use `python3` in place of `py`.

## Inputs

The only input file is `config.yaml`. The data itself comes from the APIs:

- **Shopify:** Admin GraphQL API, version `2026-07`
- **Google Ads:** API, versions `v26`, `v25` and `v24`, tried newest first
- **Meta:** Graph API, version `v24.0`
- **Klaviyo:** API, revision `2026-07-15`

How much history is loaded:

- **Orders:** whole months back to 12 months before the current one, for retention and monthly sales.
- **Daily marketing data:** at least 90 days, and back to the start of the month three months ago.

### Where config.yaml is looked for

The app searches these folders in order, for `config.yaml` and then `config.yml`:

1. The app folder
2. Its parent folder
3. `~/Downloads`
4. `~/Documents`
5. `~/Documents/Commerce Intel`

The app folder is the folder that holds the `.py`, the `.exe` or the `.app`.

## Configuration

`config.yaml` is the shared Project Integration config. The app reads only the sections and keys below and ignores any others.

The reader is a simple two-level YAML parser:

- Sections start at column 0 and keys are indented two spaces.
- Values may be quoted.
- Lists are written inline, as `["a", "b"]`.

```yaml
shopify:
  store_url: "your-store.myshopify.com"
  admin_api_token: "<admin API access token>"   # or use client_id + client_secret below
  client_id: "<app client id>"
  client_secret: "<app client secret>"

google_ads:
  client_id: "<OAuth client id>"
  client_secret: "<OAuth client secret>"
  refresh_token: "<OAuth refresh token>"
  developer_token: "<Google Ads developer token>"
  customer_id: "123-456-7890"
  login_customer_id: "123-456-7890"              # optional (manager account)

facebook_instagram:
  access_token: "<Meta access token>"            # or system_user_access_token
  ad_account_ids: ["act_<id>", "act_<id>"]

klaviyo:
  private_key: "<Klaviyo private API key>"
```

| Key | Required | Purpose |
|---|---|---|
| `shopify.store_url` | Yes | The store's `*.myshopify.com` address |
| `shopify.admin_api_token` | One of the two options | Admin API access token |
| `shopify.client_id`, `shopify.client_secret` | One of the two options | Used to get a token with the client-credentials grant when `admin_api_token` is empty. The app must be installed on the store. |
| `google_ads.client_id`, `client_secret`, `refresh_token` | For Google Ads | OAuth credentials used to get an access token |
| `google_ads.developer_token` | For Google Ads | Google Ads API developer token |
| `google_ads.customer_id` | For Google Ads | Ads account id (dashes allowed) |
| `google_ads.login_customer_id` | Optional | Manager (MCC) account id, if access is through a manager account |
| `facebook_instagram.access_token` or `system_user_access_token` | For Meta Ads | Graph API token with ads read access |
| `facebook_instagram.ad_account_ids` | For Meta Ads | Inline list of ad account ids |
| `klaviyo.private_key` | For Klaviyo | Klaviyo private API key |

The app reads no environment variables. Keep `config.yaml` private and do not commit it to this repository.

## Outputs

Outputs are written to the app folder. If that folder is read-only (for example, a quarantined app copy), they go to `~/Downloads` instead.

| Path | Contents |
|---|---|
| `dashboards/Marketing_dashboard_YYYY-MM-DD_HHMM.html` | The dashboard: self-contained, works offline, includes Chart.js |
| `exports/marketing_YYYY-MM-DD_HHMM/dashboard_data.json` | All computed data, used by **Rebuild from saved export** and `--folder` |
| `exports/marketing_YYYY-MM-DD_HHMM/*.csv` | `web_order_clicks.csv`, `sales_by_channel_daily.csv`, `sales_by_channel_monthly.csv`, `online_store_sessions_daily.csv`, `google_ads_daily.csv`, `meta_ads_daily.csv`, `retention_headline_numbers.csv` and the `retention_*.csv` tables |

## Building the app

The source does not include a build script. The prebuilt `.app` was packaged with PyInstaller (bundling Python 3.11), and the code detects a frozen build through `sys.frozen`. A comparable build would be:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "CCU Marketing Dashboard" "CCU Marketing Dashboard.py"
```

This command is illustrative and not taken from the repository. Rebuild on each target OS: Windows for the `.exe`, macOS for the `.app`.

## Troubleshooting

- **"config.yaml not found".** Place it next to the app, or use **Choose config...** / `--config`.
- **"login rejected: check the keys in config.yaml" (HTTP 401).** The credentials for that source are wrong or have expired.
- **"missing a permission for this data" (HTTP 403).** The token or app lacks a read permission (scope) for that data.
- **`shopify.store_url must be the store's *.myshopify.com address`.** Use the `myshopify.com` domain, not a custom domain.
- **`CERTIFICATE_VERIFY_FAILED` on a Mac.** Run `Install Certificates.command` in Applications > Python 3.x, or `pip install certifi`.
- **"Google Ads: none of the API versions ... answered".** Google has retired those versions. Update `GOOGLE_ADS_VERSIONS` in the source.
- **A source shows as "skipped" in the log.** The dashboard is still built without that source. The error is shown in the log and on the dashboard's source status.
- **Rate limits and brief outages.** These are retried automatically with backoff.

## Code layout

Everything is in `CCU Marketing Dashboard.py`:

| Part | Contents |
|---|---|
| Top of file | Constants: API versions, history windows, and caveat/gap texts shown on the dashboard |
| `find_config`, `read_config` | Config lookup and a small YAML reader |
| `http_json` | HTTPS with retries. Tokens are masked in errors. |
| `Shopify`, `GoogleAds`, `MetaAds`, `Klaviyo` | API clients |
| `compute_retention`, `classify_visit`, `compute_marketing` | Metrics and attribution |
| `pull`, `assemble` | Orchestration. Ads and email are pulled in parallel with Shopify. |
| `save_exports`, `load_folder` | CSV/JSON exports and rebuild |
| `build_dashboard` | Writes the HTML from `TEMPLATE` plus the embedded Chart.js (`CHARTJS_B64`) |
| `run_gui`, `main` | Tkinter window and command line |
