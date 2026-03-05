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
    h = re.search(r"(\d+)\s*hr", text)
    m = re.search(r"(\d+)\s*min", text)
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

# ── SCRAPING ──────────────────────────────────────────────────────────────────
def accept_cookies(page: Page):
    for sel in ['button:has-text("Accept all")', 'button:has-text("I agree")',
                '[aria-label="Accept all"]', 'button:has-text("Agree")']:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(800)
                return
        except Exception:
            pass

def fill_location(page: Page, placeholder_hint: str, city: str, airport_code: str):
    """Fill origin or destination field and pick the matching suggestion."""
    selectors = [
        f'input[placeholder*="{placeholder_hint}"]',
        f'input[aria-label*="{placeholder_hint}"]',
    ]
    inp = None
    for sel in selectors:
        try:
            c = page.locator(sel).first
            if c.is_visible(timeout=2000):
                inp = c
                break
        except Exception:
            pass

    if not inp:
        print(f"  [WARN] Could not find input for '{placeholder_hint}'")
        return

    inp.triple_click()
    inp.fill("")
    inp.type(city, delay=70)
    page.wait_for_timeout(2000)

    options = page.locator('[role="option"], [role="listitem"]').all()
    for opt in options:
        try:
            t = opt.inner_text()
            if airport_code in t:
                opt.click()
                page.wait_for_timeout(700)
                return
        except Exception:
            pass

    # Fallback: take first suggestion
    try:
        page.locator('[role="option"]').first.click()
        page.wait_for_timeout(700)
    except Exception:
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)

def select_calendar_date(page: Page, iso_date: str):
    """Click the correct day in the Google Flights calendar picker."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    day = dt.day
    # Try data-date attribute first (most reliable)
    for attempt in range(14):
        for sel in [
            f'[data-date="{iso_date}"]',
            f'[aria-label*="{dt.strftime("%B")}"][aria-label*="{dt.year}"][aria-label*=" {day},"]',
            f'td[data-date="{iso_date}"]',
            f'button[data-date="{iso_date}"]',
        ]:
            try:
                cell = page.locator(sel).first
                if cell.is_visible(timeout=1000):
                    cell.click()
                    page.wait_for_timeout(500)
                    return
            except Exception:
                pass
        # Navigate to next month
        for nav_sel in ['[aria-label="Next month"]', 'button[data-id="next"]', 'button:has-text("›")']:
            try:
                btn = page.locator(nav_sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    page.wait_for_timeout(600)
                    break
            except Exception:
                pass

def set_dates(page: Page, depart_date: str, return_date: str):
    """Open the date picker and select departure + return dates."""
    date_btn_selectors = [
        '[aria-label*="Departure"]',
        'input[placeholder*="Departure"]',
        '[data-label*="Departure"]',
    ]
    opened = False
    for sel in date_btn_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(1500)
                opened = True
                break
        except Exception:
            pass

    if not opened:
        print("  [WARN] Could not open date picker")
        return

    # Navigate calendar to correct month then click dates
    select_calendar_date(page, depart_date)
    page.wait_for_timeout(400)
    select_calendar_date(page, return_date)
    page.wait_for_timeout(400)

    # Click Done
    for done_sel in ['button:has-text("Done")', '[aria-label="Done"]']:
        try:
            btn = page.locator(done_sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(700)
                return
        except Exception:
            pass

def click_search(page: Page):
    for sel in ['button[aria-label="Search"]', 'button:has-text("Search")', '[type="submit"]']:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                return
        except Exception:
            pass

def parse_flight_block(text: str, depart_date: str) -> Optional[dict]:
    """Parse a raw text block from a Google Flights result card."""
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

    # Duration
    for line in lines:
        if "hr" in line and "min" in line:
            flight["duration_text"] = line
            flight["duration_hours"] = parse_duration_hours(line)
            break
    flight.setdefault("duration_hours", 999)

    # Stops
    for line in lines:
        low = line.lower()
        if "nonstop" in low:
            flight["stops"] = 0
            flight["stops_text"] = "Nonstop"
            break
        if "stop" in low:
            m = re.search(r"(\d+)\s*stop", low)
            flight["stops"] = int(m.group(1)) if m else 1
            flight["stops_text"] = line
            # Look for layover airport codes (3-letter uppercase)
            codes = re.findall(r'\b([A-Z]{3})\b', line)
            flight["layover"] = " ".join(codes)
            break
    flight.setdefault("stops", 0)

    # Times  (e.g. "10:30 – 09:15+1")
    for line in lines:
        if re.search(r'\d{1,2}:\d{2}', line) and ("–" in line or "-" in line):
            flight["times"] = line
            break

    # Airline (first non-numeric, non-price, non-time line of reasonable length)
    for line in lines:
        if (5 < len(line) < 60
                and not re.search(r'^\d', line)
                and "€" not in line and "$" not in line
                and "hr" not in line and "stop" not in line.lower()
                and not re.search(r'\d:\d{2}', line)):
            # Guess airline code from known carriers
            airline_code = ""
            known = {
                "Aer Lingus": "EI", "British Airways": "BA", "Lufthansa": "LH",
                "Air France": "AF", "KLM": "KL", "Finnair": "AY",
                "Swiss": "LX", "Turkish": "TK", "China Eastern": "MU",
                "Air China": "CA", "Cathay Pacific": "CX", "Singapore": "SQ",
                "Hainan": "HU", "Xiamen": "MF", "Shenzhen": "ZH",
            }
            for name, code in known.items():
                if name.lower() in line.lower():
                    airline_code = code
                    break
            flight["airline"] = line
            flight["airline_code"] = airline_code
            break
    flight.setdefault("airline", "Unknown Airline")

    return flight

def extract_results(page: Page, depart_date: str) -> list:
    try:
        page.wait_for_selector('[role="listitem"], li[class]', timeout=15000)
    except PWTimeout:
        print("  [WARN] Timed out waiting for results")
        return []

    page.wait_for_timeout(2000)

    result_selectors = [
        'li[role="row"]',
        '[jsname="IWWDBc"]',
        'ul[role="list"] > li',
        '[role="listitem"]',
    ]
    items = []
    for sel in result_selectors:
        items = page.locator(sel).all()
        if len(items) >= 3:
            break

    print(f"  Found {len(items)} candidate result items")

    flights = []
    for item in items[:20]:
        try:
            text = item.inner_text()
            if len(text) < 30:
                continue
            if not re.search(r'[€$£]\s*\d{3}|\d{3,4}\s*(EUR|USD)', text):
                continue
            f = parse_flight_block(text, depart_date)
            if f:
                flights.append(f)
        except Exception:
            continue
    return flights

def scrape_flights(depart_date: str) -> list:
    print(f"\n  Searching: {depart_date} -> {RETURN_DATE}")
    flights = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
            timezone_id="Europe/Dublin",
        )
        # Remove webdriver property
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()

        try:
            page.goto("https://www.google.com/travel/flights", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)

            accept_cookies(page)
            fill_location(page, "Where from", ORIGIN, ORIGIN_CODE)
            fill_location(page, "Where to", DESTINATION, DESTINATION_CODE)
            set_dates(page, depart_date, RETURN_DATE)
            click_search(page)
            page.wait_for_timeout(6000)

            flights = extract_results(page, depart_date)

            # Save screenshot for debugging
            page.screenshot(path=f"/tmp/flights_{depart_date}.png")

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

# ── EMAIL HTML ────────────────────────────────────────────────────────────────
def build_html(flights: list) -> str:
    now_str = datetime.now().strftime("%A, %d %B %Y at %H:%M")
    count = len(flights)

    PRIMARY = "#1a3a5c"
    ACCENT  = "#e8a020"
    BG      = "#eef2f7"

    cards = ""
    if not flights:
        cards = """
        <div style="text-align:center;padding:60px 20px;color:#999;">
            <p style="font-size:48px;margin:0 0 16px;">&#9992;&#65039;</p>
            <h3 style="font-weight:300;color:#aaa;margin:0 0 8px;">No qualifying flights found</h3>
            <p style="margin:0;font-size:13px;">Criteria: max 1 stop &middot; max 20 hrs &middot; no Middle East &middot; depart Apr 1-5</p>
        </div>"""
    else:
        medals = ["#f0b429", "#b0b8c1", "#c97d3e"]
        labels = ["Best Price", "2nd Best", "3rd Best"]
        for i, f in enumerate(flights[:8]):
            accent_color = medals[i] if i < 3 else "#8898aa"
            rank_label   = labels[i] if i < 3 else f"#{i+1}"

            price    = f.get("price", "?")
            airline  = f.get("airline", "Unknown")
            dur      = f.get("duration_text", "&mdash;")
            stops    = f.get("stops_text", "&mdash;")
            times    = f.get("times", "")
            layover  = f.get("layover", "")

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
                f'<p style="text-align:center;font-size:16px;letter-spacing:2px;'
                f'color:{PRIMARY};margin:16px 0 0;">{times}</p>'
                if times else ""
            )

            cards += f"""
            <div style="background:#fff;border-radius:16px;margin:0 auto 28px;max-width:660px;
                        overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.09);
                        border-top:5px solid {accent_color};">
              <!-- header -->
              <div style="background:linear-gradient(135deg,{PRIMARY},#265c8c);
                          padding:14px 24px;display:flex;justify-content:space-between;align-items:center;">
                <span style="color:{ACCENT};font-weight:700;font-size:13px;letter-spacing:1px;">{rank_label}</span>
                <span style="color:#fff;font-size:30px;font-weight:800;">&euro;{price:,}</span>
              </div>
              <!-- body -->
              <div style="padding:20px 24px 24px;">
                <p style="margin:0 0 4px;font-size:19px;font-weight:700;color:{PRIMARY};">{airline}</p>
                <p style="margin:0 0 16px;font-size:13px;color:#8898aa;">Dublin (DUB) &rarr; Shanghai Pudong (PVG) &nbsp;&middot;&nbsp; Return by 5 May 2026</p>
                <!-- detail grid -->
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
                <!-- CTA -->
                <div style="text-align:center;margin-top:20px;">
                  <a href="https://www.google.com/travel/flights"
                     style="background:linear-gradient(135deg,{ACCENT},#f5c842);color:{PRIMARY};
                            text-decoration:none;padding:12px 44px;border-radius:30px;
                            font-weight:700;font-size:14px;display:inline-block;">
                    View &amp; Book &rarr;
                  </a>
                </div>
              </div>
            </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Holiday Flight Results</title>
</head>
<body style="margin:0;padding:0;background:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">

  <!-- Hero -->
  <div style="background:linear-gradient(135deg,{PRIMARY} 0%,#0d2d4a 100%);padding:44px 20px;text-align:center;">
    <p style="margin:0 0 10px;font-size:40px;">&#9992;&#65039; &#127464;&#127475;</p>
    <h1 style="margin:0;color:#fff;font-size:30px;font-weight:800;letter-spacing:-0.5px;">Dublin &rarr; Shanghai</h1>
    <p style="margin:10px 0 4px;color:{ACCENT};font-size:15px;">Round Trip &middot; 1 Stop &middot; Max 20 hrs &middot; No Middle East</p>
    <p style="margin:4px 0 0;color:rgba(255,255,255,0.5);font-size:13px;">Depart: 1&ndash;5 April 2026 &nbsp;&middot;&nbsp; Return by: 5 May 2026</p>
  </div>

  <!-- Info bar -->
  <div style="background:{ACCENT};padding:10px 20px;text-align:center;">
    <span style="color:{PRIMARY};font-size:13px;font-weight:600;">
      &#128336; Searched: {now_str} Dublin time &nbsp;&middot;&nbsp; {count} qualifying flight(s) found
    </span>
  </div>

  <!-- Cards -->
  <div style="padding:32px 16px 8px;">
    {cards}
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:20px;color:#aaa;font-size:12px;border-top:1px solid #dce3ec;margin-top:8px;">
    <p style="margin:0;">Prices from Google Flights. Always verify before booking.</p>
    <p style="margin:4px 0 0;">Auto-report runs daily at 10:30 &amp; 22:30 Dublin time.</p>
  </div>

</body>
</html>"""

# ── EMAIL SEND ────────────────────────────────────────────────────────────────
def send_email(html: str, flights: list):
    best = f"€{flights[0]['price']:,}" if flights else "No results"
    subject = (
        f"DUB->PVG Flights: {best} best price | "
        f"{datetime.now().strftime('%d %b %H:%M')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT

    plain = f"Holiday Flight Search Results\n{datetime.now()}\n\n"
    plain += f"{len(flights)} qualifying flight(s) found.\n\n"
    for f in flights[:5]:
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

    all_flights = []
    for date in DEPART_DATES:
        raw = scrape_flights(date)
        filtered = filter_flights(raw)
        print(f"  {date}: {len(raw)} raw  ->  {len(filtered)} after filter")
        all_flights.extend(filtered)

    # Deduplicate and sort
    all_flights.sort(key=lambda x: x.get("price", 9999))
    seen, unique = set(), []
    for f in all_flights:
        key = (f.get("price"), f.get("airline"), f.get("depart_date"))
        if key not in seen:
            seen.add(key)
            unique.append(f)

    print(f"\nTotal unique qualifying flights: {len(unique)}")

    html = build_html(unique)
    send_email(html, unique)
    print("\nDone!")

if __name__ == "__main__":
    main()
