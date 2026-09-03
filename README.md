# OpenCesta

**Dataset público y con histórico de los precios de los supermercados españoles, construido con adaptadores mantenibles — no con scrapers frágiles.**

## La tesis

Los comparadores de precios privados mueren siempre por las mismas tres causas: cada cambio en la web del súper los rompe, ignoran que el mismo producto tiene precios distintos por provincia, y su histórico desaparece cuando su autor se aburre. OpenCesta ataca las tres de raíz:

1. **Un dataset público de precios con histórico** — publicado a diario como Parquet, consultable con DuckDB sin instalar nada.
2. **Zonificación desde el esquema** — nunca "el precio", siempre `(sku, cadena, zona, fecha) → precio`.
3. **Adaptadores sobre APIs JSON internas, no scraping de HTML** — con contract tests diarios que detectan cambios en minutos y golden fixtures que permiten arreglarlos con un diff pequeño.

El LLM no va en el bucle de extracción (caro, lento, no determinista). Va en el bucle de **mantenimiento** (auto-reparación de adaptadores vía PR) y de **matching** (equivalencias entre productos de distintas cadenas).

## Estado

**Dataset diario en marcha** desde el 28 de agosto de 2026: Mercadona en cuatro zonas (`vlc1`, `mad1`, `bcn1`, `alc1`) y Dia, publicado cada día como Parquet en [Releases](https://github.com/ruvelro/opencesta/releases). Emparejamiento entre cadenas, juez para la marca blanca y optimizador de cesta funcionando; falta la web, el servidor MCP y más cadenas.

| Fase | Entregable |
|---|---|
| ✅ 0 | Esqueleto, licencia, CI, política de datos |
| ✅ 1 | Adaptador Mercadona + snapshot diario + Parquet publicado |
| 2 | Parser de tickets local-first + dashboard personal |
| 🔨 3 | Dia ✅ · Alcampo/Carrefour · zonificación real de Dia |
| 🔨 4 | Marca nacional ✅ · overrides comunitarios ✅ · marca blanca (embeddings + juez) |
| ✅ 5 | Optimizador de cesta explicable |
| 6 | MCP server + carrito pre-rellenado |
| 🔨 7 | Tu inflación real vs IPC del INE ✅ · alertas de precio |

## Estructura

```
core/   Python (uv + httpx + polars): ingesta, snapshot, dataset
web/    Next.js estática + DuckDB-WASM (fase posterior)
```

El contrato entre ambas mitades es el fichero Parquet: cualquiera puede consumir el dataset sin tocar la web.

## Uso rápido

```bash
cd core
uv sync
uv run opencesta snapshot --zone vlc1 --out ../data
```

Esto genera `data/chain=mercadona/zone=vlc1/date=YYYY-MM-DD/prices.parquet` con todo el catálogo de esa zona. Consúltalo con DuckDB:

```sql
SELECT display_name, unit_price, reference_price, reference_format
FROM 'data/**/*.parquet'
ORDER BY unit_price DESC LIMIT 20;
```

Para descubrir tu zona a partir del código postal:

```bash
uv run opencesta zone 28001   # → mad3
```

El listado por categorías no trae EAN ni marca: eso vive en el endpoint de detalle. El
enriquecimiento es incremental y reanudable, así que el catálogo se construye en varias
pasadas lentas sin castigar su infraestructura:

```bash
uv run opencesta enrich --zone vlc1 --prices ../data --limit 300
```

Con dos snapshots o más, `diff` compara fechas: qué subió, qué bajó, qué entró y qué
desapareció del catálogo.

```bash
uv run opencesta diff --zone vlc1 --prices ../data
```

```
2026-08-20 -> 2026-08-27  (4315 productos en ambas fechas)
cambios de precio: 119 | nuevos: 23 | descatalogados: 18
variación del catálogo comparable: -0,06%
```

`match` empareja productos de marca nacional entre dos cadenas y escribe el resultado
con sus evidencias:

```bash
uv run opencesta match --prices ../data --overrides ../overrides.jsonl --out ../equivalences.jsonl
```

```
mercadona/vlc1 vs dia/es-default  (2026-08-27)
4338 x 5503 productos -> 132 equivalencias en 60 marcas
mismo precio: 80 | más barato en mercadona: 26 | más barato en dia: 26
```

Cada línea de `equivalences.jsonl` lleva por qué se emparejó (términos compartidos,
tamaño, score), para que una persona pueda auditarla. Si algo está mal —o si falta un
par que el matcher no ve— se corrige en [`overrides.jsonl`](overrides.jsonl) y esa
corrección gana para siempre.

### Marca blanca

Hacendado y la marca propia de Dia no comparten ni EAN ni marca, así que el emparejador
solo puede comparar las palabras. Eso basta en los extremos y no basta en el medio:
*Aceite de oliva 0,4º* y *Aceite de orujo de oliva* comparten casi todas y **no son el
mismo producto**. Esos pares van a Claude, que decide entre equivalente, sustituto o
distinto:

```bash
uv run opencesta match --prices ../data --own-brands --judge
```

Solo se manda la banda ambigua —lo que puntúa alto se acepta sin llamada y lo que puntúa
bajo se descarta sin llamada— y **cada veredicto se cachea para siempre** en
[`verdicts.jsonl`](verdicts.jsonl), con su motivo, así que el mismo par no se paga dos
veces y la caché se revisa en git. Sin credenciales de Anthropic el comando usa lo que ya
haya en caché y descarta solo lo que quede sin juzgar, en vez de adivinar.

Los veredictos no tienen por qué venir de la API. `--dump-ambiguous` saca los pares
pendientes a un fichero, y `import-verdicts` mete de vuelta lo que decida quien sea —una
persona, o Claude en una conversación— sin necesidad de credenciales:

```bash
uv run opencesta match --own-brands --dump-ambiguous pendientes.jsonl
uv run opencesta import-verdicts juzgados.jsonl --judged-by "quien lo decidió"
```

### Revisar los emparejamientos

Cada equivalencia afirma que dos productos son lo mismo, y una equivocada corrompe en
silencio toda comparación construida encima. `review` genera una página local para verlas:

```bash
uv run opencesta review --out review.html --open
```

Se abre con doble clic —sin servidor, sin build, y los precios no salen de tu máquina—,
ordena primero las sospechosas (diferencia de precio desmedida, sin tamaño o con score
flojo) y cada fila tiene un botón que copia al portapapeles la línea lista para pegar en
`overrides.jsonl`. Ese es el bucle: mirar, detectar, corregir, y la corrección vale para
siempre.

### Dónde comprar tu lista

`basket` es el producto: le das tu lista y te dice dónde comprar cada cosa y por qué,
contando envío y pedido mínimo, que es lo que convierte "más barato" en "compensa".

```bash
uv run opencesta basket lista.txt --prices ../data --equivalences ../equivalences.jsonl
```

```
Tu lista: 10 productos encontrados (9 comparables entre cadenas, 1 solo en una)

  todo en dia                        47,29 € productos + 4,99 € envío = 52,28 €
  todo en mercadona                        —             ✗ «pañales talla 4» no está en mercadona
  repartido entre dia y mercadona    49,49 € productos + 13,19 € envío = 62,68 €   ✗ mercadona no llega al pedido mínimo de 60,00 €

Mejor: todo en dia por 52,28 €

Por producto:
  pizza cuatro quesos      dia  2,49 €   mercadona  3,90 €  dia ahorra 1,41 €
  salsa de soja            dia  1,20 €   mercadona  2,50 €  dia ahorra 1,30 €
  mayonesa hellmann's      dia  2,95 €   mercadona  2,65 €  mercadona ahorra 0,30 €
  …
```

Con dos cadenas el espacio de decisión son tres planes —todo en A, todo en B, o repartido—
y cada uno se expone en euros con su motivo: un plan que no llega al pedido mínimo lo dice
en vez de fingir que se puede pedir. Solo se comparan productos que el emparejador ha
juzgado equivalentes; lo que existe en una sola cadena se compra allí o no se compra, nunca
se sustituye en silencio por algo que nadie ha revisado.

Las condiciones de envío por defecto (Mercadona 8,20 € y mínimo de 60 €; Dia 4,99 €, gratis
desde 100 €) cambian por código postal y sin avisar: pásalas con `--delivery-a`, `--min-a`,
`--free-b`… Un mínimo mal puesto convierte un buen plan en uno fantasma.

Y `inflation` contrasta esa variación con el IPC de alimentación del INE:

```bash
uv run opencesta inflation --zone vlc1 --prices ../data
```

Solo compara cuando los tramos son equivalentes (~30 días contra la variación mensual del
INE, ~365 contra la anual). Con menos histórico lo dice y no compara: enfrentar una semana
de cesta a una cifra anual del INE es la forma más fácil de publicar un titular engañoso.

## Tests

```bash
cd core
uv run pytest                      # unit tests contra golden fixtures (sin red)
uv run pytest -m contract          # contract tests contra la API viva
```

Los contract tests corren a diario en CI. Si Mercadona cambia su esquema, se abre una issue automáticamente con el diff.

## Política de datos

Lee [DATA_POLICY.md](DATA_POLICY.md). Resumen: rate limits suaves, User-Agent identificable con la URL de este repo, caché agresiva, cero login ajeno, cero reventa de datos, checkout nunca automático.

## Licencia

Código bajo [MIT](LICENSE). El dataset publicado, bajo [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
