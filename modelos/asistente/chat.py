r"""
TFM Energia UCM - Asistente LLM + herramientas (tool use), no RAG documental (30-ago-2026)

Envuelve las funciones deterministas de `herramientas.py` como tools de Claude (patron "tool
use" / function calling del SDK oficial de Anthropic: `@beta_tool` + `tool_runner`). El LLM
nunca inventa numeros -- entiende la pregunta, elige que herramienta llamar y con que
parametros, y redacta la respuesta a partir de lo que la herramienta devuelve.

Regla central del system prompt, la misma que ya quedo documentada en la nota 33: "prediccion"
solo existe para D+1 (prediccion_d_mas_1); cualquier otro horizonte se responde como
"referencia historica" (percentiles reales, precio_historico_percentiles), nunca disfrazado de
prediccion del modelo.

Modelo por defecto: claude-opus-5 (el que recomienda el SDK). Si el coste es una preocupacion
para las pruebas, se puede cambiar a "claude-haiku-4-5" pasando el parametro `modelo` -- esta
tarea (elegir una herramienta y redactar la respuesta) no necesita el modelo mas grande.

Uso:
    from modelos.asistente.chat import preguntar
    print(preguntar("¿Cuánto ha costado la luz históricamente los domingos de agosto?"))
"""

import json
import sys
from pathlib import Path

import anthropic
from anthropic import beta_tool

sys.path.append(str(Path(__file__).parent.parent.parent / "ingesta"))
sys.path.append(str(Path(__file__).parent))

from config import load_anthropic_key
import herramientas as _h

SYSTEM_PROMPT = """Eres el asistente del proyecto de prediccion de precio electrico y baterias (TFM UCM).

REGLA MAS IMPORTANTE, NUNCA LA ROMPAS: solo existe una "prediccion" real -- la del dia siguiente
(D+1), que sale de `prediccion_d_mas_1`. Para cualquier otro horizonte (una semana, un mes, un
año, un rango de años futuro) NUNCA respondas como si el modelo lo hubiera predicho -- usa
`precio_historico_percentiles` y presenta el resultado explicitamente como "esto es lo que ha
pasado historicamente en circunstancias parecidas", nunca como una prediccion.

Si `prediccion_d_mas_1` devuelve el campo `advertencia`, TRASLADA esa advertencia al usuario tal
cual, no la omitas -- significa que el sistema de produccion todavia no esta conectado a datos en
vivo.

Para preguntas sobre cuanto ganaria una bateria con ciertas caracteristicas, usa `simular_bateria`
-- es siempre un backtest sobre precio REAL ya ocurrido, dejalo claro en la respuesta.

Responde en español, con los numeros que las herramientas devuelven -- nunca inventes una cifra
que no venga de una llamada a herramienta."""


@beta_tool
def precio_historico_percentiles(hora: int | None = None, mes: int | None = None,
                                  dia_semana: int | None = None,
                                  anio_desde: int | None = None, anio_hasta: int | None = None) -> str:
    """Percentiles del precio REAL historico del mercado electrico español, filtrado por hora del
    dia, mes del año y/o dia de la semana. Es la herramienta de referencia historica -- usala para
    CUALQUIER pregunta sobre precios que no sea "mañana" (esa es prediccion_d_mas_1).

    Args:
        hora: Hora del dia en formato 24h, 0-23. Omitir para no filtrar por hora.
        mes: Mes del año, 1-12. Omitir para no filtrar por mes.
        dia_semana: Dia de la semana, 0=lunes, 6=domingo. Omitir para no filtrar.
        anio_desde: Año inicial del rango a considerar (inclusive). Omitir para no acotar.
        anio_hasta: Año final del rango a considerar (inclusive). Omitir para no acotar.
    """
    return json.dumps(_h.precio_historico_percentiles(hora, mes, dia_semana, anio_desde, anio_hasta),
                       ensure_ascii=False)


@beta_tool
def simular_bateria(potencia_mw: float, capacidad_mwh: float, eficiencia: float,
                     desde: str, hasta: str) -> str:
    """Simula cuanto habria ganado una bateria de red con las caracteristicas indicadas, operando
    sobre el precio REAL ya ocurrido entre dos fechas (backtest historico, no una proyeccion a
    futuro). Un ciclo de carga (en las horas mas baratas del dia) y descarga (en las mas caras)
    por dia.

    Args:
        potencia_mw: Potencia de la bateria en MW.
        capacidad_mwh: Capacidad de energia de la bateria en MWh.
        eficiencia: Eficiencia de ida y vuelta, entre 0 y 1 (ej. 0.9 para 90%).
        desde: Fecha de inicio del backtest, formato YYYY-MM-DD.
        hasta: Fecha de fin del backtest, formato YYYY-MM-DD.
    """
    return json.dumps(_h.simular_bateria(potencia_mw, capacidad_mwh, eficiencia, desde, hasta),
                       ensure_ascii=False)


@beta_tool
def precio_negativos(anio: int | None = None) -> str:
    """Cuenta cuantas horas de precio NEGATIVO ha habido en un año (y cual fue el minimo /
    mas negativo). Referencia historica, sobre precio real. En España el precio spot SI puede
    ser negativo (exceso de renovables) -- no es un error.

    Args:
        anio: Año a consultar. Omitir para usar el año en curso.
    """
    return json.dumps(_h.precio_negativos(anio), ensure_ascii=False)


@beta_tool
def prediccion_d_mas_1() -> str:
    """La UNICA prediccion real del proyecto: el precio que el modelo entrenado predice para las
    24 horas del dia siguiente. No acepta parametros -- siempre es "mañana" respecto a la fecha
    mas reciente que el pipeline de datos tiene disponible (puede traer una advertencia si esa
    fecha no es literalmente mañana, ver el campo `advertencia` de la respuesta)."""
    return json.dumps(_h.prediccion_d_mas_1(), ensure_ascii=False)


TOOLS = [precio_historico_percentiles, precio_negativos, simular_bateria, prediccion_d_mas_1]


def preguntar(pregunta: str, modelo: str = "claude-opus-5") -> str:
    """Hace una pregunta al asistente y devuelve su respuesta final en texto."""
    client = anthropic.Anthropic(api_key=load_anthropic_key())

    runner = client.beta.messages.tool_runner(
        model=modelo,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=[{"role": "user", "content": pregunta}],
    )

    ultimo = None
    for mensaje in runner:
        ultimo = mensaje
    if ultimo is None:
        return "(el asistente no devolvio respuesta)"
    return next((b.text for b in ultimo.content if b.type == "text"), "(sin texto en la respuesta)")


if __name__ == "__main__":
    preguntas_ejemplo = [
        "¿Cuánto ha costado la luz históricamente los domingos de agosto entre las 20h y las 21h?",
        "Tengo una batería de 2 MW y 4 MWh con 90% de eficiencia. ¿Cuánto habría ganado en 2023?",
        "¿Cuál es el precio previsto para mañana?",
    ]
    for p in preguntas_ejemplo:
        print(f"\n=== Pregunta: {p} ===")
        print(preguntar(p))
