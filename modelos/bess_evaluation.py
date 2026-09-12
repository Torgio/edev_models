"""Motor compartido de arbitraje: potencias AC, SOC interno y horizonte cerrado.

MILP: carga y descarga mutuamente excluyentes incluso con precios negativos.
El limite de ciclos es energia interna descargada / capacidad; no obliga a operar.
No es un estudio de inversion: los costes por defecto son cero y quedan declarados.
"""
from dataclasses import dataclass, asdict

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class Battery:
    potencia_mw: float = 1.0
    capacidad_mwh: float = 2.0
    eficiencia: float = 0.90
    ciclos_max: float = 1.0
    soc_inicial_mwh: float = 0.0
    soc_final_mwh: float = 0.0
    coste_descarga_eur_mwh: float = 0.0
    paso_h: float = 1.0

    def __post_init__(self):
        if not all(np.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Parametros BESS no finitos")
        if self.potencia_mw <= 0 or self.capacidad_mwh <= 0 or not 0 < self.eficiencia <= 1:
            raise ValueError("Potencia, capacidad o eficiencia invalidas")
        if self.paso_h <= 0 or self.ciclos_max < 0 or self.coste_descarga_eur_mwh < 0:
            raise ValueError("Paso, ciclos o coste invalidos")
        if not all(0 <= x <= self.capacidad_mwh for x in (self.soc_inicial_mwh, self.soc_final_mwh)):
            raise ValueError("SOC fuera de capacidad")

    def metadata(self):
        return {**asdict(self), "version": "bess_fisico_v2", "horizonte": "D+1",
                "regla": "MILP cronologico; sin carga/descarga simultanea; SOC final fijado",
                "potencias": "AC", "ciclos_regla": "MWh internos descargados / capacidad",
                "costes": "solo coste_descarga_eur_mwh; sin peajes ni O&M"}


DEFAULT = Battery()
SIMULADOR = DEFAULT.metadata()


def _prices(values):
    p = np.asarray(values, dtype=float)
    if p.ndim != 1 or not len(p) or not np.isfinite(p).all():
        raise ValueError("Se necesita una curva completa y finita")
    return p


def validar_plan(plan, bat=DEFAULT):
    c, d, soc = (_prices(plan[k]) for k in ("carga", "descarga", "soc"))
    if not (len(c) == len(d) == len(soc)):
        raise ValueError("Longitudes de plan distintas")
    tol = 1e-6
    eta = np.sqrt(bat.eficiencia)
    expected = bat.soc_inicial_mwh + np.cumsum((eta*c - d/eta) * bat.paso_h)
    if (np.min(c) < -tol or np.min(d) < -tol or np.max(c) > bat.potencia_mw + tol
            or np.max(d) > bat.potencia_mw + tol or np.any((c > tol) & (d > tol))
            or np.min(soc) < -tol or np.max(soc) > bat.capacidad_mwh + tol
            or not np.allclose(soc, expected, atol=tol, rtol=0)
            or abs(soc[-1] - bat.soc_final_mwh) > tol
            or d.sum()*bat.paso_h/eta > bat.ciclos_max*bat.capacidad_mwh + tol):
        raise ValueError("Plan BESS fisicamente invalido")


def optimizar(precios, bat=DEFAULT):
    p = _prices(precios)
    n, eta, dt = len(p), np.sqrt(bat.eficiencia), bat.paso_h
    # Variables: carga AC | descarga AC | SOC interno | modo de carga binario.
    obj = np.r_[p*dt, (-p + bat.coste_descarga_eur_mwh)*dt, np.zeros(2*n)]
    balance = np.zeros((n+1, 4*n))
    for t in range(n):
        balance[t, t], balance[t, n+t], balance[t, 2*n+t] = -eta*dt, dt/eta, 1
        if t:
            balance[t, 2*n+t-1] = -1
    balance[n, 3*n-1] = 1
    rhs = np.zeros(n+1)
    rhs[0], rhs[-1] = bat.soc_inicial_mwh, bat.soc_final_mwh
    limits = np.zeros((2*n+1, 4*n))
    for t in range(n):
        limits[t, t], limits[t, 3*n+t] = 1, -bat.potencia_mw
        limits[n+t, n+t], limits[n+t, 3*n+t] = 1, bat.potencia_mw
    limits[-1, n:2*n] = dt/eta
    upper = np.r_[np.zeros(n), np.full(n, bat.potencia_mw), bat.ciclos_max*bat.capacidad_mwh]
    result = milp(obj, integrality=np.r_[np.zeros(3*n), np.ones(n)],
                  bounds=Bounds(np.zeros(4*n), np.r_[np.full(2*n, bat.potencia_mw),
                                np.full(n, bat.capacidad_mwh), np.ones(n)]),
                  constraints=[LinearConstraint(balance, rhs, rhs),
                               LinearConstraint(limits, -np.inf, upper)],
                  options={"time_limit": 30, "mip_rel_gap": 1e-8})
    if not result.success or result.x is None:
        raise RuntimeError(f"BESS no ha alcanzado el optimo: {result.message}")
    plan = dict(carga=result.x[:n], descarga=result.x[n:2*n], soc=result.x[2*n:3*n])
    validar_plan(plan, bat)
    return plan


def valorar(plan, precios, bat=DEFAULT):
    validar_plan(plan, bat)
    p = _prices(precios)
    if len(p) != len(plan["carga"]):
        raise ValueError("Plan y precios deben cubrir los mismos periodos")
    return ((np.asarray(plan["descarga"])-np.asarray(plan["carga"]))*p
            - np.asarray(plan["descarga"])*bat.coste_descarga_eur_mwh)*bat.paso_h


def ciclos(plan, bat=DEFAULT):
    return float(np.sum(plan["descarga"])*bat.paso_h/np.sqrt(bat.eficiencia)/bat.capacidad_mwh)
