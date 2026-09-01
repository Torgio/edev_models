# Pendiente · módulo de estudio de batería

Cosas acordadas y no hechas. Cada una con lo que ya está resuelto, para no volver a
empezar de cero.

---

## 1 · Que el modelo busque el tamaño, no solo lo evalúe

**Lo que hay hoy.** El usuario escribe una potencia y una duración, y el motor le dice si
esa batería concreta sale a cuenta. Si no sale, no le dice cuál sí.

**Lo que se quiere.** Que devuelva el tamaño óptimo, o mejor, el **rango de tamaños que
interesan** — porque el óptimo suele ser plano y decirle a alguien «140 kW» cuando de 110
a 180 va casi igual es una precisión falsa.

### Por qué es más fácil de lo que parece

El VAN en función de la energía instalada tiene una forma muy concreta:

```
VAN(E) = −c·E + f(E)
```

`c` es el precio por MWh instalado, así que el coste es **lineal**. Y `f(E)`, el valor que
la batería captura, es **cóncavo**: los huecos de precio buenos del día son finitos, y una
batería mayor solo puede coger los peores. Está medido, no supuesto — al doblar la potencia
el margen se multiplicó por 2,000 exactamente, el tope teórico, y los ciclos por día bajaron
de 0,34 a 0,25.

Lineal menos cóncava da una función **unimodal**: baja, sube y vuelve a bajar, con un solo
máximo. Eso significa que no hace falta barrer una rejilla:

- **búsqueda ternaria** sobre `E`: unas 10 resoluciones para acotar el óptimo, frente a las
  30 o 40 de un barrido decente;
- el **rango** sale del mismo trabajo: los tamaños cuyo VAN queda dentro de, digamos, el
  90 % del máximo. Es más honesto que un número y no cuesta nada más.

### El atajo que probablemente sobra

El LP ya calcula, sin pedírselo, el **precio sombra de la cota superior del estado de
carga**: cuánto mejoraría el objetivo con un MWh más de batería. Es la derivada de `f(E)`
en el punto que se acaba de resolver. Con una sola resolución se sabe hacia dónde ir, y con
dos o tres se está encima del óptimo.

`scipy.optimize.linprog` lo devuelve en `res.ineqlin.marginals` / `res.upper.marginals`.
Habría que comprobar que HiGHS los da con la formulación actual — las cotas de variable no
generan fila, así que el multiplicador está en `upper`, no en `ineqlin`.

### Dos cosas que no se pueden olvidar

**La duración también es una variable.** Potencia y horas no son intercambiables: una de
1 h y otra de 4 h con la misma energía se comportan distinto porque el límite de potencia
muerde en horas diferentes. Lo suyo es buscar sobre la rejilla de duraciones comerciales
(1, 2, 3, 4, 6, 8 h) y dentro de cada una hacer la ternaria sobre la potencia. Son seis
búsquedas de diez resoluciones: sesenta, y son independientes entre sí.

**El coste de ciclo depende del tamaño.** No es una constante que se pueda sacar fuera del
bucle: sale de `capex_eur_mwh` y de los ciclos de vida, así que cambia con la batería. Está
medido en el notebook 11: al bajar el CAPEX de 200.000 a 35.000 €/MWh, el coste de ciclo
cae de 37 a 6,5 €/MWh **y los ciclos diarios suben de 0,27 a 0,70** — el optimizador
encuentra huecos que antes descartaba. Por eso no vale con dividir el VAN entre dos para
estimar una batería que cuesta la mitad.

### Lo que ya está hecho y sirve

- `production/app/caso.py` resuelve un tamaño de punta a punta. La búsqueda es un bucle
  por encima, no un motor nuevo.
- Los casos `DIM-50R`, `DIM-100R` y `DIM-200R` son un barrido a mano de tres puntos, con
  el perfil solar correcto. Sirven de contraste para validar que la búsqueda automática
  encuentra lo mismo: el mejor de los tres es el de **50 kW, con VAN +2.544 € y positivo
  en el 100 % de los escenarios**.
- El notebook 11 tiene el barrido de CAPEX con el punto de equilibrio interpolado. La misma
  estructura vale para el barrido de tamaño.

---

## 2 · Dejar probar sin subir nada

**Lo que se quiere.** Que alguien pueda entrar, trastear y ver qué hace la herramienta sin
tener a mano un fichero horario de su instalación. Hoy, sin curva de consumo no hay estudio.

Tres caminos, y no son excluyentes:

**Batería sola contra el mercado.** El motor ya lo soporta: `--modo standalone`, sin consumo
ni generación. Es arbitraje puro, y además es el caso más fácil de explicar. Falta la
pantalla.

**Perfiles tipo.** Elegir «fábrica», «oficina», «vivienda», «hotel» y un consumo anual, y
que el sistema ponga la forma. Es barato de hacer —una tabla de 576 valores por perfil, la
misma rejilla de `app_consump_shape`— pero hay que decirlo con todas las letras en pantalla:
**el resultado depende muchísimo de la forma**, y no es un matiz. Con un perfil sintético
suave la misma batería daba 0,93 ciclos al día y con la curva real 0,27. Un factor de 3,4
con idéntico consumo anual. Así que un perfil tipo vale para enseñar la herramienta y no
vale para decidir una inversión, y la pantalla tiene que dejarlo claro.

**Generación sin fichero.** Con los kWp y poco más se puede sintetizar una curva
fotovoltaica razonable: el motor ya asume 1.600 kWh/kWp cuando no hay fichero. Aquí el
riesgo es menor que en el consumo — una cubierta solar en España se parece bastante a otra,
y un consumo industrial no se parece a nada.

---

## 3 · La identificación del usuario

El modelo de datos ya va por usuario: las seis tablas cuelgan de `app_user` con
`ON DELETE CASCADE`, los códigos son únicos por usuario y las consultas del API filtran por
`user_id`. **Lo que no hay es forma de saber quién es**: el correo llega en un parámetro y el
servidor se lo cree, así que sabiendo el correo de alguien se leen y escriben sus
instalaciones.

Lo razonable no es montar otro login, sino que el API confíe en el que la web de Pulso
Energía ya tiene: que la sesión llegue al servidor y de ahí salga el correo.

Y con el login, dos límites que hoy no existen y hacen falta: **cuota por usuario** —cada
estudio son cuatro minutos de CPU— y **limpieza de despachos**, que son 534.672 filas por
estudio y guardarlos todos para siempre no se sostiene.

---

## 4 · Menor, pero conviene no perderlo

- **~~La API~~.** HECHA el 01/09/2026: `production/api/bateria.py`, siete endpoints, y la
  pantalla servida desde el mismo origen en `/bateria/`. Falta que el despacho que dibuja
  la pantalla venga de `/api/bat/despacho` en vez de calcularse en el navegador.
- **El informe completo.** El botón imprime la pantalla, que es lo que el usuario quiere en
  el momento. El informe de verdad —las 534.672 filas de despacho, el año a año, el barrido—
  tiene que generarlo el servidor.
- **Los `.xlsx` en el navegador.** La vista previa inmediata solo funciona con CSV; los Excel
  se suben y contesta el servidor. Funciona, pero se nota la espera.
