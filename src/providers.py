"""Enrichment providers, including a real live one.

The offline provider reads a local file and exists so the golden set is
reproducible. The live provider actually goes out to the internet.

**Waterfall enrichment**, which is the one idea worth taking from Clay: ask
source A, and if it returns nothing for a field, ask source B, then C, and
stop at the first hit. Coverage goes up because no single source has
everything, and cost goes down because you only pay the expensive source for
the rows the cheap one missed.

Four live sources, none of which needs an API key or an account:

  1. company homepage    JSON-LD Organization block and meta tags
  2. HTML tech signals   which vendor scripts the page actually loads
  3. Wikidata            employee count, industry, country, founding year
  4. DNS over HTTPS      MX records, which reveal the mail platform

Two consequences of a waterfall that most implementations skip, and that are
the actually interesting part:

  * **Each field gets its own waterfall.** Wikidata is best for headcount and
    useless for tech stack. The homepage is the reverse.
  * **You must record which source answered**, or you cannot tell a confident
    value from a last-resort guess. That is what `field_sources` is for, and
    it is why `confidence` here is derived from coverage rather than asserted.

    python src/providers.py stripe.com
    python src/providers.py stripe.com --offline
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "enrichment_source.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

EXPECTED_FIELDS = [
    "company_name", "employee_count", "industry", "hq_country",
    "tech_stack", "funding_stage", "annual_revenue",
]

# Vendor fingerprints found in page source. Only entries whose presence is
# genuinely diagnostic, because a false tech signal is worse than none.
TECH_SIGNATURES = {
    "HubSpot": ("js.hs-scripts.com", "hs-analytics", "hubspot"),
    "Salesforce": ("salesforce.com", "force.com", "pardot"),
    "Marketo": ("marketo.net", "mktoresp"),
    "Segment": ("cdn.segment.com", "analytics.min.js"),
    "Google Analytics": ("googletagmanager.com", "google-analytics.com"),
    "Intercom": ("intercom.io", "intercomcdn"),
    "Drift": ("js.driftt.com",),
    "Zendesk": ("zdassets.com", "zendesk"),
    "Snowflake": ("snowflake.com/",),
    "Amplitude": ("amplitude.com", "cdn.amplitude"),
    "Mixpanel": ("mixpanel.com",),
    "Stripe": ("js.stripe.com",),
    "Shopify": ("cdn.shopify.com",),
    "Cloudflare": ("cloudflare", "cdnjs.cloudflare.com"),
    "AWS": ("amazonaws.com", "cloudfront.net"),
    "Vercel": ("vercel.app", "vercel-insights"),
    "Webflow": ("webflow.com",),
    "Contentful": ("ctfassets.net",),
}

MAIL_PLATFORM = {
    "google": "Google Workspace",
    "outlook": "Microsoft 365",
    "protection.outlook": "Microsoft 365",
    "pphosted": "Proofpoint",
    "mimecast": "Mimecast",
    "zoho": "Zoho Mail",
}


def _get(url: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            return resp.read(400_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def _get_json(url: str, timeout: int = 12):
    body = _get(url, timeout)
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------- source 1: homepage

def from_homepage(domain: str) -> dict:
    """JSON-LD Organization plus meta tags from the company's own site."""
    out: dict = {}
    html = None
    for scheme in ("https://", "https://www."):
        html = _get(f"{scheme}{domain}")
        if html:
            break
    if not html:
        return out

    out["_html"] = html

    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            blob = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        for item in (blob if isinstance(blob, list) else [blob]):
            if not isinstance(item, dict):
                continue
            if "Organization" not in str(item.get("@type", "")):
                continue
            if item.get("name"):
                out["company_name"] = item["name"]
            addr = item.get("address") or {}
            if isinstance(addr, dict) and addr.get("addressCountry"):
                out["hq_country"] = addr["addressCountry"]
            if item.get("numberOfEmployees"):
                n = item["numberOfEmployees"]
                n = n.get("value") if isinstance(n, dict) else n
                try:
                    out["employee_count"] = int(n)
                except (TypeError, ValueError):
                    pass

    if "company_name" not in out:
        if m := re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)',
                          html, re.I):
            out["company_name"] = m.group(1).strip()
        elif m := re.search(r"<title>(.*?)</title>", html, re.S | re.I):
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            out["company_name"] = re.split(r"\s[|\-]\s", title)[0].strip() or None
    return out


# --------------------------------------------------- source 2: tech signals

def from_tech_signals(html: str | None) -> dict:
    if not html:
        return {}
    low = html.lower()
    found = [
        vendor for vendor, needles in TECH_SIGNATURES.items()
        if any(n in low for n in needles)
    ]
    return {"tech_stack": found} if found else {}


# ------------------------------------------------------- source 3: wikidata

def from_wikidata(domain: str, name_hint: str | None = None) -> dict:
    """Real structured company data. Free, keyless, and often has headcount."""
    query = name_hint or domain.rsplit(".", 1)[0].replace("-", " ")
    search = _get_json(
        "https://www.wikidata.org/w/api.php?action=wbsearchentities"
        f"&search={urllib.parse.quote(query)}&language=en&limit=3&format=json"
    )
    if not search or not search.get("search"):
        return {}

    for hit in search["search"]:
        entity = _get_json(
            f"https://www.wikidata.org/wiki/Special:EntityData/{hit['id']}.json"
        )
        if not entity:
            continue
        claims = (entity.get("entities", {}).get(hit["id"], {}) or {}).get("claims", {})

        # P856 official website. Used to confirm we matched the right company
        # rather than a same-named one, which is the main risk with a name search.
        sites = []
        for c in claims.get("P856", []):
            value = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
            if isinstance(value, str):
                sites.append(value.lower())
        root = domain.lower().replace("www.", "")
        if sites and not any(root in s for s in sites):
            continue

        out: dict = {}
        for c in claims.get("P1128", []):          # employees
            value = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
            if isinstance(value, dict) and value.get("amount"):
                try:
                    out["employee_count"] = int(float(value["amount"].lstrip("+")))
                except ValueError:
                    pass
        if claims.get("P452"):                      # industry
            out["_industry_qid"] = (
                claims["P452"][0].get("mainsnak", {}).get("datavalue", {}) or {}
            ).get("value", {}).get("id")
        if claims.get("P17"):                       # country
            out["_country_qid"] = (
                claims["P17"][0].get("mainsnak", {}).get("datavalue", {}) or {}
            ).get("value", {}).get("id")

        for key, target in (("_industry_qid", "industry"), ("_country_qid", "hq_country")):
            qid = out.pop(key, None)
            if not qid:
                continue
            label = _get_json(
                f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
            )
            if label:
                name = (label.get("entities", {}).get(qid, {}) or {}) \
                    .get("labels", {}).get("en", {}).get("value")
                if name:
                    out[target] = name
        if out:
            out["_wikidata_id"] = hit["id"]
            return out
    return {}


# -------------------------------------------------------- source 4: DNS MX

def from_dns(domain: str) -> dict:
    """MX records over DNS-over-HTTPS. Reveals the mail platform, keyless."""
    data = _get_json(f"https://dns.google/resolve?name={domain}&type=MX")
    if not data or not data.get("Answer"):
        return {}
    blob = " ".join(a.get("data", "").lower() for a in data["Answer"])
    for needle, platform in MAIL_PLATFORM.items():
        if needle in blob:
            return {"_mail_platform": platform, "tech_stack": [platform]}
    return {}


# ---------------------------------------------------------------- providers

def offline(email: str) -> dict:
    """Deterministic. Backs the golden set so the accuracy number reproduces."""
    domain = email.strip().lower().rsplit("@", 1)[-1]
    with DATA.open(encoding="utf-8") as fh:
        table = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    raw = table.get(domain)

    record = {
        "domain": domain,
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "offline",
        "field_sources": {},
    }
    if raw is None:
        for f in EXPECTED_FIELDS:
            record[f] = None
        record["confidence"] = 0.0
        record["provider_hit"] = False
    else:
        for f in EXPECTED_FIELDS:
            record[f] = raw.get(f)
            if record[f] not in (None, "", []):
                record["field_sources"][f] = "offline"
        record["confidence"] = raw.get("confidence", 0.0)
        record["provider_hit"] = True
    record["unresolved_fields"] = [
        f for f in EXPECTED_FIELDS if record.get(f) in (None, "", [])
    ]
    return record


def live(email: str) -> dict:
    """Real network enrichment through a four source waterfall."""
    domain = email.strip().lower().rsplit("@", 1)[-1]

    record: dict = {
        "domain": domain,
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "live-waterfall",
        "field_sources": {},
    }
    for f in EXPECTED_FIELDS:
        record[f] = None

    def absorb(payload: dict, label: str) -> None:
        for key, value in payload.items():
            if key.startswith("_") or value in (None, "", []):
                continue
            if key not in EXPECTED_FIELDS:
                continue
            if record.get(key) in (None, "", []):
                record[key] = value
                record["field_sources"][key] = label
            elif key == "tech_stack" and isinstance(value, list):
                merged = list(dict.fromkeys(record["tech_stack"] + value))
                record["tech_stack"] = merged
                record["field_sources"]["tech_stack"] += f"+{label}"

    home = from_homepage(domain)
    html = home.pop("_html", None)
    absorb(home, "homepage")
    absorb(from_tech_signals(html), "tech-signals")

    wiki = from_wikidata(domain, record.get("company_name"))
    absorb(wiki, "wikidata")
    if wiki.get("_wikidata_id"):
        record["wikidata_id"] = wiki["_wikidata_id"]

    dns = from_dns(domain)
    absorb(dns, "dns-mx")
    if dns.get("_mail_platform"):
        record["mail_platform"] = dns["_mail_platform"]

    record["unresolved_fields"] = [
        f for f in EXPECTED_FIELDS if record.get(f) in (None, "", [])
    ]
    record["provider_hit"] = bool(record["field_sources"])

    # Confidence is DERIVED from how much of the expected shape came back,
    # not asserted. An enrichment that resolved two fields out of seven should
    # not report the same confidence as one that resolved six.
    resolved = len(EXPECTED_FIELDS) - len(record["unresolved_fields"])
    record["confidence"] = round(resolved / len(EXPECTED_FIELDS), 2)
    return record


PROVIDERS = {"offline": offline, "live": live}


def enrich(email: str, provider: str = "offline") -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}, have {list(PROVIDERS)}")
    return PROVIDERS[provider](email)


def render(record: dict) -> str:
    order = [
        "company_name", "domain", "employee_count", "industry", "hq_country",
        "tech_stack", "funding_stage", "annual_revenue", "mail_platform",
        "wikidata_id", "enriched_at", "source", "confidence",
        "field_sources", "unresolved_fields",
    ]
    width = max(len(k) for k in order)
    lines = []
    for key in order:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        elif isinstance(value, dict):
            value = ", ".join(f"{k}<-{v}" for k, v in value.items()) or "{}"
        lines.append(f"  {key.ljust(width)} : {value}")
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    which = "offline" if "--offline" in sys.argv else "live"
    target = args[0] if args else "stripe.com"
    if "@" not in target:
        target = "hello@" + target

    print(f"INPUT    : {target}")
    print(f"PROVIDER : {which}")
    print("OUTPUT   :")
    print(render(enrich(target, which)))
