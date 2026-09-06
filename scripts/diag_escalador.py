"""¿Por que el escalador de train cambia entre nucleo y produccion?

LO QUE YA SE DESCARTO
Las dos matrices son identicas dentro de train: 0 de 129 columnas cambian, tanto en CSV
como en parquet. Y `preparar_tensores` ajusta los escaladores SOLO sobre train
(`Escalador().fit(X_enc[tr])`). Asi que la diferencia tiene que estar en que `tr`
seleccione ventanas distintas, o en que el encoder de esas ventanas salga distinto.

Esto compara las dos preparaciones paso a paso hasta dar con la primera diferencia.

    python scripts/diag_escalador.py
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "scripts"))
from preparar_tensores import preparar   # noqa: E402


def main():
    A = preparar("nucleo", verbose=True)
    B = preparar("produccion", verbose=True)

    print(f"\ndias con ventana valida : nucleo {len(A.fechas):,} · produccion {len(B.fechas):,}")
    print(f"dias marcados train     : nucleo {int(A.tr.sum()):,} · produccion {int(B.tr.sum()):,}")

    fa, fb = set(map(str, A.fechas[A.tr])), set(map(str, B.fechas[B.tr]))
    print(f"dias de train solo en nucleo    : {len(fa - fb)}  {sorted(fa - fb)[:5]}")
    print(f"dias de train solo en produccion: {len(fb - fa)}  {sorted(fb - fa)[:5]}")

    if fa != fb:
        print("\n>>> CAUSA: los dos tensores NO usan los mismos dias de train.")
        print("    El escalador se ajusta sobre conjuntos distintos, de ahi la diferencia.")
        return

    print("\nlos dias de train coinciden; comparando el encoder de esas ventanas...")
    ia = np.argsort(A.fechas[A.tr]); ib = np.argsort(B.fechas[B.tr])
    Xa, Xb = A.X_enc[A.tr][ia], B.X_enc[B.tr][ib]
    print(f"  forma: {Xa.shape} vs {Xb.shape}")
    if Xa.shape != Xb.shape:
        print("\n>>> CAUSA: el encoder tiene otra forma (numero de canales o de pasos).")
        return
    d = np.abs(Xa - Xb)
    print(f"  diferencia maxima en el encoder ya escalado: {d.max():.6f}")
    peor = np.unravel_index(np.argmax(d), d.shape)
    print(f"  peor posicion: ventana {peor[0]}, paso {peor[1]}, canal {peor[2]}"
          f" ({A.canales[peor[2]] if peor[2] < len(A.canales) else '?'})")
    print("\n>>> Si esto es ~0, la diferencia entra DESPUES: mira como se calcula T.esc.")


if __name__ == "__main__":
    main()
