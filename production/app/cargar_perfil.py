"""Leer una curva horaria de consumo o generacion desde un Excel o CSV cualquiera.

QUE PROBLEMA RESUELVE
Los ficheros de curva horaria que mandan distribuidoras, comercializadoras y clientes vienen
en media docena de formatos incompatibles, y casi todos tropiezan en las mismas tres cosas:

  EL CAMBIO DE HORA. El ultimo domingo de marzo tiene 23 horas -- las 2:00 no existe -- y el
  de octubre tiene 25, porque las 2:00 ocurre dos veces. Los ficheros lo resuelven de todas
  las maneras imaginables: con 24 filas igualmente (y un dia desplazado), con una fila vacia,
  con la hora repetida sin marcar, con un sufijo `bis`, o con la hora numerada 1..25.

  EL AÑO BISIESTO. 8.784 horas en vez de 8.760. Un perfil tomado de un año normal y aplicado
  a uno bisiesto deja el 29 de febrero sin cubrir, y aplicado al reves sobra un dia.

  LA HORA 1..24. La convencion del sector electrico español numera las horas de 1 a 24, no de
  0 a 23. Leerla como 0-23 desplaza toda la curva una hora y nadie se entera, porque la serie
  sigue pareciendo razonable.

CONTRA QUE SE NORMALIZA, Y POR QUE
Contra la rejilla de la curva de precios: **24 horas por dia, siempre**. `curva_fundamental`
genera `date_range(freq="D") x arange(24)` y por tanto ignora el cambio de hora; si el perfil
llegara con 23 o 25 no se podrian multiplicar. Se resuelve igual que en `preparar_tensores`:
la hora repetida de octubre se PROMEDIA y la que falta en marzo se INTERPOLA. Es una
convencion, no una verdad, y por eso queda escrita aqui.

    from cargar_perfil import cargar, a_forma, proyectar
    s = cargar("consumo_cliente.xlsx")          # serie horaria, 24/dia, Europe/Madrid
    f = a_forma(s)                              # forma normalizada (mes, tipo, hora)
    c = proyectar(f, anual_mwh=350, desde="2026-09-01", hasta="2046-12-31")

    python scripts/cargar_perfil.py fichero.xlsx --auditar
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Europe/Madrid"

# Nombres que se han visto en ficheros reales, sin acentos y en minusculas.
COL_FECHA = ["fecha", "dia", "date", "day", "fechahora", "datetime", "fecha_hora",
             "timestamp", "ts", "fecha y hora", "periodo"]
COL_HORA = ["hora", "hour", "h", "periodo_horario", "hh"]
COL_VALOR = ["consumo", "valor", "value", "energia", "kwh", "mwh", "kw", "mw",
             "generacion", "produccion", "activa", "ae_kwh", "consumo_kwh", "demanda"]


def _norm(s: str) -> str:
    """minusculas, sin acentos y sin espacios: para comparar nombres de columna."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# Las listas de arriba se normalizan tambien: comparar "fecha_y_hora" contra "fecha y hora"
# no casa nunca, y el fichero se rechaza por un espacio.
COL_FECHA = [_norm(c) for c in COL_FECHA]
COL_HORA = [_norm(c) for c in COL_HORA]
COL_VALOR = [_norm(c) for c in COL_VALOR]


def _a_fecha(x) -> pd.Series:
    """Fechas sin imponer el orden de dia y mes.

    `dayfirst=True` sobre cadenas ISO ("2024-12-13") hace que pandas devuelva NaT en parte de
    las filas. Medido: un fichero de prueba perdio 19 dias y 456 horas, y la serie resultante
    parecia correcta -- simplemente terminaba antes de tiempo. Se mira el formato primero.
    """
    t = pd.Series(x).astype(str).str.strip()
    iso = t.str.match(r"^\d{4}-\d{2}-\d{2}").mean() > .5
    return pd.to_datetime(x, errors="coerce", dayfirst=not iso)


def _leer_crudo(ruta: Path, hoja=None) -> pd.DataFrame:
    """Excel o CSV, adivinando separador y decimal. El decimal es el que mas duele.

    Un CSV español trae `1.234,56`. Leerlo con la convencion inglesa da 1,23456 -- mil veces
    menos -- y la serie sigue pareciendo plausible porque todo se escala igual. Se detecta
    por si hay comas donde deberia haber decimales.
    """
    if ruta.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        return pd.read_excel(ruta, sheet_name=hoja or 0)
    texto = ruta.read_text(encoding="utf-8", errors="replace")[:20000]
    sep = ";" if texto.count(";") > texto.count(",") else ","
    dec = "," if (sep == ";" and re.search(r"\d,\d", texto)) else "."
    for enc in ("utf-8", "latin-1"):
        try:
            return pd.read_csv(ruta, sep=sep, decimal=dec, encoding=enc)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return pd.read_csv(ruta, sep=sep, decimal=dec, encoding="latin-1",
                       engine="python", on_bad_lines="skip")


def _a_largo(d: pd.DataFrame, verbose=True) -> pd.DataFrame:
    """De cualquier formato a (fecha, hora, valor). Reconoce tres familias.

    ANCHO      una fila por dia y 24-25 columnas H1..H24 -- lo tipico de un Excel
    LARGO 2    fecha + hora + valor
    LARGO 1    una sola columna de fecha-hora + valor
    """
    d = d.copy()
    d.columns = [_norm(c) for c in d.columns]

    # ── ANCHO: columnas h1..h24 (o 1..24, o hora1..hora24) ────────────────────
    horas = [c for c in d.columns if re.fullmatch(r"(h|hora|hour)?_?(\d{1,2})", c)
             and 1 <= int(re.findall(r"\d+", c)[0]) <= 25]
    if len(horas) >= 20:
        fecha = next((c for c in d.columns if c in COL_FECHA), d.columns[0])
        horas = sorted(horas, key=lambda c: int(re.findall(r"\d+", c)[0]))
        largo = d.melt(id_vars=[fecha], value_vars=horas,
                       var_name="_h", value_name="valor")
        largo["hora"] = largo._h.map(lambda c: int(re.findall(r"\d+", c)[0]))
        largo = largo.rename(columns={fecha: "fecha"})[["fecha", "hora", "valor"]]
        if verbose:
            print(f"  formato ANCHO: {len(horas)} columnas de hora")
        return largo

    # ── LARGO con fecha y hora separadas ──────────────────────────────────────
    fecha = next((c for c in d.columns if c in COL_FECHA), None)
    hora = next((c for c in d.columns if c in COL_HORA), None)
    valor = next((c for c in d.columns if c in COL_VALOR), None)
    if valor is None:                      # la ultima numerica que no sea fecha ni hora
        num = [c for c in d.columns
               if pd.api.types.is_numeric_dtype(d[c]) and c not in (fecha, hora)]
        valor = num[-1] if num else None
    if fecha is None or valor is None:
        raise ValueError(f"no reconozco las columnas: {list(d.columns)}")

    if hora is not None:
        if verbose:
            print(f"  formato LARGO: fecha='{fecha}' hora='{hora}' valor='{valor}'")
        return d.rename(columns={fecha: "fecha", hora: "hora",
                                 valor: "valor"})[["fecha", "hora", "valor"]]

    # ── LARGO con una sola columna de fecha-hora ──────────────────────────────
    t = _a_fecha(d[fecha])
    if t.isna().mean() > .5:
        raise ValueError(f"la columna '{fecha}' no parece una fecha")
    if verbose:
        print(f"  formato LARGO: una columna de fecha-hora ('{fecha}')")
    out = pd.DataFrame({"fecha": t.dt.normalize(), "hora": t.dt.hour + 1,
                        "valor": pd.to_numeric(d[valor], errors="coerce"),
                        "_minuto": t.dt.minute})
    if out._minuto.nunique() > 1:          # cuarto-horaria: se agrega
        if verbose:
            print(f"  resolucion sub-horaria detectada "
                  f"({out._minuto.nunique()} marcas por hora): se suma a horaria")
        out = out.groupby(["fecha", "hora"], as_index=False).valor.sum()
    return out[["fecha", "hora", "valor"]]


def _unidad_del_encabezado(cols) -> str | None:
    """kWh o MWh, leidos del NOMBRE de la columna, que es donde estan escritos.

    Adivinarlo por la magnitud no se puede: 40 por hora es 40 kWh en una pyme y 40 MWh en una
    industria, y equivocarse multiplica o divide el estudio entero por mil sin que nada
    parezca raro -- la curva conserva su forma. Si el encabezado no lo dice, no se inventa.
    """
    # `_` es caracter de palabra, asi que `\bkwh\b` NO casa dentro de "consumo_kwh" y la
    # unidad se daba por desconocida justo cuando estaba escrita. Se delimita a mano.
    t = " ".join(_norm(c) for c in cols)
    if re.search(r"(?:^|[^a-z0-9])(mwh|mw|megavatio\w*)(?:[^a-z0-9]|$)", t):
        return "mwh"
    if re.search(r"(?:^|[^a-z0-9])(kwh|kw|kilovatio\w*)(?:[^a-z0-9]|$)", t):
        return "kwh"
    return None


def cargar(ruta, hoja=None, unidad="auto", verbose=True) -> pd.DataFrame:
    """Lee el fichero y devuelve (dia, hora 0-23, valor) en la rejilla de 24 h/dia.

    `unidad` convierte a MWh. Con 'auto' se lee del encabezado; si no esta, NO se adivina:
    se avisa y se deja el valor sin tocar. Ver `_unidad_del_encabezado`.
    """
    ruta = Path(ruta)
    _crudo = _leer_crudo(ruta, hoja)
    _uni_cab = _unidad_del_encabezado(_crudo.columns)
    d = _a_largo(_crudo, verbose)
    d["fecha"] = _a_fecha(d.fecha).dt.normalize()
    d["hora"] = pd.to_numeric(d.hora, errors="coerce")
    d["valor"] = pd.to_numeric(d.valor, errors="coerce")
    d = d.dropna(subset=["fecha", "hora"])

    # ── la hora 1..24 del sector electrico español, y los dos dias raros ──────
    h0, h1 = int(d.hora.min()), int(d.hora.max())
    if h0 == 1:
        n_por_dia = d.groupby("fecha").hora.transform("size")
        h = d.hora.to_numpy()
        # 24 h: h-1 · 25 h (octubre): 2A y 2B caen las dos en la 2:00 · 23 h (marzo): no hay 2:00
        d["hora"] = np.select(
            [n_por_dia == 25, n_por_dia == 23],
            [np.where(h <= 3, h - 1, h - 2), np.where(h <= 2, h - 1, h)],
            default=h - 1)
        raros = d.assign(n=n_por_dia).query("n != 24").fecha.unique()
        if verbose:
            print(f"  horas numeradas 1..{h1} (convencion española): se pasa a 0..23")
            for f_ in raros:
                n_ = int((d.fecha == f_).sum())
                que = ("cambio de hora de octubre, 2A y 2B a la misma hora" if n_ == 25
                       else "cambio de hora de marzo, no existe la 2:00" if n_ == 23
                       else "dia incompleto en el fichero")
                print(f"     {pd.Timestamp(f_):%d-%m-%Y}: {n_} horas · {que}")
    elif verbose:
        print(f"  horas numeradas {h0}..{h1}")
    # cualquier hora que se salga de 0..23 es un error de formato, no un dato
    fuera = int(((d.hora < 0) | (d.hora > 23)).sum())
    if fuera:
        if verbose:
            print(f"  AVISO: {fuera} filas con hora fuera de 0..23, se descartan")
        d = d[(d.hora >= 0) & (d.hora <= 23)]

    n_bruto = len(d)
    # la hora repetida de octubre se promedia; es lo mismo que hace `preparar_tensores`
    d = d.groupby(["fecha", "hora"], as_index=False).valor.mean()
    dup = n_bruto - len(d)

    # ── rejilla completa: 24 h por dia, todos los dias del rango ──────────────
    dias = pd.date_range(d.fecha.min(), d.fecha.max(), freq="D")
    idx = pd.MultiIndex.from_product([dias, range(24)], names=["dia", "hora"])
    s = (d.set_index(["fecha", "hora"]).valor.reindex(idx))
    faltan = int(s.isna().sum())
    # se interpola DENTRO de cada dia: asi la hora que marzo se salta se rellena con sus
    # vecinas y un dia entero ausente no se inventa a partir del dia anterior
    s = s.groupby(level=0, group_keys=False).apply(
        lambda g: g.interpolate(limit_direction="both"))
    sin_arreglo = int(s.isna().sum())

    out = s.reset_index().rename(columns={"valor": "valor"})
    if unidad == "auto":
        unidad = _uni_cab
        if unidad is None:
            if verbose:
                print(f"  AVISO: el encabezado no dice la unidad y NO se adivina.")
                print(f"  Se deja el valor tal cual (mediana "
                      f"{out.valor.abs().median():,.2f} por hora). Si el fichero viene en")
                print(f"  kWh, vuelve a cargarlo con --unidad kwh o el estudio saldra x1000.")
            unidad = "mwh"
        elif verbose:
            print(f"  unidad {unidad.upper()}, leida del encabezado")
    if unidad == "kwh":
        out["valor"] /= 1000.0

    out.attrs.update(filas_originales=n_bruto, duplicadas=dup,
                     huecos_rellenados=faltan, sin_arreglo=sin_arreglo,
                     unidad_origen=unidad)
    if verbose:
        print(f"  {len(out):,} horas · {out.dia.nunique():,} dias · "
              f"{out.dia.min():%Y-%m-%d} -> {out.dia.max():%Y-%m-%d}")
        if dup:
            print(f"  {dup} filas duplicadas promediadas (hora repetida de octubre)")
        if faltan:
            print(f"  {faltan} horas ausentes interpoladas dentro de su dia")
        if sin_arreglo:
            print(f"  AVISO: {sin_arreglo} horas sin arreglar (dias enteros vacios)")
    return out


def auditar(s: pd.DataFrame, verbose=True) -> dict:
    """Lo que hay que mirar antes de fiarse de una curva: cambios de hora y bisiestos."""
    dias = pd.DatetimeIndex(sorted(s.dia.unique()))
    anos = sorted({d.year for d in dias})
    inf = {"horas": len(s), "dias": len(dias), "anos": anos,
           "total_mwh": float(s.valor.sum()),
           "media_mwh_h": float(s.valor.mean()),
           "negativos": int((s.valor < 0).sum()),
           "ceros": int((s.valor == 0).sum())}

    filas = []
    for a in anos:
        d_a = dias[dias.year == a]
        completo = len(d_a) == (366 if pd.Timestamp(f"{a}-12-31").dayofyear == 366 else 365)
        # ultimo domingo de marzo y de octubre: los dias del cambio de hora
        mar = pd.date_range(f"{a}-03-01", f"{a}-03-31", freq="W-SUN")[-1]
        oct_ = pd.date_range(f"{a}-10-01", f"{a}-10-31", freq="W-SUN")[-1]
        bis = pd.Timestamp(f"{a}-02-29") if pd.Timestamp(f"{a}-12-31").dayofyear == 366 \
            else None
        filas.append({
            "año": a, "dias": len(d_a), "completo": completo,
            "bisiesto": bis is not None,
            "29-feb": "sí" if (bis is not None and bis in set(d_a)) else
                      ("FALTA" if bis is not None else "-"),
            "cambio_marzo": f"{mar:%d-%m}" if mar in set(d_a) else "FALTA",
            "cambio_octubre": f"{oct_:%d-%m}" if oct_ in set(d_a) else "FALTA",
            "MWh": round(s[s.dia.dt.year == a].valor.sum(), 1)})
    inf["por_ano"] = pd.DataFrame(filas)
    # dias con menos energia de la mitad de la mediana: casi siempre un dia a medio cargar
    por_dia = s.groupby("dia").valor.sum()
    inf["dias_sospechosos"] = int((por_dia < por_dia.median() * .5).sum())

    if verbose:
        print(f"\n  {inf['horas']:,} horas · {inf['dias']:,} dias · "
              f"{inf['total_mwh']:,.1f} MWh en total")
        print(f"  media {inf['media_mwh_h']:.4f} MWh/h · "
              f"{inf['negativos']} valores negativos · {inf['ceros']} ceros\n")
        print(inf["por_ano"].to_string(index=False))
        if (inf["por_ano"]["29-feb"] == "FALTA").any():
            print("\n  AVISO: falta el 29 de febrero en un año bisiesto.")
        if inf["dias_sospechosos"]:
            print(f"\n  AVISO: {inf['dias_sospechosos']} dias con menos de la mitad de la")
            print("  energia mediana. Suelen ser dias a medio volcar en el fichero de origen.")
        if not inf["por_ano"].completo.all():
            print("\n  AVISO: hay años incompletos. Si es a proposito, bien; si no, la")
            print("  forma normalizada saldra sesgada hacia los meses que si estan.")
    return inf


def a_forma(s: pd.DataFrame) -> pd.DataFrame:
    """Forma NORMALIZADA por (mes, tipo de dia, hora), con media 1.

    Se normaliza a proposito: la forma es forma y el tamaño va aparte. Asi el mismo perfil
    sirve para un cliente de 200 MWh/año y para uno de 2.000, y proyectar a veinte años es
    multiplicar en vez de volver a pedir el fichero.

    Se agrupa por (mes, laborable/finde, hora) y no por dia del año porque el dia del año no
    casa entre bisiestos y el dia de la semana si importa: un lunes de julio no se parece a
    un domingo de julio.
    """
    d = s.copy()
    d["mes"] = d.dia.dt.month
    d["tipo"] = np.where(d.dia.dt.dayofweek >= 5, "finde", "laborable")
    f = d.groupby(["mes", "tipo", "hora"]).valor.mean()
    return (f / d.valor.mean()).rename("pu").reset_index()


def proyectar(forma: pd.DataFrame, anual_mwh: float, desde, hasta,
              crecimiento_pct: float = 0.0, ano_base: int | None = None) -> pd.DataFrame:
    """Estira la forma al horizonte pedido, en la rejilla de 24 h/dia de la curva.

    El año bisiesto se resuelve solo: se recorre el calendario real y cada dia toma la forma
    de su (mes, tipo, hora). El 29 de febrero existe si el año lo tiene, y hereda la forma
    de un dia de febrero de su mismo tipo -- que es lo unico razonable, porque un 29 de
    febrero no tiene historico propio.

    El cambio de hora NO aparece: la rejilla es de 24 h/dia, la misma que la curva de
    precios. Ver la nota de cabecera.
    """
    dias = pd.date_range(desde, hasta, freq="D")
    ano_base = ano_base or dias[0].year
    m = forma.set_index(["mes", "tipo", "hora"]).pu

    fut = pd.DataFrame({"dia": np.repeat(dias, 24),
                        "hora": np.tile(np.arange(24), len(dias))})
    fut["mes"] = fut.dia.dt.month
    fut["tipo"] = np.where(fut.dia.dt.dayofweek >= 5, "finde", "laborable")
    pu = m.reindex(pd.MultiIndex.from_arrays([fut.mes, fut.tipo, fut.hora])).to_numpy()
    if np.isnan(pu).any():                 # combinacion que el historico no tenia
        pu = pd.Series(pu).fillna(pd.Series(pu).mean()).to_numpy()

    # la forma tiene media 1 sobre TODAS las horas, asi que el valor medio horario que
    # corresponde a `anual_mwh` es anual/8766 (media de año normal y bisiesto)
    base = anual_mwh / 8766.0
    factor = (1 + crecimiento_pct / 100) ** (fut.dia.dt.year - ano_base)
    fut["valor"] = base * pu * factor
    return fut[["dia", "hora", "valor"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("fichero")
    ap.add_argument("--hoja", help="hoja del Excel, si no es la primera")
    ap.add_argument("--unidad", default="auto", choices=["auto", "kwh", "mwh"])
    ap.add_argument("--auditar", action="store_true")
    ap.add_argument("--forma", help="guardar la forma normalizada en este CSV")
    a = ap.parse_args()

    print(f"\n  {Path(a.fichero).name}")
    s = cargar(a.fichero, a.hoja, a.unidad)
    if a.auditar:
        auditar(s)
    if a.forma:
        f = a_forma(s)
        f.to_csv(a.forma, index=False, float_format="%.6f")
        print(f"\n  forma normalizada ({len(f)} filas) en {a.forma}")
        piv = f.pivot_table(index="hora", columns="mes", values="pu")
        print(f"\n  perfil medio por hora y mes (p.u., media = 1)")
        print(piv.round(2).to_string())


if __name__ == "__main__":
    main()
