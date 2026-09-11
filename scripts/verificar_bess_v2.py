"""Verificar el ultimo plan v2 y su liquidacion, exclusivamente en solo lectura.

Salida 0: verificado; 3: pendiente de plan/precio/resultado; 1: inconsistencia.
"""
import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO=Path(__file__).resolve().parents[1]
for folder in ('scripts','modelos','ingesta'):
    sys.path.insert(0,str(REPO/folder))
from guardar_predicciones import conexion
from evaluar_diario import curva_real, dia_de
from tiempo_mercado import indice_local, dia_completo, cierre_prediccion, naive_local
from bess_evaluation import SIMULADOR, validar_plan


def verificar(con, day=None):
    with con.cursor() as cur:
        if day is None:
            cur.execute("""SELECT max((datetime AT TIME ZONE 'Europe/Madrid')::date)
                           FROM bess_plan WHERE simulador->>'version'='bess_fisico_v2'""")
            day=cur.fetchone()[0]
    if day is None:
        print('PENDIENTE: todavia no se ha guardado un plan v2.')
        return 3
    plans=pd.read_sql("""SELECT datetime AS ts, model, carga_mw, descarga_mw, soc_mwh,
                               simulador, updated_at FROM bess_plan
                        WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date=%s
                          AND simulador->>'version'='bess_fisico_v2'
                        ORDER BY model, datetime""", con, params=(day,))
    if plans.empty:
        print(f'PENDIENTE: sin plan v2 para {day}.')
        return 3
    prices=curva_real(con,day-pd.Timedelta(days=1),day)
    results=pd.read_sql('SELECT * FROM bess_result WHERE fecha_objetivo=%s',con,params=(day,))
    plans=plans.assign(ts=indice_local(plans.ts))
    pending=False
    for model, saved in plans.groupby('model'):
        saved=saved.sort_values('ts')
        if (not dia_completo(saved.ts) or not saved.simulador.map(lambda x:x==SIMULADOR).all()
                or not (pd.to_datetime(saved.updated_at,utc=True)<cierre_prediccion(day)).all()):
            raise ValueError(f'{model}: cobertura, supuestos o corte temporal incorrectos')
        plan={k:saved[c].to_numpy(dtype=float) for k,c in
              [('carga','carga_mw'),('descarga','descarga_mw'),('soc','soc_mwh')]}
        validar_plan(plan)
        g=pd.DataFrame({'ts':saved.ts.to_numpy(),'real':prices.reindex(pd.DatetimeIndex(saved.ts)).to_numpy()})
        expected=dia_de(g,naive_local(prices,g.ts).to_numpy(),plan)
        print(f'PLAN OK: {day} {model}, {len(saved)} periodos, SOC fisico y escritura previa al corte.')
        got=results[results.model==model]
        if expected is None or got.empty:
            print(f'PENDIENTE: precio completo o liquidacion de {model}.')
            pending=True
            continue
        if len(got)!=1 or got.iloc[0].simulador!=SIMULADOR:
            raise ValueError(f'{model}: resultado duplicado o de otra version')
        for key,value in expected.items():
            actual=got.iloc[0][key]
            if value is None:
                valid=pd.isna(actual)
            else:
                valid=pd.notna(actual) and np.isclose(float(actual),value,atol=1e-5,rtol=1e-7)
            if not valid:
                raise ValueError(f'{model}: {key} no coincide con el plan guardado')
        print(f'LIQUIDACION OK: {model}, {expected["ingreso_eur"]:.2f} EUR a precio publicado.')
    return 3 if pending else 0


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dia',type=lambda x:pd.Timestamp(x).date())
    args=ap.parse_args()
    con=conexion()
    con.set_session(readonly=True,isolation_level='REPEATABLE READ')
    try:
        return verificar(con,args.dia)
    finally:
        con.rollback()
        con.close()

if __name__=='__main__':
    sys.exit(main())
