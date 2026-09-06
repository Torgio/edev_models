"""
Permite usar preparar_tensores.preparar() con matriz_nucleo_tensores sin
modificar preparar_tensores.py (copia deliberada del notebook del equipo --
"si divergen, manda el notebook").

EL PROBLEMA: _clasificar() reparte las columnas de la matriz en listas
(cols_dec, cols_prog, cols_dm1, cols_est) mirando patrones de NOMBRE fijos
(termina en "_meteo", empieza con "ree_ntc_", es exactamente "es_esios_D",
etc.). Ninguno de esos patrones reconoce "tensor_emb_*" -- esas 32 columnas
quedan fuera de las cuatro listas, y como preparar() arma X_enc/X_dec/X_est
EXCLUSIVAMENTE con lo que está adentro de esas listas, el embedding queda
cargado en memoria pero nunca llega a ningún tensor de entrada del modelo.
Descarte silencioso, sin ningún error que avise.

LA SOLUCION: en tiempo de ejecucion, se reemplaza (monkeypatch) la funcion
_clasificar del modulo preparar_tensores por una version que hace exactamente
lo mismo y ademas agrega las columnas tensor_emb_* a cols_dec -- el mismo
balde que *_meteo, la decision ya tomada de que el embedding es "clima de
D+1 conocido de antemano". El archivo preparar_tensores.py en disco nunca
se toca; el reemplazo se deshace automaticamente al terminar.

Uso:
    from preparar_con_embeddings import preparar_con_embeddings
    T = preparar_con_embeddings(matriz="nucleo_tensores")
    # T.X_dec ahora incluye las 32 columnas tensor_emb_ junto con *_meteo
"""

import preparar_tensores as pt

_clasificar_original = pt._clasificar


def _clasificar_con_embeddings(df, multicanal=True):
    cols_dec, cols_prog, cols_dm1, cols_est, cols_est_media = _clasificar_original(df, multicanal)
    cols_tensor_emb = [c for c in df.columns if c.startswith("tensor_emb_")]
    if not cols_tensor_emb:
        raise RuntimeError(
            "No se encontro ninguna columna tensor_emb_* en la matriz -- "
            "¿es realmente matriz_nucleo_tensores, o la matriz original sin embeddings?"
        )
    return cols_dec + cols_tensor_emb, cols_prog, cols_dm1, cols_est, cols_est_media


def preparar_con_embeddings(matriz="nucleo_tensores", **kwargs):
    """Igual que preparar_tensores.preparar(), pero con tensor_emb_* incluidas
    en cols_dec. Restaura _clasificar original al terminar, incluso si falla."""
    pt._clasificar = _clasificar_con_embeddings
    try:
        return pt.preparar(matriz=matriz, **kwargs)
    finally:
        pt._clasificar = _clasificar_original


if __name__ == "__main__":
    T = preparar_con_embeddings()
    print(f"\nColumnas en cols_dec (deberian incluir 32 tensor_emb_ + las *_meteo de siempre):")
    tensor_emb_en_dec = [c for c in T.cols_dec if c.startswith("tensor_emb_")]
    print(f"  tensor_emb_* encontradas en X_dec: {len(tensor_emb_en_dec)} (esperado: 32)")
    print(f"  X_dec shape: {T.X_dec.shape}")
