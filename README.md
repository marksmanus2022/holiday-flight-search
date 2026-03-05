# Holiday Flight Search

Automatically searches Google Flights for the best Dublin -> Shanghai round-trip prices and emails results twice daily.

## Search criteria
- **Route**: Dublin (DUB) -> Shanghai Pudong (PVG), round trip
- **Departure**: April 1-5, 2026
- **Return**: by May 5, 2026 (~30 day stay)
- **Stops**: max 1
- **Duration**: max 20 hours
- **Avoided routes**: No Middle East stopovers
- **Schedule**: 10:30 and 22:30 Dublin time, every day

---

## One-time setup

### Step 1 — Create a GitHub repository

1. Go to https://github.com/new
2. Name the repo `holiday-flight-search`
3. Set it to **Private**
4. Do **not** add a README (you already have one)
5. Click **Create repository**

### Step 2 — Push this folder to GitHub

Run these commands from inside the `Holiday` folder:

```bash
cd /Users/edebqin/claude/Holiday
git init
git add .
git commit -m "Initial flight search setup"
git remote add origin https://github.com/marksmanus2022/holiday-flight-search.git
git branch -M main
git push -u origin main
```

### Step 3 — Add Gmail secrets to GitHub

1. In your repo on GitHub, go to **Settings -> Secrets and variables -> Actions**
2. Click **New repository secret** and add:

| Name | Value |
|------|-------|
| `GMAIL_USER` | `marksman.us2022@gmail.com` |
| `GMAIL_APP_PASSWORD` | `flkk mwht vcjr hwiu` |

### Step 4 — Test it manually

1. In your repo go to **Actions -> Holiday Flight Search**
2. Click **Run workflow -> Run workflow**
3. Wait ~5-10 minutes for it to complete
4. Check your inbox at marksman.us2022@gmail.com

After that it will run automatically at 10:30 and 22:30 Dublin time every day.

---

## Files

| File | Purpose |
|------|---------|
| `flight_search.py` | Main script: scrapes Google Flights, filters, sends email |
| `requirements.txt` | Python dependencies |
| `.github/workflows/flight_search.yml` | GitHub Actions schedule |

## Troubleshooting

- **No results in email**: Google Flights may have blocked the scraper. The Action will upload screenshots under the **Artifacts** tab - check those to see what happened.
- **Email not received**: Check spam folder. Verify the App Password is correct in GitHub Secrets.
- **Action not running**: GitHub pauses scheduled Actions after 60 days of repo inactivity. Re-enable via the Actions tab.
