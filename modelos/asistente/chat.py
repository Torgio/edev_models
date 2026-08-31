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

Modelo: se elige con `MODELO_POR_DEFECTO`, un solo sitio para cambiarlo (antes estaba repetido
como valor por defecto en cada funcion). Precios de referencia (30-ago-2026, EUR/1M tokens
entrada+salida): Opus 5 ~5+25, Sonnet 5 ~2+10, Haiku 4.5 ~1+5 -- para esta tarea (elegir que
herramienta llamar y redactar la respuesta con el resultado) no hace falta el modelo mas grande;
Haiku 4.5 es el mas barato y normalmente basta. Cambiar el modelo NO afecta a que numeros salen
(esos los da siempre `herramientas.py`, nunca el LLM) -- solo a que tan bien elige la herramienta
y que tan natural redacta, asi que conviene probar unas cuantas preguntas tipicas antes de dejarlo
como definitivo.

Historial de conversaciones: cada llamada a `preguntar`/`preguntar_con_imagenes` queda registrada
en `historial.jsonl` (una linea JSON por pregunta: fecha, modelo, pregunta, respuesta, tokens).
Es local, no se sube a git (ver .gitignore) -- para revisarlo desde terminal, usar
`ver_historial.py`. La consola de Anthropic (Console) NO sirve para esto: su pestaña de Usage
muestra tokens y coste agregado por clave/modelo, pero no el contenido de las preguntas ni
respuestas -- por diseño de privacidad, Anthropic no expone el texto de las llamadas API en la
consola.

Uso:
    from modelos.asistente.chat import preguntar
    print(preguntar("¿Cuánto ha costado la luz históricamente los domingos de agosto?"))
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from anthropic import beta_tool

sys.path.append(str(Path(__file__).parent.parent.parent / "ingesta"))
sys.path.append(str(Path(__file__).parent))

from config import load_anthropic_key
import herramientas as _h

MODELO_POR_DEFECTO = "claude-opus-5"
HISTORIAL_PATH = Path(__file__).parent / "historial.jsonl"


def _registrar_historial(pregunta: str, texto: str, modelo: str, tokens_entrada: int,
                          tokens_salida: int, n_imagenes: int = 0) -> None:
    """Anade una linea al historial local (JSON Lines, un registro por pregunta). No falla la
    conversacion si el registro falla (ej. disco lleno) -- es una conveniencia para depurar/mostrar
    el uso, no una parte critica del asistente."""
    try:
        registro = {
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "modelo": modelo,
            "pregunta": pregunta,
            "respuesta": texto,
            "tokens_entrada": tokens_entrada,
            "tokens_salida": tokens_salida,
            "n_imagenes": n_imagenes,
        }
        with open(HISTORIAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass

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

Para VER los precios en crudo de un rango corto ("los precios de hoy por hora", "la tabla/
evolucion de esta semana", "el precio de ayer"), usa `precio_tabla_horaria` -- no
`precio_historico_percentiles`, esa resume/filtra, no da el detalle hora a hora. No digas que no
puedes mostrar esto, el dato SI esta disponible con esta herramienta (limite 500 horas).

Para preguntas de "cuantas horas negativas", "cual fue el minimo" (un numero resumen), usa
`precio_negativos`. Para "lista/tabla/grafica de las horas o dias con los precios mas
negativos" (el detalle, no el resumen), usa `precio_horas_negativas` -- no digas que no puedes
mostrar esto, el dato SI esta disponible con esta herramienta.

Para preguntas sobre cuanto ganaria una bateria con ciertas caracteristicas, usa `simular_bateria`
-- es siempre un backtest sobre precio REAL ya ocurrido, dejalo claro en la respuesta.

Para preguntas sobre el precio a MESES O AÑOS vista (2027, "dentro de una decada", curvas a
2046...), usa `precio_futuro_curva`, NUNCA `precio_historico_percentiles` -- esa ultima es solo
para patrones YA OCURRIDOS, no para proyectar. `precio_futuro_curva` es la metodologia del
equipo validada por backtest; sigue siendo un ESCENARIO con supuestos, no una prediccion
determinista -- traslada siempre el campo `advertencia` si aparece, igual que con
`prediccion_d_mas_1`.

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
def precio_tabla_horaria(desde: str, hasta: str) -> str:
    """Tabla de precio REAL hora a hora, sin resumir. Usa esta herramienta -- no
    `precio_historico_percentiles` -- cuando pidan VER los precios en crudo de un rango corto:
    "los precios de hoy", "la tabla/evolucion de esta semana", "el precio de ayer por horas".
    Limite de 500 horas (~3 semanas); para periodos mas largos usa percentiles en su lugar.

    Args:
        desde: Fecha de inicio, YYYY-MM-DD.
        hasta: Fecha de fin, YYYY-MM-DD (inclusive). Para "hoy", pon la misma fecha en ambas.
    """
    return json.dumps(_h.precio_tabla_horaria(desde, hasta), ensure_ascii=False)


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
def precio_horas_negativas(anio: int | None = None, limite: int = 100) -> str:
    """Lista detallada (dia, hora, precio) de las horas de precio NEGATIVO de un año, de mas a
    menos negativa. Usa esta herramienta -- no `precio_negativos` -- cuando pidan un LISTADO,
    tabla o grafica de las horas/dias con los precios mas negativos, no solo el conteo o el
    minimo absoluto.

    Args:
        anio: Año a consultar. Omitir para usar el año en curso.
        limite: Cuantas horas devolver como maximo (100 por defecto, tope duro 500).
    """
    return json.dumps(_h.precio_horas_negativas(anio, limite), ensure_ascii=False)


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
def precio_futuro_curva(desde: str, hasta: str, nivel_por_anio: dict[int, float] | None = None) -> str:
    """Curva de precio a largo plazo (meses o años vista, incluidas decadas) -- metodologia del
    equipo validada por backtest (scripts/curva_precios.py), no percentiles historicos simples.
    Usa esta herramienta para "precio en 2030", "curva hasta 2046", "como evolucionara el
    precio" -- NUNCA `precio_historico_percentiles` para horizontes futuros largos, esa es solo
    para patrones YA OCURRIDOS. Sigue siendo un ESCENARIO, no una prediccion determinista --
    dejalo claro en la respuesta, sobre todo el campo `advertencia` si aparece.

    Args:
        desde: Fecha de inicio, YYYY-MM-DD.
        hasta: Fecha de fin, YYYY-MM-DD.
        nivel_por_anio: Opcional -- nivel de precio medio anual en EUR/MWh, con solo unas pocas
            anclas (2-4 años, ej. {2027: 66, 2030: 60, 2046: 52}) -- el resto se interpola
            automaticamente, no hace falta dar todos los años. Si el usuario no da ninguno,
            omitir este parametro -- la herramienta avisa que usara un marcador de posicion.
    """
    return json.dumps(_h.precio_futuro_curva(desde, hasta, nivel_por_anio), ensure_ascii=False)


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
TOOLS = [precio_historico_percentiles, precio_tabla_horaria, precio_negativos, precio_horas_negativas,
         simular_bateria, simular_autoconsumo_solar, precio_futuro_curva, extrapolar_consumo_cliente,
         prediccion_d_mas_1, buscar_documentacion]


def preguntar_con_imagenes(pregunta: str, modelo: str = MODELO_POR_DEFECTO) -> dict:
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
    tokens_entrada = tokens_salida = 0
    for mensaje in runner:
        tokens_entrada += mensaje.usage.input_tokens
        tokens_salida += mensaje.usage.output_tokens
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
    _registrar_historial(pregunta, texto, modelo, tokens_entrada, tokens_salida, len(imagenes))
    return {"texto": texto, "imagenes_base64": imagenes}


def preguntar(pregunta: str, modelo: str = MODELO_POR_DEFECTO) -> str:
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
    tokens_entrada = tokens_salida = 0
    for mensaje in runner:
        ultimo = mensaje
        tokens_entrada += mensaje.usage.input_tokens
        tokens_salida += mensaje.usage.output_tokens
    if ultimo is None:
        _registrar_historial(pregunta, "(el asistente no devolvio respuesta)", modelo, tokens_entrada, tokens_salida)
        return "(el asistente no devolvio respuesta)"
    texto = next((b.text for b in ultimo.content if b.type == "text"), "(sin texto en la respuesta)")
    _registrar_historial(pregunta, texto, modelo, tokens_entrada, tokens_salida)
    return texto


if __name__ == "__main__":
    preguntas_ejemplo = [
        "¿Cuánto ha costado la luz históricamente los domingos de agosto entre las 20h y las 21h?",
        "Tengo una batería de 2 MW y 4 MWh con 90% de eficiencia. ¿Cuánto habría ganado en 2023?",
        "¿Cuál es el precio previsto para mañana?",
    ]
    for p in preguntas_ejemplo:
        print(f"\n=== Pregunta: {p} ===")
        print(preguntar(p))
