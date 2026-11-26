"""Transporte MIDI: dispositivo bruto do ALSA (/dev/snd/midiC*D*), sem dependências."""

import glob
import os
import select
import time


class MidiError(RuntimeError):
    pass


def find_device(keywords=("zoom",)):
    """Procura a pedaleira em /proc/asound/cards. Retorna (caminho, descrição)."""
    cards = {}
    try:
        with open("/proc/asound/cards") as f:
            for line in f:
                line = line.strip()
                if line and line[0].isdigit():
                    cards[line.split()[0]] = line
    except OSError:
        pass
    for dev in sorted(glob.glob("/dev/snd/midiC*D*")):
        card = dev.split("midiC")[1].split("D")[0]
        desc = cards.get(card, "")
        if any(k in desc.lower() for k in keywords):
            return dev, desc
    return None, None


class Pedal:
    """Conversa com uma pedaleira da família G1on usando os comandos do device.json."""

    def __init__(self, path, catalog):
        self.catalog = catalog
        self.cmd = catalog.device["midi"]["commands"]
        self.mfr = catalog.device["midi"]["manufacturer_id"]
        self.size = catalog.layout.size
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise MidiError("sem permissão em %s: adicione seu usuário ao grupo 'audio' "
                            "(sudo usermod -aG audio $USER) e entre de novo na sessão" % path)
        except OSError as e:
            raise MidiError("não consegui abrir %s: %s (o ToneLib está aberto?)" % (path, e))
        self.buf = bytearray()
        self.model_id = None
        self.model = None
        self.firmware = None

    # -- baixo nível ------------------------------------------------------------
    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            if self.model_id is not None:
                self.editor(False)
        except OSError:
            pass
        self.close()

    def send(self, msg, pause=0.05):
        os.write(self.fd, bytes(msg))
        time.sleep(pause)

    def drain(self):
        time.sleep(0.2)
        try:
            while os.read(self.fd, 4096):
                pass
        except (BlockingIOError, OSError):
            pass
        self.buf.clear()

    def read_sysex(self, prefix, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    self.buf += os.read(self.fd, 4096)
                except BlockingIOError:
                    pass
            while 0xF0 in self.buf:
                s = self.buf.index(0xF0)
                if 0xF7 not in self.buf[s:]:
                    break
                e = self.buf.index(0xF7, s)
                msg = list(self.buf[s:e + 1])
                del self.buf[:e + 1]
                if msg[:len(prefix)] == list(prefix):
                    return msg
        return None

    def _zoom(self, body):
        return [0xF0, self.mfr, 0x00, self.model_id] + list(body) + [0xF7]

    def _command(self, name, slot=None):
        return [slot if x == "SLOT" else x for x in self.cmd[name]]

    # -- alto nível -------------------------------------------------------------
    def identify(self):
        self.drain()
        self.send([0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7])
        r = self.read_sysex([0xF0, 0x7E, 0x00, 0x06, 0x02, self.mfr])
        if not r:
            raise MidiError("a pedaleira não respondeu à identificação")
        self.model_id = r[6]
        self.firmware = "".join(chr(c) for c in r[10:14] if 32 <= c < 127)
        self.model = self.catalog.model_name(self.model_id)
        if not self.model:
            raise MidiError("modelo 0x%02X não está no catálogo %s; abortando por segurança"
                            % (self.model_id, self.catalog.device["id"]))
        return self.model, self.firmware

    def editor(self, on=True):
        self.send(self._zoom(self._command("editor_on" if on else "editor_off")))

    def select(self, slot):
        self.send([0xC0, slot], pause=0.3)
        self.drain()

    def read_patch(self, slot):
        """Lê o patch GRAVADO na memória 'slot'."""
        self.send(self._zoom(self._command("request_patch") + [slot]), pause=0)
        r = self.read_sysex(self._zoom(self._command("patch"))[:-1])
        if not r:
            return None
        payload = r[4 + len(self.cmd["patch"]):-1]       # [slot, h1, h2, 128 bytes]
        if len(payload) < 3 + self.size or payload[0] != slot:
            return None
        self.patch_header = payload[1:3]
        return payload[3:3 + self.size]

    def write_patch(self, slot, patch):
        """EXPERIMENTAL: grava direto na memória, devolvendo a mensagem de leitura (0x08)."""
        header = getattr(self, "patch_header", None)
        if header is None:
            if self.read_patch(slot) is None:
                raise MidiError("não consegui ler %s antes de gravar" % self.catalog.slot_name(slot))
            header = self.patch_header
        self.send(self._zoom(self._command("write_patch", slot) + list(header) + list(patch)), pause=1.0)

    def read_current(self):
        """Lê o buffer de edição (o patch que está soando)."""
        self.send(self._zoom(self._command("request_current_patch")), pause=0)
        r = self.read_sysex(self._zoom(self._command("current_patch"))[:-1])
        n = 4 + len(self.cmd["current_patch"])
        if not r or len(r) < n + self.size + 1:
            return None
        return r[n:n + self.size]

    def write_current(self, patch):
        """Escreve no buffer de edição (não grava)."""
        self.send(self._zoom(self._command("current_patch") + list(patch)), pause=0.3)

    def store(self, slot):
        self.send(self._zoom(self._command("store_patch", slot)), pause=1.0)

    # -- arquivos .syx ------------------------------------------------------------
    def syx_bytes(self, patch):
        return bytes(self._zoom(self._command("current_patch") + list(patch)))


def load_syx(path, catalog):
    raw = list(open(path, "rb").read())
    size = catalog.layout.size
    head = 4 + len(catalog.device["midi"]["commands"]["current_patch"])
    if len(raw) != head + size + 1 or raw[0] != 0xF0 or raw[-1] != 0xF7 \
            or raw[1] != catalog.device["midi"]["manufacturer_id"]:
        raise MidiError("arquivo .syx inválido para %s: %s" % (catalog.device["name"], path))
    return raw[head:head + size]


def save_syx(path, catalog, model_id, patch):
    cmd = catalog.device["midi"]["commands"]["current_patch"]
    data = [0xF0, catalog.device["midi"]["manufacturer_id"], 0x00, model_id] + cmd + list(patch) + [0xF7]
    with open(path, "wb") as f:
        f.write(bytes(data))
