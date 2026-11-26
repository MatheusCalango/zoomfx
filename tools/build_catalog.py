#!/usr/bin/env python3
"""Gera catalog/g1on/effects/<categoria>/<id>.json juntando:
  - bits.json  : bits de cada parâmetro (tools/extract_bits.py)
  - tags.json  : tag de identificação de cada efeito
  - official_params.json : nomes, faixas e páginas da lista oficial da Zoom
Uso: build_catalog.py bits.json tags.json official_params.json saida_dir
"""
import json
import os
import re
import sys

bits = json.load(open(sys.argv[1]))
tags = json.load(open(sys.argv[2]))
off = json.load(open(sys.argv[3]))
outdir = sys.argv[4]
templates, tables = off["templates"], off["tables"]
tested = set(off.get("tested", []))


def width(enc):
    if enc == "max150":
        return 8
    if enc == "cab":
        return 6
    return sum(bin(m).count("1") for _, m, _ in enc)


def expand_values(spec):
    vals = []
    for part in spec.split(":", 1)[1].split(","):
        if ".." in part:
            a, b = (int(x) for x in re.match(r"(-?\d+)\.\.(-?\d+)", part).groups())
            vals += list(range(a, b + 1))
        else:
            vals.append(int(part))
    return vals


def compact_json(obj):
    """indent=2, mas listas "folha" (só números/strings) ficam numa linha."""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    leaf = re.compile(r"\[\s*((?:-?[\d.]+|\"[^\"\[\]]*\"|null)(?:,\s*(?:-?[\d.]+|\"[^\"\[\]]*\"|null))*)\s*\]")
    prev = None
    while prev != text:
        prev = text
        text = leaf.sub(lambda m: "[" + re.sub(r",\s*", ", ", m.group(1)) + "]", text)
    # lista de listas folha (encoding) numa linha só
    text = re.sub(r"\[\s*(\[[^\[\]]*\](?:,\s*\[[^\[\]]*\])*)\s*\]",
                  lambda m: "[" + re.sub(r"\],\s*\[", "], [", m.group(1)) + "]", text)
    return text


errors = []
count = 0
for category, effects in off["categories"].items():
    for eid, (name, desc, params) in effects.items():
        if isinstance(params, str):
            params = templates[params]
        if eid not in bits or eid not in tags:
            errors.append("%s: sem bits/tag" % eid)
            continue
        if len(params) != len(bits[eid]):
            errors.append("%s: %d parâmetros oficiais x %d na engenharia reversa"
                          % (eid, len(params), len(bits[eid])))
            continue
        plist = []
        for i, (pname, page, spec, pedal) in enumerate(params):
            enc = bits[eid][str(i)]
            p = {"name": pname, "page": page}
            if spec == "raw":
                p.update(type="raw", min=0, max=(1 << width(enc)) - 1, default=0,
                         note="codificação desconhecida: valor bruto")
            elif spec == "CAB":
                p.update(type="enum", options=tables["CAB"], default=None,
                         note="opcional; se omitido, usa a caixa do próprio amp")
            elif spec in tables:
                p.update(type="enum", options=tables[spec], default=tables[spec][0])
            elif spec.startswith("values:"):
                vals = expand_values(spec)
                p.update(type="enum", options=vals, default=vals[len(vals) // 2])
            elif "|" in spec:
                opts = spec.split("|")
                if all(re.fullmatch(r"-?\d+(\.\d+)?", o) for o in opts):
                    opts = [float(o) if "." in o else int(o) for o in opts]
                p.update(type="enum", options=opts, default=opts[0])
            else:
                a, b = (int(x) for x in re.fullmatch(r"(-?\d+)\.\.(-?\d+)", spec).groups())
                dflt = 100 if pname.upper() == "LEVEL" else (a + b) // 2
                if a <= 0 <= b and pname.lower() in ("gain1", "gain2") or re.fullmatch(r"[\d.]+k?Hz", pname):
                    dflt = 0
                p.update(type="int", min=a, max=b, default=dflt)
            p["pedal"] = bool(pedal)
            p["encoding"] = enc
            # checa se a faixa cabe nos bits
            span = (p["max"] - p["min"]) if p["type"] in ("int", "raw") else len(p["options"]) - 1
            if span > (1 << width(enc)) - 1:
                errors.append("%s.%s: faixa %d não cabe em %d bits" % (eid, pname, span, width(enc)))
            plist.append(p)
        doc = {
            "id": eid, "name": name, "category": category, "description": desc,
            "tag": tags[eid],
            "status": "tested" if eid in tested else "unverified",
            "params": plist,
        }
        os.makedirs(os.path.join(outdir, category), exist_ok=True)
        with open(os.path.join(outdir, category, eid + ".json"), "w", encoding="utf-8") as f:
            f.write(compact_json(doc) + "\n")
        count += 1

print("efeitos gerados:", count)
print("\n".join(errors) or "sem erros")
