"""A self-contained local page for eyeballing the matcher's output.

Every equivalence the matcher produces is a claim that two products are the same
thing, and a wrong one quietly corrupts every price comparison built on top. The
cheapest way to catch those is to look at them — sorted so the suspicious ones
surface first — and to make writing the correction a copy-paste away.

The page embeds its own data and opens with a double click: no server, no build
step, and the prices never leave the machine.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

TEMPLATE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCesta · revisión de equivalencias</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfbf9; --fg: #1a1a17; --muted: #6b6b62; --line: #e2e2db;
  --card: #ffffff; --accent: #1f6f4a; --warn: #9a4a1f; --bad: #97302b;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#16161a; --fg:#ecece8; --muted:#9a9a92; --line:#2c2c32;
          --card:#1e1e23; --accent:#6ec49a; --warn:#e0a06a; --bad:#e08a84; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,
       BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
header { padding:24px 20px 12px; border-bottom:1px solid var(--line); }
h1 { margin:0 0 4px; font-size:19px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:13px; }
.stats { display:flex; flex-wrap:wrap; gap:20px; margin-top:14px; }
.stat b { display:block; font-size:20px; font-variant-numeric:tabular-nums; }
.stat span { color:var(--muted); font-size:12px; }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
            padding:12px 20px; position:sticky; top:0; background:var(--bg);
            border-bottom:1px solid var(--line); z-index:2; }
input[type=search] { flex:1; min-width:180px; padding:7px 10px; border-radius:7px;
       border:1px solid var(--line); background:var(--card); color:var(--fg); font-size:14px; }
button { padding:6px 11px; border-radius:7px; border:1px solid var(--line);
         background:var(--card); color:var(--fg); font-size:13px; cursor:pointer; }
button[aria-pressed=true] { background:var(--fg); color:var(--bg); border-color:var(--fg); }
main { padding:12px 20px 60px; display:grid; gap:10px; }
.row { background:var(--card); border:1px solid var(--line); border-radius:10px;
       padding:12px 14px; display:grid; gap:8px; }
.head { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
.tag { font-size:11px; padding:2px 7px; border-radius:99px; border:1px solid var(--line);
       color:var(--muted); white-space:nowrap; }
.tag.substitute { color:var(--warn); border-color:currentColor; }
.tag.judged { color:var(--accent); border-color:currentColor; }
.why { color:var(--muted); font-size:13px; font-style:italic; }
.sides { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
@media (max-width:640px){ .sides{ grid-template-columns:1fr; } }
.side { display:grid; gap:2px; min-width:0; }
.side .who { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.side .name { overflow-wrap:anywhere; }
.price { font-variant-numeric:tabular-nums; }
.cheap { color:var(--accent); font-weight:600; }
.gap { font-variant-numeric:tabular-nums; font-weight:600; }
.gap.big { color:var(--bad); }
.acts { display:flex; gap:8px; flex-wrap:wrap; }
.acts button { font-size:12px; }
.empty { color:var(--muted); padding:40px 0; text-align:center; }
code { font:12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
       background:var(--bg); padding:1px 4px; border-radius:4px; }
</style></head><body>
<header>
  <h1>Revisión de equivalencias</h1>
  <div class="sub">__SUBTITLE__</div>
  <div class="stats">__STATS__</div>
</header>
<div class="controls">
  <input type="search" id="q" placeholder="Buscar producto, marca o motivo…">
  <button data-f="all" aria-pressed="true">Todas</button>
  <button data-f="suspect" aria-pressed="false">Sospechosas</button>
  <button data-f="substitute" aria-pressed="false">Sustitutos</button>
  <button data-f="judged" aria-pressed="false">Juzgadas</button>
  <button data-f="gap" aria-pressed="false">Mayor diferencia</button>
</div>
<main id="list"></main>
<script>
const DATA = __DATA__;
const list = document.getElementById('list');
const q = document.getElementById('q');
let filter = 'all';

const eur = n => n == null ? '—' : n.toFixed(2).replace('.', ',');

function matches(d) {
  const text = q.value.trim().toLowerCase();
  if (text && !(d.a.name + ' ' + d.b.name + ' ' + d.brand + ' ' + (d.why||''))
      .toLowerCase().includes(text)) return false;
  if (filter === 'suspect') return d.suspect;
  if (filter === 'substitute') return d.method.endsWith('substitute');
  if (filter === 'judged') return d.method.startsWith('own-brand-') &&
                                  !d.method.endsWith('score');
  return true;
}

function render() {
  let rows = DATA.filter(matches);
  if (filter === 'gap') rows = [...rows].sort((x, y) =>
      Math.abs(y.gap ?? 0) - Math.abs(x.gap ?? 0));
  if (!rows.length) { list.innerHTML = '<p class="empty">Nada que mostrar.</p>'; return; }
  list.innerHTML = rows.map(d => {
    // gap is Dia's price relative to Mercadona's, so a positive gap means
    // Mercadona is the cheaper side. The arrow marks whichever that is.
    const aCheap = d.gap != null && d.gap > 1, bCheap = d.gap != null && d.gap < -1;
    const arrow = '<b class="cheap" title="más barato">&darr;</b>';
    const judged = d.method.startsWith('own-brand-') && !d.method.endsWith('score');
    return `<article class="row">
      <div class="head">
        <span class="tag ${judged ? 'judged' : ''} ${d.method.endsWith('substitute')
          ? 'substitute' : ''}">${d.method}</span>
        <span class="tag">score ${d.score.toFixed(2)}</span>
        <span class="tag">${d.size == null ? 'sin tamaño' : eur(d.size) + ' ' + d.fmt}</span>
        ${d.gap == null ? '' : `<span class="gap ${Math.abs(d.gap) > 10 ? 'big' : ''}">${
          d.gap > 0 ? '+' : ''}${d.gap.toFixed(1)}% en Dia</span>`}
        ${d.suspect ? '<span class="tag" style="color:var(--bad);border-color:currentColor">revisar</span>' : ''}
      </div>
      ${d.why ? `<div class="why">${d.why}</div>` : ''}
      <div class="sides">
        <div class="side"><span class="who">Mercadona</span>
          <span class="name">${d.a.name}</span>
          <span class="price">${eur(d.a.price)} €/${d.fmt} ${aCheap ? arrow : ''}</span>
        </div>
        <div class="side"><span class="who">Dia</span>
          <span class="name">${d.b.name}</span>
          <span class="price">${eur(d.b.price)} €/${d.fmt} ${bCheap ? arrow : ''}</span>
        </div>
      </div>
      <div class="acts">
        <button onclick="copyOverride('different', ${JSON.stringify(d.a.sku)}, ${
          JSON.stringify(d.b.sku)}, this)">No son lo mismo</button>
        <button onclick="copyOverride('equivalent', ${JSON.stringify(d.a.sku)}, ${
          JSON.stringify(d.b.sku)}, this)">Confirmar equivalencia</button>
      </div>
    </article>`;
  }).join('');
}

function copyOverride(verdict, skuA, skuB, btn) {
  const line = JSON.stringify({verdict, a: {sku: skuA}, b: {sku: skuB}, note: ''});
  navigator.clipboard.writeText(line).then(() => {
    const was = btn.textContent;
    btn.textContent = 'Copiado a overrides.jsonl ✓';
    setTimeout(() => { btn.textContent = was; }, 1600);
  }, () => { window.prompt('Copia esta línea en overrides.jsonl:', line); });
}

document.querySelectorAll('.controls button').forEach(b => b.onclick = () => {
  filter = b.dataset.f;
  document.querySelectorAll('.controls button').forEach(o =>
    o.setAttribute('aria-pressed', String(o === b)));
  render();
});
q.oninput = render;
render();
</script></body></html>
"""


def is_suspect(row: dict[str, Any]) -> bool:
    """Flag the pairs most likely to be wrong, so a reviewer starts there.

    A large price gap between supposedly identical products is the strongest
    smell: chains price the same goods within a couple of percent, so a 25% gap
    usually means the match, not the price, is wrong.
    """
    if row["gap"] is not None and abs(row["gap"]) > 25:
        return True
    return row["size"] is None or row["score"] < 0.5


def build_rows(
    equivalences: list[dict[str, Any]],
    by_sku_a: dict[str, dict],
    by_sku_b: dict[str, dict],
) -> list[dict[str, Any]]:
    rows = []
    for equivalence in equivalences:
        a = by_sku_a.get(str(equivalence["a"]["sku"]))
        b = by_sku_b.get(str(equivalence["b"]["sku"]))
        price_a = (a or {}).get("reference_price")
        price_b = (b or {}).get("reference_price")
        gap = None
        if price_a and price_b:
            gap = round((price_b - price_a) / price_a * 100, 2)
        terms = equivalence.get("shared_terms") or []
        judged = equivalence["method"].startswith("own-brand-") and not equivalence[
            "method"
        ].endswith("score")
        row = {
            "method": equivalence["method"],
            "brand": equivalence.get("brand") or "",
            "score": equivalence.get("score", 0.0),
            "size": equivalence.get("size"),
            "fmt": equivalence.get("reference_format") or "",
            "why": terms[0] if judged and terms else ", ".join(terms),
            "gap": gap,
            "a": {"sku": str(equivalence["a"]["sku"]), "name": equivalence["a"]["name"],
                  "price": price_a},
            "b": {"sku": str(equivalence["b"]["sku"]), "name": equivalence["b"]["name"],
                  "price": price_b},
        }
        row["suspect"] = is_suspect(row)
        rows.append(row)
    return rows


def write_review(rows: list[dict[str, Any]], path: Path, subtitle: str) -> Path:
    gaps = [r["gap"] for r in rows if r["gap"] is not None]
    cheaper_b = sum(1 for g in gaps if g < -1)
    cheaper_a = sum(1 for g in gaps if g > 1)
    stats = [
        (len(rows), "equivalencias"),
        (sum(1 for r in rows if r["suspect"]), "para revisar"),
        (cheaper_a, "más baratas en Dia"),
        (cheaper_b, "más baratas en Mercadona"),
    ]
    stats_html = "".join(
        f'<div class="stat"><b>{value}</b><span>{html.escape(label)}</span></div>'
        for value, label in stats
    )
    page = (
        TEMPLATE.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
        .replace("__STATS__", stats_html)
        .replace("__SUBTITLE__", html.escape(subtitle))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
