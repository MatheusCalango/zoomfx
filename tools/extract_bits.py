#!/usr/bin/env python3
"""Extrai as especificações de bits de effects.py (sysex_tones/g1on) lendo o
arquivo como TEXTO (não executa o código de terceiros).

Saída (JSON): { efeito: { "N": [[byte, máscara, deslocamento], ...] | "max150" | "cab" } }
deslocamento > 0 = shift à esquerda ao gravar no byte.
"""
import json
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
func_re = re.compile(r"^def set_([a-z0-9]+)_param_(\d+)\( d, v \):\n(.*?)(?=^def |\Z)", re.S | re.M)
line_re = re.compile(r"retval\[(\d+)\] \|= \(?\(?v & (0x[0-9a-fA-F]+)\)?(?: (>>|<<) (\d+))?\)?")

out = {}
problems = []
for name, idx, body in func_re.findall(src):
    if "set_max_150" in body:
        spec = "max150"
    elif "set_cab_sim" in body:
        spec = "cab"
    else:
        merged = {}
        for m in line_re.finditer(body):
            byte, mask, op, sh = int(m.group(1)), int(m.group(2), 16), m.group(3), m.group(4)
            s = 0 if not op else (-int(sh) if op == ">>" else int(sh))
            # na fonte: retval[b] |= ((v & mask) >> s)  =>  bit do valor 'mask' vai para mask>>s
            merged[(byte, s)] = merged.get((byte, s), 0) | mask
        n_lines = len(re.findall(r"retval\[\d+\] \|=", body))
        if n_lines != len(line_re.findall(body)):
            problems.append("%s_param_%s" % (name, idx))
        spec = [[b, mk, s] for (b, s), mk in sorted(merged.items(), key=lambda kv: -kv[1])]
    out.setdefault(name, {})[idx] = spec

json.dump(out, open(sys.argv[2], "w"), indent=1, sort_keys=True)
print("efeitos:", len(out), "| parâmetros:", sum(len(v) for v in out.values()))
print("linhas não reconhecidas:", problems)
