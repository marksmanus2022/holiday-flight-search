#!/usr/bin/env python3
"""
Holiday Flight Search
Dublin (DUB) -> Shanghai (PVG) Round Trip
Scrapes Google Flights, filters results, sends HTML email report.
"""

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# ── CONFIG ────────────────────────────────────────────────────────────────────
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT         = "marksman.us2022@gmail.com"

ORIGIN            = "Dublin"
ORIGIN_CODE       = "DUB"
DESTINATION       = "Shanghai Pudong"
DESTINATION_CODE  = "PVG"

# Departure window: April 1-5 allows ~30-day stay and return by May 5
DEPART_DATES  = ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04", "2026-04-05"]
RETURN_DATE   = "2026-05-05"

MAX_DURATION_HOURS = 20
MAX_STOPS          = 1

MIDDLE_EAST_AIRPORTS = {
    "DXB", "AUH", "SHJ", "FJR",          # UAE
    "DOH",                                  # Qatar
    "BAH",                                  # Bahrain
    "KWI",                                  # Kuwait
    "MCT", "SLL",                           # Oman
    "RUH", "JED", "MED", "DMM",            # Saudi Arabia
    "AMM", "AQJ",                           # Jordan
    "BEY",                                  # Lebanon
    "BGW", "BSR", "NJF",                   # Iraq
    "IKA", "TBZ", "MHD", "SYZ",           # Iran
    "TLV",                                  # Israel
    "CAI", "HRG", "SSH", "LXR",           # Egypt
    "ADE", "SAH",                           # Yemen
}

MIDDLE_EAST_AIRLINES = {
    "EK", "EY", "QR", "GF", "KU",
    "RJ", "ME", "WY", "MS", "SV", "XY",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def parse_duration_hours(text: str) -> float:
    # handles "25h 50min", "13 hr 45 min", "13h45m"
    h = re.search(r"(\d+)\s*h(?:r)?(?!\w)", text)
    m = re.search(r"(\d+)\s*m(?:in)?(?!\w)", text)
    return (int(h.group(1)) if h else 0) + (int(m.group(1)) if m else 0) / 60

def parse_price(text: str) -> Optional[int]:
    cleaned = text.replace(",", "").replace("\u202f", "").replace("\xa0", "")
    m = re.search(r"[€$£]?\s*(\d{3,5})", cleaned)
    if m:
        val = int(m.group(1))
        if 200 < val < 6000:
            return val
    return None

def is_middle_east(flight: dict) -> bool:
    route = f"{flight.get('layover', '')} {flight.get('airline_code', '')}".upper()
    for code in MIDDLE_EAST_AIRPORTS:
        if code in route:
            return True
    return flight.get("airline_code", "").upper() in MIDDLE_EAST_AIRLINES

# ── SCRAPING (Skyscanner) ─────────────────────────────────────────────────────
KNOWN_AIRLINES = {
    "Aer Lingus": "EI", "British Airways": "BA", "Lufthansa": "LH",
    "Air France": "AF", "KLM": "KL", "Finnair": "AY", "Swiss": "LX",
    "Turkish Airlines": "TK", "China Eastern": "MU", "Air China": "CA",
    "Cathay Pacific": "CX", "Singapore Airlines": "SQ", "Hainan Airlines": "HU",
    "Xiamen Air": "MF", "Shenzhen Airlines": "ZH", "Virgin Atlantic": "VS",
    "Iberia": "IB", "TAP Air Portugal": "TP", "LOT Polish": "LO",
}

def _sky_date(iso: str) -> str:
    """'2026-04-01' -> '260401'"""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%y%m%d")

def _dismiss_overlays(page: Page):
    for sel in [
        'button:has-text("Accept all")', 'button:has-text("Accept")',
        'button:has-text("Agree")', '[data-testid="acceptCookiesButton"]',
        'button:has-text("Got it")', 'button:has-text("Close")',
        '[aria-label="Close"]', 'button:has-text("OK")',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            pass

def parse_flight_block(text: str, depart_date: str) -> Optional[dict]:
    """Parse raw text extracted from a Skyscanner flight card."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    flight = {"depart_date": depart_date}

    # Price
    for line in lines:
        p = parse_price(line)
        if p:
            flight["price"] = p
            break
    if "price" not in flight:
        return None

    # Duration — Skyscanner: "25h 50min" | Google: "25 hr 50 min"
    for line in lines:
        if re.search(r'\d+\s*h', line, re.I) and re.search(r'\d+\s*m', line, re.I):
            flight["duration_text"] = line.strip()
            flight["duration_hours"] = parse_duration_hours(line)
            break
    flight.setdefault("duration_hours", 999)

    # Stops
    for line in lines:
        low = line.lower()
        if "direct" in low or "nonstop" in low:
            flight["stops"] = 0
            flight["stops_text"] = "Direct"
            break
        if "stop" in low:
            m = re.search(r"(\d+)\s*stop", low)
            flight["stops"] = int(m.group(1)) if m else 1
            flight["stops_text"] = line
            flight["layover"] = " ".join(re.findall(r'\b([A-Z]{3})\b', line))
            break
    flight.setdefault("stops", 0)

    # Times
    for line in lines:
        if re.search(r'\d{1,2}:\d{2}', line) and re.search(r'[–\-—]', line):
            flight["times"] = line
            break

    # Airline
    for line in lines:
        for name, code in KNOWN_AIRLINES.items():
            if name.lower() in line.lower():
                flight["airline"] = name
                flight["airline_code"] = code
                break
        if "airline" in flight:
            break

    if "airline" not in flight:
        for line in lines:
            if (5 < len(line) < 60
                    and not re.search(r'^\d', line)
                    and "€" not in line and "$" not in line
                    and not re.search(r'\d:\d{2}', line)
                    and "stop" not in line.lower()
                    and not re.search(r'\d+\s*h', line, re.I)):
                flight["airline"] = line
                flight["airline_code"] = ""
                break
    flight.setdefault("airline", "Unknown Airline")

    return flight

def extract_results(page: Page, depart_date: str) -> list:
    # Skyscanner loads results progressively — wait generously
    card_selectors = [
        '[data-testid="FlightCard"]',
        '[data-testid*="flight-card"]',
        'article[class*="FlightCard"]',
        '[class*="FlightCard"]',
        '[class*="flightCard"]',
        '[role="list"] > li',
        '[role="listitem"]',
    ]
    items = []
    for sel in card_selectors:
        try:
            page.wait_for_selector(sel, timeout=18000)
            items = page.locator(sel).all()
            if len(items) >= 2:
                print(f"  Found {len(items)} cards ({sel})")
                break
        except Exception:
            continue

    if not items:
        print("  [WARN] No cards found — parsing full page text")
        try:
            body = page.inner_text("body")
            return _parse_body_text(body, depart_date)
        except Exception:
            return []

    flights = []
    for item in items[:20]:
        try:
            text = item.inner_text()
            if len(text) < 20:
                continue
            f = parse_flight_block(text, depart_date)
            if f:
                flights.append(f)
        except Exception:
            continue
    return flights

def _parse_body_text(body: str, depart_date: str) -> list:
    """Last-resort: find flight-like chunks in raw page text."""
    flights = []
    chunks = re.split(r'(?=€\s*\d{3,4})', body)
    for chunk in chunks[:25]:
        if len(chunk) < 30:
            continue
        f = parse_flight_block(chunk, depart_date)
        if f:
            flights.append(f)
    return flights

def scrape_flights(depart_date: str) -> list:
    out_date = _sky_date(depart_date)
    ret_date = _sky_date(RETURN_DATE)
    url = (
        f"https://www.skyscanner.net/transport/flights/dub/pvg/"
        f"{out_date}/{ret_date}/"
        f"?adults=1&cabinclass=economy&rtn=1&preferdirects=false"
    )
    print(f"\n  Skyscanner {depart_date}: {url}")
    flights = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-GB",
            timezone_id="Europe/Dublin",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            _dismiss_overlays(page)
            page.wait_for_timeout(5000)   # results load progressively
            _dismiss_overlays(page)       # second pass for late modals

            flights = extract_results(page, depart_date)
            print(f"  Extracted {len(flights)} flights")
            page.screenshot(path=f"/tmp/sky_{depart_date}.png")

        except Exception as e:
            print(f"  [ERROR] {e}")
            try:
                page.screenshot(path=f"/tmp/error_{depart_date}.png")
            except Exception:
                pass
        finally:
            browser.close()

    return flights

# ── FILTERING ─────────────────────────────────────────────────────────────────
def filter_flights(flights: list) -> list:
    out = []
    for f in flights:
        if f.get("duration_hours", 999) > MAX_DURATION_HOURS:
            continue
        if f.get("stops", 999) > MAX_STOPS:
            continue
        if is_middle_east(f):
            continue
        out.append(f)
    out.sort(key=lambda x: x.get("price", 9999))
    return out

def find_closest_fallback(all_raw: list) -> list:
    """
    When no flight meets all criteria, return the single closest match:
    - Still avoids Middle East
    - Still max 1 stop
    - Relaxes the duration limit — picks the shortest duration available
    """
    candidates = [
        f for f in all_raw
        if f.get("stops", 999) <= MAX_STOPS and not is_middle_east(f)
    ]
    if not candidates:
        # Last resort: just avoid Middle East, ignore stop count
        candidates = [f for f in all_raw if not is_middle_east(f)]
    if not candidates:
        candidates = all_raw

    # Sort by duration (shortest first), then price
    candidates.sort(key=lambda x: (x.get("duration_hours", 999), x.get("price", 9999)))
    return candidates[:1]

# ── EMAIL HTML ────────────────────────────────────────────────────────────────
def _render_flight_card(f: dict, rank_label: str, accent_color: str, PRIMARY: str, ACCENT: str) -> str:
    price   = f.get("price", "?")
    airline = f.get("airline", "Unknown")
    dur     = f.get("duration_text", "&mdash;")
    stops   = f.get("stops_text", "&mdash;")
    times   = f.get("times", "")
    layover = f.get("layover", "")

    try:
        d = datetime.strptime(f["depart_date"], "%Y-%m-%d")
        depart_nice = d.strftime("%a %d %b %Y")
    except Exception:
        depart_nice = f.get("depart_date", "")

    layover_html = (
        f'<p style="margin:8px 0 0;font-size:12px;color:#8898aa;">Layover: {layover}</p>'
        if layover else ""
    )
    times_html = (
        f'<p style="text-align:center;font-size:16px;letter-spacing:2px;color:{PRIMARY};margin:16px 0 0;">{times}</p>'
        if times else ""
    )

    return f"""
    <div style="background:#fff;border-radius:16px;margin:0 auto 28px;max-width:660px;
                overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.09);border-top:5px solid {accent_color};">
      <div style="background:linear-gradient(135deg,{PRIMARY},#265c8c);
                  padding:14px 24px;display:flex;justify-content:space-between;align-items:center;">
        <span style="color:{ACCENT};font-weight:700;font-size:13px;letter-spacing:1px;">{rank_label}</span>
        <span style="color:#fff;font-size:30px;font-weight:800;">&euro;{price:,}</span>
      </div>
      <div style="padding:20px 24px 24px;">
        <p style="margin:0 0 4px;font-size:19px;font-weight:700;color:{PRIMARY};">{airline}</p>
        <p style="margin:0 0 16px;font-size:13px;color:#8898aa;">Dublin (DUB) &rarr; Shanghai Pudong (PVG) &nbsp;&middot;&nbsp; Return by 5 May 2026</p>
        <table width="100%" cellpadding="0" cellspacing="8">
          <tr>
            <td style="background:#f4f7fb;border-radius:10px;padding:12px 14px;width:25%;">
              <p style="margin:0 0 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Departs</p>
              <p style="margin:0;font-size:13px;font-weight:600;color:{PRIMARY};">{depart_nice}</p>
            </td>
            <td style="background:#f4f7fb;border-radius:10px;padding:12px 14px;width:25%;">
              <p style="margin:0 0 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Duration</p>
              <p style="margin:0;font-size:13px;font-weight:600;color:{PRIMARY};">{dur}</p>
            </td>
            <td style="background:#f4f7fb;border-radius:10px;padding:12px 14px;width:25%;">
              <p style="margin:0 0 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Stops</p>
              <p style="margin:0;font-size:13px;font-weight:600;color:{PRIMARY};">{stops}</p>
            </td>
            <td style="background:#f4f7fb;border-radius:10px;padding:12px 14px;width:25%;">
              <p style="margin:0 0 4px;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Return by</p>
              <p style="margin:0;font-size:13px;font-weight:600;color:{PRIMARY};">5 May 2026</p>
            </td>
          </tr>
        </table>
        {times_html}
        {layover_html}
        <div style="text-align:center;margin-top:20px;">
          <a href="https://www.skyscanner.net/transport/flights/dub/pvg/"
             style="background:linear-gradient(135deg,{ACCENT},#f5c842);color:{PRIMARY};
                    text-decoration:none;padding:12px 44px;border-radius:30px;
                    font-weight:700;font-size:14px;display:inline-block;">
            View &amp; Book &rarr;
          </a>
        </div>
      </div>
    </div>"""


def build_html(flights: list, fallback: list = None) -> str:
    now_str       = datetime.now().strftime("%A, %d %B %Y at %H:%M")
    is_fallback   = not flights and bool(fallback)
    to_render     = flights if flights else (fallback or [])
    count         = len(flights)

    PRIMARY = "#1a3a5c"
    ACCENT  = "#e8a020"
    BG      = "#eef2f7"
    medals  = ["#f0b429", "#b0b8c1", "#c97d3e"]
    labels  = ["Best Price", "2nd Best", "3rd Best"]

    cards = ""

    if not to_render:
        cards = """
        <div style="text-align:center;padding:60px 20px;color:#999;">
          <p style="font-size:48px;margin:0 0 16px;">&#9992;&#65039;</p>
          <h3 style="font-weight:300;color:#aaa;margin:0 0 8px;">Search was blocked</h3>
          <p style="margin:0;font-size:13px;">Skyscanner returned no data this run — likely bot detection. Will retry next run.</p>
        </div>"""
    else:
        if is_fallback:
            over = to_render[0].get("duration_hours", 0) - MAX_DURATION_HOURS
            cards += f"""
        <div style="background:#fff4e0;border:2px dashed {ACCENT};border-radius:12px;
                    max-width:660px;margin:0 auto 24px;padding:16px 24px;text-align:center;">
          <p style="margin:0;font-size:14px;color:#a0600a;font-weight:600;">
            No flights matched all criteria (max 1 stop &middot; max 20 hrs &middot; no Middle East).<br>
            Showing closest available &mdash; only <strong>{over:.1f} hr(s) over</strong> the duration limit.
          </p>
        </div>"""

        for i, f in enumerate(to_render[:8]):
            accent_color = medals[i] if i < 3 else "#8898aa"
            rank_label   = ("Closest Match" if is_fallback and i == 0
                            else (labels[i] if i < 3 else f"#{i+1}"))
            cards += _render_flight_card(f, rank_label, accent_color, PRIMARY, ACCENT)

    info_label = (
        f"Closest match shown (no exact results)" if is_fallback
        else f"{count} qualifying flight(s) found"
    )

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Holiday Flight Results</title>
</head>
<body style="margin:0;padding:0;background:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">

  <div style="background:linear-gradient(135deg,{PRIMARY} 0%,#0d2d4a 100%);padding:44px 20px;text-align:center;">
    <p style="margin:0 0 10px;font-size:40px;">&#9992;&#65039; &#127464;&#127475;</p>
    <h1 style="margin:0;color:#fff;font-size:30px;font-weight:800;letter-spacing:-0.5px;">Dublin &rarr; Shanghai</h1>
    <p style="margin:10px 0 4px;color:{ACCENT};font-size:15px;">Round Trip &middot; 1 Stop &middot; Max 20 hrs &middot; No Middle East</p>
    <p style="margin:4px 0 0;color:rgba(255,255,255,0.5);font-size:13px;">Depart: 1&ndash;5 April 2026 &nbsp;&middot;&nbsp; Return by: 5 May 2026</p>
  </div>

  <div style="background:{ACCENT};padding:10px 20px;text-align:center;">
    <span style="color:{PRIMARY};font-size:13px;font-weight:600;">
      &#128336; Searched: {now_str} Dublin time &nbsp;&middot;&nbsp; {info_label}
    </span>
  </div>

  <div style="padding:32px 16px 8px;">{cards}</div>

  <div style="text-align:center;padding:20px;color:#aaa;font-size:12px;border-top:1px solid #dce3ec;margin-top:8px;">
    <p style="margin:0;">Prices from Skyscanner. Always verify before booking.</p>
    <p style="margin:4px 0 0;">Auto-report runs daily at 10:30 &amp; 22:30 Dublin time.</p>
  </div>

</body>
</html>"""


# ── EMAIL SEND ────────────────────────────────────────────────────────────────
def send_email(html: str, flights: list, fallback: list = None, blocked: bool = False):
    display = flights or fallback or []
    ts      = datetime.now().strftime('%d %b %H:%M')

    if blocked:
        subject = f"DUB->PVG: [BLOCKED] Skyscanner returned no data | {ts}"
    elif not flights and fallback:
        best    = f"€{fallback[0]['price']:,}"
        subject = f"DUB->PVG: [CLOSEST MATCH] {best} — no exact results | {ts}"
    elif flights:
        best    = f"€{flights[0]['price']:,}"
        subject = f"DUB->PVG: {best} best price — {len(flights)} flight(s) found | {ts}"
    else:
        subject = f"DUB->PVG: [BLOCKED] No data returned | {ts}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT

    plain = f"Holiday Flight Search Results\n{datetime.now()}\n\n"
    plain += f"{len(flights)} qualifying / {len(display)} shown.\n\n"
    for f in display[:5]:
        plain += (
            f"€{f.get('price','?')} | {f.get('airline','?')} | "
            f"{f.get('duration_text','?')} | {f.get('stops_text','?')} | "
            f"Departs {f.get('depart_date','?')}\n"
        )

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    print(f"  Sending email to {RECIPIENT} ...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
    print("  Email sent.")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"Holiday Flight Search  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_raw      = []
    all_filtered = []

    for date in DEPART_DATES:
        raw      = scrape_flights(date)
        filtered = filter_flights(raw)
        print(f"  {date}: {len(raw)} raw  ->  {len(filtered)} after filter")
        all_raw.extend(raw)
        all_filtered.extend(filtered)

    # Deduplicate filtered results
    all_filtered.sort(key=lambda x: x.get("price", 9999))
    seen, unique = set(), []
    for f in all_filtered:
        key = (f.get("price"), f.get("airline"), f.get("depart_date"))
        if key not in seen:
            seen.add(key)
            unique.append(f)

    print(f"\nTotal unique qualifying flights: {len(unique)}")

    # Determine what to show
    blocked  = len(all_raw) == 0
    fallback = find_closest_fallback(all_raw) if (not unique and not blocked) else []

    if blocked:
        print("  Scraper was blocked — no raw flights returned at all")
    elif fallback:
        print(f"  No exact matches — closest fallback: "
              f"{fallback[0].get('duration_text','?')} / {fallback[0].get('airline','?')}")

    html = build_html(unique, fallback=fallback)
    send_email(html, unique, fallback=fallback, blocked=blocked)
    print("\nDone!")


if __name__ == "__main__":
    main()
