# Política de datos

OpenCesta recopila precios públicos de supermercados para publicar un dataset abierto. Estas reglas no son negociables y cualquier PR que las viole será rechazado:

1. **APIs públicas, no login.** Solo se consultan endpoints accesibles sin autenticación, los mismos que usa la web pública de cada cadena. Nunca se usan credenciales, ni propias ni de terceros.
2. **robots.txt respetado.** Si una cadena excluye una ruta, no se consulta.
3. **Rate limits suaves.** Pausa mínima entre peticiones (≥ 400 ms por adaptador), caché agresiva, un snapshot al día por zona. La carga sobre la infraestructura del supermercado debe ser indistinguible de un cliente humano lento.
4. **User-Agent identificable.** Todas las peticiones llevan `OpenCesta/<versión> (+URL de este repo)`. Si una cadena quiere contactarnos o bloquearnos, puede.
5. **Solo datos de productos.** Precios, nombres, formatos, categorías. Nunca datos personales, nunca datos de otros clientes.
6. **Cero reventa.** El dataset es abierto (ODbL) y gratuito. Nadie —incluidos los mantenedores— lo vende.
7. **Checkout nunca automático.** Cualquier herramienta construida sobre este dataset propone; el botón de pagar lo pulsa siempre una persona.
8. **Tickets locales por defecto.** El parser de tickets (fase 2) procesa todo en la máquina del usuario. La contribución de precios desde tickets es anonimizada y opt-in: solo `(producto, precio, tienda, fecha)`, sin identidad.
9. **Derecho de retirada.** Si una cadena solicita formalmente la exclusión de sus datos, se atiende y se documenta públicamente.
10. **Nunca evadimos detección de bots.** No suplantamos huellas TLS de navegador (`curl_cffi` y similares), no rotamos proxies ni IPs, no resolvemos CAPTCHAs y no ocultamos quiénes somos. Si una cadena bloquea nuestro User-Agent identificado, la respuesta es **parar y documentarlo**, no disfrazarnos.

## Dia: qué cabeceras enviamos y por qué

Dia está detrás de Akamai. Midiendo el comportamiento real, siempre con nuestro
User-Agent identificado:

| Petición | Respuesta | Bytes |
|---|---|---|
| Solo `User-Agent` | 200 | 154.777 |
| `+ accept: */*` | 200 | — |
| `+ connection: keep-alive` | 200 | — |
| `+ accept-encoding: gzip, deflate` | **403** | — |
| `+ accept-encoding: gzip` | 200 | 24.654 |
| `+ accept-encoding: gzip, deflate, br` | 200 | 22.567 |

Akamai marca como bot la firma clásica de un cliente Python (`gzip, deflate`) y deja pasar
la de un navegador (`gzip, deflate, br`). **Mandar la variante de navegador sería evadir
detección de bots, y el punto 10 lo prohíbe.**

Enviamos `accept-encoding: gzip` a secas. Esa elección es deliberada por tres razones:
es **literalmente cierta** (aceptamos y descomprimimos gzip), **no imita a ningún
navegador** (ninguno manda solo `gzip`), y descarga **6× menos** de los servidores de Dia
que omitir la cabecera, que es lo que exige el punto 3. Las cabeceras exactas que salen
están declaradas en `MINIMAL_HEADERS` en
[`core/src/opencesta/adapters/dia.py`](core/src/opencesta/adapters/dia.py), y el transporte
tiene un test que garantiza que no añade ninguna por su cuenta.


## Tercera cadena: qué contestó cada una (2026-09-07)

Sondeo con nuestro User-Agent identificado, sin cabeceras de navegador ni trucos, para
elegir la tercera cadena. Se documenta entero porque un "no" también es un dato, y porque
la siguiente persona que lo intente merece no repetirlo:

| Cadena | robots.txt | Páginas con datos | Veredicto |
|---|---|---|---|
| Carrefour | 200 | **403 en todo** (Cloudflare) | Descartada: bloquea al agente identificado |
| Alcampo | 200, muy permisivo | home 200, **categorías 403** | Descartada: bloquea justo donde están los datos |
| Consum | 200 | páginas de 8 KB; los datos vienen de `/api/`, que su propio robots.txt **prohíbe** | Descartada: la única fuente está vedada |
| Eroski | 200 | 200, ~1,2 MB con productos servidos | Viable, pero en **marcado HTML**, no JSON |

Carrefour y Alcampo entran de lleno en el punto 10: bloquean a un cliente que se
identifica, y pasar de ahí exigiría suplantar a un navegador. Se paró y se documentó.

Consum es un caso distinto y más limpio: sus datos existen y son alcanzables, pero su
robots.txt excluye `/api/`. Respetarlo es respetarlo también cuando incomoda.

Eroski sí es viable. La decisión que queda abierta no es de permisos sino de arquitectura:
su catálogo llega como HTML renderizado en servidor, así que sería el primer adaptador que
lee marcado en vez de JSON — exactamente la fragilidad que este proyecto dice evitar en su
primera página. Merece decidirse a conciencia, no por inercia.

## Sondeo ampliado a 25 cadenas (2026-09-17)

Dos cadenas no bastan para que un comparador sirva, así que se sondearon veinticinco con
el User-Agent identificado, clasificándolas por plataforma: si varias comparten stack, un
adaptador cubre a todas.

**Publican precios y se dejan leer**

| Cadena | Cómo | Estado |
|---|---|---|
| Mercadona | API JSON interna | en producción |
| Dia | JSON embebido (Vike) | en producción |
| **Ahorramas** | **`application/ld+json`, 4.599 productos** | **en producción** |
| Eroski | HTML renderizado en servidor | viable, decisión de arquitectura pendiente |

**No publican precios de alimentación en la web abierta.** No es una barrera técnica: el
dato no existe. Lidl España vende solo bazar online (de 6.586 productos del sitemap, los
visibles son colchones y carros de jardín) y Aldi lista su surtido sin un solo campo de
precio en su payload de 68 KB. Ninguna de las dos sirve para comparar la compra.

**Bloquean al agente identificado:** Carrefour, El Corte Inglés y Alimerka responden 403.
Alcampo y Bonpreu comparten plataforma (`__URQL_DATA__`) y sirven la portada, pero dan 403
en las páginas con datos. Consum expone los suyos solo por `/api/`, que su robots excluye.

**Sin tienda online con precios nacionales:** Spar, Covirán, Caprabo, Suma, Froiz, Gadis,
Lupa y HiperDino resultaron ser webs corporativas o de folletos, sin catálogo con precios.

La conclusión operativa es que el universo abordable hoy en España son unas cuatro o cinco
cadenas, no quince. Conviene saberlo antes de prometer lo contrario.
