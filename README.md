# CCU Marketing Dashboard

A one-file desktop app for CashCards that pulls marketing and sales data and builds a dashboard you open in your browser. It is for whoever runs the store's marketing and wants Shopify sales, ad spend and email results in one place. The data comes from Shopify, Google Ads, Meta Ads and Klaviyo, and every pull is read only. The dashboard is a single offline HTML file with all data and charts embedded.

## Quick start

1. Put a `config.yaml` with your API keys next to the app (see [Configuration](#configuration)).
2. Open the app:
   - Windows: double-click `CCU Marketing Dashboard.exe`.
   - macOS: unzip `CCU Marketing Dashboard.app.zip`, then open `CCU Marketing Dashboard.app`.
   - From source: `py "CCU Marketing Dashboard.py"` (Windows) or `python3 "CCU Marketing Dashboard.py"` (macOS).
3. Click **Start**. Progress shows in the log box, and the finished dashboard opens in your browser. It is saved under `dashboards/` (see [Outputs](#outputs)).

## Features

### Data it pulls (read only)

- **Shopify:** every order on every sales channel, refunds, product costs, and for online-store orders the first and last visit before purchase (referrer, landing page, UTM tags and ad-click ids). It also pulls online-store sessions, the shopping funnel and landing pages from Shopify analytics (ShopifyQL).
- **Google Ads:** daily spend, impressions, clicks and conversions per campaign; budgets; impression share lost to budget and to rank; and ad-click (gclid) to campaign matching for the last 90 days.
- **Meta Ads:** daily spend, impressions and link clicks per ad account.
- **Klaviyo:** flows and email campaigns with recipients, opens, clicks, bounces, unsubscribes, spam complaints and attributed orders.

### Dashboard tabs

| Tab | Contents |
|---|---|
| Overview | What to do next, Month pacing, Needs attention, Highlights, How the money moves, Net revenue by sales channel |
| Website | Shopping funnel, Sessions by traffic source, Where visitors come from, Landing pages |
| Click attribution | Revenue and orders by click channel, Click channels, Campaigns and sources, first-click to last-click journeys |
| Ads & email | Google Ads funnel, campaigns and spend vs. credited revenue; Meta Ads; email list health, campaigns and flows |
| Products & promos | Products, Product types, Rising and falling, Discount codes |
| Sales channels | Sales by channel, net revenue by channel over time, last 12 months by channel |
| Customers | Customer value by acquisition source, where and when customers buy, retention over the last 12 months, repeat buyers, win-back audience, refunds, Klaviyo flows |
| Data & definitions | Status of each data source, what is not covered yet, and how every number is defined |

### Other behaviour

- **Time windows:** last 7, 28 or 90 days, any calendar month, or a custom range within the loaded data. Windows use complete days in the store's time zone.
- **Click credit:** switch attribution between last click, first click and a split.
- **Viewing:** light/dark theme, printing one tab or all tabs, and tables with CSV download (larger ones also have a row filter).
- **Exports:** every pull also saves CSV files, plus a JSON file that lets you rebuild the dashboard later without calling any API.
- **Secrets stay out:** no key or token from `config.yaml` is written into the exports or the dashboard.
- **Partial failures:** if Google Ads, Meta Ads, Klaviyo or Shopify analytics fails or is not configured, that source is skipped and logged, and the dashboard is still built. Shopify orders are required.

## Requirements

- **Prebuilt apps:** no Python needed. They bundle Python 3.11.
  - Windows: `CCU Marketing Dashboard.exe`
  - macOS: `CCU Marketing Dashboard.app.zip`
- **From source:** Python 3 with tkinter (3.11 is the version the prebuilt apps use). Only the standard library is required.
  - `certifi` is optional. On macOS, HTTPS needs certificates: the app uses `certifi` if it is installed, otherwise it exports the Mac keychain's trusted roots to `~/Library/Caches/ccu_marketing_roots.pem`.
  - `tzdata` is optional. On Windows, Python has no time-zone data without it; the app then falls back to built-in daylight-saving rules for the Eastern, Central, Mountain, Pacific and Alaska time zones, and to a fixed offset for any other zone.
- **API access:** see [Configuration](#configuration) for the credentials each source needs.

## How to run

### Prebuilt app

1. Put `config.yaml` in the same folder as the app (or another folder the app searches, see [Where config.yaml is looked for](#where-configyaml-is-looked-for)).
2. Open the app.
3. Check the **Config:** line in the window. If it says config.yaml was not found, click **Choose config...** and select your `config.yaml`.
4. Click **Start**.

| Button | What it does |
|---|---|
| Start | Pull all sources, save exports and build the dashboard |
| Choose config... | Pick a `config.yaml` / `config.yml` |
| Rebuild from saved export... | Build a dashboard from an earlier export folder, with no API calls |
| Open folder | Open the app folder, which holds `exports/` and `dashboards/` |

If Windows SmartScreen or macOS Gatekeeper blocks the app, allow it in the system security/privacy settings ("Run anyway" / "Open anyway") and open it again.

### From source

With no options, the app opens the same window as the prebuilt app:

```
py "CCU Marketing Dashboard.py"          # Windows
python3 "CCU Marketing Dashboard.py"     # macOS
```

Command line, with no window (use `python3` in place of `py` on macOS):

```
py "CCU Marketing Dashboard.py" --pull [--config path/to/config.yaml] [--out DIR] [--no-open]
py "CCU Marketing Dashboard.py" --folder <saved export folder> [--out DIR] [--no-open]
```

| Option | Meaning |
|---|---|
| `--pull` | Pull every source and build the dashboard |
| `--folder DIR` | Rebuild from a saved export with no API calls. `DIR` can be one export folder, or a folder containing several (the newest `dashboard_data.json` is used). |
| `--config PATH` | Path to `config.yaml` (default: found automatically, see below) |
| `--out DIR` | Where to write the dashboard HTML (default `dashboards/` in the app folder). Exports always go to `exports/`. |
| `--no-open` | Do not open the dashboard in the browser |

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

- Sections start at column 0 and keys are indented exactly two spaces.
- Values may be quoted. A `#` preceded by a space starts a comment.
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
| `klaviyo.private_key` | For Klaviyo | Klaviyo private API key with read access |

**Shopify permissions.** The app reads orders (including customer order counts and visit data), products, item costs and ShopifyQL analytics. The Shopify app or token therefore typically needs `read_orders`, `read_all_orders` (for orders older than 60 days), `read_customers`, `read_products`, `read_inventory` and `read_reports`.

The app reads no environment variables. Keep `config.yaml` private and do not commit it to this repository.

## Outputs

Outputs are written to the app folder. For the prebuilt apps only, if that folder is read-only (for example, a quarantined app copy), they go to `~/Downloads` instead.

| Path | Contents |
|---|---|
| `dashboards/Marketing_dashboard_YYYY-MM-DD_HHMM.html` | The dashboard: self-contained, works offline, includes Chart.js |
| `exports/marketing_YYYY-MM-DD_HHMM/dashboard_data.json` | All computed data, used by **Rebuild from saved export** and `--folder` |
| `exports/marketing_YYYY-MM-DD_HHMM/*.csv` | `web_order_clicks.csv`, `sales_by_channel_daily.csv`, `sales_by_channel_monthly.csv`, `online_store_sessions_daily.csv`, `google_ads_daily.csv`, `meta_ads_daily.csv`, `retention_headline_numbers.csv` and the `retention_*.csv` tables |

The export folder is stamped with the store's local time; the dashboard file with your computer's local time.

## Troubleshooting

- **"No config.yaml found" / "config.yaml not found".** Place it next to the app, or use **Choose config...** / `--config`.
- **"config.yaml: ... is missing ..." or "fill in ...".** A required key for that source is empty. Check the indentation (exactly two spaces) and spelling against [Configuration](#configuration).
- **"login rejected: check the keys in config.yaml" (HTTP 401).** The credentials for that source are wrong or have expired.
- **"missing a permission for this data" (HTTP 403).** The token or app lacks a read permission (scope) for that data. For Shopify, see the scopes under [Configuration](#configuration).
- **`shopify.store_url must be the store's *.myshopify.com address`.** Use the `myshopify.com` domain, not a custom domain.
- **"Shopify gave no access token".** With `client_id` / `client_secret`, the Shopify app must be installed on the store.
- **"Shopify bulk export: ...".** Shopify allows one bulk export per app at a time. Wait for any other tool using the same app to finish, then try again.
- **`CERTIFICATE_VERIFY_FAILED` on a Mac.** Run `Install Certificates.command` in Applications > Python 3.x, or `pip install certifi`.
- **"Google Ads: none of the API versions ... answered".** Google has retired those versions. Update `GOOGLE_ADS_VERSIONS` in the source.
- **A source shows as "skipped" in the log.** The dashboard is still built without that source. The error is shown in the log and on the dashboard's **Data & definitions** tab.
- **Rate limits and brief outages.** These are retried automatically with backoff.

## Building the app

The source does not include a build script. The prebuilt apps were packaged with PyInstaller (bundling Python 3.11), and the code detects a frozen build through `sys.frozen`. A comparable build would be:

```
pip install pyinstaller certifi
pyinstaller --onefile --windowed --name "CCU Marketing Dashboard" "CCU Marketing Dashboard.py"
```

This command is illustrative and not taken from the repository. Rebuild on each target OS: Windows for the `.exe`, macOS for the `.app`.

## Code layout

Everything is in `CCU Marketing Dashboard.py`:

| Part | Contents |
|---|---|
| Top of file | Constants: API versions, history windows, and caveat/gap texts shown on the dashboard |
| `find_config`, `read_config` | Config lookup and a small YAML reader |
| `http_json` | HTTPS with retries (rate limits, server errors, and dropped or timed-out connections). The Shopify client secret, Google client secret and refresh token, Meta token and Klaviyo key are masked in error messages. |
| `Shopify`, `GoogleAds`, `MetaAds`, `Klaviyo` | API clients |
| `compute_retention`, `classify_visit`, `compute_marketing` | Metrics and attribution |
| `pull`, `assemble` | Orchestration. Ads and email are pulled in parallel with Shopify. |
| `save_exports`, `load_folder` | CSV/JSON exports and rebuild |
| `build_dashboard` | Writes the HTML from `TEMPLATE` plus the embedded Chart.js (`CHARTJS_B64`) |
| `run_gui`, `main` | Tkinter window and command line |
