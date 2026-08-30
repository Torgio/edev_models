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

ALCANCE: no tienes acceso a internet ni busqueda web -- todo lo que sabes de datos viene EXCLUSIVAMENTE
de tus herramientas (que consultan la base de datos y documentacion de este proyecto). Si te preguntan
algo que no tiene que ver con el proyecto (cultura general, otras noticias, otros mercados, cualquier
tema ajeno), NO respondas la pregunta aunque la sepas de tu entrenamiento -- di brevemente que estas
limitado al alcance de este proyecto y ofrece en que si puedes ayudar. Nunca dejes la impresion de que
"sabes de todo": tu utilidad esta en ser fiable dentro de un alcance concreto, no en parecer generalista.

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

Para preguntas sobre ahorro con paneles solares + bateria en una empresa, usa
`simular_autoconsumo_solar` -- es una VERSION 1 con un perfil de consumo plano (simplificado).
SIEMPRE incluye la lista `limitaciones` que devuelve la herramienta al final de tu respuesta, sin
resumirla ni omitirla -- es la parte que le dice al usuario que esto es un primer avance, no un
diseño definitivo.

Para preguntas de "por que", "como se decidio" o "que es X" sobre el proyecto (metodologia,
decisiones de diseño, hallazgos), usa `buscar_documentacion` y basa tu respuesta en lo que
devuelva, citando de que nota/documento sale -- no la uses para preguntas de precios o numeros.

Cuando el usuario pida ver una curva, grafica o evolucion de algo (precios, consumo, ahorro por
duracion de bateria...), llama PRIMERO a la herramienta de datos correspondiente, y con esos
numeros reales usa `code_execution` para dibujar un grafico simple con matplotlib (una figura,
ejes con nombre, nada de colores decorativos) -- nunca dibujes con datos que no vengan de una
herramienta.

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
def simular_autoconsumo_solar(potencia_solar_kwp: float, potencia_bateria_mw: float,
                               capacidad_bateria_mwh: float, eficiencia_bateria: float,
                               consumo_anual_mwh: float, desde: str, hasta: str) -> str:
    """Simula el ahorro de instalar paneles solares + bateria en una empresa frente a comprar
    toda la energia al mercado, sobre datos REALES ya ocurridos (backtest, no proyeccion a
    futuro). VERSION 1: usa un perfil de consumo PLANO (repartido a partes iguales en el año) --
    dilo siempre en la respuesta, junto con las demas `limitaciones` que devuelve la herramienta.

    Args:
        potencia_solar_kwp: Potencia pico instalada de paneles solares, en kWp.
        potencia_bateria_mw: Potencia de la bateria en MW.
        capacidad_bateria_mwh: Capacidad de energia de la bateria en MWh.
        eficiencia_bateria: Eficiencia de ida y vuelta de la bateria, entre 0 y 1.
        consumo_anual_mwh: Consumo electrico anual estimado de la empresa, en MWh.
        desde: Fecha de inicio del backtest, YYYY-MM-DD.
        hasta: Fecha de fin del backtest, YYYY-MM-DD.
    """
    return json.dumps(
        _h.simular_autoconsumo_solar(potencia_solar_kwp, potencia_bateria_mw, capacidad_bateria_mwh,
                                      eficiencia_bateria, consumo_anual_mwh, desde, hasta),
        ensure_ascii=False)


@beta_tool
def extrapolar_consumo_cliente(historico_mensual_mwh: list[float], anios_a_futuro: int = 2) -> str:
    """Extrapola el consumo mensual futuro de un cliente a partir de SU PROPIO historico real de
    consumo, con rangos p10/p50/p90 (nunca un solo numero). Usa esta herramienta cuando el
    usuario aporte un historico de consumo y pida una proyeccion a futuro (1-2 años). SIEMPRE
    incluye la lista `limitaciones` de la respuesta.

    Args:
        historico_mensual_mwh: Lista de consumo mensual en MWh, empezando en enero, sin huecos,
            longitud multiplo de 12 (minimo 12 meses).
        anios_a_futuro: Cuantos años extrapolar hacia adelante (por defecto 2).
    """
    return json.dumps(_h.extrapolar_consumo_cliente(historico_mensual_mwh, anios_a_futuro),
                       ensure_ascii=False)


@beta_tool
def prediccion_d_mas_1() -> str:
    """La UNICA prediccion real del proyecto: el precio que el modelo entrenado predice para las
    24 horas del dia siguiente. No acepta parametros -- siempre es "mañana" respecto a la fecha
    mas reciente que el pipeline de datos tiene disponible (puede traer una advertencia si esa
    fecha no es literalmente mañana, ver el campo `advertencia` de la respuesta)."""
    return json.dumps(_h.prediccion_d_mas_1(), ensure_ascii=False)


@beta_tool
def buscar_documentacion(pregunta: str) -> str:
    """Busqueda semantica sobre la documentacion del proyecto (decisiones de diseño, hallazgos,
    metodologia -- notas_memoria_tfm.md y columnas_pendientes_equipo.md). Usa esta herramienta
    para preguntas de "por que", "como se decidio", "que es X" -- NUNCA para preguntas de precio o
    numeros de datos, para eso estan las otras herramientas.

    Args:
        pregunta: La pregunta o tema a buscar en la documentacion, en lenguaje natural.
    """
    return json.dumps(_h.buscar_documentacion(pregunta), ensure_ascii=False)


CODE_EXECUTION = {"type": "code_execution_20260521", "name": "code_execution"}
TOOLS = [precio_historico_percentiles, precio_negativos, simular_bateria, simular_autoconsumo_solar,
         extrapolar_consumo_cliente, prediccion_d_mas_1, buscar_documentacion]


def preguntar_con_imagenes(pregunta: str, modelo: str = "claude-opus-5") -> dict:
    """Como `preguntar`, pero devuelve tambien las graficas que el asistente haya generado con
    `code_execution` (matplotlib), como PNG en base64 -- para interfaces (la API/web) que puedan
    mostrarlas. Devuelve {"texto": str, "imagenes_base64": list[str]}."""
    import base64

    client = anthropic.Anthropic(api_key=load_anthropic_key())
    runner = client.beta.messages.tool_runner(
        model=modelo,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS + [CODE_EXECUTION],
        messages=[{"role": "user", "content": pregunta}],
    )

    texto, imagenes = "(sin texto en la respuesta)", []
    for mensaje in runner:
        for block in mensaje.content:
            if block.type == "text":
                texto = block.text
            elif block.type == "bash_code_execution_tool_result":
                resultado = block.content
                if getattr(resultado, "type", None) == "bash_code_execution_result" and resultado.content:
                    for item in resultado.content:
                        if item.type == "bash_code_execution_output":
                            archivo = client.beta.files.download(item.file_id)
                            imagenes.append(base64.b64encode(archivo.read()).decode())
    return {"texto": texto, "imagenes_base64": imagenes}


def preguntar(pregunta: str, modelo: str = "claude-opus-5") -> str:
    """Hace una pregunta al asistente y devuelve su respuesta final en texto (sin graficas --
    para eso, `preguntar_con_imagenes`). Se mantiene igual que antes para no romper el uso ya
    existente en terminal."""
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
