"""Arquivos de patch (JSON) <-> 128 bytes da pedaleira."""

import json

from .catalog import CatalogError
from .codec import get_field, set_field

FORMAT = "zoomfx-patch/1"


class PatchError(ValueError):
    pass


def load(path):
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        raise PatchError("não consegui abrir %s: %s" % (path, e.strerror))
    with f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise PatchError("%s: JSON inválido na linha %d, coluna %d: %s"
                             % (path, e.lineno, e.colno, e.msg))


def save(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def validate(doc, catalog):
    """Confere o patch inteiro e devolve a lista de avisos.
    Lança PatchError com todos os erros encontrados."""
    errors, warnings = [], []
    if doc.get("format") != FORMAT:
        errors.append('campo "format" deve ser "%s"' % FORMAT)
    if doc.get("device", catalog.device["id"]) != catalog.device["id"]:
        errors.append('patch é para "%s", catálogo é "%s"' % (doc.get("device"), catalog.device["id"]))
    name = doc.get("name", "")
    if not isinstance(name, str) or not name:
        errors.append('campo "name" é obrigatório')
    else:
        if len(name) > catalog.layout.name_length:
            warnings.append('nome "%s" será cortado para %d caracteres' % (name, catalog.layout.name_length))
        if any(not (32 <= ord(c) <= 126) for c in name):
            errors.append("nome só pode ter caracteres ASCII (sem acentos)")
    level = doc.get("level")
    if level is not None and not (isinstance(level, int) and 0 <= level <= 127):
        errors.append('"level" deve ser null ou um inteiro de 0 a 127')
    chain = doc.get("chain")
    if not isinstance(chain, list) or not chain:
        errors.append('"chain" deve ser uma lista com pelo menos 1 efeito')
        chain = []
    if len(chain) > catalog.layout.max_effects:
        errors.append("no máximo %d efeitos (o patch tem %d)" % (catalog.layout.max_effects, len(chain)))
    for i, item in enumerate(chain, 1):
        if item.get("effect") == "unknown":
            raw = _unknown_bytes(item)
            if raw is None:
                errors.append('efeito %d: "unknown" precisa do campo "raw" (bytes lidos da pedaleira)' % i)
            else:
                warnings.append("efeito %d: desconhecido (tag %s), será mantido exatamente como veio da pedaleira"
                                % (i, item.get("tag")))
            continue
        try:
            eff = catalog.effect(item.get("effect", ""))
        except CatalogError as e:
            errors.append("efeito %d: %s" % (i, e))
            continue
        if eff.status != "tested":
            warnings.append("efeito %d (%s) ainda não foi testado na pedaleira" % (i, eff.name))
        for pname, value in (item.get("params") or {}).items():
            try:
                eff.param(pname).to_raw(value)
            except CatalogError as e:
                errors.append("efeito %d: %s" % (i, e))
    if errors:
        raise PatchError("patch inválido:\n  - " + "\n  - ".join(errors))
    return warnings


def _unknown_bytes(item):
    try:
        raw = [int(x, 16) for x in str(item.get("raw", "")).split()]
    except ValueError:
        return None
    return raw if len(raw) >= 20 and all(0 <= b < 256 for b in raw) else None


def encode(doc, catalog, level_fallback=None):
    """Monta os 128 bytes. 'level' null usa level_fallback (o da memória de destino)."""
    validate(doc, catalog)
    lay = catalog.layout
    data = [0] * lay.size
    lay.set_name(data, doc["name"])
    level = doc.get("level")
    if level is None:
        level = level_fallback
    if level is None:
        raise PatchError('"level" é null e não há patch de destino para copiar o volume')
    data[lay.level_byte] = level
    for (a, b), item in zip(lay.slots, doc["chain"]):
        if item["effect"] == "unknown":
            e = (_unknown_bytes(item) + [0])[:21]
            e[1] = (e[1] & 0xFE) | (1 if item.get("on", True) else 0)
            data[a:b] = e[: b - a]
            continue
        eff = catalog.effect(item["effect"])
        e = [0] * 21
        t0, t1, t2, t4 = eff.tag
        e[0], e[1], e[2], e[4] = t0, t1, t2, t4
        if item.get("on", True):
            e[1] |= 0x01
        given = {eff.param(k).name: v for k, v in (item.get("params") or {}).items()}
        for p in eff.params:
            value = given.get(p.name, p.default)
            if value is None:          # parâmetro opcional (ex.: CAB)
                continue
            set_field(e, p.encoding, p.to_raw(value))
        data[a:b] = e[: b - a]
    data[lay.count_bytes[1]] = len(doc["chain"])
    return lay.scramble(data)


def decode(patch, catalog, full=True):
    """128 bytes -> documento de patch. Efeitos fora do catálogo viram 'unknown'."""
    lay = catalog.layout
    data, count = lay.descramble(patch)
    chain = []
    for a, b in lay.slots[:count]:
        e = data[a:b]
        eff = catalog.by_tag(e)
        if eff is None:
            chain.append({"effect": "unknown", "on": bool(e[1] & 1),
                          "tag": [e[0] & 0x40, e[1] & 0x7A, e[2] & 0x07, e[4] & 0x1E],
                          "raw": " ".join("%02x" % x for x in e)})
            continue
        params = {}
        for p in eff.params:
            v = p.from_raw(get_field(e, p.encoding))
            if v is None:
                continue
            if full or v != p.default:
                params[p.name] = v
        chain.append({"effect": eff.id, "on": bool(e[1] & 1), "params": params})
    return {
        "$schema": "../schema/patch.schema.json",
        "format": FORMAT,
        "device": catalog.device["id"],
        "name": lay.get_name(data),
        "description": "",
        "level": data[lay.level_byte],
        "chain": chain,
    }


def describe(doc, catalog):
    lines = ["%s  (level %s)" % (doc["name"], "do destino" if doc.get("level") is None else doc["level"])]
    for i, item in enumerate(doc["chain"], 1):
        if item["effect"] == "unknown":
            lines.append("  %d. ??? tag=%s" % (i, item["tag"]))
            continue
        eff = catalog.effect(item["effect"])
        given = {eff.param(k).name: v for k, v in (item.get("params") or {}).items()}
        vals = []
        for p in eff.params:
            v = given.get(p.name, p.default)
            if v is not None:
                vals.append("%s=%s%s" % (p.name, v, "" if p.name in given else "*"))
        flag = "ON " if item.get("on", True) else "OFF"
        lines.append("  %d. %-10s %s %s" % (i, eff.name, flag, ", ".join(vals)))
    lines.append("  (* = valor padrão do catálogo)")
    return "\n".join(lines)
