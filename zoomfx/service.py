"""Operações com a pedaleira, compartilhadas pela CLI e pela interface web."""

import os
import time

from .midi import MidiError, Pedal, find_device, save_syx

BACKUP_DIR = "backups"


def connect(catalog, device=None):
    """Abre a pedaleira, identifica e liga o modo editor. Retorna (pedal, info)."""
    desc = ""
    if not device:
        device, desc = find_device()
        if not device:
            raise MidiError("pedaleira não encontrada em /dev/snd/midi*. Ligue via USB e feche o ToneLib")
    pedal = Pedal(device, catalog)
    try:
        model, fw = pedal.identify()
        pedal.editor(True)
    except Exception:
        pedal.close()
        raise
    return pedal, {"device": device, "card": desc, "model": model,
                   "model_id": pedal.model_id, "firmware": fw}


def backup(pedal, catalog, slot, folder=BACKUP_DIR, label="backup"):
    old = pedal.read_patch(slot)
    if old is None:
        raise MidiError("não consegui ler a memória %s; nada foi alterado" % catalog.slot_name(slot))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s_%s_%s.syx" % (label, catalog.slot_name(slot),
                                                  time.strftime("%Y%m%d-%H%M%S")))
    save_syx(path, catalog, pedal.model_id, old)
    return old, path


def load_to_buffer(pedal, slot, new):
    """Seleciona 'slot', envia para o buffer de edição e confere. Retorna (ok, devolvido)."""
    pedal.select(slot)
    pedal.write_current(new)
    back = pedal.read_current()
    return back == new, back


def is_stored(pedal, slot, new):
    return pedal.read_patch(slot) == new


def try_store(pedal, slot, new):
    """Tenta gravar sozinho. Retorna o nome do método que funcionou, ou None."""
    pedal.write_patch(slot, new)
    if is_stored(pedal, slot, new):
        return "write_patch"
    pedal.store(slot)
    if is_stored(pedal, slot, new):
        return "store_patch"
    return None


def diff(catalog, sent, stored):
    """Descreve em poucas linhas o que difere entre o enviado e o gravado."""
    from . import patch as P
    if stored is None:
        return ["não consegui ler a memória de volta"]
    idx = [i for i, (a, b) in enumerate(zip(sent, stored)) if a != b]
    lines = ["%d byte(s) diferente(s): %s" % (len(idx), ", ".join(
        "%d: %02x→%02x" % (i, sent[i], stored[i]) for i in idx[:12]))]
    a, b = P.decode(sent, catalog), P.decode(stored, catalog)
    if a["name"] != b["name"]:
        lines.append("nome: %r → %r" % (a["name"], b["name"]))
    for i, (x, y) in enumerate(zip(a["chain"], b["chain"]), 1):
        if x != y:
            lines.append("efeito %d: %s → %s" % (i, x.get("effect"), y.get("effect")))
            for k in sorted(set(x.get("params", {})) | set(y.get("params", {}))):
                if x.get("params", {}).get(k) != y.get("params", {}).get(k):
                    lines.append("   %s: %s → %s" % (k, x["params"].get(k), y["params"].get(k)))
    if len(a["chain"]) != len(b["chain"]):
        lines.append("número de efeitos: %d → %d" % (len(a["chain"]), len(b["chain"])))
    return lines
