"""Codificação dos patches da família G1on: bits dos parâmetros e o
"embaralhamento" dos 128 bytes.

Porte do trabalho de engenharia reversa do projeto sysex_tones/g1on
(https://github.com/SysExTones/g1on, AGPL-3.0).
"""

# --------------------------------------------------------------------------
# Campos de bits
# --------------------------------------------------------------------------
# Um campo é uma lista de [byte, máscara_do_valor, deslocamento]:
# os bits 'máscara' do valor vão para o byte, deslocados ('deslocamento' > 0
# = para a esquerda). Codificações especiais têm nome: "max150", "cab".


def _shift(x, s):
    return (x << s) if s >= 0 else (x >> -s)


def set_field(data, encoding, raw):
    if encoding == "max150":
        if raw & 0x80:
            data[8] |= 0x40
        data[9] |= raw & 0x7F
    elif encoding == "cab":
        # posição na tabela de caixas × 2 (confirmado nos patches de fábrica da G1Xon)
        data[16] |= 0x20
        data[15] |= (raw << 1) & 0x3E
    else:
        for byte, mask, s in encoding:
            data[byte] |= _shift(raw & mask, s)


def get_field(data, encoding):
    """Retorna o valor bruto. Para "cab", None se a caixa não foi definida."""
    if encoding == "max150":
        return (data[9] & 0x7F) | (0x80 if data[8] & 0x40 else 0)
    if encoding == "cab":
        return ((data[15] & 0x3E) >> 1) if data[16] & 0x20 else None
    raw = 0
    for byte, mask, s in encoding:
        raw |= _shift(data[byte] & _shift(mask, s), -s)
    return raw


def field_width(encoding):
    if encoding == "max150":
        return 8
    if encoding == "cab":
        return 5
    return sum(bin(m).count("1") for _, m, _ in encoding)


def effect_tag(effect_bytes):
    e = effect_bytes
    return (e[0] & 0x40, e[1] & 0x7A, e[2] & 0x07, e[4] & 0x1E)


# --------------------------------------------------------------------------
# Embaralhamento
# --------------------------------------------------------------------------

def _move_byte(e, a, b):
    e.insert(b, e.pop(a))


def _swap_bits(e, mask, shift, ia, ib):
    a = e[ia] & mask
    e[ia] &= 0xFF ^ mask
    m2 = _shift(mask, shift)
    b = e[ib] & m2
    e[ib] &= 0xFF ^ m2
    e[ia] |= _shift(b, -shift)
    e[ib] |= _shift(a, shift)


def _exchange_bits(ea, eb, mask, shift, ia, ib):
    a = ea[ia] & mask
    ea[ia] &= 0xFF ^ mask
    m2 = _shift(mask, shift)
    b = eb[ib] & m2
    eb[ib] &= 0xFF ^ m2
    ea[ia] |= _shift(b, -shift)
    eb[ib] |= _shift(a, shift)


class Layout:
    """Posições fixas dentro dos 128 bytes (vêm do device.json)."""

    def __init__(self, cfg):
        self.size = cfg["size"]
        self.max_effects = cfg["max_effects"]
        self.slots = [tuple(x) for x in cfg["effect_slots"]]
        self.count_bytes = cfg["effect_count_bytes"]
        self.level_byte = cfg["level_byte"]
        self.name_offset = cfg["name_offset"]
        self.name_skip = set(cfg["name_skip"])
        self.name_length = cfg["name_length"]

    # -- nome ---------------------------------------------------------------
    def _name_positions(self):
        pos, i = [], self.name_offset
        while len(pos) < self.name_length:
            if i not in self.name_skip:
                pos.append(i)
            i += 1
        return pos

    def set_name(self, data, name):
        name = name[: self.name_length].ljust(self.name_length)
        for i, ch in zip(self._name_positions(), name):
            data[i] = ord(ch)

    def get_name(self, data):
        chars = [data[i] for i in self._name_positions()]
        return "".join(chr(c) for c in chars if 32 <= c <= 126).strip()

    # -- embaralhar ---------------------------------------------------------
    def scramble(self, data):
        """data (descomprimido) -> patch como a pedaleira espera."""
        p = list(data)
        e1, e2, e3, e4, e5 = [list(data[a:b]) for a, b in self.slots]
        if e5[0] & 0x40:
            _exchange_bits(e5, e4, 0x40, -2, 0, 17)
        if e4[0] & 0x40:
            _exchange_bits(e4, e3, 0x40, -5, 0, 16)
        if e3[0] & 0x40:
            _exchange_bits(e3, e2, 0x40, -1, 0, 17)
        if e2[0] & 0x40:
            _exchange_bits(e2, e1, 0x40, -4, 0, 16)
        _exchange_bits(e5, e4, 0x0C, -2, 0, 17)
        _swap_bits(e5, 0x02, 5, 8, 16)
        _swap_bits(e5, 0x20, -2, 16, 16)
        _swap_bits(e5, 0x7C, -2, 8, 8)
        _swap_bits(e5, 0x02, 5, 0, 8)
        _swap_bits(e4, 0x20, -5, 16, 16)
        _swap_bits(e4, 0x1E, 2, 8, 16)
        _swap_bits(e4, 0x60, -5, 8, 8)
        _swap_bits(e4, 0x0E, 2, 0, 8)
        _exchange_bits(e3, e2, 0x0E, -1, 0, 17)
        _swap_bits(e3, 0x20, -1, 16, 16)
        _swap_bits(e3, 0x7E, -1, 8, 8)
        _swap_bits(e2, 0x20, -4, 16, 16)
        _swap_bits(e2, 0x0E, 3, 8, 16)
        _swap_bits(e2, 0x70, -4, 8, 8)
        _swap_bits(e2, 0x0E, 3, 0, 8)
        _move_byte(e5, 8, 6)
        _move_byte(e5, 16, 14)
        _move_byte(e4, 19, 20)
        _move_byte(e4, 17, 19)
        _move_byte(e4, 16, 11)
        _move_byte(e4, 8, 3)
        _move_byte(e3, 16, 15)
        _move_byte(e3, 8, 7)
        _move_byte(e2, 20, 19)
        _move_byte(e2, 17, 20)
        _move_byte(e2, 16, 12)
        _move_byte(e2, 8, 4)
        for (a, b), e in zip(self.slots, (e1, e2, e3, e4, e5)):
            p[a:b] = e[: b - a]
        b96, b103 = self.count_bytes
        count = data[b103]
        p[b96] &= 0xFE
        p[b103] = 0
        if count in (4, 5):
            p[b96] |= 0x01
        if count in (1, 5):
            p[b103] |= 0x20
        elif count == 2:
            p[b103] |= 0x40
        elif count == 3:
            p[b103] |= 0x60
        return p

    def descramble(self, patch):
        """patch da pedaleira -> (data descomprimido, número de efeitos)."""
        d = list(patch)
        b96, b103 = self.count_bytes
        if d[b96] & 0x01:
            count = 5 if d[b103] & 0x20 else 4
        elif (d[b103] & 0x60) == 0x60:
            count = 3
        elif d[b103] & 0x40:
            count = 2
        elif d[b103] & 0x20:
            count = 1
        else:
            count = 0
        d[b96] &= 0xFE
        d[b103] = count
        e1, e2, e3, e4, e5 = [d[a:b] for a, b in self.slots]
        _move_byte(e2, 4, 8)
        _move_byte(e2, 12, 16)
        _move_byte(e2, 20, 17)
        _move_byte(e2, 19, 20)
        _move_byte(e3, 7, 8)
        _move_byte(e3, 15, 16)
        _move_byte(e4, 3, 8)
        _move_byte(e4, 11, 16)
        _move_byte(e4, 19, 17)
        _move_byte(e4, 20, 19)
        _move_byte(e5, 14, 16)
        _move_byte(e5, 6, 8)
        _swap_bits(e2, 0x70, -3, 8, 0)
        _swap_bits(e2, 0x07, 4, 8, 8)
        _swap_bits(e2, 0x70, -3, 16, 8)
        _swap_bits(e2, 0x02, 4, 16, 16)
        _swap_bits(e3, 0x3F, 1, 8, 8)
        _swap_bits(e3, 0x10, 1, 16, 16)
        _exchange_bits(e2, e3, 0x07, 1, 17, 0)
        _swap_bits(e4, 0x38, -2, 8, 0)
        _swap_bits(e4, 0x03, 5, 8, 8)
        _swap_bits(e4, 0x78, -2, 16, 8)
        _swap_bits(e4, 0x01, 5, 16, 16)
        _swap_bits(e5, 0x40, -5, 8, 0)
        _swap_bits(e5, 0x1F, 2, 8, 8)
        _swap_bits(e5, 0x08, 2, 16, 16)
        _swap_bits(e5, 0x40, -5, 16, 8)
        _exchange_bits(e4, e5, 0x03, 2, 17, 0)
        if e1[16] & 0x04:
            _exchange_bits(e1, e2, 0x04, 4, 16, 0)
        if e2[17] & 0x20:
            _exchange_bits(e2, e3, 0x20, 1, 17, 0)
        if e3[16] & 0x02:
            _exchange_bits(e3, e4, 0x02, 5, 16, 0)
        if e4[17] & 0x10:
            _exchange_bits(e4, e5, 0x10, 2, 17, 0)
        for (a, b), e in zip(self.slots, (e1, e2, e3, e4, e5)):
            d[a:b] = e[: b - a]
        return d, count
