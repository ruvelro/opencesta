# OpenCesta

**Dataset público y con histórico de los precios de los supermercados españoles, construido con adaptadores mantenibles — no con scrapers frágiles.**

## La tesis

Los comparadores de precios privados mueren siempre por las mismas tres causas: cada cambio en la web del súper los rompe, ignoran que el mismo producto tiene precios distintos por provincia, y su histórico desaparece cuando su autor se aburre. OpenCesta ataca las tres de raíz:

1. **Un dataset público de precios con histórico** — publicado a diario como Parquet, consultable con DuckDB sin instalar nada.
2. **Zonificación desde el esquema** — nunca "el precio", siempre `(sku, cadena, zona, fecha) → precio`.
3. **Adaptadores sobre APIs JSON internas, no scraping de HTML** — con contract tests diarios que detectan cambios en minutos y golden fixtures que permiten arreglarlos con un diff pequeño.

El LLM no va en el bucle de extracción (caro, lento, no determinista). Va en el bucle de **mantenimiento** (auto-reparación de adaptadores vía PR) y de **matching** (equivalencias entre productos de distintas cadenas).

## Estado

**Fase 1 en curso**: adaptador de Mercadona sobre su API JSON interna, con zonificación por warehouse (`vlc1`, `mad1`, `bcn1`, `alc1`…) y snapshot diario a Parquet.

| Fase | Entregable |
|---|---|
| ✅ 0 | Esqueleto, licencia, CI, política de datos |
| ✅ 1 | Adaptador Mercadona + snapshot diario + Parquet publicado |
| 2 | Parser de tickets local-first + dashboard personal |
| 🔨 3 | Dia ✅ · Alcampo/Carrefour · zonificación real de Dia |
| 4 | Motor de equivalencias + overrides comunitarios (`equivalences.jsonl`) |
| 5 | Optimizador de cesta explicable |
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
