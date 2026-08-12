from flask import Flask, request, jsonify, send_from_directory
import requests
from urllib.parse import urlencode

app = Flask(__name__, static_folder='static', static_url_path='/static')

USER_AGENT = "weather-dashboard/1.0 (contact: your@email.example)"

def geocode_place(place):
    # Use Nominatim to get lat/lon for a place name
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    item = data[0]
    return {"name": item.get("display_name"), "lat": float(item["lat"]), "lon": float(item["lon"])}

def fetch_weather(lat, lon, days=7):
    # Use Open-Meteo for current weather and daily forecast
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": days
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/weather')
def api_weather():
    q = request.args.get('q')          # place name
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    days = int(request.args.get('days', 7))
    if q:
        geo = geocode_place(q)
        if not geo:
            return jsonify({"error": "Location not found"}), 404
        lat = geo['lat']
        lon = geo['lon']
        name = geo['name']
    elif lat and lon:
        name = f"{lat},{lon}"
    else:
        return jsonify({"error": "Provide 'q' (place name) or 'lat' and 'lon'"}), 400

    try:
        weather = fetch_weather(lat, lon, days=days)
    except requests.RequestException as e:
        return jsonify({"error": "Weather API error", "details": str(e)}), 502

    # Build simplified response
    result = {
        "location": name,
        "latitude": lat,
        "longitude": lon,
        "current": weather.get("current_weather"),
        "daily": weather.get("daily", {})
    }
    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
