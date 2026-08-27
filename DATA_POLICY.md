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

## Dia: adaptador en pausa por decisión pendiente

Dia está detrás de Akamai. Midiendo el comportamiento real con nuestro User-Agent
identificado, el bloqueo se dispara con una cabecera concreta:

| Petición (siempre con nuestro User-Agent) | Respuesta |
|---|---|
| Solo `User-Agent` | 200 |
| `+ accept: */*` | 200 |
| `+ connection: keep-alive` | 200 |
| `+ accept-encoding: gzip, deflate` | **403** |
| `+ accept-encoding: gzip, deflate, br` | 200 |

Es decir, Akamai marca como bot la firma clásica de un cliente Python (`gzip, deflate`) y
deja pasar la de un navegador (`…, br`). **Ajustar esa cabecera para parecer un navegador
sería evadir detección de bots y el punto 10 lo prohíbe.**

Enviar únicamente nuestro `User-Agent`, sin `accept-encoding`, también recibe 200 — y eso
no es suplantar a nadie, sino mandar la petición más simple posible. Pero es una decisión
de política, no técnica, así que el adaptador de Dia **queda escrito y testeado contra
fixtures, pero sin ejecución en vivo** hasta que se resuelva de forma explícita y pública.
El código está en [`core/src/opencesta/adapters/dia.py`](core/src/opencesta/adapters/dia.py).
