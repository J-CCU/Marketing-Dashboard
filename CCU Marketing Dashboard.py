#!/usr/bin/env python3
"""
CashCards Marketing Dashboard: one-file app (same look as the CashCards LIVE Dashboard).

Double-click to open. It reads config.yaml (the Project Integration config) for the logins, so keep
config.yaml next to this file (or in Downloads / Documents).
Press Start:  1) pulls, read only (nothing in any account is changed):
                 Shopify    every order (all sales channels), refunds, and for web orders the visit that
                            brought the customer (referrer, landing page, UTM tags, ad-click ids)
                 Google Ads daily spend, impressions and clicks per campaign, budgets and impression share
                 Meta Ads   daily spend and clicks per ad account
                 Klaviyo    flows, campaigns, recipients / opens / clicks / bounces / unsubscribes / attributed orders
              2) works out a to-do list, click attribution, ad efficiency, products and promo codes,
                 customer value, sales by channel and retention
              3) saves the numbers in ./exports and writes a self-contained dashboard to ./dashboards
              The dashboard shows the last 7 / 28 / 90 days, any calendar month or any custom range in the
              loaded days (back to the start of the month three months ago).

No key or token from config.yaml is ever written into the exports or the dashboard.
Command line:  python "CCU Marketing Dashboard.py" --pull [--config <config.yaml>] [--no-open]
               python "CCU Marketing Dashboard.py" --folder <a saved export folder>   (rebuild, no API calls)
No extra packages needed (standard library only).
"""
import argparse, base64, csv, json, queue, re, statistics, sys, threading, time, webbrowser, zlib
import urllib.error, urllib.parse, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

APP_NAME = "CashCards Marketing Dashboard"
HERE = Path(sys.argv[0] if getattr(sys, "frozen", False) else __file__).resolve().parent
EXPORTS = HERE / "exports"
DASHBOARDS = HERE / "dashboards"
CONFIG_NAMES = ["config.yaml", "config.yml"]
CONFIG_DIRS = [HERE, Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Documents" / "Commerce Intel"]
SHOPIFY_API = "2026-07"                       # same versions the Commerce Collector uses
KLAVIYO_REVISION = "2026-07-15"
GOOGLE_ADS_VERSIONS = ["v26", "v25", "v24"]   # newest first; Google retires old versions (v22 is gone)
META_API = "v24.0"
MONTHS = 12                                   # retention and monthly sales: whole months before this one
DAYS = 90                                     # daily marketing data: at least this many days, and back to the start of
MONTHS_BACK = 3                               # the month this many months ago, so recent calendar months can be compared
WINBACK = re.compile(r"win.?back", re.I)      # Klaviyo flows counted as win-back

RET_TITLE = "Customer Retention and Win-Back"
RET_INTRO = ("Understand why customers buy once, find who is likely to return, and run win-back that earns "
             "incremental profit rather than discounting customers who would return anyway.")
RET_CAVEATS = [
    "Customers by month counts every order except in-store POS orders. A customer's first purchase means their first order inside the window (earlier history is not loaded).",
    "An order is returning when the customer had ordered before, including before the window: Shopify's lifetime order count for the customer, less their later orders in the window (all channels).",
    "Orders without a customer (guest checkouts) or without a lifetime order count are counted as unknown, not as new; the repeat-order rate is among orders whose position is known.",
    "Cohorts, time since last purchase and days between purchases use every channel, POS included, and count purchase days (several orders on one day are one purchase).",
    "Refunds are counted by the date of the refund, on orders placed inside the window. The refund rate is orders with any refund (full or partial) out of all orders placed that month.",
]
NOT_COMPUTED = [["Win-back test vs comparison group", "needs a held-out group in Klaviyo: not set up"]]
CAVEATS = [
    "Click attribution uses the visits Shopify records for each online-store order: the first visit and the last visit before purchase (referrer, landing page, UTM tags and ad-click ids). Shopify keeps only those two, so 'split' gives each half the credit; it is not a full multi-touch model.",
    "Google Ads clicks are recognised by their ad-click id (gclid/gbraid/wbraid) or a google cpc/paid UTM tag; the campaign comes from the campaign id in the UTM tag when present. Google Ads' own 'conversions' include add-to-cart and checkout steps, so they are shown as reported and never added to revenue.",
    "Email clicks are visits tagged utm_source=klaviyo or medium email. Klaviyo's own attributed orders (any order within its attribution window after an open or click) are shown separately as Klaviyo reports them; they overlap with other channels and are not incremental lift.",
    "Order value is the order total including tax and shipping, before refunds. Net revenue counts standing orders only (not cancelled, fully refunded or voided) and is the subtotal after discounts and refunds, without tax and shipping. ROAS = click-attributed net revenue / ad spend.",
    "Marketplace orders (TikTok Shop, Walmart, eBay) arrive through Shopify, so their clicks happen on the marketplace and cannot be attributed here. In-store (POS) orders have no clicks.",
    "Sessions, add-to-cart, checkout and conversion come from Shopify's own analytics (ShopifyQL) and use Shopify's traffic-source grouping, which differs slightly from the click classification above. Shopify counts bot visits as 'direct' sessions.",
    "Windows use complete days in the store's time zone. 7/28/90 days end yesterday and compare with the same number of days just before; a calendar month compares with the month before (a month still running compares with the same days of last month); a custom range compares with the same number of days just before it. Comparisons show only when that earlier period is inside the loaded days. Klaviyo flow results are grouped by day in UTC, so a flow email sent late in the evening can land on the next day; campaigns count by send date with all their results to date. New customers are first-ever orders by Shopify's lifetime order count.",
    "Channel names for sales apps come from Shopify (for example the Shop app and the Facebook & Instagram shop).",
    "Products: items still on standing orders x the item price after every discount; product type comes from Shopify. Discount codes: standing orders that used the code, with the order's total discount (an order with two codes counts under both). Places use the shipping address (POS excluded). Order times are in the store's time zone.",
    "Customer value follows each first-time buyer (first ever order by Shopify's count) through all their later standing orders on any channel. '180-day value' uses customers whose first order was at least 180 days before the last complete day, so every one of them had a full 180 days. 'Value so far' for recent buyers is still growing.",
    "Google Ads budget headroom: impression share is the share of eligible impressions the campaign got; 'lost to budget' is the share it missed because the daily budget ran out (Google's own figures, Search and Shopping inventory only). Email health rates are per delivered email (bounces per email sent).",
    "Gross profit = net revenue less product cost (Shopify's cost per item x items still on the order). Costs are today's cost per item, not the cost when sold; items without a cost count as zero cost, and the coverage shows how many items had one. Shipping, fees and ad spend are not in product cost.",
]
GAPS = [
    {"what": "Google search impressions, rankings and queries (Search Console)", "adds": "how often Google shows the site, for which searches, and click-through rate",
     "how": "sign in once with Google for the search_console scope and put the refresh_token under search_console in config.yaml"},
    {"what": "Google Analytics 4", "adds": "engagement, returning visitors and devices across the whole site, beyond Shopify's session counts",
     "how": "put the service-account file named in ga4.service_account_json next to config.yaml (it is missing), or add a ga4 refresh_token"},
    {"what": "TikTok video views and LIVE analytics", "adds": "attention on TikTok next to TikTok Shop sales (the CCU LIVE Dashboard covers LIVE sessions)",
     "how": "authorize the TikTok Shop app for analytics; video views are only scraped by the Social Collector today"},
    {"what": "YouTube and Instagram content reach", "adds": "views, followers and engagement for organic social",
     "how": "the keys are already in config.yaml (youtube.api_key, facebook_instagram token); these pulls were not approved in this setup"},
    {"what": "Fees and shipping costs", "adds": "contribution margin after marketplace fees, payment fees and shipping labels",
     "how": "needs the marketplace payout reports and shipping-label costs (QuickBooks or the shipping app); not pulled"},
]


class AppError(Exception):
    pass


# ============================================================= config ====
def find_config(folder=HERE):
    for d in [Path(folder), Path(folder).parent] + CONFIG_DIRS:
        for n in CONFIG_NAMES:
            if (d / n).exists():
                return d / n
    return None


def read_config(path):
    """Tiny two-level YAML reader: {section: {key: value}}. Enough for config.yaml; no package needed."""
    cfg, sect = {}, None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([^:#]+?)\s*:\s*(.*)$", line)
        if not m:
            continue
        indent, key, val = len(m.group(1)), m.group(2).strip(), m.group(3)
        val = re.sub(r"\s+#.*$", "", val).strip().strip('"').strip("'")
        if indent == 0:
            sect = cfg.setdefault(key, {})
        elif indent == 2 and sect is not None:
            sect[key] = val
    return cfg


def yaml_list(v):
    """'["a", "b"]' -> ['a', 'b'] (the reader keeps inline lists as text)."""
    return [x.strip().strip('"').strip("'") for x in str(v or "").strip("[]").split(",") if x.strip().strip('"').strip("'")]


# ================================================================ API ====
def http_json(url, data=None, headers=None, retries=4, secret=None):
    """GET (or POST when data is given) and parse JSON, retrying rate limits and brief outages."""
    host = urllib.parse.urlparse(url).hostname
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(float(e.headers.get("Retry-After") or 2 ** attempt), 90)); continue
            body = e.read()[:4000].decode("utf-8", "replace")
            title = re.search(r"<title>(.*?)</title>", body, re.S)   # Shopify's OAuth errors are HTML pages
            detail = title.group(1).strip() if title else re.sub(r"\s+", " ", body)[:300]
            if secret:
                detail = detail.replace(secret, "<token>")
            hint = " (login rejected: check the keys in config.yaml)" if e.code == 401 or (e.code == 400 and "auth" in url + detail.lower()) \
                else " (the app or token is missing a permission for this data)" if e.code == 403 else ""
            raise AppError(f"{host} answered HTTP {e.code}{hint}: {detail}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt); continue
            raise AppError(f"Can't reach {host}: {e.reason}") from None


class Shopify:
    BULK_RUN = "mutation($q:String!){ bulkOperationRunQuery(query:$q){ bulkOperation{ id } userErrors{ message } } }"
    BULK_STATUS = "query($id:ID!){ node(id:$id){ ... on BulkOperation { status errorCode objectCount url } } }"
    ORDERS = ('{ orders(query:"created_at:>=%s") { edges { node { id createdAt sourceName test cancelledAt displayFinancialStatus '
              'totalPriceSet { shopMoney { amount } } currentSubtotalPriceSet { shopMoney { amount } } '
              'app { name } customer { id numberOfOrders } refunds { id createdAt totalRefundedSet { shopMoney { amount } } } '
              'discountCodes totalDiscountsSet { shopMoney { amount } } shippingAddress { provinceCode countryCodeV2 } '
              'lineItems { edges { node { title currentQuantity discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } } '
              'product { id handle productType } variant { inventoryItem { unitCost { amount } } } } } } } } } }')
    REFUND_ITEMS = ("query($ids:[ID!]!){ nodes(ids:$ids){ ... on Order { refunds { id "
                    "refundLineItems(first:250){ nodes{ quantity } } } } } }")
    VISIT = "source referrerUrl landingPage occurredAt utmParameters { source medium campaign content term }"
    JOURNEYS = ("query($q:String!,$after:String){ orders(first:100, after:$after, query:$q) { pageInfo { hasNextPage endCursor } "
                "nodes { id customerJourneySummary { ready daysToConversion momentsCount { count } "
                "firstVisit { %s } lastVisit { %s } } } } }" % (VISIT, VISIT))

    def __init__(self, cfg, log):
        b, self.log = cfg.get("shopify", {}), log
        host = re.sub(r"^https?://", "", b.get("store_url", "")).strip("/")
        if not host.endswith(".myshopify.com"):
            raise AppError("config.yaml: shopify.store_url must be the store's *.myshopify.com address")
        self.root = f"https://{host}"
        self.token = b.get("admin_api_token")
        if not self.token:   # same client-credentials login the Commerce Collector uses
            if not (b.get("client_id") and b.get("client_secret")):
                raise AppError("config.yaml: fill in shopify.client_id and shopify.client_secret")
            body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": b["client_id"],
                                           "client_secret": b["client_secret"]}).encode()
            r = http_json(self.root + "/admin/oauth/access_token", body, {"Content-Type": "application/x-www-form-urlencoded"})
            self.token = r.get("access_token")
            if not self.token:
                raise AppError("Shopify gave no access token: check the app is installed on the store")

    def gql(self, query, variables=None):
        for attempt in range(8):
            r = http_json(f"{self.root}/admin/api/{SHOPIFY_API}/graphql.json",
                          json.dumps({"query": query, "variables": variables or {}}).encode(),
                          {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"})
            errs = r.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errs if isinstance(e, dict)):
                time.sleep(2 + attempt * 2); continue
            if errs:
                raise AppError("Shopify rejected the query: " + "; ".join(str(e.get("message", e)) for e in errs)[:300])
            return r.get("data") or {}
        raise AppError("Shopify kept rate-limiting the request; try again in a minute")

    def shop(self):
        return self.gql("{ shop { name ianaTimezone timezoneOffsetMinutes primaryDomain { host } } }")["shop"]

    def ql(self, query):
        """Rows of a ShopifyQL analytics query (sessions and the shopping funnel), as dicts."""
        r = self.gql("query($q:String!){ shopifyqlQuery(query:$q){ tableData { columns { name } rows } parseErrors } }",
                     {"q": query})["shopifyqlQuery"]
        if r.get("parseErrors"):
            raise AppError("Shopify analytics query rejected: " + "; ".join(r["parseErrors"])[:300])
        return (r.get("tableData") or {}).get("rows") or []

    def traffic(self, start, end):
        """Online-store sessions per day and traffic source, with the shopping funnel; plus top landing pages."""
        funnel = "sessions, sessions_with_cart_additions, sessions_that_reached_checkout, sessions_that_completed_checkout"
        daily = self.ql(f"FROM sessions SHOW {funnel} GROUP BY day, referrer_source, referrer_name "
                        f"SINCE {start} UNTIL {end} ORDER BY day LIMIT 20000")
        # biggest day x page rows first; the 20,000-row cap only drops pages with a visit or two that day
        pages = self.ql(f"FROM sessions SHOW {funnel} GROUP BY day, landing_page_path "
                        f"SINCE {start} UNTIL {end} ORDER BY sessions DESC LIMIT 20000")
        return {"daily": daily, "pages_daily": pages}

    def orders_since(self, since_utc):
        """Every order created since since_utc, through one bulk export (fast and gentle on rate limits)."""
        r = self.gql(self.BULK_RUN, {"q": self.ORDERS % since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")})["bulkOperationRunQuery"]
        if r["userErrors"]:
            raise AppError("Shopify bulk export: " + "; ".join(e["message"] for e in r["userErrors"]))
        op, t0, said = r["bulkOperation"]["id"], time.time(), 0
        while True:
            time.sleep(3)
            s = self.gql(self.BULK_STATUS, {"id": op})["node"]
            if s["status"] not in ("CREATED", "RUNNING"):
                break
            if time.time() - said > 15:
                self.log(f"  Shopify export running... {int(s['objectCount'] or 0):,} orders so far"); said = time.time()
            if time.time() - t0 > 1800:
                raise AppError("Shopify bulk export took over 30 minutes; try again later")
        if s["status"] != "COMPLETED":
            raise AppError(f"Shopify bulk export ended {s['status']} ({s['errorCode']})")
        if not s["url"]:
            return []
        with urllib.request.urlopen(s["url"], timeout=600) as f:
            rows = [json.loads(line) for line in f.read().decode("utf-8").splitlines() if line.strip()]
        # bulk exports list line items as their own rows pointing at the order (__parentId); put them back on it
        orders = {r["id"]: dict(r, lines=[]) for r in rows if "__parentId" not in r}
        for r in rows:
            if "__parentId" in r and r["__parentId"] in orders:
                cost = (((r.get("variant") or {}).get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
                price = ((r.get("discountedUnitPriceAfterAllDiscountsSet") or {}).get("shopMoney") or {}).get("amount")
                p = r.get("product") or {}
                # [items still on the order, cost per item, title, price per item after discounts, product type, handle, product id]
                orders[r["__parentId"]]["lines"].append([int(r.get("currentQuantity") or 0), float(cost) if cost not in (None, "") else None,
                                                         r.get("title") or "", float(price or 0), p.get("productType") or "",
                                                         p.get("handle") or "", p.get("id") or ""])
        return list(orders.values())

    def refund_items(self, order_ids):
        """{refund id: items refunded}. Only orders that have refunds are asked about, 50 at a time."""
        out = {}
        for i in range(0, len(order_ids), 50):
            for node in self.gql(self.REFUND_ITEMS, {"ids": order_ids[i:i + 50]})["nodes"]:
                for rf in (node or {}).get("refunds") or []:
                    out[rf["id"]] = sum(int(x.get("quantity") or 0) for x in rf["refundLineItems"]["nodes"])
        return out

    def web_journeys(self, since_utc):
        """{order id: customerJourneySummary} for online-store orders since since_utc."""
        q, after, out = f"source_name:web created_at:>={since_utc:%Y-%m-%dT%H:%M:%SZ}", None, {}
        while True:
            conn = self.gql(self.JOURNEYS, {"q": q, "after": after})["orders"]
            for n in conn["nodes"]:
                out[n["id"]] = n.get("customerJourneySummary")
            if not conn["pageInfo"]["hasNextPage"]:
                return out
            after = conn["pageInfo"]["endCursor"]
            if len(out) % 1000 == 0:
                self.log(f"  visits for {len(out):,} web orders...")


class GoogleAds:
    def __init__(self, cfg):
        b = cfg.get("google_ads", {})
        missing = [k for k in ("client_id", "client_secret", "refresh_token", "developer_token", "customer_id") if not b.get(k)]
        if missing:
            raise AppError("config.yaml: google_ads is missing " + ", ".join(missing))
        tok = http_json("https://oauth2.googleapis.com/token", urllib.parse.urlencode({
            "client_id": b["client_id"], "client_secret": b["client_secret"], "refresh_token": b["refresh_token"],
            "grant_type": "refresh_token"}).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
        self.h = {"Authorization": "Bearer " + tok["access_token"], "developer-token": b["developer_token"],
                  "Content-Type": "application/json"}
        if b.get("login_customer_id"):
            self.h["login-customer-id"] = b["login_customer_id"].replace("-", "")
        self.cid, self.version = b["customer_id"].replace("-", ""), None

    def search(self, query):
        for v in ([self.version] if self.version else GOOGLE_ADS_VERSIONS):
            try:
                r = http_json(f"https://googleads.googleapis.com/{v}/customers/{self.cid}/googleAds:searchStream",
                              json.dumps({"query": query}).encode(), self.h)
                self.version = v
                return [x for seg in r for x in seg.get("results", [])]
            except AppError as e:
                if self.version or not re.search(r"HTTP 404|UNSUPPORTED_VERSION|deprecated", str(e)):
                    raise
        raise AppError("Google Ads: none of the API versions " + ", ".join(GOOGLE_ADS_VERSIONS) + " answered; update GOOGLE_ADS_VERSIONS")

    def campaign_daily(self, start, end):
        return self.search("SELECT segments.date, campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
                           "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.search_impression_share, "
                           "metrics.search_budget_lost_impression_share, metrics.search_rank_lost_impression_share FROM campaign "
                           f"WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.impressions > 0")

    def gclid_campaigns(self, by_day):
        """{gclid: campaign id} for ad clicks found on landing pages, looked up one day at a time."""
        out = {}
        for d, ids in by_day.items():
            ids = sorted(ids)
            for i in range(0, len(ids), 200):
                quoted = ",".join("'" + g.replace("'", "") + "'" for g in ids[i:i + 200])
                for r in self.search(f"SELECT click_view.gclid, campaign.id FROM click_view WHERE segments.date = '{d}' "
                                     f"AND click_view.gclid IN ({quoted})"):
                    out[r["clickView"]["gclid"]] = r["campaign"]["id"]
        return out

    def budgets(self):
        """{campaign id: today's daily budget}."""
        return {r["campaign"]["id"]: round(int((r.get("campaignBudget") or {}).get("amountMicros") or 0) / 1e6, 2)
                for r in self.search("SELECT campaign.id, campaign_budget.amount_micros FROM campaign WHERE campaign.status != 'REMOVED'")}


class MetaAds:
    def __init__(self, cfg):
        b = cfg.get("facebook_instagram", {})
        self.token = b.get("access_token") or b.get("system_user_access_token")
        self.accounts = yaml_list(b.get("ad_account_ids"))
        if not (self.token and self.accounts):
            raise AppError("config.yaml: facebook_instagram needs access_token and ad_account_ids")

    def get(self, path, params):
        url, out = f"https://graph.facebook.com/{META_API}/{path}?" + urllib.parse.urlencode(dict(params, access_token=self.token)), []
        while url:
            d = http_json(url, secret=self.token)
            out += d.get("data") or []
            url = (d.get("paging") or {}).get("next")
        return out

    def daily(self, start, end):
        rows = []
        for a in self.accounts:
            name = http_json(f"https://graph.facebook.com/{META_API}/{a}?" + urllib.parse.urlencode(
                {"fields": "name", "access_token": self.token}), secret=self.token).get("name", a)
            for x in self.get(f"{a}/insights", {"level": "account", "time_increment": 1, "limit": 500,
                                                 "time_range": json.dumps({"since": str(start), "until": str(end)}),
                                                 "fields": "spend,impressions,clicks,inline_link_clicks"}):
                rows.append([x["date_start"], name, float(x.get("spend") or 0), int(x.get("impressions") or 0),
                             int(x.get("inline_link_clicks") or x.get("clicks") or 0)])
        return rows


class Klaviyo:
    STATS = ["recipients", "opens_unique", "clicks_unique", "conversions", "conversion_value",
             "delivered", "unsubscribes", "spam_complaints", "bounced"]

    def __init__(self, cfg):
        self.key = cfg.get("klaviyo", {}).get("private_key")
        if not self.key:
            raise AppError("config.yaml: fill in klaviyo.private_key")
        self.h = {"Authorization": "Klaviyo-API-Key " + self.key, "revision": KLAVIYO_REVISION,
                  "Accept": "application/vnd.api+json", "Content-Type": "application/vnd.api+json"}

    def get_all(self, path, params):
        url, out = f"https://a.klaviyo.com/api/{path}?{urllib.parse.urlencode(params)}", []
        while url:
            if not url.startswith("https://a.klaviyo.com/api/"):
                raise AppError("Klaviyo sent an unexpected next-page address")
            d = http_json(url, headers=self.h)
            out += d.get("data") or []
            url = (d.get("links") or {}).get("next")
        return out

    def flows(self):
        return [dict(f["attributes"], id=f["id"]) for f in self.get_all("flows/", {"fields[flow]": "name,status,trigger_type,updated"})]

    def email_campaigns(self):
        return [dict(c["attributes"], id=c["id"]) for c in self.get_all("campaigns/", {
            "filter": "equals(messages.channel,'email')", "fields[campaign]": "name,status,send_time"})]

    def placed_order_metric(self):
        mets = self.get_all("metrics/", {"fields[metric]": "name,integration"})
        po = [m for m in mets if m["attributes"]["name"] == "Placed Order"]
        po.sort(key=lambda m: ((m["attributes"].get("integration") or {}).get("name") != "Shopify"))
        return po[0]["id"] if po else None

    def flow_series(self, start, end, metric_id):
        """Flow results per day: [{"dates": [...], "results": [{groupings, statistics: {stat: [per day]}}]}, ...].
        Klaviyo allows at most 60 days per daily report, so it is asked in pieces."""
        out, a0 = [], start
        while a0 <= end:
            a1 = min(end, a0 + timedelta(days=59))
            body = {"data": {"type": "flow-series-report", "attributes": {
                "statistics": self.STATS, "conversion_metric_id": metric_id, "interval": "daily",
                "timeframe": {"start": f"{a0}T00:00:00", "end": f"{a1 + timedelta(days=1)}T00:00:00"}}}}
            a = http_json("https://a.klaviyo.com/api/flow-series-reports/", json.dumps(body).encode(), self.h, retries=10)["data"]["attributes"]
            out.append({"dates": [d[:10] for d in a.get("date_times") or []], "results": a.get("results") or []})
            a0 = a1 + timedelta(days=1)
        return out

    def values(self, kind, start, end, metric_id):
        """Recipients / opens / clicks / Klaviyo-attributed orders per campaign or flow message over start..end.
        Klaviyo allows about 2 of these reports a minute, so rate-limit waits are expected."""
        body = {"data": {"type": f"{kind}-values-report", "attributes": {
            "statistics": self.STATS, "conversion_metric_id": metric_id,
            "timeframe": {"start": f"{start}T00:00:00", "end": f"{end + timedelta(days=1)}T00:00:00"}}}}
        d = http_json(f"https://a.klaviyo.com/api/{kind}-values-reports/", json.dumps(body).encode(), self.h, retries=10)
        return d["data"]["attributes"].get("results", [])


# ========================================================== time zone ====
class USZone(tzinfo):
    """US daylight-saving rules, for Windows Pythons without time-zone data: DST runs from the
    2nd Sunday of March to the 1st Sunday of November, 02:00 local."""
    def __init__(self, std_hours):
        self.std = timedelta(hours=std_hours)

    def _bounds(self, y):
        m, n = datetime(y, 3, 8), datetime(y, 11, 1)
        return m + timedelta(days=(6 - m.weekday()) % 7, hours=2), n + timedelta(days=(6 - n.weekday()) % 7, hours=2)

    def dst(self, dt):
        start, end = self._bounds(dt.year)
        return timedelta(hours=1) if start <= dt.replace(tzinfo=None) < end else timedelta(0)

    def utcoffset(self, dt):
        return self.std + self.dst(dt)

    def fromutc(self, dt):
        start, end = self._bounds(dt.year)
        u = dt.replace(tzinfo=None)
        return dt + self.std + (timedelta(hours=1) if start - self.std <= u < end - self.std - timedelta(hours=1) else timedelta(0))

    def tzname(self, dt):
        return None


US_STD_HOURS = {"America/New_York": -5, "America/Chicago": -6, "America/Denver": -7,
                "America/Los_Angeles": -8, "America/Anchorage": -9}


def shop_tz(shop):
    name = shop.get("ianaTimezone") or ""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    if name in US_STD_HOURS:
        return USZone(US_STD_HOURS[name])
    return timezone(timedelta(minutes=int(shop.get("timezoneOffsetMinutes") or 0)))


# ================================================================ retention ====
def pct(a, b, digits=1):
    return f"{a / b * 100:.{digits}f}%" if b else ""


def month_list(since, today):
    out, y, m = [], since.year, since.month
    while (y, m) <= (today.year, today.month):
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def compute_retention(orders, refund_qty, flows, campaigns, tz, since, now):
    """Turn the raw API rows into the headline numbers and tables (same definitions as the old exports)."""
    today = now.date()
    local = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz)
    rows = []
    for o in orders:
        t = local(o["createdAt"])
        if o.get("test") or t.date() < since:
            continue
        c = o.get("customer") or {}
        n = c.get("numberOfOrders")
        rows.append({"t": t, "m": t.strftime("%Y-%m"), "pos": o.get("sourceName") == "pos", "cid": c.get("id"),
                     "n": int(n) if n not in (None, "") else None, "refunds": o.get("refunds") or [],
                     "status": o.get("displayFinancialStatus") or ""})
    rows.sort(key=lambda r: r["t"])
    by_cust = defaultdict(list)
    for r in rows:
        if r["cid"]:
            by_cust[r["cid"]].append(r)
    # Order position = lifetime order count less the customer's later orders in the window (all channels).
    for seq in by_cust.values():
        ns = [r["n"] for r in seq if r["n"] is not None]
        for j, r in enumerate(seq, 1):
            r["ret"] = None if not ns else max(ns) - (len(seq) - j) > 1
    months = month_list(since, today)

    # Customers by month (every order except POS)
    t_cust, tot = [], defaultdict(int)
    for m in months:
        os_ = [r for r in rows if r["m"] == m and not r["pos"]]
        ids = sum(1 for r in os_ if r["cid"])
        new = sum(1 for r in os_ if r["cid"] and r.get("ret") is False)
        ret = sum(1 for r in os_ if r["cid"] and r.get("ret") is True)
        t_cust.append([m, len(os_), ids, pct(ids, len(os_), 0), new, ret, len(os_) - new - ret, pct(ret, new + ret)])
        for k, v in (("orders", len(os_)), ("ids", ids), ("new", new), ("ret", ret)):
            tot[k] += v

    # Orders and refunds by month (all channels)
    t_ord = []
    for m in months:
        os_ = [r for r in rows if r["m"] == m]
        rf = sum(1 for r in os_ if r["status"] in ("REFUNDED", "PARTIALLY_REFUNDED"))
        full_rf = sum(1 for r in os_ if r["status"] == "REFUNDED")
        t_ord.append([m, len(os_), rf, full_rf, pct(rf, len(os_))])
    full = [r for r in t_ord if r[0] != today.strftime("%Y-%m")]
    last_full = full[-1] if full else None

    # Refunds by refund date
    rf_m = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        for x in r["refunds"]:
            m = local(x["createdAt"]).strftime("%Y-%m")
            rf_m[m][0] += 1
            rf_m[m][1] += float(x["totalRefundedSet"]["shopMoney"]["amount"])
            rf_m[m][2] += refund_qty.get(x["id"], 0)
    t_ref = [[m, rf_m[m][0], f"${rf_m[m][1]:,.2f}", rf_m[m][2]] for m in months if m in rf_m]

    # Purchase days per customer (all channels; several orders on one day = one purchase)
    days = {c: sorted({r["t"].date() for r in seq}) for c, seq in by_cust.items()}
    gaps = [(d[i] - d[i - 1]).days for d in days.values() for i in range(1, len(d))]
    buckets = {"30-60 days": 0, "60-90 days": 0, "90-365 days": 0}
    for d in days.values():
        since_last = (today - d[-1]).days
        key = "30-60 days" if 30 <= since_last < 60 else "60-90 days" if 60 <= since_last < 90 else \
              "90-365 days" if 90 <= since_last <= 365 else None
        if key:
            buckets[key] += 1
    coh = defaultdict(lambda: [0, 0])
    for d in days.values():
        coh[d[0].strftime("%Y-%m")][0] += 1
        coh[d[0].strftime("%Y-%m")][1] += any(0 < (x - d[0]).days <= 90 for x in d[1:])
    t_coh = [[m, coh[m][0], coh[m][1], pct(coh[m][1], coh[m][0], 0)] for m in months if coh[m][0]]

    # Klaviyo
    t_flows = sorted([[f.get("name", ""), f.get("status", ""), f.get("trigger_type", ""), (f.get("updated") or "")[:10]]
                      for f in flows], key=lambda r: r[0].lower())
    sent = defaultdict(int)
    for c in campaigns:
        if c.get("status") == "Sent" and c.get("send_time"):
            sent[local(c["send_time"]).strftime("%Y-%m")] += 1
    t_camp = [[m, sent[m]] for m in reversed(months[-6:])]
    wb = [f for f in t_flows if WINBACK.search(f[0])]

    shop_orders = tot["orders"]
    headline = {
        "klaviyo_flows": len(t_flows), "winback_flows": len(wb), "winback_flows_live": sum(f[1] == "live" for f in wb),
        "refund_rate_last_month": round(last_full[2] / last_full[1], 4) if last_full and last_full[1] else "",
        "customer_data_from": since.isoformat(), "customer_data_to": today.isoformat(),
        "orders_with_customer_id_share": round(tot["ids"] / shop_orders, 4) if shop_orders else "",
        "customers_known": len({r["cid"] for r in rows if r["cid"] and not r["pos"]}),
        "new_customer_orders": tot["new"], "returning_customer_orders": tot["ret"],
        "unknown_customer_orders": shop_orders - tot["new"] - tot["ret"],
        "repeat_order_rate": round(tot["ret"] / (tot["new"] + tot["ret"]), 4) if tot["new"] + tot["ret"] else "",
        "median_days_between_purchases": statistics.median(gaps) if gaps else "",
        "all_orders": len(rows), "pos_orders_excluded": sum(r["pos"] for r in rows),
    }
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    w = f"{since.isoformat()} to {today.isoformat()}"
    tables = [
        ("klaviyo_flows", "Klaviyo flows", ["flow", "status", "trigger", "updated"], t_flows),
        ("orders_and_refunds_by_month", "Orders and refunds by month (Shopify, all channels)",
         ["month", "orders", "refunded", "fully refunded", "refund rate"], t_ord),
        ("email_campaigns_sent", "Email campaigns sent (last 6 months)", ["month", "campaigns"], t_camp),
        ("customers_by_month", f"Customers by month (Shopify orders, {w}; POS excluded)",
         ["month", "orders", "with customer id", "id share", "new", "returning", "new/returning unknown", "repeat-order rate"], t_cust),
        ("refunds_by_refund_date", "Refunds by refund date (Shopify)", ["month", "refunds", "amount", "items"], t_ref),
        ("customers_by_time_since_last_purchase", "Customers by time since their last purchase (dormant segments)",
         ["since last purchase", "customers"], [[k, v] for k, v in buckets.items()]),
        ("first_purchase_cohorts", "First-purchase cohorts: share back within 90 days",
         ["first purchase month", "customers", "back within 90 days", "share"], t_coh),
        ("not_computed_yet", "Not computed yet", ["metric", "status"], NOT_COMPUTED),
    ]
    return {"headline": [{"k": k, "v": str(v)} for k, v in headline.items()], "computed_at": stamp, "data_as_of": stamp,
            "tables": [{"file": f"table_{i:02d}_{slug}.csv", "title": title, "named": True, "columns": cols,
                        "rows": [[str(x) for x in r] for r in rr]} for i, (slug, title, cols, rr) in enumerate(tables, 1)],
            "findings": None, "source": "api",
            "report": {"title": RET_TITLE, "intro": RET_INTRO, "caveats": RET_CAVEATS, "exported": "", "titles": []}}



# ================================================================ marketing ====
SALES_CHANNELS = {"web": "Online store", "tiktok": "TikTok Shop", "walmart": "Walmart", "ebay": "eBay",
                  "pos": "In-store (POS)", "shopify_draft_order": "Draft orders"}
PAID_WORDS = {"cpc", "ppc", "paid", "paidsearch", "paid_search", "paidsocial", "paid_social", "paid-social", "cpm",
              "ads", "ad", "display", "shopping", "pmax", "sem", "onlineads", "online_ads"}
SEARCH_ENGINES = {"google": "Google", "bing": "Bing", "yahoo": "Yahoo", "duckduckgo": "DuckDuckGo", "ecosia": "Ecosia",
                  "baidu": "Baidu", "yandex": "Yandex", "brave": "Brave", "googlequicksearchbox": "Google"}
SOCIAL_SITES = {"instagram": "Instagram", "ig": "Instagram", "facebook": "Facebook", "fb": "Facebook", "katana": "Facebook",
                "tiktok": "TikTok", "musically": "TikTok", "youtube": "YouTube", "youtu": "YouTube", "linktr": "Linktree",
                "linktree": "Linktree", "reddit": "Reddit", "twitter": "X", "discord": "Discord", "pinterest": "Pinterest",
                "whatnot": "Whatnot", "threads": "Threads", "snapchat": "Snapchat"}
AI_SITES = {"chatgpt": "ChatGPT", "openai": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini",
            "copilot": "Copilot", "claude": "Claude"}
EMAIL_SOURCES = {"klaviyo": "Klaviyo", "rivo": "Rivo loyalty", "shopify_email": "Shopify Email", "mailchimp": "Mailchimp",
                 "omnisend": "Omnisend", "gm": "Gmail app", "gmail": "Gmail", "outlook": "Outlook", "mail": "email link"}
META_WORDS = {"facebook", "fb", "instagram", "ig", "meta"}


APP_CHANNELS = {"Shop": "Shop app", "Facebook & Instagram": "Facebook & Instagram shop"}


def sales_channel(src, app=None):
    """Readable sales channel: Shopify's source name, or the app's own name for channels Shopify only numbers."""
    if src in SALES_CHANNELS:
        return SALES_CHANNELS[src]
    if src and src.startswith("subscription"):
        return "Subscriptions"
    if app:
        return APP_CHANNELS.get(app, app)
    return f"App {src}" if src else "No channel recorded"


def words(*texts):
    """Whole words of hosts, tags and app ids: 'android-app://com.google.android.gm' -> {..., 'google', 'gm'}."""
    out = set()
    for t in texts:
        out |= {w for w in re.split(r"[^a-z0-9_]+", (t or "").lower()) if w}
        h = (t or "").lower()
        if h in ("t.co", "x.com", "twitter.com") or h.endswith(".x.com"):
            out.add("twitter")
    return out


def first_hit(ws, table):
    return next((v for k, v in table.items() if k in ws), None)


def classify_visit(v, own_hosts, gads_names, gclid_map):
    """(channel, detail) for one Shopify visit: the click that brought the customer."""
    if not v:
        return "Not recorded", "Shopify kept no visit for this order"
    utm = v.get("utmParameters") or {}
    src, med, camp = (utm.get("source") or "").strip(), (utm.get("medium") or "").strip(), (utm.get("campaign") or "").strip()
    lp = urllib.parse.urlparse(v.get("landingPage") or "")
    q = {k.lower(): vals[0] for k, vals in urllib.parse.parse_qs(lp.query).items()}
    ref_url = v.get("referrerUrl") or ""
    ref = (urllib.parse.urlparse(ref_url).hostname or "").lower()
    if ref and any(ref == h or ref.endswith("." + h) for h in own_hosts):
        ref, ref_url = "", ""
    tag = words(src, med)                       # what the UTM tags say
    site = words(src, ref, ref_url.split("://")[-1].split("/")[0])   # where the click came from
    paid = bool(tag & PAID_WORDS)

    def gads_detail():
        cid = camp if camp in gads_names else gclid_map.get(q.get("gclid", ""), "")
        return gads_names.get(cid) or (f"campaign {camp}" if camp.isdigit() else camp) or "campaign not tagged"
    if q.get("gclid") or q.get("gbraid") or q.get("wbraid") or camp in gads_names or (paid and "google" in site):
        return "Google Ads", gads_detail()
    if q.get("msclkid") or (paid and site & {"bing", "microsoft"}):
        return "Microsoft Ads", camp or "campaign not tagged"
    if q.get("ttclid") or (paid and site & {"tiktok", "musically"}):
        return "TikTok Ads", camp or "campaign not tagged"
    if paid and tag & META_WORDS | (site & META_WORDS if paid else set()):
        return "Meta Ads", camp or "campaign not tagged"
    if paid:
        return "Other paid", f"{src} / {med}".strip(" /")
    email = first_hit(words(src), EMAIL_SOURCES) if src else None
    if email or tag & {"email", "e-mail", "newsletter"} or q.get("_kx") or (not src and first_hit(site, {"gm": 1, "gmail": 1, "outlook": 1, "mail": 1})):
        who = email or first_hit(site, EMAIL_SOURCES) or "email"
        kind = "flow" if "flow" in tag else "campaign" if "campaign" in tag else ""
        return "Email", f"{who} {kind}".strip() + (f": {camp}" if camp else "")
    if "google" in site and tag & {"product_sync", "organic", "free", "surfaces_across_google", "shopping_free"}:
        return "Google free listings", "Merchant Center product listings"
    ai = first_hit(site, AI_SITES)
    if ai:
        return "AI assistants", ai
    if site & {"shop_app", "shop", "shopify_shop"} and (src.lower().startswith("shop") or ref in ("shop.app", "shop.com")):
        return "Shop app", "Shopify's Shop app"
    social = first_hit(site, SOCIAL_SITES)
    if social:
        return "Social", social
    engine = first_hit(site, SEARCH_ENGINES)
    if engine:
        return "Organic search", engine
    if site & {"amazon", "amzn_us_shopdirect"} or src.lower().startswith("amzn"):
        return "Referral", "Amazon"
    if src:
        return "Other tagged", f"{src} / {med}".strip(" /")
    if ref:
        return "Referral", "Amazon" if "amazon" in site else re.sub(r"^www\.", "", ref)
    if q.get("fbclid"):
        return "Social", "Facebook / Instagram"
    return "Direct", "typed in, bookmark or app"


def compute_marketing(orders, journeys, gads_rows, gclid_map, meta_rows, kl, traffic, tz, start, end, own_hosts, since=None):
    """Compact daily rows the dashboard filters by window: sales by channel, web orders with their clicks,
    ad spend, email results and site traffic. `end` is the last complete day (yesterday)."""
    local = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz)
    gads_names = {r["campaign"]["id"]: r["campaign"]["name"] for r in gads_rows}
    rows = []
    for o in orders:
        if o.get("test"):
            continue
        c = o.get("customer") or {}
        rows.append((local(o["createdAt"]), o, c.get("id"), int(c["numberOfOrders"]) if c.get("numberOfOrders") not in (None, "") else None))
    rows.sort(key=lambda r: r[0])
    # New vs returning, same definition as retention: lifetime order count less the customer's later orders here.
    by_cust, position = defaultdict(list), {}
    for r in rows:
        if r[2]:
            by_cust[r[2]].append(r)
    for seq in by_cust.values():
        ns = [r[3] for r in seq if r[3] is not None]
        for j, r in enumerate(seq, 1):
            position[r[1]["id"]] = None if not ns else ("returning" if max(ns) - (len(seq) - j) > 1 else "new")
    # orders, standing, value, net, new, product cost, items, items with a cost, discounts, refunded
    blank = lambda: [0, 0, 0.0, 0.0, 0, 0.0, 0, 0, 0.0, 0.0]
    daily, monthly, web = defaultdict(blank), defaultdict(blank), []
    # products, discount codes, places and order times per day, so the page can add up any date range
    pmeta, pidx, chs, chidx = [], {}, [], {}
    prod, codes, geo, hours = defaultdict(lambda: [0, 0.0, 0.0, 0]), defaultdict(lambda: [0, 0.0, 0.0, 0]), \
        defaultdict(lambda: [0, 0.0, 0]), defaultdict(lambda: [0, 0.0])
    order_val, clicks = {}, {}   # order id -> (net, product cost); web order id -> (first, last click channel)
    for t, o, cid, _ in rows:
        if t.date() > end:
            continue
        ch = sales_channel(o.get("sourceName"), (o.get("app") or {}).get("name"))
        standing = not o.get("cancelledAt") and o.get("displayFinancialStatus") not in ("REFUNDED", "VOIDED")
        value = float((o.get("totalPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
        net = float((o.get("currentSubtotalPriceSet") or {}).get("shopMoney", {}).get("amount") or 0) if standing else 0.0
        new = standing and position.get(o["id"]) == "new"
        lines = [l for l in (o.get("lines") or []) if l[0] > 0] if standing else []
        cogs = sum(l[0] * l[1] for l in lines if l[1] is not None)
        items, costed = sum(l[0] for l in lines), sum(l[0] for l in lines if l[1] is not None)
        disc = float(((o.get("totalDiscountsSet") or {}).get("shopMoney") or {}).get("amount") or 0)
        refunded = sum(float(x["totalRefundedSet"]["shopMoney"]["amount"]) for x in o.get("refunds") or [])
        order_val[o["id"]] = (net, cogs)
        for agg, key in ((monthly, (t.strftime("%Y-%m"), ch)), (daily, (t.date().isoformat(), ch))):
            if agg is daily and t.date() < start:
                continue
            a = agg[key]; a[0] += 1; a[1] += standing; a[2] += value; a[3] += net; a[4] += new; a[5] += cogs; a[6] += items; a[7] += costed
            a[8] += disc; a[9] += refunded
        if standing and t.date() >= start:
            d, ci = t.date().isoformat(), chidx.setdefault(ch, len(chidx))
            if ci == len(chs):
                chs.append(ch)
            seen = set()
            for l in lines:   # [items, cost, title, price after discounts, type, handle, product id]
                if len(l) < 4:
                    continue
                key = l[6] or "t:" + l[2]
                if key not in pidx:
                    pidx[key] = len(pmeta); pmeta.append([l[2], l[4], l[5]])
                pr = prod[(d, pidx[key], ci)]
                pr[0] += l[0]; pr[1] += l[0] * l[3]; pr[2] += l[0] * (l[1] or 0); pr[3] += key not in seen; seen.add(key)
            for code in [c.upper() for c in (o.get("discountCodes") or [])] or ([""] if "discountCodes" in o else []):
                c = codes[(d, code, ci)]; c[0] += 1; c[1] += net; c[2] += disc; c[3] += new
            if o.get("sourceName") != "pos":
                ad = o.get("shippingAddress") or {}
                g = geo[(d, f"{ad.get('countryCodeV2') or '?'}-{ad.get('provinceCode') or '?'}" if ad else "no address")]
                g[0] += 1; g[1] += net; g[2] += new
                h = hours[(d, t.hour)]; h[0] += 1; h[1] += net
        if o.get("sourceName") == "web" and t.date() >= start:
            j = journeys.get(o["id"]) or {}
            f = classify_visit(j.get("firstVisit"), own_hosts, gads_names, gclid_map)
            l = classify_visit(j.get("lastVisit"), own_hosts, gads_names, gclid_map)
            web.append([t.date().isoformat(), round(value, 2), round(net, 2), int(standing), f[0], f[1], l[0], l[1],
                        j.get("daysToConversion"), (j.get("momentsCount") or {}).get("count"), position.get(o["id"]) or "", round(cogs, 2)])
            clicks[o["id"]] = (f[0], l[0])
    web.sort()
    # Customer value: what a first-time buyer is worth afterwards (all their standing orders, any channel)
    newcust, matured = [], defaultdict(lambda: [0, 0.0, 0.0, 0, 0])
    for seq in by_cust.values():
        seq = [r for r in seq if r[0].date() <= end]
        if not seq or position.get(seq[0][1]["id"]) != "new":
            continue
        t0, o0 = seq[0][0], seq[0][1]
        vals = [order_val.get(r[1]["id"], (0, 0)) for r in seq]
        ch0 = sales_channel(o0.get("sourceName"), (o0.get("app") or {}).get("name"))
        if t0.date() >= start:
            fc, lc = clicks.get(o0["id"], ("", ""))
            newcust.append([t0.date().isoformat(), ch0, fc, lc, round(vals[0][0], 2), round(sum(v[0] for v in vals), 2),
                            round(sum(v[0] - v[1] for v in vals), 2), len(seq)])
        if t0.date() <= end - timedelta(days=180):
            in180 = [v for r, v in zip(seq, vals) if (r[0] - t0).days < 180]
            for key in ("All channels", ch0):
                m = matured[key]; m[0] += 1; m[1] += sum(v[0] for v in in180); m[2] += sum(v[0] - v[1] for v in in180)
                m[3] += len(in180); m[4] += len(in180) > 1
    newcust.sort()
    def share(m):   # eligible impressions and those lost to budget / rank, so shares can be added up over any days
        imp, is_ = int(m.get("impressions") or 0), m.get("searchImpressionShare")
        if is_ in (None, "") or float(is_) <= 0:
            return [None, None, None]
        elig = imp / float(is_)
        return [round(elig), round(elig * float(m.get("searchBudgetLostImpressionShare") or 0)), round(elig * float(m.get("searchRankLostImpressionShare") or 0))]
    gads = [[r["segments"]["date"], r["campaign"]["id"], int(r["metrics"].get("impressions") or 0), int(r["metrics"].get("clicks") or 0),
             round(int(r["metrics"].get("costMicros") or 0) / 1e6, 2), round(float(r["metrics"].get("conversions") or 0), 2), *share(r["metrics"])]
            for r in gads_rows if r["segments"]["date"] <= end.isoformat()]
    gads_info = {r["campaign"]["id"]: {"name": r["campaign"]["name"], "status": r["campaign"].get("status", ""),
                                       "type": r["campaign"].get("advertisingChannelType", "")} for r in gads_rows}

    def kl_rows(results, key, names):
        """Klaviyo report rows are per message; add them up per campaign / flow."""
        out = {}
        for r in results or []:
            g = r["groupings"].get(key)
            if not g:
                continue
            cur = out.setdefault(g, {"id": g, **names.get(g, {"name": g}), **{s: 0 for s in Klaviyo.STATS}})
            for s in Klaviyo.STATS:
                cur[s] = round(cur[s] + (r["statistics"].get(s) or 0), 2)
        return sorted(out.values(), key=lambda x: -x["conversion_value"])
    camp_names = {c["id"]: {"name": c.get("name", ""), "sent": (c.get("send_time") or "")[:10]} for c in kl.get("campaigns", [])}
    flow_names = {f["id"]: {"name": f.get("name", ""), "status": f.get("status", "")} for f in kl.get("flows", [])}
    vals = kl.get("values") or {}
    klav = kl_rows(vals.get("campaign"), "campaign_id", camp_names) if "campaign" in vals else []
    flow_daily, fs = defaultdict(lambda: [0.0] * len(Klaviyo.STATS)), kl.get("flow_series") or []
    for part in ([fs] if isinstance(fs, dict) else fs):
        for r in part.get("results") or []:
            fid = r["groupings"].get("flow_id")
            for i, d in enumerate(part.get("dates") or []):
                v = [((r["statistics"].get(st) or [])[i:i + 1] or [0])[0] or 0 for st in Klaviyo.STATS]
                if any(v) and d <= end.isoformat():
                    a = flow_daily[(d, fid)]
                    for j, x in enumerate(v):
                        a[j] += x
    sent = defaultdict(int)
    for c in kl.get("campaigns", []):
        if c.get("status") == "Sent" and c.get("send_time"):
            sent[local(c["send_time"]).date().isoformat()] += 1
    n_ = lambda v: int(float(v or 0))
    funnel = ("sessions", "sessions_with_cart_additions", "sessions_that_reached_checkout", "sessions_that_completed_checkout")
    sessions = [[str(r.get("day"))[:10], r.get("referrer_source") or "unknown", r.get("referrer_name") or "",
                 *[n_(r.get(k)) for k in funnel]] for r in (traffic or {}).get("daily", [])]
    paths, pidx_ = [], {}
    for r in (traffic or {}).get("pages_daily") or []:
        pth = r.get("landing_page_path") or "(none)"
        if pth not in pidx_:
            pidx_[pth] = len(paths); paths.append(pth)
    pages = sorted([str(r.get("day"))[:10], pidx_[r.get("landing_page_path") or "(none)"], *[n_(r.get(k)) for k in funnel]]
                   for r in (traffic or {}).get("pages_daily") or [])
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "sales_daily": [[d, ch, *[round(x, 2) for x in v]] for (d, ch), v in sorted(daily.items())],
        "sales_monthly": [[m, ch, *[round(x, 2) for x in v]] for (m, ch), v in sorted(monthly.items())],
        "web": web, "gads": gads, "gads_campaigns": gads_info, "meta": [r for r in meta_rows if r[0] <= end.isoformat()],
        "email_campaigns": klav, "email_flows": {f: dict(v, id=f) for f, v in flow_names.items()}, "email_stats": Klaviyo.STATS,
        "email_flows_daily": [[d, f, *[round(x, 2) for x in v]] for (d, f), v in sorted(flow_daily.items())],
        "campaigns_sent": sorted((d, n) for d, n in sent.items() if d <= end.isoformat()),
        "sessions": sessions, "pages_daily": pages, "page_paths": paths, "channels": chs, "product_info": pmeta,
        # [day, product #, channel #, items, revenue, product cost, orders]; [day, code, channel #, orders, net, discount, new]
        "products_daily": sorted([d, p, c, v[0], round(v[1], 2), round(v[2], 2), v[3]] for (d, p, c), v in prod.items()),
        "codes_daily": sorted([d, code, c, v[0], round(v[1], 2), round(v[2], 2), v[3]] for (d, code, c), v in codes.items()),
        "geo_daily": sorted([d, g, v[0], round(v[1], 2), v[2]] for (d, g), v in geo.items()),
        "hours_daily": sorted([d, h, v[0], round(v[1], 2)] for (d, h), v in hours.items()),
        "newcust": newcust,
        "matured": {k: [v[0], round(v[1], 2), round(v[2], 2), v[3], v[4]] for k, v in matured.items()},
        "matured_from": [since.isoformat() if since else "", (end - timedelta(days=180)).isoformat()],
    }


def pull(cfg_path, log=print, raw_dir=None):
    """Pull every source (ads and email in parallel with Shopify), compute everything, save exports."""
    from concurrent.futures import ThreadPoolExecutor
    if not cfg_path:
        raise AppError("No config.yaml found. Put it next to this program (or in Downloads / Documents).")
    cfg = read_config(cfg_path)
    log(f"Config: {cfg_path}")
    sh = Shopify(cfg, log)
    shop = sh.shop()
    tz = shop_tz(shop)
    now = datetime.now(tz)
    today = now.date()
    y, m = now.year, now.month - MONTHS
    while m < 1:
        y, m = y - 1, m + 12
    end = today - timedelta(days=1)            # windows use complete days only
    sy, sm = end.year, end.month - MONTHS_BACK
    while sm < 1:
        sy, sm = sy - 1, sm + 12
    since, start = date(y, m, 1), min(end - timedelta(days=DAYS - 1), date(sy, sm, 1))
    to_utc = lambda d: datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)

    def google():
        ga = GoogleAds(cfg)
        rows, budgets = ga.campaign_daily(start, end), {}
        try:
            budgets = ga.budgets()
        except AppError as e:
            log(f"  Google Ads budgets skipped: {e}")
        return ga, rows, budgets

    def klaviyo():
        k = Klaviyo(cfg)
        out = {"flows": k.flows(), "campaigns": k.email_campaigns(), "values": {}}
        metric = k.placed_order_metric()
        if metric:
            out["values"] = {"campaign": k.values("campaign", start, end, metric)}
            log("  Klaviyo campaign report done")
            try:
                out["flow_series"] = k.flow_series(start, end, metric)
                log("  Klaviyo daily flow report done")
            except AppError as e:
                log(f"  Klaviyo flow results skipped: {e}")
        return out

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {"Google Ads": pool.submit(google), "Meta Ads": pool.submit(lambda: MetaAds(cfg).daily(start, end)),
                "Klaviyo": pool.submit(klaviyo)}
        log(f"Shopify: {shop['name']} ({shop['ianaTimezone']}); every order since {since}...")
        orders = sh.orders_since(to_utc(since))
        log(f"  {len(orders):,} orders; refunded items...")
        qty = sh.refund_items([o["id"] for o in orders if o.get("refunds")])
        log(f"  visit data for online-store orders since {start}...")
        journeys = sh.web_journeys(to_utc(start))
        log(f"  {len(journeys):,} online-store orders with visit data")
        log("  online-store sessions and landing pages...")
        try:
            traffic = sh.traffic(start, end)
            traffic_src = {"name": "Shopify analytics", "status": "ok" if traffic["daily"] else "empty",
                           "detail": f"{sum(int(float(r.get('sessions') or 0)) for r in traffic['daily']):,} online-store sessions since {start}"}
        except AppError as e:
            log(f"  sessions skipped: {e}")
            traffic, traffic_src = {}, {"name": "Shopify analytics", "status": "error", "detail": str(e)[:300]}
        sources = [{"name": "Shopify", "status": "ok", "detail": f"{len(orders):,} orders since {since}; visit data for {len(journeys):,} online-store orders"},
                   traffic_src]
        results = {}
        for name, job in jobs.items():
            try:
                results[name] = job.result()
            except AppError as e:
                log(f"  {name} skipped: {e}")
                sources.append({"name": name, "status": "error", "detail": str(e)[:300]})

    ga, gads_rows, gads_room = results.get("Google Ads") or (None, [], {})   # gads_room: daily budget per campaign
    gclid_map = {}
    if ga:
        sources.append({"name": "Google Ads", "status": "ok" if gads_rows else "empty",
                        "detail": f"API {ga.version}; {len({r['campaign']['id'] for r in gads_rows})} campaigns with impressions" if gads_rows else "no impressions in the window"})
        names = {r["campaign"]["id"] for r in gads_rows}
        by_day = defaultdict(set)
        for j in journeys.values():
            for v in ((j or {}).get("firstVisit"), (j or {}).get("lastVisit")):
                if not v or ((v.get("utmParameters") or {}).get("campaign") or "") in names:
                    continue
                g = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(v.get("landingPage") or "").query)).get("gclid")
                d = datetime.fromisoformat(v["occurredAt"].replace("Z", "+00:00")).astimezone(tz).date() if v.get("occurredAt") else None
                if g and d and d >= today - timedelta(days=89):
                    by_day[d.isoformat()].add(g)
        if by_day:
            log(f"  matching {sum(map(len, by_day.values()))} ad clicks to campaigns...")
            try:
                gclid_map = ga.gclid_campaigns(by_day)
            except AppError as e:
                log(f"  ad-click matching skipped: {e}")
    meta_rows = results.get("Meta Ads") or []
    if "Meta Ads" in results:
        spend = sum(r[2] for r in meta_rows)
        sources.append({"name": "Meta Ads", "status": "ok" if spend else "empty",
                        "detail": f"${spend:,.2f} spend since {start}" if spend else f"no spend since {start}"})
    kl = results.get("Klaviyo") or {}
    if "Klaviyo" in results:
        sources.append({"name": "Klaviyo", "status": "ok" if kl.get("values") else "empty",
                        "detail": f"{len(kl['flows'])} flows, {len(kl['campaigns'])} email campaigns; results from {start}"
                                  + ("" if kl.get("values") else "; no Placed Order metric, so no attributed orders")})
    sources.append({"name": "Search Console", "status": "not connected",
                    "detail": "no refresh_token or service account under search_console in config.yaml"})
    sources.append({"name": "GA4", "status": "not connected",
                    "detail": "the service_account_json file is missing and there is no refresh_token under ga4"})
    if raw_dir:
        Path(raw_dir).mkdir(parents=True, exist_ok=True)
        (Path(raw_dir) / "raw.json").write_text(json.dumps({
            "shop": shop, "orders": orders, "qty": qty, "journeys": journeys, "gads_rows": gads_rows, "gclid_map": gclid_map, "gads_room": gads_room,
            "meta_rows": meta_rows, "kl": kl, "traffic": traffic, "sources": sources, "now": now.isoformat(), "since": since.isoformat(),
            "start": start.isoformat(), "end": end.isoformat()}), encoding="utf-8")
    data = assemble(shop, tz, now, since, start, end, orders, qty, journeys, gads_rows, gclid_map, meta_rows, kl, traffic, sources, gads_room)
    save_exports(data, EXPORTS / f"marketing_{now:%Y-%m-%d_%H%M}", log)
    return data


def assemble(shop, tz, now, since, start, end, orders, qty, journeys, gads_rows, gclid_map, meta_rows, kl, traffic, sources, gads_room=None):
    host = (shop.get("primaryDomain") or {}).get("host") or ""
    own = [h for h in {host, re.sub(r"^www\.", "", host), "myshopify.com", "shopify.com"} if h]
    ret = compute_retention(orders, qty, kl.get("flows", []), kl.get("campaigns", []), tz, since, now)
    mk = compute_marketing(orders, journeys, gads_rows, gclid_map, meta_rows, kl, traffic, tz, start, end, own, since)
    mk["gads_budgets"] = gads_room or {}
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    return {"store": shop.get("name", ""), "domain": host, "timezone": shop.get("ianaTimezone", ""), "pulled_at": stamp,
            "sources": sources, "marketing": mk, "retention": ret, "caveats": CAVEATS, "gaps": GAPS, "source": "api"}


def replay(raw_path):
    """Recompute from a raw.json saved with --raw (development only: no API calls)."""
    r = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    tz, now = shop_tz(r["shop"]), datetime.fromisoformat(r["now"])
    now = now.astimezone(tz)
    start = date.fromisoformat(r["start"])
    end = date.fromisoformat(r.get("end") or (now.date() - timedelta(days=1)).isoformat())
    return assemble(r["shop"], tz, now, date.fromisoformat(r["since"]), start, end, r["orders"], r["qty"], r["journeys"],
                    r["gads_rows"], r["gclid_map"], r["meta_rows"], r["kl"], r.get("traffic") or {}, r["sources"], r.get("gads_room"))


# ============================================================ exports ====
def write_rows(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([header] + rows)


def save_exports(data, folder, log=print):
    """dashboard_data.json (to rebuild without the API) plus CSVs for spreadsheets."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dashboard_data.json").write_text(json.dumps(data), encoding="utf-8")
    mk, ret = data["marketing"], data["retention"]
    write_rows(folder / "web_order_clicks.csv", ["date", "order value", "net revenue", "standing", "first click channel",
               "first click detail", "last click channel", "last click detail", "days to convert", "visits", "customer", "product cost"], mk["web"])
    cols = ["channel", "orders", "standing orders", "order value", "net revenue", "new-customer orders", "product cost", "items", "items with a cost"]
    write_rows(folder / "sales_by_channel_daily.csv", ["date"] + cols, mk["sales_daily"])
    write_rows(folder / "sales_by_channel_monthly.csv", ["month"] + cols, mk["sales_monthly"])
    write_rows(folder / "online_store_sessions_daily.csv", ["date", "traffic source", "referrer", "sessions", "added to cart",
               "reached checkout", "completed checkout"], mk["sessions"])
    write_rows(folder / "google_ads_daily.csv", ["date", "campaign id", "campaign", "impressions", "clicks", "cost", "platform conversions",
                                                  "eligible impressions", "impressions lost to budget", "impressions lost to rank"],
               [[r[0], r[1], mk["gads_campaigns"].get(r[1], {}).get("name", ""), *r[2:]] for r in mk["gads"]])
    write_rows(folder / "meta_ads_daily.csv", ["date", "ad account", "spend", "impressions", "link clicks"], mk["meta"])
    with open(folder / "retention_headline_numbers.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["metric", "value"]] + [[h["k"], h["v"]] for h in ret["headline"]])
    for t in ret["tables"]:
        write_rows(folder / ("retention_" + t["file"]), t["columns"], t["rows"])
    log(f"Exports saved: {folder}")


def find_export_folder(start):
    """The folder itself if it has dashboard_data.json, else the newest export inside it."""
    if (start / "dashboard_data.json").exists():
        return start
    found = sorted(start.glob("**/dashboard_data.json"), key=lambda p: p.stat().st_mtime)
    return found[-1].parent if found else start


def load_folder(folder, log=print):
    p = Path(folder) / "dashboard_data.json"
    if not p.exists():
        raise AppError(f"No dashboard_data.json in {folder}")
    log(f"Read {p}")
    return json.loads(p.read_text(encoding="utf-8"))


# ========================================================== dashboard ====
def build_dashboard(data, out_dir=DASHBOARDS, log=print):
    data = dict(data, built_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    chartjs = zlib.decompress(base64.b64decode(CHARTJS_B64)).decode("utf-8")
    payload = json.dumps(data).replace("</", "<\\/")
    page = (TEMPLATE.replace("/*__CHARTJS__*/", chartjs.replace("</", "<\\/"), 1)
                    .replace("<!--__EMBED_DATA__-->",
                             f'<script id="embedded-data" type="application/json">{payload}</script>', 1))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Marketing_dashboard_{datetime.now():%Y-%m-%d_%H%M}.html"
    out.write_text(page, encoding="utf-8")
    log(f"Dashboard saved: {out}")
    return out


# ================================================================ GUI ====
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, scrolledtext

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("720x460")
    root.minsize(560, 360)
    q = queue.Queue()
    state = {"cfg": find_config()}

    frm = ttk.Frame(root, padding=16); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(frm, text="Pull Shopify, Google Ads, Meta and Klaviyo (read only) and build the marketing dashboard.",
              foreground="#555").pack(anchor="w", pady=(0, 10))
    cfgvar = tk.StringVar()
    ttk.Label(frm, textvariable=cfgvar).pack(anchor="w", pady=(0, 10))
    bar = ttk.Frame(frm); bar.pack(fill="x")
    logbox = scrolledtext.ScrolledText(frm, height=12, font=("Consolas", 9), state="disabled")
    logbox.pack(fill="both", expand=True, pady=(10, 0))

    def log(msg):   # safe from the worker thread
        q.put(("log", msg))

    def show(msg):
        logbox.configure(state="normal"); logbox.insert("end", msg + "\n"); logbox.see("end"); logbox.configure(state="disabled")

    def refresh():
        cfgvar.set(f"Config: {state['cfg']}" if state["cfg"] else "Config: config.yaml not found. Use Choose config...")

    def work(job):
        def run():
            try:
                q.put(("done", job()))
            except AppError as e:
                q.put(("error", str(e)))
            except Exception as e:  # keep the window alive and show what broke
                q.put(("error", f"{type(e).__name__}: {e}"))
        for b in buttons:
            b.configure(state="disabled")
        threading.Thread(target=run, daemon=True).start()

    def pump():
        while not q.empty():
            kind, val = q.get()
            if kind == "log":
                show(val)
                continue
            for b in buttons:
                b.configure(state="normal")
            if kind == "done":
                webbrowser.open(val.as_uri()); show("Opened in your browser. ✔")
            else:
                show(f"✖ {val}"); messagebox.showerror(APP_NAME, val)
        root.after(150, pump)

    def start():
        cfg = state["cfg"]
        work(lambda: build_dashboard(pull(cfg, log), log=log))

    def rebuild():
        d = filedialog.askdirectory(title="A saved export folder", initialdir=str(EXPORTS if EXPORTS.exists() else HERE))
        if d:
            work(lambda: build_dashboard(load_folder(find_export_folder(Path(d)), log), log=log))

    def pick_cfg():
        p = filedialog.askopenfilename(title="config.yaml", filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")])
        if p:
            state["cfg"] = Path(p); refresh()

    buttons = [ttk.Button(bar, text="▶  Start", command=start),
               ttk.Button(bar, text="Choose config...", command=pick_cfg),
               ttk.Button(bar, text="Rebuild from saved export...", command=rebuild),
               ttk.Button(bar, text="Open folder", command=lambda: webbrowser.open(HERE.as_uri()))]
    for i, b in enumerate(buttons):
        b.pack(side="left", padx=(0 if i == 0 else 8, 0))
    refresh()
    pump()
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--pull", action="store_true", help="pull every source and build (no window)")
    ap.add_argument("--folder", help="rebuild from a saved export folder (no API calls)")
    ap.add_argument("--config", help="path to config.yaml (default: next to this file, then Downloads / Documents)")
    ap.add_argument("--out", default=str(DASHBOARDS), help="where to write the dashboard HTML")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard in the browser")
    ap.add_argument("--raw", help=argparse.SUPPRESS)        # development: save raw API results here
    ap.add_argument("--replay", help=argparse.SUPPRESS)     # development: rebuild from a saved raw.json
    a = ap.parse_args()
    if not (a.pull or a.folder or a.replay):
        return run_gui()
    cfg = Path(a.config) if a.config else find_config()
    try:
        data = replay(a.replay) if a.replay else load_folder(find_export_folder(Path(a.folder))) if a.folder \
            else pull(cfg, raw_dir=a.raw)
        out = build_dashboard(data, a.out)
    except AppError as e:
        sys.exit(f"Error: {e}")
    if not a.no_open:
        webbrowser.open(out.as_uri())


# =========================================================== template ====
TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marketing Dashboard</title>
<style>
:root{
  color-scheme: light;
  --bg:#f4f3f0; --surface:#fcfcfb; --border:#e3e2dd; --grid:#ecebe7;
  --text:#0b0b0b; --text2:#52514e; --muted:#8a8984;
  --accent:#2a78d6; --dead:#e34948; --dead-bg:rgba(227,73,72,.12); --ok-bg:rgba(27,175,122,.14); --up:#1a8a5f; --down:#c4403f;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#eda100; --c5:#8e5bd0; --c6:#d1477a; --c7:#1d9fb5; --c8:#7a8c2e; --c9:#b0712f; --c10:#5a6ad6; --cg:#a3a29c;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --bg:#111110; --surface:#1a1a19; --border:#2e2e2c; --grid:#262624;
    --text:#f0efec; --text2:#c3c2b7; --muted:#8a8984;
    --accent:#3987e5; --dead:#e66767; --dead-bg:rgba(230,103,103,.16); --ok-bg:rgba(25,158,112,.18); --up:#3fc28d; --down:#ef7a79;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --c1:#3987e5; --c2:#e0703f; --c3:#2bb583; --c4:#d69a12; --c5:#a07ee0; --c6:#e0668f; --c7:#3bb4c9; --c8:#9aae45; --c9:#c98a4a; --c10:#7d8ae6; --cg:#6f6e69;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#111110; --surface:#1a1a19; --border:#2e2e2c; --grid:#262624;
  --text:#f0efec; --text2:#c3c2b7; --muted:#8a8984;
  --accent:#3987e5; --dead:#e66767; --dead-bg:rgba(230,103,103,.16); --ok-bg:rgba(25,158,112,.18); --up:#3fc28d; --down:#ef7a79;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
  --c1:#3987e5; --c2:#e0703f; --c3:#2bb583; --c4:#d69a12; --c5:#a07ee0; --c6:#e0668f; --c7:#3bb4c9; --c8:#9aae45; --c9:#c98a4a; --c10:#7d8ae6; --cg:#6f6e69;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1240px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:16px;margin:0 0 2px} h3{font-size:14px;margin:14px 0 6px} .sub{color:var(--text2);margin:0 0 14px;font-size:13px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:16px;min-width:0}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
label.ctl{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--text2)}
select,input[type=search]{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 8px;max-width:100%}
input[type=search]{width:220px}
input[type=date]{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:4px 6px}
#customBtn[aria-pressed=true],#monthSel.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
button,.btn{font:inherit;cursor:pointer;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 12px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer;user-select:none;background:none;color:var(--text)}
.chip[aria-pressed=false]{opacity:.45}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0;border-right:1px solid var(--border);padding:6px 12px;background:var(--surface)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff}
nav.tabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--border);margin:0 0 16px}
nav.tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:none;padding:8px 12px;color:var(--text2)}
nav.tabs button[aria-selected=true]{color:var(--text);border-bottom-color:var(--accent);font-weight:600}
.sticky{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px}
.kpi .l{font-size:12px;color:var(--text2)} .kpi .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.kpi .n{font-size:12px;color:var(--muted)} .kpi .d{font-size:12px;font-weight:600}
.d.up{color:var(--up)} .d.down{color:var(--down)} .d.flat{color:var(--muted)}
.kpis.small .kpi{padding:8px 10px} .kpis.small .kpi .v{font-size:15px}
.funnel{display:flex;flex-wrap:wrap;gap:6px;align-items:stretch}
.funnel .step{flex:1 1 120px;background:var(--grid);border-radius:8px;padding:8px 10px}
.funnel .step b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
.funnel .step span{font-size:12px;color:var(--text2)}
.funnel .arrow{align-self:center;color:var(--muted);font-size:12px;text-align:center;min-width:44px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:900px){.grid2{grid-template-columns:1fr}}
.chartbox{position:relative;height:280px}
.chartbox.tall{height:340px}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}
th{color:var(--text2);font-weight:600;cursor:pointer;position:sticky;top:0;background:var(--surface)}
td.num,th.num{text-align:right}
td.wrap{white-space:normal;min-width:200px}
tbody tr:hover{background:var(--grid)}
tfoot td{font-weight:600;border-top:1px solid var(--border)}
.scroll{max-height:420px;overflow:auto}
.tag{font-size:11px;padding:1px 6px;border-radius:4px;font-weight:600;background:var(--grid);color:var(--text2)}
.tag.ok{background:var(--ok-bg);color:var(--s3)} .tag.bad{background:var(--dead-bg);color:var(--dead)}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:6px;vertical-align:-1px}
details{margin-top:12px} summary{cursor:pointer;color:var(--text2);font-size:13px}
.hidden{display:none!important}
.note{font-size:12px;color:var(--muted)}
.insights li,.caveats li{margin:5px 0}
.caveats{color:var(--text2);font-size:13px;padding-left:20px}
#flow{overflow-x:auto}
.flow svg{width:100%;min-width:640px;height:auto;display:block}
.flow text{fill:var(--text);font-size:12px}
.flow text.v{fill:var(--text2)}
.callout{border-left:3px solid var(--s4);padding:8px 12px;background:var(--grid);border-radius:6px;font-size:13px;margin-top:10px}
ol.actions{margin:6px 0 0;padding-left:22px} ol.actions li{margin:8px 0;padding-left:2px}
ol.actions .tag{margin-left:6px} ol.actions a{color:var(--accent);font-size:12px;margin-left:6px;cursor:pointer}
.heat{overflow-x:auto} .heat table{width:auto;border-collapse:separate;border-spacing:2px;font-size:11px}
.heat td{width:26px;height:22px;padding:0;text-align:center;border:0;border-radius:3px;color:var(--text)}
.heat th{position:static;font-weight:500;padding:0 4px;cursor:default;background:none}
.movers{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media (max-width:600px){.movers{grid-template-columns:1fr}}
.movers ol{margin:4px 0;padding-left:20px;font-size:13px} .movers li{margin:3px 0}
</style>
</head>
<body>
<main>
  <div class="row" style="justify-content:space-between;align-items:flex-start;flex-wrap:nowrap">
    <div><h1 id="title">Marketing Dashboard</h1><p class="sub" id="sub"></p></div>
    <button id="themeBtn" title="Toggle light/dark" style="flex:none">◐ Theme</button>
  </div>
  <div class="sticky">
    <div class="row" style="justify-content:space-between;margin-bottom:6px">
      <div class="row"><span class="note">Window</span><div class="seg" id="winSeg"><button data-w="7">7 days</button><button data-w="28">28 days</button><button data-w="90">90 days</button></div>
        <select id="monthSel" aria-label="Calendar month"></select><button id="customBtn" aria-pressed="false">Custom…</button>
        <span id="customBox" class="row hidden" style="gap:6px"><input type="date" id="cFrom" aria-label="From"><span class="note">to</span><input type="date" id="cTo" aria-label="To"><button id="cApply">Apply</button></span>
        <span class="note" id="winNote"></span></div>
      <div class="row"><span class="note">Click credit</span><div class="seg" id="modelSeg"><button data-m="last">Last click</button><button data-m="first">First click</button><button data-m="split">Split</button></div></div>
    </div>
    <nav class="tabs" id="tabs">
      <button data-t="overview">Overview</button><button data-t="web">Website</button><button data-t="attr">Click attribution</button><button data-t="ads">Ads &amp; email</button>
      <button data-t="prod">Products &amp; promos</button><button data-t="sales">Sales channels</button><button data-t="ret">Customers</button><button data-t="data">Data &amp; definitions</button>
    </nav>
  </div>

  <section id="overview">
    <div class="kpis" id="ovKpis" style="margin-bottom:16px"></div>
    <div class="card" style="border-left:3px solid var(--accent)"><h2>What to do next</h2><p class="sub" id="actSub"></p><ol class="actions" id="actions"></ol><div id="actMore"></div></div>
    <div class="card"><h2>Month pacing</h2><p class="sub" id="paceSub"></p><div class="kpis small" id="pace"></div></div>
    <div class="grid2">
      <div class="card" style="border-left:3px solid var(--down)"><h2>Needs attention</h2><ul class="insights" id="warnings"></ul></div>
      <div class="card" style="border-left:3px solid var(--up)"><h2>Highlights</h2><ul class="insights" id="insights"></ul></div>
    </div>
    <div class="card flow"><h2>How the money moves</h2><p class="sub" id="flowSub"></p><div id="flow"></div></div>
    <div class="card"><h2>Net revenue by sales channel</h2><p class="sub" id="ovTrendSub"></p><div class="chartbox"><canvas id="ovTrend"></canvas></div></div>
  </section>

  <section id="web" class="hidden">
    <div class="kpis" id="wbKpis" style="margin-bottom:16px"></div>
    <div class="card"><h2>Shopping funnel</h2><p class="sub" id="wbFunnelSub"></p><div class="funnel" id="wbFunnel"></div><div id="wbNote"></div></div>
    <div class="card"><h2>Sessions by traffic source</h2><p class="sub" id="wbTrendSub"></p><div class="chartbox"><canvas id="wbTrend"></canvas></div></div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><h2>Where visitors come from</h2><p class="sub">Shopify's own traffic sources for every online-store session (not just buyers). Conversion = sessions that completed checkout.</p></div>
      <div class="seg" id="wbGroup"><button data-g="src" aria-pressed="true">By source</button><button data-g="ref" aria-pressed="false">By site</button></div></div><div id="wbTable"></div></div>
    <div class="card"><h2>Landing pages</h2><p class="sub" id="wbPagesSub"></p><div id="wbPages"></div></div>
  </section>

  <section id="attr" class="hidden">
    <div class="kpis" id="atKpis" style="margin-bottom:16px"></div>
    <div class="grid2">
      <div class="card"><h2>Online-store revenue by click channel</h2><p class="sub" id="atBarSub"></p><div class="chartbox tall"><canvas id="atBar"></canvas></div></div>
      <div class="card"><h2>Orders by click channel over time</h2><p class="sub" id="atTrendSub"></p><div class="chartbox tall"><canvas id="atTrend"></canvas></div></div>
    </div>
    <div class="card"><h2>Click channels</h2><p class="sub">Credit uses the click-credit setting above. First and last click are shown side by side so you can see which channels start journeys and which close them.</p><div id="atTable"></div></div>
    <div class="card"><h2>Campaigns and sources</h2><p class="sub">Each channel broken down by campaign, site or tag.</p><div id="atDetail"></div></div>
    <div class="card"><h2>Journeys: first click → last click</h2><p class="sub" id="pathSub"></p><div id="atPaths"></div></div>
  </section>

  <section id="ads" class="hidden">
    <div class="card"><h2>Google Ads: from impressions to revenue</h2><p class="sub" id="gFunnelSub"></p><div class="funnel" id="gFunnel"></div></div>
    <div class="card"><h2>Google Ads campaigns</h2><p class="sub">Spend, clicks and Google's own conversions come from Google Ads. Orders and revenue are online-store orders credited to the campaign by click (setting above). Google's conversions include add-to-cart and checkout steps, so they are not orders.</p><div id="gTable"></div></div>
    <div class="card"><h2>Google Ads: spend vs credited revenue per day</h2><div class="chartbox"><canvas id="gDaily"></canvas></div></div>
    <div class="card"><h2>Meta Ads</h2><div id="metaBox"></div></div>
    <div class="card"><h2>Email: from inbox to revenue</h2><p class="sub" id="eFunnelSub"></p><div class="funnel" id="eFunnel"></div><h3>List health</h3><div class="kpis small" id="eHealth"></div><div id="eNote"></div></div>
    <div class="card"><h2>Email campaigns</h2><p class="sub" id="ecSub"></p><div id="ecTable"></div></div>
    <div class="card"><h2>Email flows (automations)</h2><p class="sub" id="efSub"></p><div id="efTable"></div></div>
  </section>

  <section id="prod" class="hidden">
    <div class="kpis" id="pdKpis" style="margin-bottom:16px"></div>
    <div class="card"><h2>Products</h2><p class="sub" id="pdSub"></p><div id="pdTable"></div></div>
    <div class="grid2">
      <div class="card"><h2>Product types</h2><p class="sub" id="ptSub"></p><div class="chartbox tall"><canvas id="ptBar"></canvas></div><details><summary>Show table</summary><div id="ptTable"></div></details></div>
      <div class="card"><h2>Rising and falling</h2><p class="sub" id="mvSub"></p><div id="movers"></div></div>
    </div>
    <div class="card"><h2>Discount codes</h2><p class="sub" id="dcSub"></p><div id="dcTable"></div><div id="dcNote"></div></div>
  </section>

  <section id="sales" class="hidden">
    <div class="card"><h2>Sales by channel</h2><p class="sub" id="scSub"></p><div id="scTable"></div><div id="scCallout"></div></div>
    <div class="card"><h2>Net revenue by channel over time</h2><p class="sub" id="scTrendSub"></p><div class="chartbox tall"><canvas id="scTrend"></canvas></div></div>
    <div class="card"><h2>Last 12 months by channel</h2><p class="sub">Net revenue per month. The current month is partial.</p><div class="chartbox tall"><canvas id="scMonthly"></canvas></div><details><summary>Show table</summary><div id="scMonthlyTable"></div></details></div>
  </section>

  <section id="ret" class="hidden">
    <div class="kpis" id="cuKpis" style="margin-bottom:16px"></div>
    <div class="card"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><h2>What a new customer is worth, by where they came from</h2><p class="sub" id="acqSub"></p></div>
      <div class="seg" id="acqGroup"><button data-g="sales" aria-pressed="true">By sales channel</button><button data-g="click" aria-pressed="false">By click (online store)</button></div></div><div id="acqTable"></div></div>
    <div class="grid2">
      <div class="card"><h2>Where customers are</h2><p class="sub" id="geoSub"></p><div id="geoTable"></div></div>
      <div class="card"><h2>When customers buy</h2><p class="sub" id="heatSub"></p><div class="heat" id="heat"></div><div id="heatNote"></div></div>
    </div>
    <h2 style="margin:8px 0 4px">Retention, last 12 months</h2>
    <p class="note">This part uses the last 12 months and does not follow the window setting.</p>
    <div class="kpis" id="rtKpis" style="margin-bottom:16px"></div>
    <div class="card"><h2>Customers by month</h2><p class="sub">New vs returning customers per month (bars) and the share of orders from returning customers (line). Online orders only (POS excluded).</p><div class="chartbox"><canvas id="rtCust"></canvas></div><details><summary>Show table</summary><div id="rtCustT"></div></details></div>
    <div class="grid2">
      <div class="card"><h2>First-time buyers back within 90 days</h2><p class="sub">Share of each month's first-time buyers who bought again within 90 days. Lighter = 90 days not over yet.</p><div class="chartbox"><canvas id="rtCoh"></canvas></div><details><summary>Show table</summary><div id="rtCohT"></div></details></div>
      <div class="card"><h2>Win-back audience</h2><p class="sub">Customers by time since their last purchase (any channel).</p><div class="chartbox"><canvas id="rtDorm"></canvas></div><details><summary>Show table</summary><div id="rtDormT"></div></details></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Refunds by refund date</h2><div class="chartbox"><canvas id="rtRef"></canvas></div><details><summary>Show table</summary><div id="rtRefT"></div></details></div>
      <div class="card"><h2>Orders and refund rate by month</h2><p class="sub">All channels.</p><div id="rtOrdT"></div></div>
    </div>
    <div class="card"><h2>Klaviyo flows</h2><div class="chips" id="flowChips" style="margin-bottom:10px"></div><div id="rtFlowT"></div></div>
    <div class="card"><h2>Not computed yet</h2><div id="rtPendT"></div></div>
  </section>

  <section id="data" class="hidden">
    <div class="card"><h2>Data sources</h2><p class="sub" id="srcSub"></p><div id="srcTable"></div></div>
    <div class="card"><h2>Not covered yet, and what it would take</h2><div id="gapTable"></div></div>
    <div class="card"><h2>How the numbers are defined</h2><ul class="caveats" id="caveats"></ul></div>
    <div class="card"><h2>Retention definitions</h2><ul class="caveats" id="rcaveats"></ul></div>
  </section>
  <p class="note" id="foot"></p>
</main>
<div id="tip" style="position:fixed;pointer-events:none;display:none"></div>

<!--__EMBED_DATA__-->
<script>/*__CHARTJS__*/</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById('embedded-data').textContent);
const M = D.marketing, R = D.retention;
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = v => { if(v==null||String(v).trim()==='') return null; const n=parseFloat(String(v).replace(/[%$,]/g,'')); return isNaN(n)?null:n; };
const fmtN = (v,d=0) => v==null||!isFinite(v) ? '—' : v.toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});
const fmtM = (v,d) => v==null||!isFinite(v) ? '—' : (v<0?'-':'')+'$'+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:d??(Math.abs(v)<100?2:0),minimumFractionDigits:d??(Math.abs(v)<100?2:0)});
const fmtK = v => v==null||!isFinite(v) ? '—' : Math.abs(v)>=1e6 ? (v/1e6).toFixed(2)+'M' : Math.abs(v)>=1e4 ? (v/1e3).toFixed(1)+'k' : fmtN(v);
const fmtMK = v => v==null||!isFinite(v) ? '—' : Math.abs(v)>=1e6 ? '$'+(v/1e6).toFixed(2)+'M' : Math.abs(v)>=1e4 ? '$'+(v/1e3).toFixed(1)+'k' : fmtM(v);
const fmtP = (v,d=1) => v==null||!isFinite(v) ? '—' : (v*100).toFixed(d)+'%';
const fmtX = v => v==null||!isFinite(v) ? '—' : v.toFixed(2)+'×';
const sum = (a,f=x=>x) => a.reduce((s,x)=>s+(f(x)||0),0);
const charts = {};
const S = {w:'28', model:'last', tab:'overview'};
try{ const saved=JSON.parse(localStorage.getItem('mkdash')||'{}'); Object.assign(S, saved); }catch(e){}
const save = () => { try{ localStorage.setItem('mkdash', JSON.stringify({w:S.w, model:S.model, tab:S.tab})); }catch(e){} };

// ---------- dates and windows ----------
const END = M.end, END_D = new Date(M.end+'T00:00:00');
const addDays = (iso,n) => { const d=new Date(iso+'T00:00:00'); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); };
const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const addD = (s,n) => { const d=new Date(s+'T00:00:00'); d.setDate(d.getDate()+n); return iso(d); };
// S.w is '7' | '28' | '90' (days ending yesterday), 'm:2026-10' (a calendar month) or 'c:2026-10-01:2026-10-15' (custom)
const monthEnd = m => iso(new Date(+m.slice(0,4), +m.slice(5,7), 0));
function RANGE(){ const w=String(S.w);
  if(w.startsWith('m:')){ const m=w.slice(2), f=m+'-01', l=monthEnd(m); return {kind:'m', month:m, from:f<M.start?M.start:f, to:l>END?END:l, running:l>END}; }
  if(w.startsWith('c:')){ const [,f,t]=w.split(':'); return {kind:'c', from:f, to:t}; }
  return {kind:'n', n:+w, from:addD(END,-(+w)+1), to:END}; }
const nd = (w=win()) => Math.round((new Date(w.to+'T00:00:00')-new Date(w.from+'T00:00:00'))/864e5)+1;
function win(off=0){ const r=RANGE(); if(!off) return {from:r.from, to:r.to};
  if(r.kind==='m'){ const y=+r.month.slice(0,4), mo=+r.month.slice(5,7), pf=iso(new Date(y,mo-2,1)), pl=iso(new Date(y,mo-1,0));
    if(r.running){ const t=addD(pf,nd(r)-1); return {from:pf, to:t>pl?pl:t}; }   // month still running: the same days of last month
    return {from:pf, to:pl}; }
  const n=nd(r); return {from:addD(r.from,-n), to:addD(r.from,-1)}; }
const inW = (d,w) => d>=w.from && d<=w.to;
const hasPrev = () => win(1).from >= M.start;
const MONTH = m => new Date(m+'-01T00:00:00').toLocaleDateString(undefined,{month:'long',year:'numeric'});
const MON = m => new Date(m+'-01T00:00:00').toLocaleDateString(undefined,{month:'short'});
const cap = t => t.charAt(0).toUpperCase()+t.slice(1);
// words for the chosen range ("the last 28 days", "October 2026", "Oct 1 – Oct 15"), the period before it, a short form and a file-name form
function WT(){ const r=RANGE(); return r.kind==='n' ? `the last ${r.n} days` : r.kind==='m' ? MONTH(r.month)+(r.running?' so far':'') : `${fmtDay(r.from)} – ${fmtDay(r.to)}`; }
function PT(){ const r=RANGE(), p=win(1); return r.kind==='n' ? `the previous ${r.n} days` : r.kind==='m' ? (r.running?`the same days of ${MONTH(p.from.slice(0,7))}`:MONTH(p.from.slice(0,7))) : `${fmtDay(p.from)} – ${fmtDay(p.to)}`; }
function PS(){ const r=RANGE(); return r.kind==='n' ? `prev ${r.n}d` : r.kind==='m' ? MON(win(1).from.slice(0,7)) : 'prev period'; }
function FK(){ const r=RANGE(); return r.kind==='n' ? r.n+'d' : r.kind==='m' ? r.month : `${r.from}_to_${r.to}`; }
const days = w => { const out=[]; for(let d=w.from; d<=w.to; d=addD(d,1)) out.push(d); return out; };
const weekKey = d => { const x=new Date(d+'T00:00:00'); x.setDate(x.getDate()-((x.getDay()+6)%7)); return iso(x); };
const localTime = s => { const d=new Date(s); return isNaN(d)?s:d.toLocaleString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}); };
const fmtDay = s => new Date(s+'T00:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric'});

// ---------- colors: fixed per channel so a color always means the same thing ----------
const CLICK_ORDER = ['Google Ads','Meta Ads','Microsoft Ads','TikTok Ads','Other paid','Email','Organic search','Google free listings','AI assistants','Social','Shop app','Referral','Other tagged','Direct','Not recorded'];
const CLICK_COL = {'Google Ads':'--c1','Meta Ads':'--c10','Microsoft Ads':'--c7','TikTok Ads':'--c6','Other paid':'--c9','Email':'--c2','Organic search':'--c3','Google free listings':'--c8','AI assistants':'--c5','Social':'--c6','Shop app':'--c7','Referral':'--c4','Other tagged':'--c9','Direct':'--cg','Not recorded':'--cg'};
const PAID = new Set(['Google Ads','Meta Ads','Microsoft Ads','TikTok Ads','Other paid']);
const SALES_COL = {'Online store':'--c1','TikTok Shop':'--c6','Walmart':'--c4','eBay':'--c3','In-store (POS)':'--c2','Draft orders':'--c9',
  'Subscriptions':'--c5','Shop app':'--c7','Facebook & Instagram shop':'--c10'};
const EXTRA = ['--c8','--c5','--c9','--c3','--c7'], extraOf = {};
const colOf = (name, map) => { if(map[name]) return css(map[name]); if(map===SALES_COL){ if(!extraOf[name]) extraOf[name]=EXTRA[Object.keys(extraOf).length%EXTRA.length]; return css(extraOf[name]); } return css('--cg'); };
const clickCol = c => colOf(c, CLICK_COL), salesCol = c => colOf(c, SALES_COL);
const sw = c => `<span class="sw" style="background:${c}"></span>`;

// ---------- data access ----------
// web rows: [date, value, net, standing, firstCh, firstDetail, lastCh, lastDetail, daysToConvert, visits]
const WEB = M.web.map(r=>({d:r[0], value:r[1], net:r[2], st:r[3], fc:r[4], fd:r[5], lc:r[6], ld:r[7], days:r[8], visits:r[9], cust:r[10]||'', cogs:r[11]||0}));
// credit shares for an order under the current model: [[channel, detail, share], ...]
const credit = (o, model=S.model) => model==='first' ? [[o.fc,o.fd,1]] : model==='last' ? [[o.lc,o.ld,1]]
  : (o.fc===o.lc && o.fd===o.ld ? [[o.lc,o.ld,1]] : [[o.fc,o.fd,.5],[o.lc,o.ld,.5]]);
function attribute(w, model=S.model){   // {channel: {orders, net, value, details:{detail:{orders,net}}}} for standing web orders
  const out={};
  for(const o of WEB){ if(!o.st || !inW(o.d,w)) continue;
    for(const [c,dt,s] of credit(o,model)){ const a=out[c]||(out[c]={orders:0,net:0,value:0,newc:0,profit:0,days:[],details:{}});
      a.orders+=s; a.net+=o.net*s; a.value+=o.value*s; a.profit+=(o.net-o.cogs)*s; if(o.cust==='new') a.newc+=s; if(o.days!=null) a.days.push(o.days);
      const x=a.details[dt]||(a.details[dt]={orders:0,net:0,newc:0,profit:0}); x.orders+=s; x.net+=o.net*s; x.profit+=(o.net-o.cogs)*s; if(o.cust==='new') x.newc+=s; } }
  return out;
}
const SALES = M.sales_daily.map(r=>({d:r[0], ch:r[1], orders:r[2], st:r[3], value:r[4], net:r[5], newc:r[6]||0, cogs:r[7]||0, items:r[8]||0, costed:r[9]||0, disc:r[10]||0, refunded:r[11]||0}));
const HAS_COST = SALES.some(r=>r.costed>0);
function sales(w){ const out={}; for(const r of SALES){ if(!inW(r.d,w)) continue; const a=out[r.ch]||(out[r.ch]={orders:0,st:0,value:0,net:0,newc:0,cogs:0,items:0,costed:0});
  for(const k of ['orders','st','value','net','newc','cogs','items','costed','disc','refunded']) a[k]=(a[k]||0)+r[k]; } for(const a of Object.values(out)) a.profit=a.net-a.cogs; return out; }
// online-store sessions: [day, source, site, sessions, cart, checkout, completed]
const SESS = (M.sessions||[]).map(r=>({d:r[0], src:r[1], ref:r[2]||'(none)', s:r[3], cart:r[4], co:r[5], done:r[6]}));
function traffic(w, key='src'){ const out={}; for(const r of SESS){ if(!inW(r.d,w)) continue; const k=key==='src'?r.src:r.ref;
  const a=out[k]||(out[k]={s:0,cart:0,co:0,done:0}); a.s+=r.s; a.cart+=r.cart; a.co+=r.co; a.done+=r.done; } return out; }
const HAS_SESS = SESS.length>0;
const GADS = M.gads.map(r=>({d:r[0], id:r[1], impr:r[2], clicks:r[3], cost:r[4], conv:r[5], elig:r[6]??null, lostB:r[7]??0, lostR:r[8]??0}));
const gname = id => (M.gads_campaigns[id]||{}).name || id;
function gads(w){ const out={}; for(const r of GADS){ if(!inW(r.d,w)) continue; const a=out[r.id]||(out[r.id]={impr:0,clicks:0,cost:0,conv:0}); a.impr+=r.impr; a.clicks+=r.clicks; a.cost+=r.cost; a.conv+=r.conv; } return out; }
const META = (M.meta||[]).map(r=>({d:r[0], acct:r[1], spend:r[2], impr:r[3], clicks:r[4]}));
const metaSpend = w => sum(META.filter(r=>inW(r.d,w)), r=>r.spend);
// email: campaigns sent in the range (their results to date) and flow emails sent on each day of it
function KL(w=win()){
  if(!M.email_stats) return (M.klaviyo||{})[String(S.w)] || {campaigns:[],flows:[]};   // exports from older versions: preset windows only
  const ST=M.email_stats, f={};
  for(const r of M.email_flows_daily||[]){ if(!inW(r[0],w)) continue; const a=f[r[1]]||(f[r[1]]={name:r[1], ...((M.email_flows||{})[r[1]]||{}), id:r[1], ...Object.fromEntries(ST.map(x=>[x,0]))});
    ST.forEach((x,i)=>a[x]+=r[2+i]); }
  return {campaigns:(M.email_campaigns||[]).filter(c=>c.sent&&inW(c.sent,w)), flows:Object.values(f)}; }
// products, codes, places, order times and landing pages are stored per day; off=1 is the period before the range
const PINFO = M.product_info||[], CHS = M.channels||[];
const HAS_PROD = Array.isArray(M.products_daily);
const rng = off => off ? (hasPrev()?win(1):null) : win();
function PROD(off=0){ const w=rng(off), g={}; if(!w) return [];
  for(const r of M.products_daily||[]){ if(!inW(r[0],w)) continue; const i=PINFO[r[1]]||['?','',''];
    const a=g[r[1]]||(g[r[1]]={key:r[1], t:i[0], type:i[1]||'(no type)', handle:i[2], units:0, net:0, cogs:0, orders:0, ch:{}});
    a.units+=r[3]; a.net+=r[4]; a.cogs+=r[5]; a.orders+=r[6]; const c=CHS[r[2]]; a.ch[c]=(a.ch[c]||0)+r[4]; }
  return Object.values(g).sort((a,b)=>b.net-a.net); }
function PTYPES(off=0, P=PROD(off)){ const g={}; for(const p of P){ const a=g[p.type]||(g[p.type]={t:p.type,units:0,net:0,cogs:0,orders:0});
  a.units+=p.units; a.net+=p.net; a.cogs+=p.cogs; a.orders+=p.orders; } return Object.values(g); }
function PAGES(w=win()){ const g={}; for(const r of M.pages_daily||[]){ if(!inW(r[0],w)) continue; const a=g[r[1]]||(g[r[1]]=[(M.page_paths||[])[r[1]]??r[1],0,0,0,0]); for(let i=1;i<5;i++) a[i]+=r[i+1]; }
  return Object.values(g).sort((a,b)=>b[1]-a[1]).slice(0,50); }
const CODES = (off=0) => { const g={};
  const fam=c=>{ let m=/^(.*?)\s*\[RIVO/i.exec(c); if(m) return m[1]+' (Rivo loyalty coupons)'; m=/^([A-Z]+[-_])[A-Z0-9]{8,}$/.exec(c); if(m) return m[1]+'… (one-off codes)';
    m=/^([A-Z]{2,})\d+$/.exec(c); return m?m[1]+'# (numbered codes)':c; };
  const w=rng(off), by={}; if(!w) return [];
  for(const r of M.codes_daily||[]){ if(!inW(r[0],w)) continue; const a=by[r[1]]||(by[r[1]]=[r[1],0,0,0,0,{}]);
    a[1]+=r[3]; a[2]+=r[4]; a[3]+=r[5]; a[4]+=r[6]; const c=CHS[r[2]]; a[5][c]=(a[5][c]||0)+r[3]; }
  const rows=Object.values(by), cnt={}; rows.forEach(r=>{ const f=fam(r[0]); cnt[f]=(cnt[f]||0)+1; });
  for(const r of rows){ const f=fam(r[0]), k=cnt[f]>=3||f.includes('Rivo')?f:r[0];
    const a=g[k]||(g[k]={code:k, orders:0, net:0, disc:0, newc:0, ch:{}, n:0}); a.orders+=r[1]; a.net+=r[2]; a.disc+=r[3]; a.newc+=r[4]; a.n++;
    for(const [c,v] of Object.entries(r[5]||{})) a.ch[c]=(a.ch[c]||0)+v; }
  return Object.values(g); };
function GEO(off=0){ const w=rng(off), g={}; if(!w) return [];
  for(const r of M.geo_daily||[]){ if(!inW(r[0],w)) continue; const a=g[r[1]]||(g[r[1]]={g:r[1],orders:0,net:0,newc:0}); a.orders+=r[2]; a.net+=r[3]; a.newc+=r[4]; }
  return Object.values(g); }
function HOURS(w=win()){ if(!M.hours_daily) return null; const H=[...Array(7)].map(()=>[...Array(24)].map(()=>[0,0]));
  for(const r of M.hours_daily){ if(!inW(r[0],w)) continue; const c=H[(new Date(r[0]+'T00:00:00').getDay()+6)%7][r[1]]; c[0]+=r[2]; c[1]+=r[3]; }
  return H; }
const NEWC = (M.newcust||[]).map(r=>({d:r[0], sc:r[1], fc:r[2], lc:r[3], first:r[4], val:r[5], profit:r[6], n:r[7]}));
// [daily budget, impression share, share lost to budget, share lost to rank] over the range (Search and Shopping inventory)
function ROOM(id){ const w=win(); let imp=0, el=0, lb=0, lr=0;
  for(const r of GADS) if(r.id===id&&r.elig&&inW(r.d,w)){ imp+=r.impr; el+=r.elig; lb+=r.lostB; lr+=r.lostR; }
  const b=(M.gads_budgets||{})[id]; const bud=typeof b==='number'?b:null;
  return el ? [bud, imp/el, lb/el, lr/el] : bud!=null ? [bud,null,null,null] : null; }
const mainCh = ch => { const e=Object.entries(ch||{}).sort((a,b)=>b[1]-a[1]), t=sum(e,x=>x[1]); return e.length&&t ? `${e[0][0]} ${fmtP(e[0][1]/t,0)}` : '—'; };
const US = {AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',CO:'Colorado',CT:'Connecticut',DE:'Delaware',DC:'Washington DC',FL:'Florida',GA:'Georgia',HI:'Hawaii',ID:'Idaho',IL:'Illinois',IN:'Indiana',IA:'Iowa',KS:'Kansas',KY:'Kentucky',LA:'Louisiana',ME:'Maine',MD:'Maryland',MA:'Massachusetts',MI:'Michigan',MN:'Minnesota',MS:'Mississippi',MO:'Missouri',MT:'Montana',NE:'Nebraska',NV:'Nevada',NH:'New Hampshire',NJ:'New Jersey',NM:'New Mexico',NY:'New York',NC:'North Carolina',ND:'North Dakota',OH:'Ohio',OK:'Oklahoma',OR:'Oregon',PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',SD:'South Dakota',TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',VA:'Virginia',WA:'Washington',WV:'West Virginia',WI:'Wisconsin',WY:'Wyoming',PR:'Puerto Rico',GU:'Guam',VI:'US Virgin Islands'};
const place = g => { if(g==='no address') return 'No shipping address'; const [c,p]=g.split('-'); return c==='US'?(US[p]||p):`${p&&p!=='?'?p+', ':''}${c}`; };
const kvAll = K => sum((K.campaigns||[]).concat(K.flows||[]),x=>x.conversion_value||0);
const goTab = t => { S.tab=t; show(); scrollTo(0,0); };
// 180-day value of a first-time buyer (customers who have had a full 180 days)
const MAT = k => { const m=(M.matured||{})[k]; return m&&m[0] ? {n:m[0], net:m[1]/m[0], profit:m[2]/m[0], orders:m[3]/m[0], repeat:m[4]/m[0]} : null; };

// ---------- small renderers ----------
function delta(cur, prev, good=1){   // good=1: up is good; -1: down is good
  if(!hasPrev() || prev==null || cur==null || !isFinite(prev) || !isFinite(cur)) return '';
  if(prev===0) return cur===0 ? '<div class="d flat">no change</div>' : '<div class="d flat">new vs 0 before</div>';
  const ch=(cur-prev)/Math.abs(prev), cls=Math.abs(ch)<.005||!good?'flat':(ch>0)===(good>0)?'up':'down';
  return `<div class="d ${cls}">${ch>0?'▲':ch<0?'▼':''} ${Math.abs(ch*100).toFixed(Math.abs(ch)<.1?1:0)}% vs ${PT()}</div>`;
}
const kpi = (label, val, note='', d='') => `<div class="kpi"><div class="l">${esc(label)}</div><div class="v">${val}</div>${d}${note?`<div class="n">${note}</div>`:''}</div>`;

// generic table: cols [{k,label,num,fmt,wrap,html}], rows [obj]; sortable, optional filter, CSV download, optional totals row
function table(el, cols, rows, {filter=false, file='table.csv', foot=null, sort=null, max=null}={}){
  el = typeof el==='string' ? $(el) : el;
  const st = el._st || (el._st={sort:sort||{k:null,dir:-1}, q:''});
  const draw = () => {
    let rs = rows.filter(r=>!st.q || cols.some(c=>String(r[c.k]??'').toLowerCase().includes(st.q)));
    if(st.sort.k){ const k=st.sort.k; rs=[...rs].sort((a,b)=>{ const x=a[k],y=b[k];
      const c=(typeof x==='number'||typeof y==='number') ? (x??-Infinity)-(y??-Infinity) : String(x??'').localeCompare(String(y??'')); return c*st.sort.dir; }); }
    const body = rs.length ? rs.map(r=>`<tr>${cols.map(c=>`<td class="${c.num?'num':c.wrap?'wrap':''}">${c.html?c.html(r):esc(c.fmt?c.fmt(r[c.k],r):(r[c.k]??'—'))}</td>`).join('')}</tr>`).join('')
      : `<tr><td colspan="${cols.length}" class="note">Nothing in this window.</td></tr>`;
    const tf = foot ? `<tfoot><tr>${cols.map((c,i)=>`<td class="${c.num?'num':''}">${foot[c.k]!=null?esc(c.fmt?c.fmt(foot[c.k],foot):foot[c.k]):i===0?'Total':''}</td>`).join('')}</tr></tfoot>` : '';
    el.querySelector('table').innerHTML = `<thead><tr>${cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${esc(c.label)}${st.sort.k===c.k?(st.sort.dir>0?' ▲':' ▼'):''}</th>`).join('')}</tr></thead><tbody>${body}</tbody>${tf}`;
  };
  el.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:6px">${filter?'<input type="search" placeholder="Filter rows…" aria-label="Filter rows">':'<span></span>'}<button>Download CSV</button></div><div class="scroll"${max?` style="max-height:${max}px"`:''}><table></table></div>`;
  const inp = el.querySelector('input'); if(inp){ inp.value=st.q; inp.oninput=()=>{ st.q=inp.value.toLowerCase(); draw(); }; }
  el.querySelector('table').onclick = e => { const th=e.target.closest('th'); if(!th) return; const k=th.dataset.k; st.sort={k,dir:st.sort.k===k?-st.sort.dir:-1}; draw(); };
  el.querySelector('button').onclick = () => { const q=v=>{ v=String(v??''); return /[",\n]/.test(v)?`"${v.replace(/"/g,'""')}"`:v; };
    const csv=[cols.map(c=>q(c.label)).join(','), ...rows.map(r=>cols.map(c=>q(typeof r[c.k]==='number'?Math.round(r[c.k]*100)/100:r[c.k])).join(','))].join('\n');
    const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download=file; a.click(); };
  draw();
}

// ---------- charts (same styling as the LIVE dashboard) ----------
function baseOpts(yLabel){
  const t=css('--text2'), g=css('--grid');
  return {responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:'index',intersect:false},
    plugins:{legend:{position:'top',align:'start',labels:{color:t,boxWidth:10,boxHeight:10,usePointStyle:true,pointStyle:'rectRounded'}},
      tooltip:{backgroundColor:css('--surface'),titleColor:css('--text'),bodyColor:css('--text'),borderColor:css('--border'),borderWidth:1,padding:10}},
    scales:{x:{ticks:{color:t,maxRotation:0,autoSkip:true},grid:{display:false},border:{color:g}},
            y:{beginAtZero:true,ticks:{color:t},grid:{color:g},border:{display:false},title:{display:!!yLabel,text:yLabel,color:t}}}};
}
function mkChart(id,cfg){ if(charts[id]) charts[id].destroy(); const el=$(id); if(!el) return; charts[id]=new Chart(el,cfg); return charts[id]; }
const moneyAxis = o => { o.scales.y.ticks.callback=v=>fmtMK(v); };
// buckets for time charts: days for 7/28, weeks (Mon) for 90
function buckets(w){ const ds=days(w); if(nd()<=35) return {keys:ds, of:d=>d, label:fmtDay};
  const ks=[...new Set(ds.map(weekKey))]; return {keys:ks, of:weekKey, label:k=>'wk '+fmtDay(k)}; }

// ---------- Overview ----------
function totals(w){
  const s=sales(w), a=attribute(w), g=gads(w), ms=metaSpend(w);
  const gc=sum(Object.values(g),x=>x.cost), web=s['Online store']||{st:0,net:0,newc:0}, t=traffic(w), ts=sum(Object.values(t),x=>x.s), td=sum(Object.values(t),x=>x.done);
  return {net:sum(Object.values(s),x=>x.net), orders:sum(Object.values(s),x=>x.st), newAll:sum(Object.values(s),x=>x.newc), webNet:web.net, webOrders:web.st, webNew:web.newc,
    spend:gc+ms, gcost:gc, meta:ms, gNet:(a['Google Ads']||{}).net||0, gOrders:(a['Google Ads']||{}).orders||0,
    eNet:(a['Email']||{}).net||0, paidNet:sum(Object.entries(a).filter(([c])=>PAID.has(c)),([,x])=>x.net),
    paidNew:sum(Object.entries(a).filter(([c])=>PAID.has(c)),([,x])=>x.newc), sessions:ts, conv:ts?td/ts:null,
    profit:sum(Object.values(s),x=>x.profit), items:sum(Object.values(s),x=>x.items), costed:sum(Object.values(s),x=>x.costed),
    paidProfit:sum(Object.entries(a).filter(([c])=>PAID.has(c)),([,x])=>x.profit), disc:sum(Object.values(s),x=>x.disc||0), s, a, g};
}
function renderOverview(){
  const w=win(), cur=totals(w), prev=hasPrev()?totals(win(1)):null, P=k=>prev&&prev[k], R_=(x,k1,k2)=>x&&x[k2]?x[k1]/x[k2]:null;
  $('ovKpis').innerHTML = [
    kpi('Net revenue, all channels', fmtMK(cur.net), 'after discounts and refunds, before tax and shipping', delta(cur.net,P('net'))),
    HAS_COST ? kpi('Gross profit, all channels', fmtMK(cur.profit), `${fmtP(cur.profit/cur.net,0)} margin after product cost · ${fmtP(cur.costed/cur.items,0)} of items have a cost`, delta(cur.profit,P('profit'))) : '',
    kpi('Orders, all channels', fmtN(cur.orders), `avg ${fmtM(cur.net/cur.orders,0)} net`, delta(cur.orders,P('orders'))),
    kpi('New customers', fmtN(cur.newAll), `${fmtP(cur.newAll/cur.orders,0)} of orders were a first purchase`, delta(cur.newAll,P('newAll'))),
    kpi('Online-store net revenue', fmtMK(cur.webNet), `${fmtP(cur.webNet/cur.net,0)} of all net revenue`, delta(cur.webNet,P('webNet'))),
    HAS_SESS ? kpi('Online-store sessions', fmtK(cur.sessions), `${fmtP(cur.conv,2)} completed checkout`, delta(cur.sessions,P('sessions'))) : '',
    kpi('Ad spend', fmtMK(cur.spend), `Google ${fmtMK(cur.gcost)} · Meta ${fmtMK(cur.meta)}`, delta(cur.spend,P('spend'),0)),
    kpi('Return on ad spend', fmtX(R_(cur,'paidNet','spend')), `${modelName()} net revenue from ad clicks / ad spend`, delta(R_(cur,'paidNet','spend'), R_(prev,'paidNet','spend'))),
    kpi('Cost per new customer from ads', fmtM(R_(cur,'spend','paidNew'),0), `ad spend / ${fmtN(cur.paidNew,0)} first-time buyers from ad clicks`, delta(R_(cur,'spend','paidNew'), R_(prev,'spend','paidNew'),-1)),
    HAS_COST ? kpi('Gross profit per ad dollar', fmtX(R_(cur,'paidProfit','spend')), `${modelName()} gross profit from ad clicks / ad spend; above 1× pays for the ads`, delta(R_(cur,'paidProfit','spend'), R_(prev,'paidProfit','spend')))
      : kpi('Cost per online order', fmtM(R_(cur,'spend','webOrders')), 'all ad spend / online-store orders', delta(R_(cur,'spend','webOrders'), R_(prev,'spend','webOrders'),-1)),
    kpi('Online-store revenue per ad dollar', fmtX(R_(cur,'webNet','spend')), 'all online-store net revenue / all ad spend (blended, no click credit needed)', delta(R_(cur,'webNet','spend'), R_(prev,'webNet','spend'))),
    kpi('Email-click revenue', fmtMK(cur.eNet), `${modelName()} online orders from email clicks`, delta(cur.eNet,P('eNet'))),
    (()=>{ const m=MAT('Online store')||MAT('All channels'), cac=R_(cur,'spend','paidNew'); return m ? kpi('New-customer payback', HAS_COST&&cac?fmtX(m.profit/cac):fmtM(m.net,0),
      HAS_COST&&cac?`180-day gross profit of an online-store first-time buyer (${fmtM(m.profit,0)}) / ad cost per new customer (${fmtM(cac,0)})`:'net revenue per online-store new customer in their first 180 days') :
      kpi('Repeat-order rate', fmtP(num(RH.repeat_order_rate)), 'last 12 months, online orders'); })(),
    kpi('Discounts given', fmtMK(cur.disc), `${fmtP(cur.disc/(cur.net+cur.disc),1)} of revenue before discounts`, delta(cur.disc,P('disc'),0)),
  ].join('');
  renderFlow(cur);
  renderPace();
  renderActions(cur, prev);
  // trend
  const b=buckets(w), chs=Object.entries(cur.s).sort((x,y)=>y[1].net-x[1].net).map(([c])=>c);
  const ds=chs.map(c=>{ const m={}; for(const r of SALES) if(r.ch===c&&inW(r.d,w)) m[b.of(r.d)]=(m[b.of(r.d)]||0)+r.net;
    return {label:c,data:b.keys.map(k=>m[k]||0),backgroundColor:salesCol(c),stack:'n'}; });
  const o=baseOpts('Net revenue'); o.scales.x.stacked=true; o.scales.y.stacked=true; moneyAxis(o);
  o.plugins.tooltip.callbacks={label:c=>`${c.dataset.label}: ${fmtM(c.raw,0)}`, footer:its=>'Total: '+fmtM(sum(its,i=>i.raw),0)};
  mkChart('ovTrend',{type:'bar',data:{labels:b.keys.map(b.label),datasets:ds},options:o});
  $('ovTrendSub').textContent = (nd()<=35?'Per day':'Per week (Mon–Sun)')+`. Complete days through ${fmtDay(END)}.`;
  insights(cur, prev);
}
const modelName = () => ({last:'Last-click',first:'First-click',split:'Split-credit'})[S.model];

// ---------- month pacing: this month so far vs the same days last month, and the last full month ----------
function renderPace(){
  const e=END_D, y=e.getFullYear(), m=e.getMonth(), dom=e.getDate(), dim=new Date(y,m+1,0).getDate(), pdim=new Date(y,m,0).getDate();
  const ms=iso(new Date(y,m,1)), pms=iso(new Date(y,m-1,1)), pmSame=iso(new Date(y,m-1,Math.min(dom,pdim))), pme=iso(new Date(y,m,0)), ppms=iso(new Date(y,m-2,1)), ppme=iso(new Date(y,m-1,0));
  const net=(a,b)=>sum(SALES.filter(r=>r.d>=a&&r.d<=b),r=>r.net), spend=(a,b)=>sum(GADS.filter(r=>r.d>=a&&r.d<=b),r=>r.cost)+sum(META.filter(r=>r.d>=a&&r.d<=b),r=>r.spend);
  const mon=k=>sum(M.sales_monthly.filter(r=>r[0]===k),r=>r[5]), monWeb=k=>sum(M.sales_monthly.filter(r=>r[0]===k&&r[1]==='Online store'),r=>r[5]), mname=d=>new Date(d+'T00:00:00').toLocaleDateString(undefined,{month:'long'});
  const mtd=net(ms,END), same=net(pms,pmSame), proj=mtd/dom*dim, last=mon(pme.slice(0,7)), before=mon(ppme.slice(0,7));
  const sp=spend(ms,END), lsp=pms>=M.start?spend(pms,pme):null, bsp=ppms>=M.start?spend(ppms,ppme):null;
  const d=(c,p,g=1)=>p?`<div class="d ${Math.abs(c/p-1)<.005||!g?'flat':(c>p)===(g>0)?'up':'down'}">${c>=p?'▲':'▼'} ${fmtP(Math.abs(c/p-1),0)} vs ${'$1'}</div>`:'';
  const dd=(c,p,lab,g=1)=>d(c,p,g).replace('$1',lab);
  $('pace').innerHTML=[
    kpi(`${mname(ms)} so far (${dom} day${dom>1?'s':''})`, fmtMK(mtd), `net revenue, all channels`, dd(mtd,same,`${mname(pms)} 1–${Math.min(dom,pdim)}`)),
    kpi(`${mname(ms)} on pace for`, fmtMK(proj), dom<7?'rough: only a few days in':'if the daily rate so far holds', dd(proj,last,mname(pms))),
    kpi(`Ad spend, ${mname(ms)} so far`, fmtMK(sp), `on pace for ${fmtMK(sp/dom*dim)}`, lsp!=null?dd(sp/dom*dim,lsp,mname(pms),0):''),
    kpi(`${mname(pms)} (full month)`, fmtMK(last), `net revenue${lsp!=null?` · ad spend ${fmtMK(lsp)} · online store ${fmtX(monWeb(pme.slice(0,7))/lsp)} per ad dollar`:''}`, dd(last,before,mname(ppms))),
    kpi(`${mname(ppms)} (full month)`, fmtMK(before), `net revenue${bsp!=null?` · ad spend ${fmtMK(bsp)}`:''}`),
  ].join('');
  $('paceSub').textContent=`Calendar months, all sales channels, complete days through ${fmtDay(END)}. Does not follow the window setting.`;
}

// ---------- the action list: specific decisions, biggest money first ----------
function renderActions(cur){
  const w=win(), out=[], A={last:attribute(w,'last'), first:attribute(w,'first'), split:attribute(w,'split')};
  const gnet=(n,m)=>(((A[m]['Google Ads']||{}).details||{})[n]||{}).net||0, link=(t,l)=>`<a data-go="${t}">${l} →</a>`;
  // Google: budget-limited winners, and campaigns that lose money under every click credit
  for(const [id,x] of Object.entries(cur.g)){ const n=gname(id), r=ROOM(id), now=gnet(n,S.model), roas=x.cost?now/x.cost:0, best=Math.max(...['last','first','split'].map(m=>gnet(n,m)));
    if(r&&r[2]!=null&&r[2]>=.05&&roas>=3&&x.cost>=50) out.push({v:x.cost*Math.min(r[2]/Math.max(r[1]||.1,.1),1)*(roas-1), tag:'Google Ads', h:`<b>Raise the budget on ${esc(n)}</b>: ${fmtX(roas)} return on ${fmtMK(x.cost)} (${modelName().toLowerCase()}), yet it missed ${fmtP(r[2],0)} of the impressions it qualified for because its ${fmtM(r[0],0)}/day budget ran out.`, go:link('ads','campaigns')});
    if(x.cost>=Math.max(150,cur.gcost*.05)&&best<x.cost) out.push({v:x.cost-best, tag:'Google Ads', h:`<b>Cut or rework ${esc(n)}</b>: ${fmtMK(x.cost)} spent, and even its best click credit gives it only ${fmtMK(best)} net revenue (${fmtX(best/x.cost)}).`+(HAS_COST?' On gross profit it is further behind.':''), go:link('ads','campaigns')}); }
  // landing pages with traffic and no sales
  if(HAS_SESS){ const t=traffic(w), ts=sum(Object.values(t),x=>x.s), aov=cur.webOrders?cur.webNet/cur.webOrders:0, rate=ts?sum(Object.values(t),x=>x.done)/ts:0;
    for(const pg of PAGES(w).filter(p=>p[1]>=Math.max(500,ts*.02)&&p[4]/p[1]<.001).slice(0,2))
      out.push({v:pg[1]*rate*aov*.5, tag:'Website', h:`<b>Fix or re-point ${esc(pg[0])}</b>: ${fmtK(pg[1])} landing visits, ${fmtN(pg[4])} orders. At the store's usual ${fmtP(rate,2)} conversion that page would have made about ${fmtN(pg[1]*rate)} orders.`, go:link('web','landing pages')});
    // checkout abandonment and the flow meant to catch it
    const co=sum(Object.values(t),x=>x.co), dn=sum(Object.values(t),x=>x.done), fl=RT('klaviyo_flows'), ab=(fl?fl.rows:[]).filter(r=>/abandon|checkout|cart/i.test(r[0]));
    if(co-dn>0&&!ab.some(r=>r[1]==='live')) out.push({v:(co-dn)*aov*.05, tag:'Email', h:`<b>Turn on an abandoned-checkout email</b>: ${fmtN(co-dn)} sessions reached checkout without buying in ${WT()}, and no Klaviyo flow named for abandoned cart or checkout is live${ab.length?` (${ab.map(r=>esc(r[0])+': '+esc(r[1])).join(', ')})`:''}.`, go:link('ads','email flows')}); }
  // win-back
  const dm=RT('since_last_purchase'), dorm=dm?num((dm.rows.find(r=>r[0].startsWith('60'))||[])[1]):0, live=num(RH.winback_flows_live)||0;
  if(dorm&&!live) out.push({v:dorm*(cur.orders?cur.net/cur.orders:0)*.05, tag:'Customers', h:`<b>Start a win-back flow</b>: ${fmtN(dorm)} customers last bought 60–90 days ago and none of the ${fmtN(num(RH.winback_flows)||0)} win-back flows in Klaviyo is live.`, go:link('ret','dormant customers')});
  // discount codes that mostly discount people who would buy anyway
  const C=CODES(0).filter(c=>c.code), td=sum(C,c=>c.disc);
  for(const c of C.filter(c=>c.disc>=Math.max(100,td*.1)&&c.orders>=5&&c.newc/c.orders<.15).sort((a,b)=>b.disc-a.disc).slice(0,2))
    out.push({v:c.disc*.5, tag:'Promos', h:`<b>Review code ${esc(c.code)}</b>: ${fmtMK(c.disc)} discounted on ${fmtN(c.orders)} orders, and only ${fmtP(c.newc/c.orders,0)} of them were first-time buyers.`, go:link('prod','discount codes')});
  // email list health
  const K=KL(), all=(K.campaigns||[]).concat(K.flows||[]), rc=sum(all,x=>x.recipients||0), dl=sum(all,x=>x.delivered||0), bo=sum(all,x=>x.bounced||0), un=sum(all,x=>x.unsubscribes||0), sp=sum(all,x=>x.spam_complaints||0);
  if(dl&&(un/dl>.005||sp/dl>.001||bo/rc>.02)) { const bad=[bo/rc>.02?`${fmtP(bo/rc,1)} of emails bounced (healthy is under 2%)`:'', un/dl>.005?`${fmtP(un/dl,2)} unsubscribed (under 0.5%)`:'', sp/dl>.001?`${fmtP(sp/dl,3)} marked it spam (under 0.1%)`:''].filter(Boolean);
    out.push({v:Math.max(cur.eNet,kvAll(K))*.05*(bo/rc>.04?2:1), tag:'Email', h:`<b>Clean up the email list</b>: ${bad.join('; ')}. High bounce and complaint rates push all email toward spam folders. Suppress bounced and long-inactive addresses in Klaviyo.`, go:link('ads','email')}); }
  // product costs that make margins look better than they are
  if(HAS_COST) for(const [c,x] of Object.entries(cur.s).filter(([c,x])=>x.net>=cur.net*.05&&x.items&&x.costed/x.items<.9))
    out.push({v:x.net*(1-x.costed/x.items)*.03, tag:'Data', h:`<b>Fill in product costs for ${esc(c)}</b>: ${fmtP(1-x.costed/x.items,0)} of its items have no cost in Shopify, so its profit and margin are overstated.`, go:link('sales','sales channels')});
  // where new customers are worth the most for the money
  const acq=acquisition('click', w).filter(r=>PAID.has(r.k)&&r.cac&&r.n>=10).sort((a,b)=>b.val/b.cac-a.val/a.cac);
  if(acq.length>1&&acq[0].val/acq[0].cac>1.5*(acq[acq.length-1].val/acq[acq.length-1].cac)) out.push({v:acq[0].n*acq[0].val*.1, tag:'Ads', h:`<b>Lean toward ${esc(acq[0].k)} for new customers</b>: each costs ${fmtM(acq[0].cac,0)} and has spent ${fmtM(acq[0].val,0)} so far, against ${fmtM(acq[acq.length-1].cac,0)} and ${fmtM(acq[acq.length-1].val,0)} on ${esc(acq[acq.length-1].k)}.`, go:link('ret','customer value')});
  if(HAS_PROD&&HAS_COST) for(const p of PROD(0).filter(p=>p.cogs>p.net&&p.net>=Math.max(500,cur.net*.002)).sort((a,b)=>(a.net-a.cogs)-(b.net-b.cogs)).slice(0,2))
    out.push({v:p.cogs-p.net, tag:'Products', h:`<b>Check price and cost on ${esc(p.t)}</b>: ${fmtMK(p.net)} sold for ${fmtMK(p.cogs)} of product cost, a ${fmtP((p.net-p.cogs)/p.net,0)} margin. Either it sells below cost or the cost per item in Shopify is wrong.`, go:link('prod','products')});
  for(const f of (K.flows||[]).filter(f=>f.status==='live'&&f.recipients>=1000&&f.delivered!=null&&(f.delivered/f.recipients<.8||f.opens_unique/(f.delivered||1)<.1)).sort((a,b)=>b.recipients-a.recipients).slice(0,2))
    out.push({v:(f.recipients-f.delivered)*1+f.recipients*.3, tag:'Email', h:`<b>Fix the ${esc(f.name)} flow</b>: it sent ${fmtK(f.recipients)} emails but only ${fmtK(f.delivered)} were delivered (${fmtP(f.delivered/f.recipients,0)}) and ${fmtP(f.opens_unique/(f.delivered||1),0)} of those were opened. It is probably mailing bad or old addresses; check its trigger and audience filters in Klaviyo.`, go:link('ads','email flows')});
  out.sort((a,b)=>b.v-a.v);
  const li=x=>`<li>${x.h}<span class="tag">${esc(x.tag)}</span>${x.go}</li>`;
  $('actions').innerHTML=out.length?out.slice(0,6).map(li).join(''):'<li class="note">Nothing needs a decision in this window.</li>';
  $('actMore').innerHTML=out.length>6?`<details><summary>${out.length-6} more</summary><ol class="actions" start="7">${out.slice(6).map(li).join('')}</ol></details>`:'';
  $('actSub').textContent=`${cap(WT())}, ordered by the money involved. Each one links to the numbers behind it.`;
  document.querySelectorAll('[data-go]').forEach(a=>a.onclick=()=>goTab(a.dataset.go));
}

// Sankey-style flow: where the click came from → where the sale happened, width = net revenue
function renderFlow(cur){
  const links=[];
  for(const [c,x] of Object.entries(cur.a)) if(x.net>0) links.push([c,'Online store',x.net]);
  const other={'TikTok Shop':'TikTok app','Walmart':'Walmart.com','eBay':'eBay.com','In-store (POS)':'Walks into the store','Draft orders':'Phone / invoice (draft)',
    'Subscriptions':'Subscription renewals','Shop app':'Shop app checkout','Facebook & Instagram shop':'Facebook / Instagram checkout'};
  for(const [ch,x] of Object.entries(cur.s)) if(ch!=='Online store' && x.net>0) links.push([other[ch]||ch+' (app)', ch, x.net]);
  const total=sum(links,l=>l[2]); if(!total){ $('flow').innerHTML='<p class="note">No revenue in this window.</p>'; return; }
  // keep the picture readable: fold tiny sources into "Other sources"
  const srcTot={}; links.forEach(l=>srcTot[l[0]]=(srcTot[l[0]]||0)+l[2]);
  const small=new Set(Object.keys(srcTot).filter(s=>srcTot[s]<total*.004));
  const L={}; links.forEach(([s,t,v])=>{ const k=(small.has(s)?'Other sources':s)+'→'+t; L[k]=(L[k]||0)+v; });
  const lk=Object.entries(L).map(([k,v])=>{ const [s,t]=k.split('→'); return [s,t,v]; });
  const lefts=[...new Set(lk.map(l=>l[0]))].sort((a,b)=>sum(lk.filter(l=>l[0]===b),l=>l[2])-sum(lk.filter(l=>l[0]===a),l=>l[2]));
  const rights=[...new Set(lk.map(l=>l[1]))].sort((a,b)=>sum(lk.filter(l=>l[1]===b),l=>l[2])-sum(lk.filter(l=>l[1]===a),l=>l[2]));
  const W=Math.max(640, $('flow').clientWidth||900), narrow=W<700, gap=6, nw=12, LABEL=18;
  const lx=narrow?150:200, rx=W-(narrow?160:230), scaleL=320/total, scaleR=320/total;
  // node heights follow revenue; spacing leaves room for each label so small sources never overlap
  const tot=(n,side)=>sum(lk.filter(l=>l[side]===n),l=>l[2]);
  const pos=(names,scale,side)=>{ let y=0; const p={}; names.forEach(n=>{ const h=Math.max(2,tot(n,side)*scale);
    p[n]={y,h,off:0}; y+=Math.max(h,LABEL)+gap; }); p._end=y; return p; };
  const PL=pos(lefts,scaleL,0), PR=pos(rights,scaleR,1), H=Math.max(PL._end,PR._end);
  const colL=n=>n==='Other sources'?css('--cg'):(CLICK_COL[n]?clickCol(n):salesCol(rights.find(r=>lk.some(l=>l[0]===n&&l[1]===r))||''));
  let paths='';
  lk.sort((a,b)=>lefts.indexOf(a[0])-lefts.indexOf(b[0])||rights.indexOf(a[1])-rights.indexOf(b[1]));
  for(const [s,t,v] of lk){ const a=PL[s], b=PR[t], h1=v*scaleL, h2=v*scaleR, y1=a.y+a.off+h1/2, y2=b.y+b.off+h2/2; a.off+=h1; b.off+=h2;
    const mx=(lx+nw+rx)/2;
    paths+=`<path d="M${lx+nw},${y1-h1/2} C${mx},${y1-h1/2} ${mx},${y2-h2/2} ${rx},${y2-h2/2} L${rx},${y2+h2/2} C${mx},${y2+h2/2} ${mx},${y1+h1/2} ${lx+nw},${y1+h1/2} Z" fill="${colL(s)}" fill-opacity=".35"><title>${esc(s)} → ${esc(t)}: ${fmtM(v,0)} (${fmtP(v/total)})</title></path>`; }
  const lab=(n,p,x,anchor,c,side)=>`<rect x="${x}" y="${p.y}" width="${nw}" height="${p.h}" fill="${c}" rx="2"/><text x="${anchor==='end'?x-6:x+nw+6}" y="${p.y+Math.max(p.h,12)/2+4}" text-anchor="${anchor}">${esc(n)} <tspan class="v">${fmtMK(tot(n,side))} · ${fmtP(tot(n,side)/total,0)}</tspan></text>`;
  $('flow').innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Net revenue flowing from where customers came from to the sales channel">${paths}${lefts.map(n=>lab(n,PL[n],lx,'end',colL(n),0)).join('')}${rights.map(n=>lab(n,PR[n],rx,'start',salesCol(n),1)).join('')}</svg>`;
  $('flowSub').textContent=`Left: where the customer came from (online-store orders by ${modelName().toLowerCase()} credit; marketplaces and the store are their own source). Right: where the sale happened. Width = net revenue, ${WT()}. Hover a band for its amount.`;
}

function insights(cur, prev){
  const out=[], a=cur.a, webTot=sum(Object.values(a),x=>x.net);
  const ranked=Object.entries(cur.s).sort((x,y)=>y[1].net-x[1].net);
  if(ranked.length) out.push(`<b>${esc(ranked[0][0])}</b> brought in the most net revenue (${fmtMK(ranked[0][1].net)}, ${fmtP(ranked[0][1].net/cur.net,0)}), then ${ranked.slice(1,3).map(([c,x])=>`${esc(c)} ${fmtMK(x.net)}`).join(' and ')}.`);
  if(prev&&prev.net){ const ch=(cur.net-prev.net)/prev.net;
    const drv=[...new Set([...Object.keys(cur.s),...Object.keys(prev.s)])].map(c=>[c,((cur.s[c]||{}).net||0)-((prev.s[c]||{}).net||0)]).sort((p,q)=>Math.abs(q[1])-Math.abs(p[1])).slice(0,3);
    out.push(`${ch<-.1?'⚠ ':''}Net revenue is ${ch>=0?'up':'down'} <b>${fmtP(Math.abs(ch),0)}</b> on ${PT()} (${fmtMK(prev.net)} → ${fmtMK(cur.net)}). Biggest movers: ${drv.map(([c,d])=>`${esc(c)} ${d>=0?'+':'−'}${fmtMK(Math.abs(d))}`).join(', ')}.`);
    const drops=Object.entries(cur.s).filter(([c,x])=>prev.s[c]&&prev.s[c].net>=prev.net*.03&&(x.net-prev.s[c].net)/prev.s[c].net<-.25)
      .map(([c,x])=>[c,(x.net-prev.s[c].net)/prev.s[c].net,prev.s[c].net-x.net]).sort((p,q)=>q[2]-p[2]);
    if(drops.length&&drops[0][0]!==drv[0][0]) out.push(`⚠ Biggest channel drops: ${drops.slice(0,3).map(([c,r,d])=>`<b>${esc(c)}</b> ${fmtP(r,0)} (−${fmtMK(d)})`).join(', ')} vs ${PT()}.`);
    const ups=Object.entries(cur.s).filter(([c,x])=>prev.s[c]&&prev.s[c].net>=prev.net*.01&&(x.net-prev.s[c].net)/prev.s[c].net>.25).map(([c,x])=>[c,(x.net-prev.s[c].net)/prev.s[c].net,x.net-prev.s[c].net]).sort((p,q)=>q[2]-p[2]);
    if(ups.length) out.push(`Growing channels: ${ups.slice(0,3).map(([c,r,d])=>`<b>${esc(c)}</b> +${fmtP(r,0)} (+${fmtMK(d)})`).join(', ')}.`); }
  const g=a['Google Ads']; if(cur.gcost) out.push(`Google Ads: ${fmtMK(cur.gcost)} spent, <b>${fmtN(g?g.orders:0)}</b> online orders and ${fmtMK(g?g.net:0)} net revenue credited by ${modelName().toLowerCase()} → return of <b>${fmtX((g?g.net:0)/cur.gcost)}</b> per dollar.`);
  const gc=Object.entries(cur.g).map(([id,x])=>({id,...x})).sort((x,y)=>y.cost-x.cost);
  if(gc.length){ const det=g?g.details:{}; const top=gc[0], cn=gname(top.id), got=(det[cn]||{}).net||0;
    out.push(`Biggest Google campaign: <b>${esc(cn)}</b> spent ${fmtMK(top.cost)} for ${fmtMK(got)} credited net revenue (${fmtX(got/top.cost)}).`);
    const best=gc.map(x=>({...x,r:((det[gname(x.id)]||{}).net||0)/x.cost})).filter(x=>x.cost>100).sort((p,q)=>q.r-p.r)[0];
    if(best&&best.r>2) out.push(`Best Google return: <b>${esc(gname(best.id))}</b> at ${fmtX(best.r)} on ${fmtMK(best.cost)}.`); }
  const webO=sum(Object.values(a),x=>x.orders);
  const acq=Object.entries(a).filter(([c,x])=>x.orders>=Math.max(10,webO*.03)&&!['Not recorded','Other tagged'].includes(c)).map(([c,x])=>[c,x.newc/x.orders,x]).sort((p,q)=>q[1]-p[1]);
  if(acq.length>1) out.push(`New customers: <b>${esc(acq[0][0])}</b> brings the highest share of first-time buyers (${fmtP(acq[0][1],0)} of its online orders); ${esc(acq[acq.length-1][0])} the lowest (${fmtP(acq[acq.length-1][1],0)}).`);
  if(HAS_SESS){ const t=traffic(win()), ts=sum(Object.values(t),x=>x.s), dr=(t['direct']||{s:0,done:0});
    if(ts) out.push(`The online store had <b>${fmtK(ts)}</b> sessions and ${fmtP(sum(Object.values(t),x=>x.done)/ts,2)} completed checkout.`+(dr.s/ts>.5?` ${fmtP(dr.s/ts,0)} of sessions are "direct" and convert at only ${fmtP(dr.done/dr.s,2)}, which usually means bots or crawlers inflate the count; judge conversion by search and email traffic instead.`:''));
  }
  if(!cur.meta) out.push(`Meta (Facebook/Instagram) ads had <b>no spend</b> in this window.`);
  const fc=attribute(win(),'first'), lc=attribute(win(),'last');
  const opener=Object.entries(fc).filter(([c])=>c!=='Direct'&&c!=='Not recorded').map(([c,x])=>[c,x.orders-((lc[c]||{}).orders||0)]).sort((x,y)=>y[1]-x[1])[0];
  if(opener&&opener[1]>0) out.push(`<b>${esc(opener[0])}</b> starts more journeys than it closes: first click on ${fmtN((fc[opener[0]]||{}).orders)} orders vs last click on ${fmtN((lc[opener[0]]||{}).orders||0)}, so last-click reports undervalue it.`);
  const mv=Object.keys(M.matured||{}).filter(k=>k!=='All channels'&&k!=='Walmart').map(k=>[k,MAT(k)]).filter(([,m])=>m&&m.n>=100).sort((x,y)=>y[1].net-x[1].net);
  if(mv.length>1) out.push(`Most valuable first-time buyers: <b>${esc(mv[0][0])}</b>, worth ${fmtM(mv[0][1].net,0)} in their first 180 days (${fmtP(mv[0][1].repeat,0)} come back), vs ${mv.slice(1,3).map(([k,m])=>`${esc(k)} ${fmtM(m.net,0)}`).join(', ')}.`);
  const ai=a['AI assistants']; if(ai&&ai.orders) out.push(`AI assistants (ChatGPT and similar) sent <b>${fmtN(ai.orders)}</b> online orders worth ${fmtMK(ai.net)}, at ${fmtM(ai.net/ai.orders,0)} per order.`);
  const mshop=cur.s['Facebook & Instagram shop']; if(mshop&&mshop.st) out.push(`Facebook & Instagram shop checkout: ${fmtN(mshop.st)} orders, ${fmtMK(mshop.net)}${cur.meta?'.':' with no Meta ad spend, so this is organic social selling.'}`);
  const dir=(a['Direct']||{net:0}).net+(a['Not recorded']||{net:0}).net; if(webTot) out.push(`${fmtP(dir/webTot,0)} of online-store revenue has no traceable click (direct, bookmarks, apps or untagged links).`);
  const apps=Object.entries(cur.s).filter(([c])=>c.startsWith('App ')); if(apps.length) out.push(`${fmtN(sum(apps,([,x])=>x.st))} orders (${fmtMK(sum(apps,([,x])=>x.net))}) came through unnamed sales-channel apps (${apps.map(([c])=>esc(c.slice(4))).join(', ')}): name them in Shopify so they can be tracked.`);
  const err=D.sources.filter(s=>s.status==='error'); if(err.length) out.push(`⚠ ${err.map(s=>esc(s.name)).join(', ')} could not be pulled this time, so ${err.length>1?'they are':'it is'} missing from the numbers (see Data &amp; definitions).`);
  const nc=D.sources.filter(s=>s.status==='not connected'); if(nc.length) out.push(`<span class="note">Not connected yet: ${nc.map(s=>esc(s.name)).join(', ')} (see Data &amp; definitions for what each would add).</span>`);
  const warn=out.filter(s=>s.startsWith('⚠')).map(s=>s.slice(1).trim()), good=out.filter(s=>!s.startsWith('⚠'));
  $('warnings').innerHTML=warn.length?warn.map(s=>`<li>${s}</li>`).join(''):'<li class="note">Nothing flagged in this window.</li>';
  $('insights').innerHTML=good.map(s=>`<li>${s}</li>`).join('');
}

// ---------- Website (Shopify's own session analytics) ----------
const SRC_COL = {search:'--c3', direct:'--cg', social:'--c6', email:'--c2', unknown:'--c9', paid:'--c1'};
const srcCol = s => css(SRC_COL[s] || '--c5');
let wbGroup='src';
function renderWeb(){
  if(!HAS_SESS){ $('wbKpis').innerHTML='<p class="note">Shopify session analytics were not available in this pull (see Data &amp; definitions).</p>'; return; }
  const w=win(), t=traffic(w), p=hasPrev()?traffic(win(1)):null, T=(x,k)=>x?sum(Object.values(x),y=>y[k]):null;
  const s=T(t,'s'), cart=T(t,'cart'), co=T(t,'co'), done=T(t,'done'), ps=T(p,'s'), pd=T(p,'done');
  const orders=(sales(w)['Online store']||{}).st||0, net=(sales(w)['Online store']||{}).net||0;
  $('wbKpis').innerHTML=[
    kpi('Sessions', fmtK(s), `${fmtN(s/nd())} a day`, delta(s,ps)),
    kpi('Conversion rate', fmtP(done/s,2), 'sessions that completed checkout', delta(done/s, p&&pd/ps)),
    kpi('Added to cart', fmtP(cart/s,1), `${fmtK(cart)} sessions`),
    kpi('Reached checkout', fmtP(co/s,1), `${fmtK(co)} sessions`),
    kpi('Checkout completion', fmtP(done/co,0), 'of sessions that reached checkout'),
    kpi('Revenue per session', fmtM(net/s,2), 'online-store net revenue / sessions', delta(net/s, p&&ps?((sales(win(1))['Online store']||{}).net||0)/ps:null)),
  ].join('');
  funnel('wbFunnel',[{v:fmtK(s),l:'sessions'},{v:fmtK(cart),l:`added to cart · ${fmtP(cart/s,1)} of sessions`},{v:fmtK(co),l:`reached checkout · ${fmtP(co/s,1)}`},
    {v:fmtN(done),l:`completed checkout · ${fmtP(done/s,2)}`},{v:fmtMK(net),l:`net revenue from ${fmtN(orders)} online orders`}]);
  $('wbFunnelSub').textContent=`${cap(WT())}, every online-store session; percentages are of all sessions. Some shoppers use buy-now and skip the cart, so more reach checkout than add to cart.`;
  const dr=t['direct']||{s:0,done:0};
  $('wbNote').innerHTML=dr.s/s>.5?`<div class="callout">${fmtP(dr.s/s,0)} of sessions are "direct" and only ${fmtP(dr.done/dr.s,2)} of them buy, against ${fmtP(((t.search||{}).done||0)/((t.search||{}).s||1),2)} for search. Most of that direct traffic is likely bots or crawlers, so the overall conversion rate understates real shoppers.</div>`:'';
  const b=buckets(w), srcs=Object.entries(t).sort((x,y)=>y[1].s-x[1].s).map(([k])=>k);
  const ds=srcs.map(k=>{ const m={}; for(const r of SESS) if(r.src===k&&inW(r.d,w)) m[b.of(r.d)]=(m[b.of(r.d)]||0)+r.s; return {label:k,data:b.keys.map(x=>m[x]||0),backgroundColor:srcCol(k),stack:'s'}; });
  const o=baseOpts('Sessions'); o.scales.x.stacked=true; o.scales.y.stacked=true; o.plugins.tooltip.callbacks={label:c=>`${c.dataset.label}: ${fmtN(c.raw)}`,footer:its=>'Total: '+fmtN(sum(its,i=>i.raw))};
  mkChart('wbTrend',{type:'bar',data:{labels:b.keys.map(b.label),datasets:ds},options:o});
  $('wbTrendSub').textContent=(nd()<=35?'Per day':'Per week')+', by Shopify\'s traffic source.';
  const drawT=()=>{ const g=traffic(w,wbGroup), pg=p?traffic(win(1),wbGroup):null;
    const rows=Object.entries(g).map(([k,x])=>({k, s:x.s, share:x.s/s, cart:x.s?x.cart/x.s:null, co:x.s?x.co/x.s:null, done:x.done, conv:x.s?x.done/x.s:null,
      chg:pg&&pg[k]&&pg[k].s?(x.s-pg[k].s)/pg[k].s:null}));
    $('wbTable')._st=null;
    table('wbTable',[{k:'k',label:wbGroup==='src'?'traffic source':'site',html:r=>(wbGroup==='src'?sw(srcCol(r.k)):'')+esc(r.k)},
      {k:'s',label:'sessions',num:1,fmt:v=>fmtN(v)},{k:'share',label:'share',num:1,fmt:v=>fmtP(v,1)},{k:'chg',label:`vs ${PS()}`,num:1,fmt:v=>v==null?'—':(v>0?'+':'')+fmtP(v,0)},
      {k:'cart',label:'added to cart',num:1,fmt:v=>fmtP(v,1)},{k:'co',label:'reached checkout',num:1,fmt:v=>fmtP(v,1)},
      {k:'done',label:'orders',num:1,fmt:v=>fmtN(v)},{k:'conv',label:'conversion',num:1,fmt:v=>fmtP(v,2)}],
      rows,{filter:wbGroup==='ref',file:`sessions_by_${wbGroup==='src'?'source':'site'}_${FK()}.csv`,sort:{k:'s',dir:-1},foot:{s,share:1,done,conv:done/s},max:380}); };
  $('wbGroup').onclick=e=>{ const x=e.target.closest('button'); if(!x) return; wbGroup=x.dataset.g; $('wbGroup').querySelectorAll('button').forEach(y=>y.setAttribute('aria-pressed',y===x)); drawT(); };
  drawT();
  const pages=PAGES(w).map(r=>({path:r[0],s:r[1],cart:r[1]?r[2]/r[1]:null,done:r[4],conv:r[1]?r[4]/r[1]:null}));
  $('wbPagesSub').textContent=`The ${pages.length} most-visited landing pages, ${WT()}. Red = many visits but almost no sales.`;
  table('wbPages',[{k:'path',label:'landing page',wrap:1,html:r=>`<a href="https://${esc(D.domain)}${esc(r.path)}" target="_blank" rel="noopener" style="color:inherit">${esc(r.path)}</a>`},
    {k:'s',label:'sessions',num:1,fmt:v=>fmtN(v)},{k:'cart',label:'added to cart',num:1,fmt:v=>fmtP(v,1)},{k:'done',label:'orders',num:1,fmt:v=>fmtN(v)},
    {k:'conv',label:'conversion',num:1,html:r=>`<span class="tag ${r.s>=500&&r.conv<.001?'bad':r.conv>=.02?'ok':''}">${fmtP(r.conv,2)}</span>`}],
    pages,{filter:true,file:`landing_pages_${FK()}.csv`,sort:{k:'s',dir:-1},max:420});
}

// ---------- Click attribution ----------
function renderAttr(){
  const w=win(), a=attribute(w), p=hasPrev()?attribute(win(1)):null, fc=attribute(w,'first'), lc=attribute(w,'last');
  const webAll=WEB.filter(o=>o.st&&inW(o.d,w)), tot=sum(Object.values(a),x=>x.net), totO=sum(Object.values(a),x=>x.orders);
  const traced=webAll.filter(o=>o.lc!=='Not recorded').length, multi=webAll.filter(o=>o.fc!==o.lc).length;
  const dd=webAll.map(o=>o.days).filter(x=>x!=null).sort((x,y)=>x-y);
  $('atKpis').innerHTML=[
    kpi('Online-store orders', fmtN(totO), 'standing orders in the window', p?delta(totO,sum(Object.values(p),x=>x.orders)):''),
    kpi('Online-store net revenue', fmtMK(tot), '', p?delta(tot,sum(Object.values(p),x=>x.net)):''),
    kpi('Paid-click share', fmtP(sum(Object.entries(a).filter(([c])=>PAID.has(c)),([,x])=>x.net)/tot,0), `of net revenue, ${modelName().toLowerCase()}`),
    kpi('Orders with a recorded visit', fmtP(traced/webAll.length,0), `${fmtN(traced)} of ${fmtN(webAll.length)}`),
    kpi('Multi-touch orders', fmtP(multi/webAll.length,0), 'first click ≠ last click'),
    kpi('Median days to buy', dd.length?fmtN(dd[Math.floor(dd.length/2)]):'—', 'from first visit to order'),
  ].join('');
  const chs=CLICK_ORDER.filter(c=>a[c]||fc[c]||lc[c]).sort((x,y)=>((a[y]||{}).net||0)-((a[x]||{}).net||0));
  const o=baseOpts(''); o.indexAxis='y'; o.plugins.legend.display=false; o.interaction={mode:'nearest',axis:'y',intersect:false};
  [o.scales.x,o.scales.y]=[{...o.scales.y,ticks:{color:css('--text2'),callback:v=>fmtMK(v)}},{...o.scales.x,grid:{display:false}}];
  o.plugins.tooltip.callbacks={label:c=>`${fmtM(c.raw,0)} · ${fmtN((a[chs[c.dataIndex]]||{}).orders,1)} orders`};
  mkChart('atBar',{type:'bar',data:{labels:chs,datasets:[{data:chs.map(c=>(a[c]||{}).net||0),backgroundColor:chs.map(clickCol),borderRadius:3}]},options:o});
  $('atBarSub').textContent=`${modelName()} credit, net revenue, ${WT()}.`;
  const b=buckets(w), ds=chs.map(c=>{ const m={}; for(const x of webAll) for(const [cc,,s] of credit(x)) if(cc===c) m[b.of(x.d)]=(m[b.of(x.d)]||0)+s;
    return {label:c,data:b.keys.map(k=>m[k]||0),backgroundColor:clickCol(c),stack:'o'}; });
  const o2=baseOpts('Orders'); o2.scales.x.stacked=true; o2.scales.y.stacked=true; o2.plugins.legend.labels.boxWidth=8;
  o2.plugins.tooltip.callbacks={label:c=>c.raw?`${c.dataset.label}: ${fmtN(c.raw,1)}`:null};
  mkChart('atTrend',{type:'bar',data:{labels:b.keys.map(b.label),datasets:ds},options:o2});
  $('atTrendSub').textContent=(nd()<=35?'Per day':'Per week')+`, ${modelName().toLowerCase()} credit.`;
  const rows=chs.map(c=>{ const x=a[c]||{orders:0,net:0,value:0,days:[]}, d=[...x.days].sort((p,q)=>p-q);
    return {c, orders:x.orders, share:tot?x.net/tot:0, net:x.net, profit:x.profit, aov:x.orders?x.net/x.orders:null, newp:x.orders?x.newc/x.orders:null, first:(fc[c]||{}).orders||0, last:(lc[c]||{}).orders||0,
      days:d.length?d[Math.floor(d.length/2)]:null, chg:p&&p[c]&&p[c].net?(x.net-p[c].net)/p[c].net:null}; });
  table('atTable',[
    {k:'c',label:'channel',html:r=>sw(clickCol(r.c))+esc(r.c)},
    {k:'orders',label:'orders',num:1,fmt:v=>fmtN(v,S.model==='split'?1:0)},{k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},
    ...(HAS_COST?[{k:'profit',label:'gross profit',num:1,fmt:v=>fmtM(v,0)}]:[]),
    {k:'share',label:'share',num:1,fmt:v=>fmtP(v,0)},{k:'aov',label:'avg order',num:1,fmt:v=>fmtM(v,0)},
    {k:'newp',label:'new customers',num:1,fmt:v=>fmtP(v,0)},
    {k:'chg',label:`vs ${PS()}`,num:1,fmt:v=>v==null?'—':(v>0?'+':'')+fmtP(v,0)},
    {k:'first',label:'first-click orders',num:1},{k:'last',label:'last-click orders',num:1},{k:'days',label:'median days to buy',num:1}],
    rows,{file:`click_channels_${FK()}_${S.model}.csv`,sort:{k:'net',dir:-1},foot:{orders:totO,net:tot,profit:sum(rows,r=>r.profit),share:1,aov:totO?tot/totO:null,first:sum(rows,r=>r.first),last:sum(rows,r=>r.last)}});
  const det=[]; for(const [c,x] of Object.entries(a)) for(const [d,y] of Object.entries(x.details)) det.push({c,d,orders:y.orders,net:y.net,share:tot?y.net/tot:0});
  table('atDetail',[{k:'c',label:'channel',html:r=>sw(clickCol(r.c))+esc(r.c)},{k:'d',label:'campaign / source',wrap:1},
    {k:'orders',label:'orders',num:1,fmt:v=>fmtN(v,S.model==='split'?1:0)},{k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},{k:'share',label:'share',num:1,fmt:v=>fmtP(v,1)}],
    det,{filter:true,file:`click_campaigns_${FK()}_${S.model}.csv`,sort:{k:'net',dir:-1}});
  const paths={}; for(const x of webAll){ const k=x.fc+' → '+x.lc; const p=paths[k]||(paths[k]={path:k,orders:0,net:0,same:x.fc===x.lc}); p.orders++; p.net+=x.net; }
  table('atPaths',[{k:'path',label:'first click → last click'},{k:'orders',label:'orders',num:1},{k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},
    {k:'same',label:'',html:r=>r.same?'<span class="tag">same channel</span>':'<span class="tag ok">assisted</span>'}],
    Object.values(paths),{filter:true,file:`journeys_${FK()}.csv`,sort:{k:'orders',dir:-1},max:360});
  $('pathSub').textContent=`${fmtN(multi)} of ${fmtN(webAll.length)} orders (${fmtP(multi/webAll.length,0)}) started on one channel and finished on another. Shopify records only the first and last visit.`;
}

// ---------- Ads & email ----------
function funnel(el, steps){ $(el).innerHTML=steps.map((s,i)=>(i?`<div class="arrow">${s.rate!=null?fmtP(s.rate,2):''}<br>→</div>`:'')+`<div class="step"><b>${s.v}</b><span>${esc(s.l)}</span></div>`).join(''); }
function renderAds(){
  const w=win(), g=gads(w), p=hasPrev()?gads(win(1)):null, a=attribute(w), det=(a['Google Ads']||{details:{}}).details;
  const T=k=>sum(Object.values(g),x=>x[k]), cost=T('cost'), clicks=T('clicks'), impr=T('impr'), go=(a['Google Ads']||{}).orders||0, gn=(a['Google Ads']||{}).net||0;
  funnel('gFunnel',[{v:fmtK(impr),l:'impressions'},{v:fmtK(clicks),l:`clicks · ${fmtM(cost/clicks)} each`,rate:clicks/impr},
    {v:fmtN(go,S.model==='split'?1:0),l:'credited online orders',rate:go/clicks},{v:fmtMK(gn),l:`net revenue · ${fmtX(gn/cost)} return on ${fmtMK(cost)}`}]);
  $('gFunnelSub').textContent=`${cap(WT())}. Percentages are conversion between steps. Orders are credited by ${({last:'last click',first:'first click',split:'split credit'})[S.model]}.`;
  const rows=Object.entries(g).map(([id,x])=>{ const c=M.gads_campaigns[id]||{}, d=det[c.name]||{orders:0,net:0,newc:0};
    return {name:c.name||id, type:(c.type||'').replace(/_/g,' ').toLowerCase(), status:(c.status||'').toLowerCase(), cost:x.cost, impr:x.impr, clicks:x.clicks,
      ctr:x.impr?x.clicks/x.impr:null, cpc:x.clicks?x.cost/x.clicks:null, conv:x.conv, orders:d.orders, newc:d.newc, net:d.net, profit:d.profit||0, poas:x.cost?(d.profit||0)/x.cost:null,
      cpo:d.orders?x.cost/d.orders:null, cac:d.newc?x.cost/d.newc:null, roas:x.cost?d.net/x.cost:null,
      budget:(ROOM(id)||[])[0]||null, is:(ROOM(id)||[])[1]??null, lostB:(ROOM(id)||[])[2]??null, lostR:(ROOM(id)||[])[3]??null}; });
  const untagged=Object.entries(det).filter(([n])=>!rows.some(r=>r.name===n)).map(([n,d])=>({name:n,type:'',status:'',cost:null,impr:null,clicks:null,ctr:null,cpc:null,conv:null,orders:d.orders,newc:d.newc,net:d.net,cpo:null,cac:null,roas:null}));
  const gNew=(a['Google Ads']||{}).newc||0;
  table('gTable',[{k:'name',label:'campaign',wrap:1,html:r=>`${esc(r.name)}<div class="note">${esc(r.type||'not in Google Ads data')}${r.status&&r.status!=='enabled'?` · <span class="tag">${esc(r.status)}</span>`:''}</div>`},
    {k:'cost',label:'spend',num:1,fmt:v=>fmtM(v,0)},{k:'clicks',label:'clicks',num:1,html:r=>r.clicks==null?'—':`${fmtN(r.clicks)}<div class="note">${fmtK(r.impr)} impr.</div>`},
    {k:'cpc',label:'CPC',num:1,html:r=>r.cpc==null?'—':`${fmtM(r.cpc,2)}<div class="note">${fmtP(r.ctr,1)} CTR</div>`},
    {k:'orders',label:'orders',num:1,fmt:v=>fmtN(v,S.model==='split'?1:0)},{k:'newc',label:'new cust.',num:1,fmt:v=>fmtN(v,S.model==='split'?1:0)},
    {k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},
    {k:'roas',label:'ROAS',num:1,html:r=>r.roas==null?'—':`<span class="tag ${r.roas>=3?'ok':r.roas<1?'bad':''}">${fmtX(r.roas)}</span>`+(HAS_COST&&r.poas!=null?`<div class="note">${fmtX(r.poas)} on profit</div>`:'')},
    {k:'cac',label:'cost / new cust.',num:1,fmt:v=>fmtM(v,0)},
    {k:'budget',label:'budget / day',num:1,fmt:v=>fmtM(v,0)},
    {k:'lostB',label:'impr. lost to budget',num:1,html:r=>r.lostB==null?'—':`<span class="tag ${r.lostB>=.05&&r.roas>=3?'ok':''}">${fmtP(r.lostB,0)}</span>`+(r.is!=null?`<div class="note">got ${fmtP(r.is,0)} · ${fmtP(r.lostR,0)} lost to rank</div>`:'')}],
    rows.concat(untagged),{filter:true,file:`google_ads_${FK()}_${S.model}.csv`,sort:{k:'cost',dir:-1},
      foot:{cost,clicks:fmtN(clicks),cpc:fmtM(cost/clicks,2),orders:go,newc:gNew,net:gn,cac:gNew?cost/gNew:null,roas:fmtX(gn/cost)+(HAS_COST?` · ${fmtX(((a['Google Ads']||{}).profit||0)/cost)} on profit`:'')}});
  const ds=days(w), byDay={}; for(const r of GADS) if(inW(r.d,w)) byDay[r.d]=(byDay[r.d]||0)+r.cost;
  const rev={}; for(const x of WEB) if(x.st&&inW(x.d,w)) for(const [c,,s] of credit(x)) if(c==='Google Ads') rev[x.d]=(rev[x.d]||0)+x.net*s;
  const o=baseOpts('Credited net revenue'); moneyAxis(o); o.plugins.tooltip.callbacks={label:c=>`${c.dataset.label}: ${fmtM(c.raw,0)}`};
  o.scales.y2={...o.scales.y,position:'right',grid:{display:false},ticks:{color:css('--text2'),callback:v=>fmtMK(v)},title:{display:true,text:'Spend',color:css('--text2')}};
  mkChart('gDaily',{data:{labels:ds.map(fmtDay),datasets:[{type:'line',label:'Credited net revenue',data:ds.map(d=>rev[d]||0),borderColor:css('--s3'),backgroundColor:css('--s3'),borderWidth:2,pointRadius:2,tension:.25},
    {type:'bar',label:'Spend (right axis)',yAxisID:'y2',data:ds.map(d=>byDay[d]||0),backgroundColor:alpha(css('--c1'),.55),borderRadius:2}]},options:o});
  const ms=metaSpend(w), mrows=META.filter(r=>inW(r.d,w));
  $('metaBox').innerHTML = ms ? `<div class="kpis small">${kpi('Spend',fmtM(ms,0))}${kpi('Impressions',fmtK(sum(mrows,r=>r.impr)))}${kpi('Link clicks',fmtN(sum(mrows,r=>r.clicks)))}${kpi('Credited orders',fmtN((a['Meta Ads']||{}).orders||0))}${kpi('Credited net revenue',fmtM((a['Meta Ads']||{}).net||0,0))}</div>`
    : `<p class="note" style="margin:0">No Meta ad spend in ${WT()} on ${esc([...new Set(META.map(r=>r.acct))].join(', ')||'the configured ad accounts')}. Organic Instagram and Facebook clicks are under <b>Social</b> in Click attribution.</p>`;
  // email
  const K=KL(), kc=K.campaigns||[], kf=K.flows||[], sentN=sum((M.campaigns_sent||[]).filter(([d])=>inW(d,w)),x=>x[1]);
  const all=kc.concat(kf), rec=sum(all,x=>x.recipients), op=sum(all,x=>x.opens_unique), cl=sum(all,x=>x.clicks_unique), kv=sum(all,x=>x.conversion_value), ko=sum(all,x=>x.conversions);
  const em=a['Email']||{orders:0,net:0};
  funnel('eFunnel',[{v:fmtK(rec),l:`emails sent · ${fmtN(sentN)} campaigns`},...(sum(all,x=>x.delivered||0)?[{v:fmtK(sum(all,x=>x.delivered||0)),l:'delivered',rate:sum(all,x=>x.delivered||0)/rec}]:[]),{v:fmtK(op),l:'unique opens',rate:op/(sum(all,x=>x.delivered||0)||rec)},{v:fmtN(cl),l:'unique clicks',rate:cl/op},
    {v:fmtN(em.orders,S.model==='split'?1:0),l:`online orders from email clicks (${modelName().toLowerCase()})`},{v:fmtMK(em.net),l:'net revenue from those clicks'}]);
  $('eFunnelSub').textContent=`${cap(WT())}, Klaviyo campaigns and flows. Click-credited orders come from Shopify visits tagged as email.`;
  const dl=sum(all,x=>x.delivered||0), bo=sum(all,x=>x.bounced||0), un=sum(all,x=>x.unsubscribes||0), spc=sum(all,x=>x.spam_complaints||0), hl=(v,warn)=>`<div class="d ${v>warn?'down':'up'}">${v>warn?'above':'within'} the usual ${fmtP(warn,warn<.002?2:1)} limit</div>`;
  $('eHealth').innerHTML=dl?[kpi('Delivered',fmtK(dl),`of ${fmtK(rec)} sent`),kpi('Not delivered',fmtP(1-dl/rec,1),`${fmtN(bo)} bounced; the rest skipped or failed`,hl(1-dl/rec,.02)),kpi('Unsubscribe rate',fmtP(un/dl,2),`${fmtN(un)} people`,hl(un/dl,.005)),
    kpi('Spam complaints',fmtP(spc/dl,3),`${fmtN(spc)} complaints`,hl(spc/dl,.001)),kpi('Revenue per email',fmtM(kv/dl,3),'Klaviyo-credited revenue / delivered')].join(''):'';
  $('eNote').innerHTML=`<div class="callout">Klaviyo's own report credits email with <b>${fmtN(ko)}</b> orders worth <b>${fmtMK(kv)}</b> (any order within its attribution window after an open or click, any channel). That is higher than click credit because it counts opens and orders that other channels also claim.</div>`;
  const ecols=[{k:'name',label:'name',wrap:1},{k:'recipients',label:'sent',num:1,fmt:v=>fmtN(v)},
    {k:'dr',label:'delivered',num:1,html:r=>r.dr==null?'—':`<span class="tag ${r.dr<.9?'bad':''}">${fmtP(r.dr,0)}</span>`},{k:'or',label:'open rate',num:1,fmt:v=>fmtP(v,0)},
    {k:'clicks_unique',label:'clicks',num:1,fmt:v=>fmtN(v)},{k:'cr',label:'click rate',num:1,fmt:v=>fmtP(v,2)},{k:'ur',label:'unsub. rate',num:1,html:r=>r.ur==null?'—':`<span class="tag ${r.ur>.005?'bad':''}">${fmtP(r.ur,2)}</span>`},{k:'conversions',label:'Klaviyo orders',num:1,fmt:v=>fmtN(v)},{k:'conversion_value',label:'Klaviyo revenue',num:1,fmt:v=>fmtM(v,0)}];
  const prep=x=>({...x, dr:x.recipients&&x.delivered!=null?x.delivered/x.recipients:null, or:x.delivered?x.opens_unique/x.delivered:x.recipients?x.opens_unique/x.recipients:null, cr:x.delivered?x.clicks_unique/x.delivered:x.recipients?x.clicks_unique/x.recipients:null, ur:x.delivered?(x.unsubscribes||0)/x.delivered:null});
  table('ecTable',[{k:'sent',label:'sent'},...ecols],kc.map(prep),{filter:true,file:`klaviyo_campaigns_${FK()}.csv`,sort:{k:'conversion_value',dir:-1},max:380});
  table('efTable',[...ecols.slice(0,1),{k:'status',label:'status',html:r=>`<span class="tag ${r.status==='live'?'ok':r.status==='draft'?'bad':''}">${esc(r.status||'')}</span>`},...ecols.slice(1)],kf.map(prep),{file:`klaviyo_flows_${FK()}.csv`,sort:{k:'conversion_value',dir:-1},max:380});
  $('ecSub').textContent=`Campaigns sent in ${WT()}, with their results through ${fmtDay(END)}.`; $('efSub').textContent=`Flow emails sent in ${WT()}.`;
}

// ---------- Sales channels ----------
function renderSales(){
  const w=win(), s=sales(w), p=hasPrev()?sales(win(1)):null, tot=sum(Object.values(s),x=>x.net);
  const rows=Object.entries(s).map(([c,x])=>({c,orders:x.st,value:x.value,net:x.net,disc:x.disc,refunded:x.refunded,profit:x.profit,margin:x.net?x.profit/x.net:null,cov:x.items?x.costed/x.items:null,aov:x.st?x.net/x.st:null,share:tot?x.net/tot:0,cancel:x.orders-x.st,
    chg:p&&p[c]&&p[c].net?(x.net-p[c].net)/p[c].net:null}));
  table('scTable',[{k:'c',label:'channel',html:r=>sw(salesCol(r.c))+esc(r.c)},{k:'orders',label:'orders',num:1,fmt:v=>fmtN(v)},{k:'value',label:'order value',num:1,fmt:v=>fmtM(v,0)},
    {k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},...(HAS_COST?[{k:'profit',label:'gross profit',num:1,fmt:v=>fmtM(v,0)},{k:'margin',label:'margin',num:1,fmt:v=>fmtP(v,0)},{k:'cov',label:'items costed',num:1,fmt:v=>fmtP(v,0)}]:[]),
    {k:'share',label:'share',num:1,fmt:v=>fmtP(v,0)},{k:'aov',label:'avg order (net)',num:1,fmt:v=>fmtM(v,0)},
    {k:'disc',label:'discounts',num:1,fmt:v=>fmtM(v,0)},{k:'refunded',label:'refunded $',num:1,fmt:v=>fmtM(v,0)},
    {k:'cancel',label:'cancelled / refunded',num:1,fmt:v=>fmtN(v)},{k:'chg',label:`vs ${PS()}`,num:1,fmt:v=>v==null?'—':(v>0?'+':'')+fmtP(v,0)}],
    rows,{file:`sales_by_channel_${FK()}.csv`,sort:{k:'net',dir:-1},foot:{orders:sum(rows,r=>r.orders),value:sum(rows,r=>r.value),net:tot,profit:sum(rows,r=>r.profit),margin:tot?sum(rows,r=>r.profit)/tot:null,share:1,disc:sum(rows,r=>r.disc),refunded:sum(rows,r=>r.refunded),cancel:sum(rows,r=>r.cancel)}});
  $('scSub').textContent=`${cap(WT())}. Order value includes tax and shipping before refunds; net revenue is standing orders after discounts and refunds, without tax and shipping.`;
  const apps=rows.filter(r=>r.c.startsWith('App '));
  $('scCallout').innerHTML=apps.length?`<div class="callout">${apps.map(r=>`<b>${esc(r.c)}</b>: ${fmtN(r.orders)} orders, ${fmtM(r.net,0)}`).join('; ')}. Shopify only gives these sales-channel apps a number. Find them in Shopify admin → Settings → Apps and sales channels.</div>`:'';
  const b=buckets(w), chs=rows.sort((x,y)=>y.net-x.net).map(r=>r.c);
  const ds=chs.map(c=>{ const m={}; for(const r of SALES) if(r.ch===c&&inW(r.d,w)) m[b.of(r.d)]=(m[b.of(r.d)]||0)+r.net; return {label:c,data:b.keys.map(k=>m[k]||0),backgroundColor:salesCol(c),stack:'n'}; });
  const o=baseOpts('Net revenue'); o.scales.x.stacked=true; o.scales.y.stacked=true; moneyAxis(o); o.plugins.tooltip.callbacks={label:c=>`${c.dataset.label}: ${fmtM(c.raw,0)}`,footer:its=>'Total: '+fmtM(sum(its,i=>i.raw),0)};
  mkChart('scTrend',{type:'bar',data:{labels:b.keys.map(b.label),datasets:ds},options:o});
  $('scTrendSub').textContent=nd()<=35?'Per day.':'Per week (Mon–Sun).';
  const months=[...new Set(M.sales_monthly.map(r=>r[0]))].sort(), mch=[...new Set(M.sales_monthly.map(r=>r[1]))];
  const mt={}; M.sales_monthly.forEach(r=>{ (mt[r[1]]||(mt[r[1]]={}))[r[0]]=r[5]; });
  mch.sort((x,y)=>sum(months,m=>(mt[y]||{})[m])-sum(months,m=>(mt[x]||{})[m]));
  const o2=baseOpts('Net revenue'); o2.scales.x.stacked=true; o2.scales.y.stacked=true; moneyAxis(o2); o2.plugins.tooltip.callbacks={label:c=>`${c.dataset.label}: ${fmtM(c.raw,0)}`,footer:its=>'Total: '+fmtM(sum(its,i=>i.raw),0)};
  const lastDay=new Date(END_D.getFullYear(),END_D.getMonth()+1,0).getDate(), part=END_D.getDate()<lastDay?END.slice(0,7):null;
  mkChart('scMonthly',{type:'bar',data:{labels:months.map(m=>m===part?m+' (partial)':m),datasets:mch.map(c=>({label:c,data:months.map(m=>(mt[c]||{})[m]||0),
    backgroundColor:months.map(m=>m===part?alpha(salesCol(c),.35):salesCol(c)),stack:'m'}))},options:o2});
  table('scMonthlyTable',[{k:'m',label:'month'},...mch.map(c=>({k:c,label:c,num:1,fmt:v=>fmtM(v,0)})),{k:'t',label:'total',num:1,fmt:v=>fmtM(v,0)}],
    months.map(m=>{ const r={m}; mch.forEach(c=>r[c]=(mt[c]||{})[m]||0); r.t=sum(mch,c=>r[c]); return r; }),{file:'net_revenue_by_channel_monthly.csv',sort:{k:'m',dir:1}});
}

// ---------- Products & promos ----------
function renderProd(){
  if(!HAS_PROD){ $('pdKpis').innerHTML='<p class="note">Product and discount-code detail is not in this export (it was pulled with an older version). Press Start to pull again.</p>'; return; }
  const w=win(), cur=PROD(0), prev=PROD(1), curKeys=new Set(cur.map(p=>p.key)), pm=Object.fromEntries(prev.map(p=>[p.key,p])), hasP=hasPrev(), s=sales(w), net=sum(Object.values(s),x=>x.net);
  const lineTot=sum(PTYPES(0),t=>t.net), top10=sum(cur.slice(0,10),p=>p.net), n=cur.length;
  const C=CODES(0), nocode=C.find(c=>!c.code)||{orders:0,net:0}, coded=C.filter(c=>c.code), codedO=sum(coded,c=>c.orders), disc=sum(Object.values(s),x=>x.disc||0);
  const pages=Object.fromEntries(PAGES(w).filter(r=>r[0].startsWith('/products/')).map(r=>[r[0].split('/')[2],r]));
  $('pdKpis').innerHTML=[
    kpi('Products sold', fmtN(n), `${fmtN(sum(PTYPES(0),t=>t.units))} items on standing orders`),
    kpi('Top 10 products', fmtP(top10/lineTot,0), `of product revenue (${fmtMK(top10)})`),
    kpi('Discounts given', fmtMK(disc), `${fmtP(disc/(net+disc),1)} of revenue before discounts`, hasPrev()?delta(disc,sum(Object.values(sales(win(1))),x=>x.disc||0),0):''),
    kpi('Orders with a code', fmtP(codedO/(codedO+nocode.orders),0), `${fmtN(codedO)} orders · ${coded.length} codes used`),
    kpi('Avg order with a code', fmtM(codedO?sum(coded,c=>c.net)/codedO:null,0), `without a code ${fmtM(nocode.orders?nocode.net/nocode.orders:null,0)}`),
  ].join('');
  const rows=cur.slice(0,500).map(p=>{ const q=pm[p.key], pg=p.handle&&pages[p.handle];
    return {...p, profit:p.net-p.cogs, margin:p.net&&p.cogs>p.net*.02?(p.net-p.cogs)/p.net:null, share:lineTot?p.net/lineTot:0, main:mainCh(p.ch),
      chg:hasP?(q&&q.net?(p.net-q.net)/q.net:null):null, isNew:hasP&&!q, visits:pg?pg[1]:null, conv:pg&&pg[1]?pg[4]/pg[1]:null}; });
  table('pdTable',[{k:'t',label:'product',wrap:1,html:r=>(r.handle?`<a href="https://${esc(D.domain)}/products/${esc(r.handle)}" target="_blank" rel="noopener" style="color:inherit">${esc(r.t)}</a>`:esc(r.t))+`<div class="note">${esc(r.type)}${r.handle?'':' · no longer in the catalog'}</div>`},
    {k:'units',label:'items',num:1,fmt:v=>fmtN(v)},{k:'orders',label:'orders',num:1,fmt:v=>fmtN(v)},{k:'net',label:'revenue',num:1,fmt:v=>fmtM(v,0)},
    ...(HAS_COST?[{k:'profit',label:'gross profit',num:1,fmt:v=>fmtM(v,0)},{k:'margin',label:'margin',num:1,fmt:v=>v==null?'no cost':fmtP(v,0)}]:[]),
    {k:'share',label:'share',num:1,fmt:v=>fmtP(v,1)},{k:'chg',label:`vs ${PS()}`,num:1,html:r=>r.isNew?'<span class="tag ok">new</span>':r.chg==null?'—':(r.chg>0?'+':'')+fmtP(r.chg,0)},
    {k:'main',label:'sells mostly on'},{k:'visits',label:'landing visits',num:1,html:r=>r.visits==null?'—':`${fmtN(r.visits)}<div class="note">${fmtP(r.conv,2)} buy</div>`}],
    rows,{filter:true,file:`products_${FK()}.csv`,sort:{k:'net',dir:-1},max:460});
  $('pdSub').textContent=`The ${fmtN(Math.min(cur.length,500))} best-selling products of ${fmtN(n)} sold in ${WT()}, all sales channels. Revenue is items still on standing orders at the price after discounts. Landing visits show for products in the top landing pages.`;
  // product types
  const T=PTYPES(0).sort((a,b)=>b.net-a.net), tp=Object.fromEntries(PTYPES(1).map(t=>[t.t,t])), top=T.slice(0,12);
  const o=baseOpts(''); o.indexAxis='y'; o.plugins.legend.display=false; o.interaction={mode:'nearest',axis:'y',intersect:false};
  [o.scales.x,o.scales.y]=[{...o.scales.y,ticks:{color:css('--text2'),callback:v=>fmtMK(v)}},{...o.scales.x,grid:{display:false},ticks:{color:css('--text2'),callback:function(v){ const l=this.getLabelForValue(v); return l.length>22?l.slice(0,21)+'…':l; }}}];
  o.plugins.tooltip.callbacks={label:c=>`${fmtM(c.raw,0)} · ${fmtN(top[c.dataIndex].units)} items`+(hasP&&tp[top[c.dataIndex].t]?` · ${fmtP(top[c.dataIndex].net/tp[top[c.dataIndex].t].net-1,0)} vs prev`:'')};
  mkChart('ptBar',{type:'bar',data:{labels:top.map(t=>t.t),datasets:[{data:top.map(t=>t.net),backgroundColor:css('--c1'),borderRadius:3}]},options:o});
  $('ptSub').textContent=`Revenue by Shopify product type, ${WT()}. Top ${top.length} of ${T.length}.`;
  table('ptTable',[{k:'t',label:'type',wrap:1},{k:'units',label:'items',num:1,fmt:v=>fmtN(v)},{k:'net',label:'revenue',num:1,fmt:v=>fmtM(v,0)},
    ...(HAS_COST?[{k:'margin',label:'margin',num:1,fmt:v=>v==null?'no cost':fmtP(v,0)}]:[]),{k:'share',label:'share',num:1,fmt:v=>fmtP(v,1)},{k:'chg',label:`vs ${PS()}`,num:1,fmt:v=>v==null?'—':(v>0?'+':'')+fmtP(v,0)}],
    T.map(t=>({...t,margin:t.net&&t.cogs>t.net*.02?(t.net-t.cogs)/t.net:null,share:lineTot?t.net/lineTot:0,chg:hasP&&tp[t.t]&&tp[t.t].net?(t.net-tp[t.t].net)/tp[t.t].net:null})),{file:`product_types_${FK()}.csv`,sort:{k:'net',dir:-1}});
  // rising and falling (types are steadier than single products, which come and go)
  if(!hasP){ $('movers').innerHTML='<p class="note">Pick 7 or 28 days to compare with the period before (90 days is all the daily data loaded).</p>'; $('mvSub').textContent=''; }
  else { const ch=[...new Set([...T.map(t=>t.t),...Object.keys(tp)])].map(k=>{ const a=(T.find(t=>t.t===k)||{net:0}).net, b=(tp[k]||{net:0}).net; return {k,a,b,d:a-b}; }).filter(x=>Math.max(x.a,x.b)>=lineTot*.005);
    const pc=rows.filter(r=>r.net>=lineTot*.003).map(r=>({k:r.t,a:r.net,b:(pm[r.key]||{net:0}).net})).concat(prev.filter(q=>!curKeys.has(q.key)&&q.net>=lineTot*.003).map(q=>({k:q.t,a:0,b:q.net}))).map(x=>({...x,d:x.a-x.b}));
    const li=x=>`<li><b>${esc(x.k)}</b> ${x.d>=0?'+':'−'}${fmtMK(Math.abs(x.d))} <span class="note">(${fmtMK(x.b)} → ${fmtMK(x.a)})</span></li>`, top5=(a,dir)=>a.filter(x=>dir>0?x.d>0:x.d<0).sort((p,q)=>dir*(q.d-p.d)).slice(0,5);
    $('movers').innerHTML=`<div class="movers"><div><h3>Product types up</h3><ol>${top5(ch,1).map(li).join('')||'<li class="note">none</li>'}</ol></div><div><h3>Product types down</h3><ol>${top5(ch,-1).map(li).join('')||'<li class="note">none</li>'}</ol></div>
      <div><h3>Products up</h3><ol>${top5(pc,1).map(li).join('')||'<li class="note">none</li>'}</ol></div><div><h3>Products down</h3><ol>${top5(pc,-1).map(li).join('')||'<li class="note">none</li>'}</ol></div></div>`;
    $('mvSub').textContent=`Revenue change vs ${PT()}. A product that sold out or ended shows as down.`; }
  // discount codes
  const crow=coded.map(c=>({...c, per:c.orders?c.disc/c.orders:null, rate:c.net+c.disc?c.disc/(c.net+c.disc):null, newp:c.orders?c.newc/c.orders:null, aov:c.orders?c.net/c.orders:null, main:mainCh(c.ch)}));
  table('dcTable',[{k:'code',label:'code'},{k:'orders',label:'orders',num:1,fmt:v=>fmtN(v)},{k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},{k:'disc',label:'discount given',num:1,fmt:v=>fmtM(v,0)},
    {k:'rate',label:'discount rate',num:1,fmt:v=>fmtP(v,0)},{k:'aov',label:'avg order',num:1,fmt:v=>fmtM(v,0)},
    {k:'newp',label:'first-time buyers',num:1,html:r=>`<span class="tag ${r.newp>=.5?'ok':r.newp<.15&&r.orders>=5?'bad':''}">${fmtP(r.newp,0)}</span>`},{k:'main',label:'used mostly on'}],
    crow,{filter:true,file:`discount_codes_${FK()}.csv`,sort:{k:'disc',dir:-1},max:420,foot:{orders:codedO,net:sum(crow,r=>r.net),disc:sum(crow,r=>r.disc)}});
  $('dcSub').textContent=`Standing orders that used a code, ${WT()}. Green = mostly first-time buyers (acquisition); red = mostly returning customers who may have bought anyway.`;
  const auto=disc-sum(coded,c=>c.disc);
  $('dcNote').innerHTML=nocode.orders?`<div class="callout">Orders without a code averaged ${fmtM(nocode.net/nocode.orders,0)} net vs ${fmtM(codedO?sum(coded,c=>c.net)/codedO:null,0)} with one.${auto>1?` ${fmtMK(auto)} of discounts came without a code (automatic discounts, sale prices set as discounts, or marketplace promotions).`:''}</div>`:'';
}

// ---------- Customers: value of new customers, places, order times ----------
let acqGroup='sales';
function acquisition(group, w){
  const out={}, add=(k,x,s)=>{ const a=out[k]||(out[k]={k,n:0,first:0,val:0,profit:0,rep:0}); a.n+=s; a.first+=x.first*s; a.val+=x.val*s; a.profit+=x.profit*s; a.rep+=(x.n>1)*s; };
  for(const x of NEWC){ if(!inW(x.d,w)) continue;
    if(group==='sales') add(x.sc,x,1); else if(x.fc){ for(const [c,,s] of credit({fc:x.fc,lc:x.lc,fd:'',ld:''})) add(c,x,s); } }
  const spend={'Google Ads':sum(Object.values(gads(w)),x=>x.cost),'Meta Ads':metaSpend(w)};
  return Object.values(out).map(a=>({k:a.k,n:a.n,first:a.first/a.n,val:a.val/a.n,profit:a.profit/a.n,rep:a.rep/a.n,spend:spend[a.k]||null,cac:spend[a.k]?spend[a.k]/a.n:null,
    mat:group==='sales'?MAT(a.k):null}));
}
function renderCust(){
  const w=win(), all=NEWC.filter(x=>inW(x.d,w)), m=MAT('Online store')||MAT('All channels'), t=totals(w), cac=t.paidNew?t.spend/t.paidNew:null;
  if(!NEWC.length&&!m){ $('cuKpis').innerHTML='<p class="note">Customer value is not in this export (pulled with an older version). Press Start to pull again.</p>'; }
  else $('cuKpis').innerHTML=[
    kpi('New customers', fmtN(all.length), `first-ever orders, all channels, ${WT()}`),
    kpi('Avg first order', fmtM(all.length?sum(all,x=>x.first)/all.length:null,0), 'net revenue'),
    kpi('Already bought again', fmtP(all.length?all.filter(x=>x.n>1).length/all.length:null,0), `of these new customers, through ${fmtDay(END)}`),
    m?kpi('180-day value, online-store new customer', fmtM(m.net,0), `${fmtN(m.orders,1)} orders each · ${fmtP(m.repeat,0)} came back`):'',
    m&&HAS_COST?kpi('180-day gross profit, same customers', fmtM(m.profit,0), 'net revenue less product cost'):'',
    kpi('Ad cost per new customer', fmtM(cac,0), `ad spend / first-time buyers from ad clicks`),
    m&&cac?kpi('Payback', fmtX((HAS_COST?m.profit:m.net)/cac), `180-day ${HAS_COST?'gross profit':'revenue'} / ad cost per new customer; above 1× the ads pay back within 6 months`):'',
  ].join('');
  const rows=acquisition(acqGroup,w);
  $('acqTable')._st=null;
  table('acqTable',[{k:'k',label:acqGroup==='sales'?'first sales channel':'first-order click',html:r=>sw(acqGroup==='sales'?salesCol(r.k):clickCol(r.k))+esc(r.k)},
    {k:'n',label:'new customers',num:1,fmt:v=>fmtN(v,acqGroup==='click'&&S.model==='split'?1:0)},{k:'first',label:'first order',num:1,fmt:v=>fmtM(v,0)},
    {k:'val',label:'spent so far',num:1,fmt:v=>fmtM(v,0)},...(HAS_COST?[{k:'profit',label:'gross profit so far',num:1,fmt:v=>fmtM(v,0)}]:[]),
    {k:'rep',label:'bought again',num:1,fmt:v=>fmtP(v,0)},
    ...(acqGroup==='sales'?[{k:'mat',label:'180-day value',num:1,html:r=>r.mat?`${fmtM(r.mat.net,0)}<div class="note">${fmtP(r.mat.repeat,0)} came back · ${fmtN(r.mat.n)} customers</div>`:'—'}]
      :[{k:'cac',label:'ad cost per new customer',num:1,html:r=>r.cac==null?'—':`${fmtM(r.cac,0)}<div class="note">${fmtX(r.val/r.cac)} back so far</div>`}])],
    rows,{file:`new_customer_value_${acqGroup}_${FK()}.csv`,sort:{k:'n',dir:-1}});
  if(acqGroup==='sales'&&rows.some(r=>r.k==='Walmart')) $('acqTable').insertAdjacentHTML('beforeend','<p class="note">Walmart passes a new customer record with almost every order, so Walmart buyers look like one-time customers even when they come back.</p>');
  $('acqSub').textContent=acqGroup==='sales'?`First-time buyers in ${WT()} by the channel of their first order, and what they have spent since (all channels). 180-day value: customers who started at least 180 days ago, over their first 180 days.`
    :`Online-store first-time buyers in ${WT()} by the click on their first order (${modelName().toLowerCase()}), what they have spent since, and the ad cost per new customer for paid channels.`;
  $('acqGroup').onclick=e=>{ const b=e.target.closest('button'); if(!b) return; acqGroup=b.dataset.g; $('acqGroup').querySelectorAll('button').forEach(y=>y.setAttribute('aria-pressed',y===b)); renderCust(); };
  // places
  const G=GEO(0), gp=Object.fromEntries(GEO(1).map(g=>[g.g,g])), gt=sum(G,g=>g.net), go=sum(G,g=>g.orders);
  table('geoTable',[{k:'p',label:'place'},{k:'orders',label:'orders',num:1,fmt:v=>fmtN(v)},{k:'net',label:'net revenue',num:1,fmt:v=>fmtM(v,0)},{k:'share',label:'share',num:1,fmt:v=>fmtP(v,1)},
    {k:'newp',label:'new customers',num:1,fmt:v=>fmtP(v,0)},{k:'chg',label:`vs ${PS()}`,num:1,fmt:v=>v==null?'—':(v>0?'+':'')+fmtP(v,0)}],
    G.map(g=>({...g,p:place(g.g),share:gt?g.net/gt:0,newp:g.orders?g.newc/g.orders:null,chg:gp[g.g]&&gp[g.g].net?(g.net-gp[g.g].net)/gp[g.g].net:null})),
    {filter:true,file:`places_${FK()}.csv`,sort:{k:'net',dir:-1},max:420});
  const g3=[...G].sort((a,b)=>b.net-a.net).slice(0,3);
  $('geoSub').textContent=G.length?`Shipping address of standing orders, ${WT()} (POS excluded). Top 3: ${g3.map(g=>`${place(g.g)} ${fmtP(g.net/gt,0)}`).join(', ')}. Use it for geo bids and shipping offers.`:'';
  // order times
  const H=HOURS(w);
  if(!H){ $('heat').innerHTML='<p class="note">Not in this export.</p>'; return; }
  const max=Math.max(1,...H.flat().map(c=>c[0])), dn=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], hr=h=>h===0?'12a':h<12?h+'a':h===12?'12p':(h-12)+'p', acc=css('--accent');
  $('heat').innerHTML=`<table><thead><tr><th></th>${[...Array(24).keys()].map(h=>`<th>${h%3===0?hr(h):''}</th>`).join('')}</tr></thead><tbody>${H.map((row,d)=>`<tr><th>${dn[d]}</th>${row.map((c,h)=>
    `<td style="background:${alpha(acc,.06+.94*c[0]/max)}" title="${dn[d]} ${hr(h)}: ${fmtN(c[0])} orders, ${fmtM(c[1],0)}"></td>`).join('')}</tr>`).join('')}</tbody></table>`;
  const byD=H.map(r=>sum(r,c=>c[0])), byH=[...Array(24).keys()].map(h=>sum(H,r=>r[h][0])), tot=sum(byD);
  const bestD=byD.indexOf(Math.max(...byD)), worstD=byD.indexOf(Math.min(...byD)), peak=[...byH.keys()].sort((a,b)=>byH[b]-byH[a]).slice(0,3).sort((a,b)=>a-b);
  $('heatSub').textContent=`Orders by weekday and hour (${D.timezone||'store time'}), ${WT()}, POS excluded. Darker = more orders; hover a square for numbers.`;
  $('heatNote').innerHTML=tot?`<div class="callout">Busiest day: <b>${dn[bestD]}</b> (${fmtP(byD[bestD]/tot,0)} of orders); quietest: ${dn[worstD]} (${fmtP(byD[worstD]/tot,0)}). Peak hours: <b>${peak.map(hr).join(', ')}</b>. Schedule email sends and LIVE shows just before the peak, and lower ad bids in the dead hours.</div>`:'';
}

// ---------- Retention (last 12 months, from the retention computation) ----------
const RH = Object.fromEntries(R.headline.map(h=>[h.k,h.v]));
const RT = k => R.tables.find(t=>t.file.includes(k));
const tcols = t => t.columns.map((c,i)=>({k:'c'+i,label:c,num:t.rows.some(r=>/^[\s$-]*[\d,.]+%?\s*$/.test(r[i]||''))&&i>0,wrap:t.rows.some(r=>String(r[i]||'').length>40)}));
const trows = t => t.rows.map(r=>Object.fromEntries(r.map((v,i)=>[`c${i}`, /^[\d.]+$/.test(v)&&i>0 ? Number(v) : v])));
const tcolsF = t => tcols(t).map(c=>c.num?{...c,fmt:v=>typeof v==='number'?fmtN(v,Number.isInteger(v)?0:1):(v??'—')}:c);
const partial = (()=>{ const d=new Date((RH.customer_data_to||END)+'T00:00:00'); return d.getDate() < new Date(d.getFullYear(),d.getMonth()+1,0).getDate() ? (RH.customer_data_to||END).slice(0,7) : null; })();
const alpha=(c,a)=>{ const h=c.replace('#',''); const n=parseInt(h,16); return `rgba(${n>>16&255},${n>>8&255},${n&255},${a})`; };
const cohortDone = m => { const e=new Date(m+'-01T00:00:00'); e.setMonth(e.getMonth()+1); e.setDate(e.getDate()+89); return e<=END_D; };
const col = (t,c) => t.rows.map(r=>r[t.columns.indexOf(c)]);
function renderRet(){
  renderCust();
  const fl=RT('klaviyo_flows'), fs=col(fl,'status');
  $('rtKpis').innerHTML=[
    kpi('Customers', fmtN(num(RH.customers_known)), 'bought online in 12 months'),
    kpi('Repeat-order rate', fmtP(num(RH.repeat_order_rate)), 'online orders from returning customers'),
    kpi('Median days between purchases', fmtN(num(RH.median_days_between_purchases)), 'for repeat buyers'),
    kpi('New-customer orders', fmtN(num(RH.new_customer_orders))), kpi('Returning-customer orders', fmtN(num(RH.returning_customer_orders))),
    kpi('Refund rate, last full month', fmtP(num(RH.refund_rate_last_month)), 'orders with a refund'),
    kpi('Win-back flows live', `${fmtN(num(RH.winback_flows_live))} of ${fmtN(num(RH.winback_flows))}`, `${fs.filter(s=>s==='live').length} of ${fs.length} Klaviyo flows live`),
  ].join('');
  const c=RT('customers_by_month'), m=col(c,'month'), per=(ms,cv)=>ms.map(x=>x===partial?alpha(cv,.35):cv);
  const o=baseOpts('Orders'); o.scales.x.stacked=true; o.scales.y.stacked=true;
  o.scales.y2={...o.scales.y,stacked:false,position:'right',max:100,grid:{display:false},ticks:{color:css('--text2'),callback:v=>v+'%'},title:{display:true,text:'Repeat-order rate',color:css('--text2')}};
  mkChart('rtCust',{data:{labels:m.map(x=>x===partial?x+' (partial)':x),datasets:[
    {type:'line',label:'Repeat-order rate',data:col(c,'repeat-order rate').map(num),yAxisID:'y2',borderColor:css('--s2'),backgroundColor:css('--s2'),borderWidth:2,pointRadius:3,tension:.25,segment:{borderDash:x=>m[x.p1DataIndex]===partial?[5,4]:undefined}},
    {type:'bar',label:'New customers',data:col(c,'new').map(num),backgroundColor:per(m,css('--s1')),stack:'o'},
    {type:'bar',label:'Returning customers',data:col(c,'returning').map(num),backgroundColor:per(m,css('--s3')),stack:'o'}]},options:o});
  table('rtCustT',tcolsF(c),trows(c),{file:'customers_by_month.csv'});
  const co=RT('cohorts'), cm=col(co,'first purchase month'), n=col(co,'customers').map(num), bk=col(co,'back within 90 days').map(num);
  const o2=baseOpts('Back within 90 days'); o2.plugins.legend.display=false; o2.scales.y.ticks.callback=v=>v+'%';
  o2.plugins.tooltip.callbacks={label:x=>`${x.raw}% back (${fmtN(bk[x.dataIndex])} of ${fmtN(n[x.dataIndex])})`+(cohortDone(cm[x.dataIndex])?'':' · still open')};
  mkChart('rtCoh',{type:'bar',data:{labels:cm.map(x=>cohortDone(x)?x:x+' (open)'),datasets:[{data:col(co,'share').map(num),backgroundColor:cm.map(x=>cohortDone(x)?css('--s1'):alpha(css('--s1'),.35)),borderRadius:3}]},options:o2});
  table('rtCohT',tcolsF(co),trows(co),{file:'first_purchase_cohorts.csv'});
  const dm=RT('since_last_purchase'), dv=col(dm,'customers').map(num), dt=sum(dv);
  const o3=baseOpts(''); o3.indexAxis='y'; o3.plugins.legend.display=false; o3.interaction={mode:'nearest',axis:'y',intersect:false};
  [o3.scales.x,o3.scales.y]=[{...o3.scales.y,title:{display:true,text:'Customers',color:css('--text2')}},{...o3.scales.x,grid:{display:false}}];
  o3.plugins.tooltip.callbacks={label:x=>`${fmtN(x.raw)} customers (${Math.round(x.raw/dt*100)}%)`};
  mkChart('rtDorm',{type:'bar',data:{labels:col(dm,'since last purchase'),datasets:[{data:dv,backgroundColor:css('--s4'),borderRadius:3}]},options:o3});
  table('rtDormT',tcolsF(dm),trows(dm),{file:'customers_by_time_since_last_purchase.csv'});
  const rf=RT('refunds_by_refund_date'), rm=col(rf,'month'), o4=baseOpts('Refunded $'); moneyAxis(o4);
  o4.scales.y2={...o4.scales.y,position:'right',grid:{display:false},ticks:{color:css('--text2')},title:{display:true,text:'Refunds',color:css('--text2')}};
  mkChart('rtRef',{data:{labels:rm.map(x=>x===partial?x+' (partial)':x),datasets:[
    {type:'line',label:'Refunds',data:col(rf,'refunds').map(num),yAxisID:'y2',borderColor:css('--s2'),backgroundColor:css('--s2'),borderWidth:2,pointRadius:3,tension:.25,segment:{borderDash:x=>rm[x.p1DataIndex]===partial?[5,4]:undefined}},
    {type:'bar',label:'Amount refunded',data:col(rf,'amount').map(num),backgroundColor:per(rm,css('--dead')),borderRadius:3}]},options:o4});
  table('rtRefT',tcolsF(rf),trows(rf),{file:'refunds_by_refund_date.csv'});
  const ot=RT('orders_and_refunds'); table('rtOrdT',tcolsF(ot),trows(ot),{file:'orders_and_refunds_by_month.csv',max:330});
  const st=[...new Set(fs)], off=new Set(), fr=trows(fl);
  $('flowChips').innerHTML=st.map(s=>`<button class="chip" aria-pressed="true" data-s="${esc(s)}">${esc(s)} · ${fs.filter(x=>x===s).length}</button>`).join('');
  const fcols=tcols(fl); fcols[1].html=r=>`<span class="tag ${r.c1==='live'?'ok':r.c1==='draft'?'bad':''}">${esc(r.c1)}</span>`;
  const drawF=()=>table('rtFlowT',fcols,fr.filter(r=>!off.has(r.c1)),{filter:true,file:'klaviyo_flows.csv'});
  $('flowChips').onclick=e=>{ const b=e.target.closest('.chip'); if(!b) return; const on=b.getAttribute('aria-pressed')==='true'; b.setAttribute('aria-pressed',!on); on?off.add(b.dataset.s):off.delete(b.dataset.s); $('rtFlowT')._st=null; drawF(); };
  drawF();
  const pd=RT('not_computed'); table('rtPendT',tcolsF(pd),trows(pd),{file:'not_computed_yet.csv'});
}

// ---------- Data & definitions ----------
function renderData(){
  table('srcTable',[{k:'name',label:'source'},{k:'status',label:'status',html:r=>`<span class="tag ${r.status==='ok'?'ok':r.status==='error'?'bad':''}">${esc(r.status)}</span>`},{k:'detail',label:'detail',wrap:1}],D.sources,{file:'data_sources.csv'});
  $('srcSub').textContent=`Pulled ${localTime(D.pulled_at)} (read only). Daily marketing data covers ${M.start} to ${M.end}; retention covers ${RH.customer_data_from} to ${RH.customer_data_to}. Times follow ${D.timezone}.`;
  $('caveats').innerHTML=D.caveats.map(c=>`<li>${esc(c)}</li>`).join('');
  table('gapTable',[{k:'what',label:'missing',wrap:1},{k:'adds',label:'what it would add',wrap:1},{k:'how',label:'how to connect',wrap:1}],D.gaps||[],{file:'not_covered.csv'});
  $('rcaveats').innerHTML=R.report.caveats.map(c=>`<li>${esc(c)}</li>`).join('');
}

// ---------- page ----------
const RENDER={overview:renderOverview, web:renderWeb, attr:renderAttr, ads:renderAds, prod:renderProd, sales:renderSales, ret:renderRet, data:renderData};
const done=new Set();
function show(){
  document.querySelectorAll('#tabs button').forEach(b=>b.setAttribute('aria-selected',b.dataset.t===S.tab));
  document.querySelectorAll('main>section').forEach(s=>s.classList.toggle('hidden',s.id!==S.tab));
  document.querySelectorAll('#winSeg button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.w===S.w));
  $('monthSel').value=S.w.startsWith('m:')?S.w:''; $('monthSel').classList.toggle('on',S.w.startsWith('m:'));
  $('customBtn').setAttribute('aria-pressed',S.w.startsWith('c:')); if(S.w.startsWith('c:')){ $('customBox').classList.remove('hidden'); $('cFrom').value=win().from; $('cTo').value=win().to; }
  document.querySelectorAll('#modelSeg button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.m===S.model));
  const w=win(); $('winNote').textContent=`${fmtDay(w.from)} – ${fmtDay(w.to)}`+(hasPrev()?` vs ${fmtDay(win(1).from)} – ${fmtDay(win(1).to)}`:' (no earlier period loaded to compare)');
  const key=S.tab+'|'+(S.tab==='data'?'':S.w+S.model);
  if(!done.has(key)){ RENDER[S.tab](); done.add(key); }
  save();
  try{ history.replaceState(null,'',`#${S.tab}/${S.w}/${S.model}`); }catch(e){}
}
// a link like page.html#ads/90/first opens that tab, window and click credit
(()=>{ const [t,w,m]=location.hash.slice(1).split('/'); if(t) S.tab=t; if(w) S.w=w; if(m) S.model=m; })();
const rerender=()=>{ done.clear(); show(); };
$('tabs').onclick=e=>{ const b=e.target.closest('button'); if(b){ S.tab=b.dataset.t; show(); } };
$('winSeg').onclick=e=>{ const b=e.target.closest('button'); if(b){ S.w=b.dataset.w; $('customBox').classList.add('hidden'); rerender(); } };
// calendar months the loaded days reach, newest first; custom ranges inside the loaded days
(()=>{ const out=[]; let d=new Date(M.start.slice(0,7)+'-01T00:00:00');
  while(iso(d)<=END){ out.push(iso(d).slice(0,7)); d=new Date(d.getFullYear(),d.getMonth()+1,1); }
  $('monthSel').innerHTML='<option value="">Calendar month…</option>'+out.reverse().map(m=>`<option value="m:${m}">${MONTH(m)}${monthEnd(m)>END?' (so far)':m+'-01'<M.start?` (from ${fmtDay(M.start)})`:''}</option>`).join(''); })();
$('monthSel').onchange=e=>{ if(e.target.value){ S.w=e.target.value; $('customBox').classList.add('hidden'); rerender(); } };
$('cFrom').min=$('cTo').min=M.start; $('cFrom').max=$('cTo').max=END;
$('customBtn').onclick=()=>{ const b=$('customBox'); b.classList.toggle('hidden'); if(!b.classList.contains('hidden')){ $('cFrom').value=win().from; $('cTo').value=win().to; $('cFrom').focus(); } };
$('cApply').onclick=()=>{ let f=$('cFrom').value, t=$('cTo').value; if(!f||!t) return; if(f>t) [f,t]=[t,f];
  S.w=`c:${f<M.start?M.start:f}:${t>END?END:t}`; rerender(); };
[$('cFrom'),$('cTo')].forEach(x=>x.onkeydown=e=>{ if(e.key==='Enter') $('cApply').click(); });
$('modelSeg').onclick=e=>{ const b=e.target.closest('button'); if(b){ S.model=b.dataset.m; rerender(); } };
$('themeBtn').onclick=()=>{ const r=document.documentElement; const dark=r.dataset.theme? r.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches; r.dataset.theme=dark?'light':'dark'; rerender(); };
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',rerender);
let rt; addEventListener('resize',()=>{ clearTimeout(rt); rt=setTimeout(()=>{ if(S.tab==='overview') renderFlow(totals(win())); },200); });
if(!RENDER[S.tab]) S.tab='overview'; S.w=String(S.w); { const m=/^m:(\d{4}-\d\d)$/.exec(S.w), c=/^c:(\d{4}-\d\d-\d\d):(\d{4}-\d\d-\d\d)$/.exec(S.w);
  if(!(['7','28','90'].includes(S.w) || (m && monthEnd(m[1])>=M.start && m[1]+'-01'<=END) || (c && c[1]<=c[2] && c[1]>=M.start && c[2]<=END))) S.w='28'; } if(!['last','first','split'].includes(S.model)) S.model='last';
$('title').textContent=`${D.store||'Store'} · Marketing`;
$('sub').textContent=[D.domain, `data pulled ${localTime(D.pulled_at)}`, `complete days through ${fmtDay(END)}`, `Shopify · Google Ads · Meta · Klaviyo`].filter(Boolean).join(' · ');
$('foot').textContent=`Built ${localTime(D.built_at)} from data pulled live ${localTime(D.pulled_at)} (read only). Missing values are shown as —, never 0.`;
show();
document.body.dataset.rendered = S.tab;   // marker: the page drew without a script error
</script>
</body>
</html>
"""

# Chart.js v4.4.1 (MIT License, (c) 2023 Chart.js Contributors), zlib + base64, same copy the LIVE Dashboard embeds.
CHARTJS_B64 = "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"

if __name__ == "__main__":
    main()
