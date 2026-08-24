"""
Monitor diario de tfm_energia
=============================
Consulta la BD en vivo, compara contra la foto del día anterior y detecta
problemas. Escribe PROBLEMS.txt + un log, regenera un HTML de estado y lanza
una notificación de Windows si hay algo que revisar.

Pensado para el Programador de tareas de Windows (una vez al día).
NO expone credenciales: usa ingesta/credentials.json local vía config.load_config().

Uso manual:
    python ingesta/monitor_tfm_energia.py
Salidas (en carpeta monitor/ del proyecto):
    PROBLEMS.txt              últimos problemas (o "Sin problemas")
    monitor_log.txt           histórico de cada ejecución
    monitor_state.json        foto anterior para comparar
    estado_tfm_energia.html   dashboard de estado autogenerado
"""
import json, sys, subprocess
from pathlib import Path
from datetime import datetime, date

HERE = Path(__file__).resolve().parent          # ...\tfm-energia\ingesta
ROOT = HERE.parent                              # ...\tfm-energia
OUT = ROOT / "monitor"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))

import psycopg2
from config import load_config

# --- clasificación por nombre para saber qué frescura esperar ---
FORECAST_HINT = ("forecast", "pbf", "spot_price", "ecmwf")   # alcanzan D+1 (futuro)
LAG_ERA5 = 20      # era5 real llega con ~2 semanas de retraso: umbral holgado
LAG_DEFAULT = 3    # resto de tablas: como mucho 3 días de retraso
TIME_PREF = ["datetime_utc", "datetime", "time_qh", "time", "ts", "fecha", "date", "deal_date", "run_date"]
OPS_TABLES = {"pipeline_log"}


def snapshot(cur):
    cur.execute("SELECT CURRENT_DATE, NOW()::timestamp(0)")
    today, now = cur.fetchone()
    cur.execute("""SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name""")
    tables = [r[0] for r in cur.fetchall()]
    snap = {"today": str(today), "now": str(now), "tables": {}}
    for t in tables:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (t,))
        names = [r[0] for r in cur.fetchall()]
        cur.execute(f'SELECT COUNT(*) FROM "{t}"'); n = cur.fetchone()[0]
        d = {"rows": n, "ncols": len(names), "tcol": None, "max": None, "nulls": {}}
        tcol = next((p for p in TIME_PREF if p in names), None)
        d["tcol"] = tcol
        if n and tcol:
            cur.execute(f'SELECT MAX("{tcol}")::timestamp FROM "{t}"')
            mx = cur.fetchone()[0]
            d["max"] = str(mx)
        if n:
            sel = ", ".join(f'COUNT("{c}")' for c in names)
            cur.execute(f'SELECT {sel} FROM "{t}"'); cnt = cur.fetchone()
            d["nulls"] = {c: round(100 * (1 - v / n), 1) for c, v in zip(names, cnt)}
        snap["tables"][t] = d
    # actividad de pipeline_log de hoy
    cur.execute("""SELECT COALESCE(SUM((estado='ok')::int),0), COALESCE(SUM((estado<>'ok')::int),0)
                   FROM pipeline_log WHERE created_at::date = CURRENT_DATE""")
    ok, fail = cur.fetchone()
    snap["pipeline_today"] = {"ok": ok, "fail": fail}
    return snap


def days_behind(today_str, max_str):
    if not max_str:
        return None
    try:
        d = datetime.fromisoformat(max_str).date()
    except ValueError:
        d = datetime.strptime(max_str[:10], "%Y-%m-%d").date()
    return (date.fromisoformat(today_str) - d).days


def detect_problems(cur_snap, prev_snap):
    probs, notes = [], []
    today = cur_snap["today"]
    cur_t, prev_t = cur_snap["tables"], (prev_snap or {}).get("tables", {})

    # tablas desaparecidas
    for t in sorted(set(prev_t) - set(cur_t)):
        probs.append(f"BAJA: la tabla '{t}' existía ayer y ya no está.")

    for t, d in cur_snap["tables"].items():
        if t in OPS_TABLES:
            continue
        n, prev = d["rows"], prev_t.get(t)
        # tabla vacía
        if n == 0:
            if prev and prev["rows"] > 0:
                probs.append(f"VACÍA: '{t}' pasó de {prev['rows']} filas a 0.")
            else:
                notes.append(f"'{t}' está vacía (0 filas).")
            continue
        # caída de filas
        if prev and prev["rows"] and n < prev["rows"] * 0.95:
            probs.append(f"CAÍDA DE FILAS: '{t}' bajó de {prev['rows']} a {n} (-{prev['rows']-n}).")
        # frescura / atasco
        db = days_behind(today, d["max"])
        is_fc = any(h in t for h in FORECAST_HINT)
        limit = 1 if is_fc else (LAG_ERA5 if "era5" in t else LAG_DEFAULT)
        if db is not None and db > limit:
            stuck = ""
            if prev and prev.get("max") == d["max"]:
                stuck = " (no avanza desde la última pasada)"
            probs.append(f"ATASCO: '{t}' llega a {str(d['max'])[:16]} — {db} días de retraso{stuck}.")
        # pico de nulos por columna (>20 puntos vs ayer)
        if prev:
            for c, pct in d["nulls"].items():
                p0 = prev["nulls"].get(c)
                if p0 is not None and pct - p0 > 20:
                    probs.append(f"NULOS: '{t}.{c}' subió de {p0}% a {pct}% de NULL.")

    # fallos de pipeline hoy
    pt = cur_snap.get("pipeline_today", {})
    if pt.get("fail", 0) > 0:
        notes.append(f"pipeline_log: {pt['fail']} ejecución(es) en estado != ok hoy "
                     f"({pt.get('ok',0)} ok).")
    return probs, notes


def notify_windows(title, msg):
    """Toast best-effort; si falla, no pasa nada (PROBLEMS.txt es el canal fiable)."""
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] "
        "| Out-Null; "
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$x=$t.GetElementsByTagName('text'); "
        f"$x.Item(0).AppendChild($t.CreateTextNode('{title}'))|Out-Null; "
        f"$x.Item(1).AppendChild($t.CreateTextNode('{msg}'))|Out-Null; "
        "$n=[Windows.UI.Notifications.ToastNotification]::new($t); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('tfm_energia').Show($n)"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=20,
                       capture_output=True)
    except Exception:
        pass


# ---------- HTML de estado (dashboard autogenerado, data-driven) ----------
CSS = """
:root{--bg:#fbfbfc;--pnl:#fff;--ink:#191c21;--mut:#6b7480;--faint:#9aa2ae;--line:#e4e7ec;
--accent:#2f5da3;--ok:#4f8a63;--warn:#b07a26;--crit:#a8493b;
--mono:ui-monospace,'Cascadia Code','SF Mono',Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--serif:'Iowan Old Style',Palatino,Georgia,serif;}
@media(prefers-color-scheme:dark){:root{--bg:#0f1216;--pnl:#161a20;--ink:#e9ecf1;--mut:#8b94a1;
--faint:#69727f;--line:#252b33;--accent:#7aa5e0;--ok:#79b98d;--warn:#d4a654;--crit:#d98a7c;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px}
.wrap{max-width:820px;margin:0 auto;padding:56px 24px 80px}
.eye{font-family:var(--mono);font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;margin-bottom:18px;display:flex;align-items:center;gap:9px}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
h1{font-family:var(--serif);font-size:30px;font-weight:600;margin:0 0 8px}
.sub{color:var(--mut);margin:0 0 28px;font-family:var(--mono);font-size:12.5px}
.banner{border-radius:9px;padding:16px 18px;margin:0 0 30px;border:1px solid}
.banner.ok{background:color-mix(in srgb,var(--ok) 12%,transparent);border-color:color-mix(in srgb,var(--ok) 35%,transparent)}
.banner.bad{background:color-mix(in srgb,var(--crit) 12%,transparent);border-color:color-mix(in srgb,var(--crit) 40%,transparent)}
.banner h2{font-family:var(--serif);font-size:19px;margin:0 0 8px}
.banner ul{margin:6px 0 0;padding-left:20px}.banner li{margin:3px 0;font-size:14px}
.banner .note{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:var(--faint);border-bottom:1px solid var(--line);padding:8px 10px}
td{padding:7px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
td.t{font-family:var(--mono);font-size:12px}
.pill{font-family:var(--mono);font-size:11px;padding:1px 7px;border-radius:10px}
.pill.ok{color:var(--ok);background:color-mix(in srgb,var(--ok) 14%,transparent)}
.pill.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 16%,transparent)}
.pill.crit{color:var(--crit);background:color-mix(in srgb,var(--crit) 16%,transparent)}
.num{text-align:right;font-family:var(--mono)}
footer{margin-top:32px;font-family:var(--mono);font-size:11px;color:var(--faint)}
"""


def build_html(snap, probs, notes):
    today = snap["today"]
    n_tab = len([t for t in snap["tables"] if t not in OPS_TABLES])
    empty = [t for t, d in snap["tables"].items() if t not in OPS_TABLES and d["rows"] == 0]
    ok = not probs
    dot = "var(--ok)" if ok else "var(--crit)"
    rows_html = []
    for t, d in sorted(snap["tables"].items()):
        if t in OPS_TABLES:
            continue
        db = days_behind(today, d["max"])
        is_fc = any(h in t for h in FORECAST_HINT)
        limit = 1 if is_fc else (LAG_ERA5 if "era5" in t else LAG_DEFAULT)
        if d["rows"] == 0:
            pill = '<span class="pill crit">vacía</span>'
        elif db is None:
            pill = '<span class="pill ok">—</span>'
        elif db > limit:
            pill = f'<span class="pill crit">{db}d atrás</span>'
        elif db > 1 and not is_fc:
            pill = f'<span class="pill warn">{db}d</span>'
        else:
            pill = '<span class="pill ok">al día</span>'
        mx = str(d["max"])[:16] if d["max"] else "—"
        rows_html.append(f'<tr><td class="t">{t}</td><td class="num">{d["rows"]:,}</td>'
                         f'<td class="num">{d["ncols"]}</td><td class="t">{mx}</td><td>{pill}</td></tr>')
    if ok:
        banner = ('<div class="banner ok"><h2>✓ Sin problemas</h2>'
                  f'<div class="note">{n_tab} tablas revisadas, ninguna atascada ni vacía inesperadamente.</div>')
    else:
        items = "".join(f"<li>{p}</li>" for p in probs)
        banner = (f'<div class="banner bad"><h2>⚠ {len(probs)} problema(s) detectado(s)</h2><ul>{items}</ul>')
    if notes:
        banner += '<div class="note" style="margin-top:8px">Notas: ' + " · ".join(notes) + "</div>"
    banner += "</div>"
    return f"""<title>Estado tfm_energia — {today}</title>
<style>{CSS}</style>
<div class="wrap">
  <div class="eye"><span class="dot" style="background:{dot}"></span> monitor diario · {snap['now']}</div>
  <h1>Estado de la base — tfm_energia</h1>
  <p class="sub">host 91.134.143.153:5432 · {n_tab} tablas de datos · {len(empty)} vacía(s) · autogenerado</p>
  {banner}
  <table><thead><tr><th>tabla</th><th class="num">filas</th><th class="num">cols</th><th>último dato</th><th>frescura</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody></table>
  <footer>monitor_tfm_energia.py · comparación contra la foto anterior · PROBLEMS.txt tiene el detalle</footer>
</div>"""


def main():
    _, db = load_config()
    conn = psycopg2.connect(**db); cur = conn.cursor()
    snap = snapshot(cur)
    cur.close(); conn.close()

    state_path = OUT / "monitor_state.json"
    prev = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None

    probs, notes = detect_problems(snap, prev)
    stamp = snap["now"]

    # PROBLEMS.txt
    if probs:
        body = f"[{stamp}] {len(probs)} problema(s):\n" + "\n".join(f"  - {p}" for p in probs)
    else:
        body = f"[{stamp}] Sin problemas."
    if notes:
        body += "\n  notas:\n" + "\n".join(f"    · {x}" for x in notes)
    (OUT / "PROBLEMS.txt").write_text(body + "\n", encoding="utf-8")
    with (OUT / "monitor_log.txt").open("a", encoding="utf-8") as f:
        f.write(body + "\n")

    # HTML
    (OUT / "estado_tfm_energia.html").write_text(build_html(snap, probs, notes), encoding="utf-8")

    # estado nuevo
    state_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # aviso
    if probs:
        notify_windows("tfm_energia: revisar", f"{len(probs)} problema(s). Ver PROBLEMS.txt")

    print(body)
    print(f"\nHTML: {OUT / 'estado_tfm_energia.html'}")
    sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
