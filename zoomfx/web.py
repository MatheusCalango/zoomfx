"""Interface web local: `zoomfx ui` sobe um servidor em 127.0.0.1 e abre o navegador.

Só biblioteca padrão. O servidor atende uma requisição por vez, o que também
garante que só uma operação MIDI aconteça de cada vez.
"""

import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

from . import patch as P
from . import service
from .catalog import CatalogError
from .midi import MidiError, load_syx

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SAFE_NAME = re.compile(r"^[\w\- .]+\.json$")


class App:
    def __init__(self, catalog, patches_dir, backups_dir, device=None):
        self.cat = catalog
        self.patches_dir = os.path.abspath(patches_dir)
        self.backups_dir = os.path.abspath(backups_dir)
        self.device = device
        self.pending = None      # gravação esperando o usuário salvar no pedal

    # -- catálogo e arquivos ------------------------------------------------------
    def catalog(self, _body):
        effects = []
        for e in sorted(self.cat.effects.values(), key=lambda x: (x.category, x.name)):
            effects.append({
                "id": e.id, "name": e.name, "category": e.category,
                "description": e.description, "status": e.status,
                "params": [{"name": p.name, "page": p.page, "type": p.type, "min": p.min,
                            "max": p.max, "options": p.options, "default": p.default,
                            "pedal": p.pedal, "note": p.note} for p in e.params],
            })
        d = self.cat.device
        return {"device": {"id": d["id"], "name": d["name"]},
                "max_effects": self.cat.layout.max_effects,
                "name_length": self.cat.layout.name_length,
                "slots": [self.cat.slot_name(i) for i in range(self.cat.slot_count)],
                "effects": effects}

    def _patch_path(self, name):
        if not SAFE_NAME.match(name):
            raise P.PatchError("nome de arquivo inválido: use letras, números, espaço, - ou _ e termine em .json")
        return os.path.join(self.patches_dir, name)

    def list_patches(self, _body):
        os.makedirs(self.patches_dir, exist_ok=True)
        out = []
        for f in sorted(os.listdir(self.patches_dir)):
            if f.endswith(".json"):
                try:
                    doc = P.load(os.path.join(self.patches_dir, f))
                    out.append({"file": f, "name": doc.get("name", ""), "description": doc.get("description", "")})
                except P.PatchError as e:
                    out.append({"file": f, "name": "?", "description": str(e)})
        return {"patches": out}

    def get_patch(self, body):
        return {"doc": P.load(self._patch_path(body["file"]))}

    def save_patch(self, body):
        doc = body["doc"]
        warnings = P.validate(doc, self.cat)
        doc.setdefault("$schema", "../schema/patch.schema.json")
        P.save(self._patch_path(body["file"]), doc)
        return {"saved": body["file"], "warnings": warnings}

    def delete_patch(self, body):
        os.remove(self._patch_path(body["file"]))
        return {"deleted": body["file"]}

    def validate(self, body):
        try:
            return {"ok": True, "warnings": P.validate(body["doc"], self.cat), "errors": []}
        except P.PatchError as e:
            errs = str(e).split("\n  - ")[1:] or [str(e)]
            return {"ok": False, "warnings": [], "errors": errs}

    def list_backups(self, _body):
        if not os.path.isdir(self.backups_dir):
            return {"backups": []}
        out = []
        for f in sorted(os.listdir(self.backups_dir), reverse=True):
            if f.endswith(".syx"):
                try:
                    doc = P.decode(load_syx(os.path.join(self.backups_dir, f), self.cat), self.cat)
                    out.append({"file": f, "name": doc["name"]})
                except Exception:
                    out.append({"file": f, "name": "?"})
        return {"backups": out}

    def open_backup(self, body):
        f = os.path.basename(body["file"])
        doc = P.decode(load_syx(os.path.join(self.backups_dir, f), self.cat), self.cat, full=False)
        doc["description"] = "Importado do backup %s" % f
        return {"doc": doc}

    # -- pedaleira -----------------------------------------------------------------
    def _close_pending(self):
        if self.pending:
            try:
                self.pending["pedal"].__exit__(None, None, None)
            except Exception:
                pass
            self.pending = None

    def status(self, _body):
        self._close_pending()
        pedal, info = service.connect(self.cat, self.device)
        with pedal:
            return {"connected": True, **info}

    def pull(self, body):
        self._close_pending()
        slot = self.cat.slot_number(body["slot"])
        pedal, _ = service.connect(self.cat, self.device)
        with pedal:
            data = pedal.read_patch(slot)
        if data is None:
            raise MidiError("não consegui ler %s" % self.cat.slot_name(slot))
        doc = P.decode(data, self.cat, full=False)
        doc["description"] = "Lido da memória %s" % self.cat.slot_name(slot)
        return {"doc": doc}

    def _write(self, slot, new, store, backup_path):
        name = self.cat.slot_name(slot)
        pedal = self.pending_pedal
        ok, back = service.load_to_buffer(pedal, slot, new)
        if not ok:
            pedal.__exit__(None, None, None)
            raise MidiError("a pedaleira devolveu um patch diferente do enviado; nada foi gravado")
        if store and service.try_store(pedal, slot, new):
            pedal.__exit__(None, None, None)
            return {"state": "stored", "slot": name, "backup": os.path.basename(backup_path)}
        # mantém a conexão aberta: sair do modo editor descartaria o patch
        self.pending = {"pedal": pedal, "slot": slot, "patch": new}
        return {"state": "awaiting_save", "slot": name, "backup": os.path.basename(backup_path),
                "store_tried": bool(store)}

    def push(self, body):
        self._close_pending()
        doc = body["doc"]
        P.validate(doc, self.cat)
        slot = self.cat.slot_number(body["slot"])
        pedal, _ = service.connect(self.cat, self.device)
        try:
            old, bak = service.backup(pedal, self.cat, slot, self.backups_dir)
            new = P.encode(doc, self.cat, level_fallback=old[self.cat.layout.level_byte])
        except Exception:
            pedal.__exit__(None, None, None)
            raise
        self.pending_pedal = pedal
        return self._write(slot, new, body.get("store", True), bak)

    def restore(self, body):
        self._close_pending()
        f = os.path.basename(body["file"])
        data = load_syx(os.path.join(self.backups_dir, f), self.cat)
        slot = self.cat.slot_number(body["slot"])
        pedal, _ = service.connect(self.cat, self.device)
        try:
            _, bak = service.backup(pedal, self.cat, slot, self.backups_dir)
        except Exception:
            pedal.__exit__(None, None, None)
            raise
        self.pending_pedal = pedal
        return self._write(slot, data, body.get("store", True), bak)

    def confirm(self, _body):
        if not self.pending:
            raise MidiError("não há gravação esperando confirmação")
        p = self.pending
        ok = service.is_stored(p["pedal"], p["slot"], p["patch"])
        name = self.cat.slot_name(p["slot"])
        if ok:
            self._close_pending()
            return {"state": "stored", "slot": name}
        return {"state": "awaiting_save", "slot": name, "not_yet": True}

    def cancel(self, _body):
        self._close_pending()
        return {"state": "cancelled"}

    ROUTES = {
        "catalog": "catalog", "patches/list": "list_patches", "patches/get": "get_patch",
        "patches/save": "save_patch", "patches/delete": "delete_patch", "validate": "validate",
        "backups/list": "list_backups", "backups/open": "open_backup",
        "pedal/status": "status", "pedal/pull": "pull", "pedal/push": "push",
        "pedal/restore": "restore", "pedal/confirm": "confirm", "pedal/cancel": "cancel",
    }


def make_handler(app, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    html = f.read().replace(b"__TOKEN__", token.encode())
                return self._send(200, html, "text/html; charset=utf-8")
            self._send(404, {"error": "não encontrado"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/") or path[5:] not in App.ROUTES:
                return self._send(404, {"error": "rota inexistente"})
            # protege contra outros sites mandando comandos para a pedaleira
            if self.headers.get("X-Zoomfx-Token") != token:
                return self._send(403, {"error": "token inválido"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                result = getattr(app, App.ROUTES[path[5:]])(body)
                self._send(200, result)
            except (CatalogError, P.PatchError, MidiError, KeyError, ValueError) as e:
                self._send(400, {"error": str(e)})
            except OSError as e:
                msg = ("a pedaleira sumiu do USB no meio da operação; nada foi gravado"
                       if e.errno == 19 else str(e))
                app.pending = None
                self._send(500, {"error": msg})

    return Handler


def serve(catalog, port=8765, open_browser=True, patches_dir="patches", backups_dir="backups", device=None):
    import secrets
    token = secrets.token_urlsafe(16)
    app = App(catalog, patches_dir, backups_dir, device)
    httpd = HTTPServer(("127.0.0.1", port), make_handler(app, token))
    url = "http://127.0.0.1:%d/" % port
    print("zoomfx rodando em %s  (Ctrl+C para sair)" % url)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app._close_pending()
        httpd.server_close()
