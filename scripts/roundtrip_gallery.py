"""Original drawing beside our reconstruction, per example.

The numbers in `trackA.json` say which rung failed; this says whether the answer
looks like a house. Both are needed: a plan can pass every check and still be
obviously wrong to anyone who has lived in one.
"""
from __future__ import annotations
import base64, json, mimetypes, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "roundtrip"


def as_png_datauri(p: Path) -> str:
    """webp/avif have to be transcoded; the browser gets a data: URI either way."""
    if p.suffix.lower() in (".webp", ".avif"):
        tmp = Path(tempfile.mkdtemp()) / (p.stem + ".png")
        subprocess.run(["sips", "-s", "format", "png", str(p), "--out", str(tmp)],
                       capture_output=True)
        p = tmp
    mt = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mt};base64," + base64.b64encode(p.read_bytes()).decode()


def rows_html(rows: list[dict]) -> str:
    out = []
    for r in rows:
        orig = ROOT / r.get("image", "")
        gen = OUT / r["png"] if r.get("png") else None
        left = (f'<img src="{as_png_datauri(orig)}">' if orig.exists()
                else '<div class="none">source image missing</div>')
        right = (f'<img src="{as_png_datauri(gen)}">' if (gen and gen.is_file())
                 else f'<div class="none">no plan produced<br><small>'
                      f'{(r.get("build") or {}).get("status", r.get("fatal","?"))}'
                      f'</small></div>')
        b = r.get("build") or {}
        notes = "".join(
            f'<li class="{("err" if f["sev"]=="error" else "wrn")}">'
            f'[{f["sev"]}] {f["rule"]}: {f["detail"]}</li>'
            for f in (r.get("findings") or [])[:30])
        errs = "".join(f"<li class='err'>{e}</li>" for e in (b.get("errors") or []))
        g4 = r.get("rung4_geometry") or {}
        w, gt = g4.get("area_sqft_want") or {}, g4.get("area_sqft_got") or {}
        area = "".join(
            f"<tr><td>{k}</td><td>{w.get(k,'-')}</td><td>{gt.get(k,'-')}</td></tr>"
            for k in sorted(set(w) | set(gt)))
        out.append(f"""
<section>
  <h2>{r['id']}</h2>
  <div class="meta">
    build <b>{b.get('status','-')}</b> &middot;
    rung1 brief {'ok' if (r.get('rung1_brief') or {}).get('ok') else 'GAP'} &middot;
    rung2 programme {'ok' if (r.get('rung2_programme') or {}).get('ok') else 'GAP'} &middot;
    {r.get('n_errors','-')} errors, {r.get('n_warns','-')} warnings
  </div>
  <div class="pair">
    <figure><figcaption>the real drawing</figcaption>{left}</figure>
    <figure><figcaption>our reconstruction</figcaption>{right}</figure>
  </div>
  <div class="cols">
    <div><h3>room area, sqft</h3>
      <table><tr><th>category</th><th>real</th><th>ours</th></tr>{area}</table></div>
    <div><h3>what the validator said</h3><ul>{errs}{notes or '<li>none</li>'}</ul></div>
  </div>
</section>""")
    return "\n".join(out)


def main() -> int:
    src = OUT / (sys.argv[1] if len(sys.argv) > 1 else "trackA.json")
    rows = json.loads(src.read_text())
    html = f"""<!doctype html><meta charset=utf-8>
<title>reconstruction round trip</title>
<style>
 body{{font:14px/1.5 -apple-system,sans-serif;margin:0;padding:24px;background:#faf9f7;color:#222}}
 h1{{font-size:20px}} h2{{font-size:16px;margin:0 0 4px}}
 section{{background:#fff;border:1px solid #e3e0da;border-radius:8px;padding:16px;margin:0 0 24px}}
 .meta{{color:#666;font-size:12px;margin-bottom:12px}}
 .pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
 figure{{margin:0}} figcaption{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#888;margin-bottom:6px}}
 img{{width:100%;border:1px solid #eee;background:#fff}}
 .none{{padding:60px 12px;text-align:center;color:#b00;background:#fff6f6;border:1px dashed #f0c0c0}}
 .cols{{display:grid;grid-template-columns:280px 1fr;gap:24px;margin-top:16px}}
 table{{border-collapse:collapse;font-size:12px;width:100%}}
 td,th{{border-bottom:1px solid #eee;padding:3px 6px;text-align:left}}
 ul{{margin:0;padding-left:18px;font-size:12px}}
 li.err{{color:#b00}} li.wrn{{color:#96690a}}
 h3{{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#888}}
</style>
<h1>Reconstruction round trip &mdash; {src.name}</h1>
{rows_html(rows)}"""
    dst = OUT / (src.stem + ".html")
    dst.write_text(html)
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
