import requests, json

creds = json.load(open("ingesta/credentials.json"))
headers = {"Host": creds["Host"], "x-api-key": creds["x-api-key"], "Accept": "application/json"}

url = "https://api.esios.ree.es/indicators/600?start_date=2026-06-24T22:00:00&end_date=2026-06-25T21:59:59&time_trunc=hour&geo_ids[]=3"
r = requests.get(url, headers=headers)
vals = r.json().get("indicator", {}).get("values", [])
print(f"Total: {len(vals)}")
for v in vals:
    precio_corregido = v['value'] / 4
    print(f"  {v['datetime_utc']} → raw: {v['value']} | corregido: {round(precio_corregido, 2)}")