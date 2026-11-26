"""Linha de comando: zoomfx <comando> ..."""

import argparse
import os
import sys

from . import patch as P
from .catalog import Catalog, CatalogError
from .midi import MidiError, load_syx, save_syx

from . import service

BACKUP_DIR = service.BACKUP_DIR


def _open_pedal(args, cat):
    pedal, info = service.connect(cat, args.device)
    if info["card"]:
        print("Dispositivo: %s  (%s)" % (info["device"], info["card"]))
    print("Modelo: %s (ID 0x%02X), firmware %s" % (info["model"], info["model_id"], info["firmware"]))
    return pedal


def _backup(pedal, cat, slot):
    return service.backup(pedal, cat, slot)


def _write_and_store(pedal, cat, slot, new, store):
    """Envia para o buffer de edição de 'slot', confere e grava."""
    ok, back = service.load_to_buffer(pedal, slot, new)
    if not ok:
        print("\nATENÇÃO: a pedaleira devolveu um patch diferente do enviado. Nada foi gravado.")
        if back:
            print(P.describe(P.decode(back, cat), cat))
        return False
    name = cat.slot_name(slot)
    print("\nPatch carregado em %s (ainda não gravado)." % name)
    if store:
        if service.try_store(pedal, slot, new):
            print("Gravado em %s e conferido." % name)
            return True
        print("O store automático não confirmou.")
    print("Salve agora na pedaleira, em %s. NÃO troque de patch antes." % name)
    input("Depois de salvar, aperte Enter para eu conferir... ")
    if service.is_stored(pedal, slot, new):
        print("Conferido: %s gravado." % name)
        return True
    print("%s ainda não está com o patch novo. Tente de novo com --store." % name)
    return False


# -- comandos --------------------------------------------------------------------

def cmd_effects(args, cat):
    effs = sorted(cat.effects.values(), key=lambda e: (e.category, e.name))
    if args.category:
        effs = [e for e in effs if e.category == args.category]
    if args.effect:
        e = cat.effect(args.effect)
        print("%s  [%s]  id=%s  status=%s\n%s\n" % (e.name, e.category, e.id, e.status, e.description))
        for p in e.params:
            extra = (" padrão=%s" % p.default) if p.default is not None else " (opcional)"
            print("  pág.%d  %-7s %-40s%s%s%s" % (p.page, p.name, p.describe_range(), extra,
                                                 "  [pedal]" if p.pedal else "",
                                                 ("  — " + p.note) if p.note else ""))
        return
    cur = None
    for e in effs:
        if e.category != cur:
            cur = e.category
            print("\n[%s]" % cur)
        print("  %-11s %-18s %s%s" % (e.name, e.id, e.description, "" if e.status == "tested" else "  (não testado)"))


def cmd_show(args, cat):
    if args.file.endswith(".syx"):
        doc = P.decode(load_syx(args.file, cat), cat)
    else:
        doc = P.load(args.file)
        for w in P.validate(doc, cat):
            print("aviso:", w)
    print(P.describe(doc, cat))


def cmd_validate(args, cat):
    ok = True
    for f in args.files:
        try:
            warns = P.validate(P.load(f), cat)
            print("%s: OK" % f)
            for w in warns:
                print("  aviso:", w)
        except (P.PatchError, CatalogError) as e:
            ok = False
            print("%s: %s" % (f, e))
    return 0 if ok else 1


def cmd_export(args, cat):
    doc = P.load(args.file)
    new = P.encode(doc, cat, level_fallback=args.level)
    ids = {v["name"]: int(k) for k, v in cat.device["midi"]["models"].items()}
    out = args.output or os.path.splitext(args.file)[0] + ".syx"
    save_syx(out, cat, ids[args.model], new)
    print("Gerado:", out)


def cmd_push(args, cat):
    doc = P.load(args.file)
    for w in P.validate(doc, cat):
        print("aviso:", w)
    slot = cat.slot_number(args.slot)
    with _open_pedal(args, cat) as pedal:
        old, bak = _backup(pedal, cat, slot)
        print("\nBackup de %s (%s) em %s" % (cat.slot_name(slot), P.decode(old, cat)["name"], bak))
        new = P.encode(doc, cat, level_fallback=old[cat.layout.level_byte])
        print("\nEnviando:\n" + P.describe(doc, cat))
        return 0 if _write_and_store(pedal, cat, slot, new, args.store) else 1


def cmd_pull(args, cat):
    slot = cat.slot_number(args.slot)
    with _open_pedal(args, cat) as pedal:
        data = pedal.read_patch(slot)
        if data is None:
            raise MidiError("não consegui ler %s" % cat.slot_name(slot))
    doc = P.decode(data, cat, full=not args.short)
    doc["description"] = "Lido da memória %s" % cat.slot_name(slot)
    out = args.output or "%s.json" % cat.slot_name(slot)
    P.save(out, doc)
    print(P.describe(doc, cat))
    print("\nSalvo em", out)


def cmd_backup(args, cat):
    slots = range(cat.slot_count) if args.all else [cat.slot_number(s) for s in args.slots]
    with _open_pedal(args, cat) as pedal:
        for s in slots:
            _, path = _backup(pedal, cat, s)
            print(cat.slot_name(s), "->", path)


def cmd_restore(args, cat):
    data = load_syx(args.file, cat)
    slot = cat.slot_number(args.slot)
    with _open_pedal(args, cat) as pedal:
        _, bak = _backup(pedal, cat, slot)
        print("Backup do conteúdo atual em", bak)
        return 0 if _write_and_store(pedal, cat, slot, data, args.store) else 1


def cmd_scan(args, cat):
    """Lê todas as memórias e lista os efeitos que o catálogo não reconhece."""
    found = {}
    with _open_pedal(args, cat) as pedal:
        for s in range(cat.slot_count):
            data = pedal.read_patch(s)
            if data is None:
                continue
            doc = P.decode(data, cat)
            for pos, c in enumerate(doc["chain"], 1):
                if c["effect"] == "unknown":
                    found.setdefault(tuple(c["tag"]), []).append("%s/%d (%s)" % (cat.slot_name(s), pos, doc["name"]))
    if not found:
        print("Todos os efeitos das memórias estão no catálogo.")
        return
    print("\nEfeitos fora do catálogo (memória/posição na cadeia):")
    for tag, where in sorted(found.items()):
        print("  tag %-20s %s" % (list(tag), ", ".join(where[:6]) + (" …" if len(where) > 6 else "")))
    print("\nOlhe na pedaleira qual efeito está nessas posições e informe o nome + a tag "
          "para incluí-lo no catálogo.")


def cmd_ui(args, cat):
    from .web import serve
    serve(cat, port=args.port, open_browser=not args.no_browser,
          patches_dir=args.patches, backups_dir=BACKUP_DIR, device=args.device)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="zoomfx", description="Patches da Zoom G1on/G1Xon em JSON.")
    ap.add_argument("--catalog", help="pasta do catálogo (padrão: a do projeto)")
    ap.add_argument("--device", help="dispositivo MIDI, ex.: /dev/snd/midiC3D0 (padrão: detecta)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("effects", help="lista os efeitos do catálogo")
    s.add_argument("effect", nargs="?", help="mostra os parâmetros de um efeito")
    s.add_argument("--category")
    s.set_defaults(func=cmd_effects)

    s = sub.add_parser("show", help="mostra um patch (.json ou .syx) decodificado")
    s.add_argument("file")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("validate", help="valida patches sem tocar na pedaleira")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("export", help="converte um patch .json em .syx")
    s.add_argument("file")
    s.add_argument("-o", "--output")
    s.add_argument("--model", default="G1Xon", choices=["G1on", "G1Xon"])
    s.add_argument("--level", type=int, default=80, help="usado se o patch tiver level null")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("push", help="envia um patch .json para uma memória")
    s.add_argument("file")
    s.add_argument("--slot", required=True, help="ex.: A7")
    s.add_argument("--store", action="store_true", help="grava automaticamente")
    s.set_defaults(func=cmd_push)

    s = sub.add_parser("pull", help="lê uma memória e salva como .json")
    s.add_argument("--slot", required=True)
    s.add_argument("-o", "--output")
    s.add_argument("--short", action="store_true", help="omite parâmetros iguais ao padrão")
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser("backup", help="salva memórias em .syx (pasta backups/)")
    s.add_argument("slots", nargs="*")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="grava um .syx numa memória")
    s.add_argument("file")
    s.add_argument("--slot", required=True)
    s.add_argument("--store", action="store_true")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("scan", help="lista os efeitos das memórias que o catálogo não conhece")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("ui", help="abre a interface no navegador")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--patches", default="patches", help="pasta dos patches .json")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_ui)

    args = ap.parse_args(argv)
    try:
        cat = Catalog(root=args.catalog)
        return args.func(args, cat) or 0
    except (CatalogError, P.PatchError, MidiError) as e:
        print("erro:", e, file=sys.stderr)
        return 2
    except OSError as e:
        if e.errno == 19:
            print("erro: a pedaleira sumiu do USB no meio da operação. Nada foi gravado; "
                  "reconecte (de preferência com fonte) e tente de novo.", file=sys.stderr)
            return 3
        raise
    except KeyboardInterrupt:
        print("\ncancelado", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
