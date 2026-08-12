# Weather Dashboard (Flask + Open-Meteo)

Simple weather dashboard:
- Geocoding via Nominatim (OpenStreetMap) — no API key required
- Weather data via Open-Meteo — no API key required
- Frontend uses Chart.js to show daily max/min temperatures

Run locally:
1. Clone repo
2. Create virtualenv and install:
   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Run:
   ```
   python app.py
   ```
4. Open http://127.0.0.1:8080

Docker:
```
docker build -t weather-dashboard .
docker run -p 8080:8080 weather-dashboard
```

Deployment:
- App listens on port 8080. On hosting platforms (Railway/Heroku), set port mapping to 8080.
- No API keys required for the default setup.

Notes:
- Nominatim requires a User-Agent header; this app sets a basic one. For heavy usage, you must follow Nominatim usage policy and consider providing an email in the UA or using a hosted geocoding service.
- Open-Meteo is free for general use but check their terms for heavy traffic.
- You can extend the app to use other parameters (hourly data, wind/humidity, icons).
