"""
TFM Energia UCM — TEST 412: localizar el BRENT en Trayport
==========================================================
CONTEXTO
Trayport ya esta descartado para historico y para serie diaria (tests 401-411):
la API devuelve un unico precio por contrato que no varia con snapshotDate, y
la suscripcion no da acceso al libro de ordenes (bid/ask siempre vacios). Aun
asi, se comprueba si el Brent esta accesible, por completitud.

METODO QUE FUNCIONA, aprendido a base de fallos:
  1. instrumentId, NUNCA marketId (con marketId la API da 404 o valor repetido).
  2. Partir del INSTRUMENTO y listar SUS sequences: pedir una sequence que no
     le pertenece devuelve 404. Buscar a ciegas en el catalogo de 14.202
     instrumentos no funciona.
  3. snapshotDate hasta AYER: el intradia devuelve 401.
  4. sequenceItemId acepta lista.

Referencias confirmadas:
    TTF  instrumentId=10002806  sequenceId=10000305  (item 272 = Aug-26, +1/mes)
    EUA  instrumentId=10003008  sequenceId=10000400  (item 830 = Dec-26, +40/año)

SOBRE LA UTILIDAD DEL BRENT PARA ESTE TFM
Su transmision al precio electrico español es INDIRECTA: ninguna central
peninsular quema fuel de forma significativa (oil_mw en entsoe_gen_data es
residual, ~30 MW), asi que el petroleo no entra en el coste marginal de
ninguna tecnologia que marque precio. Su valor seria como indicador macro
—algunos contratos de gas a plazo siguen indexados al crudo— pero eso ya lo
captura en buena medida el TTF y el MIBGAS.
Alternativa inmediata si aqui no aparece: Yahoo Finance con el ticker BZ=F,
que funciona y tiene historico completo desde 2020.

USO
    python Commodities_TEST_412.py
    python Commodities_TEST_412.py --todos    # sin filtrar derivados
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

REF_URL      = "https://referencedata.trayport.com"
SNAPSHOT_URL = "https://analytics.trayport.com/api/snapshots"
TIMEOUT      = 30

CREDS_PATH = Path(__file__).parent.parent / "credentials.json"

# Nombres habituales del Brent en catalogos de trading
PALABRAS = ["BRENT", "ICE BRENT", "DATED BRENT", "BRENT CRUDE"]

# Se descartan derivados: interesa el subyacente, no spreads ni opciones
EXCLUIR = ["OPTION", "SPREAD", "CRACK", "DIFF", "SWAP", "/", "ANON",
           "CAP", "FLOOR", "ASIAN", "CSO", "VS "]

# Sequences de periodos que interesan
SEQ_INTERES = ["MONTH", "QUARTER", "YEAR", "DAY", "SEASON", "BALMO"]


def get_headers() -> dict:
    try:
        creds = json.load(open(CREDS_PATH))
    except FileNotFoundError:
        sys.exit(f"ERROR: no se encuentra {CREDS_PATH}")
    key = creds.get("trayport_api_key")
    if not key:
        sys.exit('ERROR: falta "trayport_api_key" en credentials.json')
    return {"x-api-key": key, "Accept": "application/json"}


def api_get(url, headers, params=None):
    r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def ayer():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")


def snapshot(headers, inst_id, seq_id, item_ids, fecha):
    params = {
        "contractType":   "SinglePeriod",
        "instrumentId":   inst_id,
        "sequenceId":     seq_id,
        "sequenceItemId": item_ids,
        "snapshotDate":   fecha,
    }
    try:
        r = requests.get(SNAPSHOT_URL, params=params, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        return {}, f"EXC {str(e)[:28]}"
    if r.status_code != 200:
        return {}, f"HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return {}, "no JSON"

    snaps = (data.get("snapshots", []) if isinstance(data, dict)
             else (data if isinstance(data, list) else [data]))
    out = {}
    for sn in snaps:
        if not isinstance(sn, dict):
            continue
        px, fte = None, None
        lt = sn.get("lastTrade")
        if isinstance(lt, dict) and lt.get("price") is not None:
            px, fte = float(lt["price"]), "lastTrade"
        if px is None:
            bo = sn.get("bestOrders")
            if isinstance(bo, dict):
                b, a = bo.get("bidPrice"), bo.get("askPrice")
                if b is not None and a is not None:
                    px, fte = (float(b) + float(a)) / 2, "mid"
                elif b is not None or a is not None:
                    px, fte = float(b if b is not None else a), "bid/ask"
        c  = sn.get("contract", {}) or {}
        si = c.get("sequenceItemId") or sn.get("sequenceItemId")
        if si is None and len(item_ids) == 1:
            si = item_ids[0]
        if px is not None and si is not None:
            out[int(si)] = (px, fte)
    return out, ("OK" if out else "sin precio")


def sequences_del_instrumento(headers, inst_id):
    for ruta in (f"{REF_URL}/instruments/{inst_id}/sequences",
                 f"{REF_URL}/instruments/{inst_id}"):
        try:
            data = api_get(ruta, headers)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for k in ("sequences", "sequenceIds"):
                    if isinstance(data.get(k), list):
                        return data[k]
        except Exception:
            continue
    return []


# ── 1. Candidatos ─────────────────────────────────────────────────────────────

def buscar(headers, sin_filtro=False):
    print("=" * 96)
    print("1. INSTRUMENTOS DE BRENT EN EL CATALOGO")
    print("=" * 96)
    try:
        instrumentos = api_get(f"{REF_URL}/instruments", headers)
    except Exception as e:
        print(f"  ERROR /instruments: {str(e)[:70]}")
        return []
    print(f"  {len(instrumentos)} instrumentos en total")

    cands = []
    for ins in instrumentos:
        n = str(ins.get("name", "")).upper()
        if not any(p in n for p in PALABRAS):
            continue
        if not sin_filtro and any(x in n for x in EXCLUIR):
            continue
        cands.append(ins)

    # los nombres mas cortos suelen ser el producto puro
    cands.sort(key=lambda i: len(str(i.get("name", ""))))
    print(f"  {len(cands)} candidatos"
          + ("" if sin_filtro else " tras descartar opciones, spreads y cracks") + ":\n")
    for ins in cands[:20]:
        print(f"      instrumentId={str(ins.get('id')):<10} {ins.get('name')}")
    if len(cands) > 20:
        print(f"      ... y {len(cands)-20} mas")
    print()
    return cands


# ── 2. Sequences y precios ────────────────────────────────────────────────────

def probar(headers, cands):
    print("=" * 96)
    print("2. SEQUENCES Y PRECIOS DE CADA CANDIDATO")
    print("=" * 96)
    print("  Se listan las sequences DEL instrumento (pedir una ajena da 404)")
    print(f"  y se prueba un snapshot de ayer.\n")

    fecha = ayer()
    ganadores = []

    for ins in cands[:10]:
        inst_id = int(ins["id"])
        nombre  = str(ins.get("name"))
        seqs = sequences_del_instrumento(headers, inst_id)
        if not seqs:
            print(f"  inst={inst_id} '{nombre[:45]}' — sin sequences listables")
            continue

        relevantes = []
        for s in seqs:
            sid = s.get("id") if isinstance(s, dict) else s
            nom = str(s.get("name", "")) if isinstance(s, dict) else ""
            if any(k in nom.upper() for k in SEQ_INTERES):
                relevantes.append((sid, nom))

        print(f"\n  inst={inst_id} '{nombre[:50]}'")
        print(f"    {len(seqs)} sequences, {len(relevantes)} de periodos")

        for sid, nom in relevantes[:6]:
            try:
                items = api_get(f"{REF_URL}/sequences/{sid}/sequenceItems",
                                headers, params={"count": 6})
            except Exception:
                continue
            if not items:
                continue
            ids     = [int(i["id"]) for i in items]
            nombres = {int(i["id"]): i.get("name") for i in items}
            res, estado = snapshot(headers, inst_id, sid, ids, fecha)
            if res:
                det = ", ".join(f"{nombres.get(k,k)}={v[0]:.2f}"
                                for k, v in sorted(res.items()))
                print(f"      seq={sid:<10} [{nom[:26]:<26}] {det}")
                ganadores.append((inst_id, nombre, sid, nom, res, nombres))
            else:
                print(f"      seq={sid:<10} [{nom[:26]:<26}] {estado}")
    return ganadores


# ── 3. Conclusion ─────────────────────────────────────────────────────────────

def conclusion(ganadores):
    print("\n" + "=" * 96)
    print("3. CONCLUSION")
    print("=" * 96)
    if ganadores:
        print("\n  Brent localizado en Trayport:\n")
        for inst_id, nombre, sid, seq_nom, res, nombres in ganadores[:3]:
            item = sorted(res.keys())[0]
            px, fte = res[item]
            print(f'    instrumentId={inst_id}  sequenceId={sid}')
            print(f'      instrumento: {nombre}')
            print(f'      sequence   : {seq_nom}')
            print(f'      ejemplo    : {nombres.get(item)} = {px:.2f} ({fte})')
            print()
        print("""  AVISO: aunque el Brent este accesible, arrastra las mismas
  limitaciones que TTF y EUA en esta suscripcion (TEST 411): el precio no
  varia con snapshotDate y el bid/ask viene vacio, asi que la serie diaria
  seria constante. Para el Brent la via practica es Yahoo Finance con BZ=F,
  que funciona y tiene historico desde 2020:

      TICKERS = { "TTF=F": "gas_ttf", "BZ=F": "brent" }
      ALTER TABLE commodities ADD COLUMN brent numeric;""")
    else:
        print("""
  El Brent no aparece accesible con esta suscripcion. Es coherente con lo
  visto: los entitlements cubren gas TTF y emisiones, y no necesariamente
  petroleo.

  ALTERNATIVA INMEDIATA — Yahoo Finance, ticker BZ=F (Brent Crude Oil
  Futures). Funciona, tiene historico completo desde 2020 y solo requiere una
  linea en el script que ya existe:

      TICKERS = { "TTF=F": "gas_ttf", "BZ=F": "brent" }
      ALTER TABLE commodities ADD COLUMN brent numeric;

  Recordatorio sobre su utilidad: la transmision del Brent al precio
  electrico español es indirecta, porque ninguna central peninsular quema
  fuel de forma significativa. Sirve como indicador macro, pero no como coste
  marginal de ninguna tecnologia que marque precio.""")
    print("=" * 96)


def main():
    headers = get_headers()
    sin_filtro = "--todos" in sys.argv
    print("\n" + "=" * 96)
    print("  TEST 412 — BRENT EN TRAYPORT")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 96 + "\n")

    cands = buscar(headers, sin_filtro)
    ganadores = probar(headers, cands) if cands else []
    conclusion(ganadores)


if __name__ == "__main__":
    main()