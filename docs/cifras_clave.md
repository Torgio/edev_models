# Cifras clave del EDA

*Generado por `eda/figuras_memoria.py`. Son los números que van en el texto*
*de la memoria, no en las figuras.*

- **Precio medio · régimen apagón**: 14.19 EUR/MWh
- **Precio medio · régimen excepción ibérica**: 102.63 EUR/MWh
- **Precio medio · régimen normal**: 81.05 EUR/MWh
- **Asimetría del precio**: 1.19
- **Curtosis del precio**: 2.74
- **Rango del precio**: [-15, 700] EUR/MWh
- **Horas de precio negativo por año**: {2020: 0, 2021: 0, 2022: 0, 2023: 0, 2024: 247, 2025: 555, 2026: 665}
- **Amplitud del perfil horario por año**: {2020: 13.0, 2021: 36.3, 2022: 75.2, 2024: 57.1, 2025: 83.7, 2026: 103.3}
- **Hora local del mínimo por año**: {2020: 4, 2021: 4, 2022: 15, 2024: 15, 2025: 14, 2026: 13}
- **Autocorrelación horaria D-1**: 0.9107
- **Autocorrelación diaria D-1**: 0.9406
- **Mejor |r| sin fuga**: 0.8803 (commodities_gas_mibgas)
- **Mejor |r| con fuga**: 0.9979 (spot_pt_entsoe)
- **Ratio precio/gas por régimen**: {'apagón': 0.36, 'excepción ibérica': 1.74, 'normal': 2.58}
- **Mejor |d| utilizable**: 1.397 (solar prevista)
- **Mejor |d| total**: 2.197 (hidráulica)
- **AUC por driver para 'hora cara'**: {'demanda prevista': np.float64(0.7078), 'eólica prevista': np.float64(0.5107), 'solar prevista': np.float64(0.6636), 'gas': np.float64(0.5476), 'demanda real': np.float64(0.7076), 'eólica real': np.float64(0.5207), 'solar FV real': np.float64(0.6451), 'hidráulica': np.float64(0.8373), 'ciclo combinado': np.float64(0.6843)}
- **% horas acopladas con Portugal**: {2020: 95.9, 2021: 97.4, 2022: 97.1, 2023: 94.7, 2024: 94.1, 2025: 89.5, 2026: 84.5}
- **% horas acopladas con Francia**: {2020: 39.3, 2021: 34.8, 2022: 26.7, 2023: 32.8, 2024: 32.7, 2025: 36.3, 2026: 29.2}
- **Amplitud de la correlación entre horas**: {'demanda prevista': 0.209, 'eólica prevista': 0.233, 'solar prevista': 0.438, 'gas': 0.0, 'demanda real': 0.223, 'eólica real': 0.283, 'solar FV real': 0.391, 'hidráulica': 0.41, 'ciclo combinado': 0.047}
- **Desfase óptimo en días**: {'gas': 0, 'temperatura': 7, 'eólica real': 0, 'solar FV real': -5, 'hidráulica': -5}
- **Error de las previsiones (MW)**: {'demanda': {'MAE': np.float64(256.8), 'sesgo': np.float64(0.8)}, 'eólica': {'MAE': np.float64(771.7), 'sesgo': np.float64(128.5)}, 'solar FV': {'MAE': np.float64(547.4), 'sesgo': np.float64(197.8)}}
- **% medio de la demanda cubierto por solar**: {2020: 6.6, 2021: 8.5, 2022: 11.4, 2023: 15.7, 2024: 18.7, 2025: 20.6, 2026: 25.8}
- **Correlación entre los dos lados del balance**: 0.9984
- **Offset gas ENTSO-E vs suma ESIOS**: {2020: -806.0, 2021: -558.0, 2022: -341.0, 2023: -257.0, 2024: -222.0, 2025: -177.0, 2026: -192.0}
- **Correlación gas ENTSO-E vs suma**: 0.99627
- **Ingreso diario con previsión perfecta**: 244.65 EUR/MWh
- **Ingreso diario con regla horaria fija**: 175.66 EUR/MWh
- **% del techo que captura la regla fija**: 71.8%
- **Horas de descarga de la regla fija**: [19, 20, 21, 22]
- **Horas de carga de la regla fija**: [4, 14, 15, 16]
- **Acierto de la regla fija**: 62.5% frente al 16.7% del azar
