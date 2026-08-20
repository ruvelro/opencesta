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
