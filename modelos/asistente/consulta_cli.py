#!/usr/bin/env python
r"""
CLI de una sola consulta SQL de solo lectura, para depuracion desde Claude Code (16-sep-2026).

Reutiliza `consulta_sql_lectura` de herramientas.py tal cual -- la misma validacion (solo
SELECT/WITH, sin DDL/DML, solo las tablas de `_SQL_TABLAS_PERMITIDAS`) y la misma conexion de
solo lectura que ya usa el asistente en produccion. No añade ningun privilegio nuevo: es
exactamente lo que el asistente ya podria consultar, expuesto como comando de una linea para
poder probar herramientas nuevas contra datos reales sin pasar por el LLM.

Uso:
    python consulta_cli.py "SELECT * FROM bess_plan ORDER BY datetime DESC LIMIT 5"
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import herramientas as h


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Uso: consulta_cli.py \"<SQL de solo lectura>\""}))
        sys.exit(1)
    resultado = h.consulta_sql_lectura(sys.argv[1])
    print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
