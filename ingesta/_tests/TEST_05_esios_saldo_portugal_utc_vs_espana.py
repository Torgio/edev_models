import requests, json
from datetime import datetime
from zoneinfo import ZoneInfo

creds = json.load(open("ingesta/credentials.json"))
headers = {"Host": creds["Host"], "x-api-key": creds["x-api-key"], "Accept": "application/json"}
url = "https://api.esios.ree.es/indicators/557?start_date=2026-06-24T00:00:00&end_date=2026-06-26T23:59:59&time_trunc=hour&geo_ids[]=8741"
r = requests.get(url, headers=headers)
vals = r.json().get("indicator", {}).get("values", [])
print(f"Total valores API: {len(vals)}")
TZ = ZoneInfo("Europe/Madrid")
for v in vals:
    dt = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
    print(f"  UTC: {dt} | España: {dt.astimezone(TZ)} | valor: {v['value']}")