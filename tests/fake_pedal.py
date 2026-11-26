"""Emulador mínimo de uma G1Xon para testes, num pseudo-terminal.

    python3 tests/fake_pedal.py [--no-store]    # imprime o caminho do dispositivo

Responde: identificação, modo editor, troca de patch (C0), leitura de memória (09),
leitura/escrita do buffer de edição (29/28), gravação direta (08) e store (32).
Com --no-store, os comandos de gravação são ignorados (simula a pedaleira que exige salvar no painel);
nesse caso mande SIGUSR1 para o processo para "apertar o botão de salvar".
"""

import os
import pty
import signal
import sys
import tty

MODEL = 0x64
SIZE = 128


class Fake:
    def __init__(self, store_works=True):
        self.store_works = store_works
        self.mem = [[0] * SIZE for _ in range(100)]
        for i, m in enumerate(self.mem):          # nome "SLOT nn" em cada memória
            for j, ch in enumerate(("SLOT%02d" % i).ljust(10)):
                m[116 + j + (1 if j >= 4 else 0)] = ord(ch)
            m[105] = 70
        self.slot = 0
        self.buf = list(self.mem[0])
        self.editor = False

    def handle(self, msg):
        if msg[:5] == [0xF0, 0x7E, 0x00, 0x06, 0x01]:
            return [0xF0, 0x7E, 0x00, 0x06, 0x02, 0x52, MODEL, 0, 0, 0] + list(b"1.21") + [0xF7]
        if msg[:4] != [0xF0, 0x52, 0x00, MODEL]:
            return None
        cmd = msg[4]
        if cmd == 0x50:
            self.editor = True
        elif cmd == 0x51:
            self.editor = False
            self.buf = list(self.mem[self.slot])      # descarta o buffer
        elif cmd == 0x09:
            s = msg[7]
            return [0xF0, 0x52, 0x00, MODEL, 0x08, 0, 0, s, 0, 0] + self.mem[s] + [0xF7]
        elif cmd == 0x29:
            return [0xF0, 0x52, 0x00, MODEL, 0x28] + self.buf + [0xF7]
        elif cmd == 0x28:
            self.buf = msg[5:5 + SIZE]
        elif cmd == 0x08 and self.store_works:          # gravação direta (mensagem de leitura devolvida)
            self.mem[msg[7]] = msg[10:10 + SIZE]
        elif cmd == 0x32 and self.store_works:
            self.mem[msg[8]] = list(self.buf)
        return None

    def save_button(self, *_):
        self.mem[self.slot] = list(self.buf)


def main():
    fake = Fake(store_works="--no-store" not in sys.argv)
    master, slave = pty.openpty()
    tty.setraw(slave)
    tty.setraw(master)
    print(os.ttyname(slave), flush=True)
    signal.signal(signal.SIGUSR1, fake.save_button)
    pending = []
    while True:
        try:
            data = os.read(master, 4096)
        except InterruptedError:
            continue
        except OSError:
            break
        for b in data:
            if b == 0xC0 or (pending and pending[0] == 0xC0):
                pending.append(b)
                if len(pending) == 2:
                    fake.slot = pending[1]
                    fake.buf = list(fake.mem[fake.slot])
                    pending = []
                continue
            if b == 0xF0:
                pending = [b]
            elif pending:
                pending.append(b)
                if b == 0xF7:
                    reply = fake.handle(pending)
                    pending = []
                    if reply:
                        os.write(master, bytes(reply))


if __name__ == "__main__":
    main()
