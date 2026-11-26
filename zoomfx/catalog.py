"""Catálogo: o aparelho (device.json) e os efeitos (um JSON por efeito)."""

import difflib
import glob
import json
import os

from .codec import Layout, effect_tag, field_width

DEFAULT_CATALOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalog")


class CatalogError(ValueError):
    pass


def _suggest(word, options):
    near = difflib.get_close_matches(str(word), [str(o) for o in options], n=1, cutoff=0.5)
    return (' (você quis dizer "%s"?)' % near[0]) if near else ""


def _norm(s):
    return str(s).lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")


class Param:
    def __init__(self, effect_id, doc):
        self.effect_id = effect_id
        self.name = doc["name"]
        self.page = doc["page"]
        self.type = doc["type"]
        self.min = doc.get("min")
        self.max = doc.get("max")
        self.options = doc.get("options")
        self.default = doc.get("default")
        self.pedal = doc.get("pedal", False)
        self.encoding = doc["encoding"]
        self.note = doc.get("note")
        if self.encoding not in ("max150", "cab"):
            self.encoding = [tuple(x) for x in self.encoding]

    @property
    def optional(self):
        return self.default is None

    def describe_range(self):
        if self.type == "enum":
            return " | ".join(str(o) for o in self.options)
        return "%s..%s" % (self.min, self.max)

    def to_raw(self, value):
        """Valor da tela -> valor bruto. Lança CatalogError se inválido.

        Convenção para inteiros: bruto = valor - mínimo (confirmar na tela).
        """
        where = "%s.%s" % (self.effect_id, self.name)
        if self.type == "enum":
            for i, opt in enumerate(self.options):
                if value == opt or (isinstance(value, str) and isinstance(opt, str)
                                    and value.lower() == opt.lower()):
                    return i
            raise CatalogError("%s: valor %r inválido; opções: %s%s"
                               % (where, value, self.describe_range(), _suggest(value, self.options)))
        if isinstance(value, bool) or not isinstance(value, int):
            raise CatalogError("%s: esperado um número inteiro, veio %r" % (where, value))
        if not (self.min <= value <= self.max):
            raise CatalogError("%s: %d fora da faixa %s" % (where, value, self.describe_range()))
        return value - self.min

    def from_raw(self, raw):
        if raw is None:
            return None
        if self.type == "enum":
            return self.options[raw] if raw < len(self.options) else "?bruto=%d" % raw
        return raw + self.min


class Effect:
    def __init__(self, doc, path=None):
        self.id = doc["id"]
        self.name = doc["name"]
        self.category = doc["category"]
        self.description = doc.get("description", "")
        self.tag = tuple(doc["tag"])
        self.status = doc.get("status", "unverified")
        self.params = [Param(self.id, p) for p in doc["params"]]
        self.path = path
        for p in self.params:
            span = (p.max - p.min) if p.type != "enum" else len(p.options) - 1
            if span > (1 << field_width(p.encoding)) - 1:
                raise CatalogError("%s.%s: a faixa não cabe nos bits do campo" % (self.id, p.name))

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        for p in self.params:
            if _norm(p.name) == _norm(name):
                return p
        raise CatalogError('%s: parâmetro "%s" não existe; parâmetros: %s%s'
                           % (self.name, name, ", ".join(p.name for p in self.params),
                              _suggest(name, [p.name for p in self.params])))


class Catalog:
    def __init__(self, device="g1on", root=None):
        root = root or DEFAULT_CATALOG_DIR
        base = os.path.join(root, device)
        with open(os.path.join(base, "device.json"), encoding="utf-8") as f:
            self.device = json.load(f)
        self.layout = Layout(self.device["patch"])
        self.effects = {}
        self._by_tag = {}
        for path in sorted(glob.glob(os.path.join(base, "effects", "*", "*.json"))):
            with open(path, encoding="utf-8") as f:
                eff = Effect(json.load(f), path)
            if eff.id in self.effects:
                raise CatalogError("efeito duplicado: %s" % eff.id)
            if eff.tag in self._by_tag:
                raise CatalogError("tag duplicada: %s e %s" % (eff.id, self._by_tag[eff.tag].id))
            self.effects[eff.id] = eff
            self._by_tag[eff.tag] = eff

    def effect(self, ref):
        """Aceita o id ('bgdrive') ou o nome da tela ('BG DRIVE')."""
        if ref in self.effects:
            return self.effects[ref]
        for e in self.effects.values():
            if _norm(e.name) == _norm(ref) or _norm(e.id) == _norm(ref):
                return e
        names = [e.name for e in self.effects.values()] + list(self.effects)
        raise CatalogError('efeito "%s" não existe no catálogo%s' % (ref, _suggest(ref, names)))

    def by_tag(self, effect_bytes):
        return self._by_tag.get(effect_tag(effect_bytes))

    def model_name(self, model_id):
        m = self.device["midi"]["models"].get(str(model_id))
        return m["name"] if m else None

    # -- slots ----------------------------------------------------------------
    def slot_number(self, text):
        banks = self.device["slots"]["banks"]
        per = self.device["slots"]["per_bank"]
        t = str(text).strip().upper()
        if len(t) == 2 and t[0] in banks and t[1].isdigit():
            return banks.index(t[0]) * per + int(t[1])
        raise CatalogError('posição "%s" inválida; use %s0..%s9 (ex.: A7)' % (text, banks[0], banks[-1]))

    def slot_name(self, number):
        banks = self.device["slots"]["banks"]
        per = self.device["slots"]["per_bank"]
        return "%s%d" % (banks[number // per], number % per)

    @property
    def slot_count(self):
        return len(self.device["slots"]["banks"]) * self.device["slots"]["per_bank"]
