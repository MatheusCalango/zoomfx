import os
import unittest

from zoomfx import patch as P
from zoomfx.catalog import Catalog, CatalogError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = Catalog()

# Bytes gerados pelo script zoom_heavy_a7.py (patch level 80), que foram aceitos
# e devolvidos idênticos por uma G1Xon real (firmware 1.21).
def golden():
    hexs = ("40 21 00 00 42 02 00 00 00 64 00 00 00 00 00 00 00 00 00 00 00 41 00 00 14 06 00 38 01 02 "
            "00 00 00 00 00 00 00 00 00 00 01 00 41 02 00 08 0f 38 16 01 50 00 47 66 26 05 00 00 00 00 "
            "02 00 21 00 21 00 04 03 50 00 0c 40 30 41 01 01 0c 00 00 00 00 00 00 41 00 00 52 01 0c 20 "
            "00 08 40 01 0c 00 01 00 00 00 00 00 00 20 00 50 00 00 00 00 00 00 00 00 00 00 48 45 41 56 "
            "00 59 20 20 20 20 20 00")
    return [int(x, 16) for x in hexs.split()]


class TestCatalog(unittest.TestCase):
    def test_loads_all_effects(self):
        self.assertEqual(len(CAT.effects), 75)

    def test_lookup_by_name_or_id(self):
        self.assertIs(CAT.effect("BG DRIVE"), CAT.effect("bgdrive"))
        self.assertIs(CAT.effect("t scream"), CAT.effect("tscream"))

    def test_suggestion(self):
        with self.assertRaises(CatalogError) as ctx:
            CAT.effect("BG DRVE")
        self.assertIn("BG DRIVE", str(ctx.exception))

    def test_slots(self):
        self.assertEqual(CAT.slot_number("A7"), 7)
        self.assertEqual(CAT.slot_number("j9"), 99)
        self.assertEqual(CAT.slot_name(42), "E2")
        with self.assertRaises(CatalogError):
            CAT.slot_number("K1")


class TestCodec(unittest.TestCase):
    def test_heavy_matches_device_tested_bytes(self):
        doc = P.load(os.path.join(ROOT, "patches", "heavy.json"))
        self.assertEqual(P.encode(doc, CAT, level_fallback=80), golden())

    def test_all_patches_roundtrip(self):
        folder = os.path.join(ROOT, "patches")
        for f in sorted(os.listdir(folder)):
            doc = P.load(os.path.join(folder, f))
            raw = P.encode(doc, CAT, level_fallback=80)
            self.assertTrue(all(b < 0x80 for b in raw), f)       # SysEx: só 7 bits
            again = P.encode(P.decode(raw, CAT), CAT)
            self.assertEqual(raw, again, f)

    def test_every_effect_every_slot_extremes(self):
        """Cada efeito, em cada posição da cadeia, com valores mínimos e máximos."""
        filler = {"effect": "znr", "params": {}}
        for eff in CAT.effects.values():
            for pick in (0, -1):
                params = {}
                for p in eff.params:
                    if p.type == "enum":
                        params[p.name] = p.options[pick]
                    else:
                        params[p.name] = p.min if pick == 0 else p.max
                for slot in range(5):
                    chain = [dict(filler) for _ in range(5)]
                    chain[slot] = {"effect": eff.id, "on": pick == 0, "params": params}
                    doc = {"format": P.FORMAT, "device": "g1on", "name": "T", "level": 80, "chain": chain}
                    raw = P.encode(doc, CAT)
                    back = P.decode(raw, CAT)["chain"][slot]
                    self.assertEqual(back["effect"], eff.id)
                    self.assertEqual(back["on"], pick == 0)
                    self.assertEqual(back["params"], params, "%s slot %d" % (eff.id, slot))

    def test_chain_lengths(self):
        for n in range(1, 6):
            doc = {"format": P.FORMAT, "device": "g1on", "name": "N%d" % n, "level": 80,
                   "chain": [{"effect": "room"}] * n}
            back = P.decode(P.encode(doc, CAT), CAT)
            self.assertEqual(len(back["chain"]), n)
            self.assertEqual(back["name"], "N%d" % n)


class TestValidation(unittest.TestCase):
    def base(self, **chain0):
        return {"format": P.FORMAT, "device": "g1on", "name": "X", "level": None,
                "chain": [dict({"effect": "bgdrive"}, **chain0)]}

    def test_out_of_range(self):
        with self.assertRaises(P.PatchError) as ctx:
            P.validate(self.base(params={"Gain": 101}), CAT)
        self.assertIn("fora da faixa", str(ctx.exception))

    def test_bad_param_name_suggests(self):
        with self.assertRaises(P.PatchError) as ctx:
            P.validate(self.base(params={"Midle": 50}), CAT)
        self.assertIn("Middl", str(ctx.exception))

    def test_bad_enum(self):
        with self.assertRaises(P.PatchError):
            P.validate(self.base(params={"OUT": "COMBO"}), CAT)

    def test_too_many_effects(self):
        doc = self.base()
        doc["chain"] = [{"effect": "room"}] * 6
        with self.assertRaises(P.PatchError):
            P.validate(doc, CAT)

    def test_level_null_needs_fallback(self):
        with self.assertRaises(P.PatchError):
            P.encode(self.base(), CAT)


if __name__ == "__main__":
    unittest.main()
