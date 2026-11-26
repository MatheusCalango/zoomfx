# zoomfx

Crie patches para a **Zoom G1on / G1Xon** em arquivos **JSON** e grave na pedaleira pelo USB, sem precisar do ToneLib.

```json
{
  "format": "zoomfx-patch/1",
  "name": "HEAVY",
  "level": null,
  "chain": [
    { "effect": "ZNR",       "params": { "THRSH": 10 } },
    { "effect": "T Scream",  "params": { "Gain": 0, "Tone": 55, "Level": 130 } },
    { "effect": "BG DRIVE",  "params": { "Gain": 60, "Middl": 50, "OUT": "COMBO FRONT" } },
    { "effect": "GraphicEQ", "params": { "160Hz": 2, "400Hz": -2, "3.2kHz": 2 } },
    { "effect": "Room",      "params": { "Decay": 6, "Mix": 8 } }
  ]
}
```

```bash
zoomfx push patches/heavy.json --slot A7 --store
```

> ⚠️ A Zoom não publica o protocolo MIDI da G1on. Este projeto usa a engenharia reversa do
> [sysex_tones/g1on](https://github.com/SysExTones/g1on). Antes de qualquer gravação o programa
> faz **backup** da memória de destino, e depois **confere byte a byte** o que a pedaleira devolveu.

## Instalação

Requisitos: Linux (usa o MIDI bruto do ALSA, `/dev/snd/midi*`) e Python 3.8+. Não tem dependências.

```bash
git clone https://github.com/MatheusCalango/zoomfx
cd zoomfx
pip install -e .          # ou rode sem instalar: python -m zoomfx ...
```

Se aparecer "sem permissão em /dev/snd/midi…": `sudo usermod -aG audio $USER` e entre de novo na sessão.

**Feche o ToneLib antes de usar:** ele ocupa a porta MIDI.

## Comandos

| Comando | O que faz |
|---|---|
| `zoomfx effects` | Lista os 75 efeitos do catálogo, por categoria |
| `zoomfx effects "BG DRIVE"` | Mostra os parâmetros, as faixas e os padrões de um efeito |
| `zoomfx validate patches/*.json` | Confere os patches sem tocar na pedaleira |
| `zoomfx show patches/heavy.json` | Mostra o patch com todos os valores (aceita `.syx` também) |
| `zoomfx push arquivo.json --slot A7 [--store]` | Faz backup, envia, confere e grava |
| `zoomfx pull --slot A7 [-o meu.json]` | Lê uma memória e salva como JSON |
| `zoomfx backup --all` ou `zoomfx backup A0 A1` | Salva memórias em `backups/*.syx` |
| `zoomfx restore backups/x.syx --slot A7 [--store]` | Devolve um backup para uma memória |
| `zoomfx export arquivo.json` | Gera um `.syx` sem conectar na pedaleira |

As memórias vão de **A0 a J9** (10 bancos com 10 memórias cada).

### Como a gravação funciona

1. Lê a memória de destino e salva em `backups/`.
2. Seleciona a memória e envia o patch para o **buffer de edição**.
3. Lê o buffer de volta e compara com o que foi enviado.
4. Grava a memória:
   - com `--store`, o programa manda gravar e confere relendo a memória;
   - sem `--store`, ou se a gravação automática falhar, o programa espera você salvar na própria pedaleira e confere depois que você aperta Enter.

O buffer de edição é descartado quando o programa sai do modo editor, quando você troca de patch ou quando a pedaleira reinicia. Use a pedaleira **na fonte ou com pilhas**: só com a alimentação do USB ela pode reiniciar no meio do processo.

## Formato do patch

| Campo | Obrigatório | Significado |
|---|---|---|
| `format` | sim | Sempre `"zoomfx-patch/1"` |
| `name` | sim | Até 10 caracteres ASCII (sem acentos) |
| `level` | não | Patch level de 0 a 127. `null` mantém o da memória de destino. |
| `description` | não | Texto livre (o JSON não tem comentários) |
| `chain` | sim | De 1 a 5 efeitos, na ordem da pedaleira |
| `chain[].effect` | sim | Id (`bgdrive`) ou nome da tela (`BG DRIVE`) |
| `chain[].on` | não | `false` deixa o efeito salvo, mas desligado (padrão `true`) |
| `chain[].params` | não | Valores **como aparecem na tela**. Parâmetros omitidos usam o padrão do catálogo. |

Erros comuns são detectados antes de enviar: valor fora da faixa, opção inválida, parâmetro ou efeito com nome errado (com sugestão do nome certo), mais de 5 efeitos. O arquivo `schema/patch.schema.json` dá autocompletar e validação no VS Code.

## Estrutura

```
catalog/g1on/
  device.json              IDs MIDI, comandos SysEx, layout dos 128 bytes
  effects/<categoria>/*.json   um arquivo por efeito
patches/                   patches de exemplo
schema/                    JSON Schema dos patches
zoomfx/
  codec.py                 campos de bits + embaralhamento dos 128 bytes
  catalog.py               carrega e valida o catálogo; conversão tela <-> bruto
  patch.py                 JSON <-> bytes, validação
  midi.py                  transporte USB-MIDI (ALSA)
  cli.py                   comandos
tools/                     gerador do catálogo
tests/                     testes (python -m unittest discover -s tests)
```

## Catálogo de efeitos

Cada efeito é um JSON:

```json
{
  "id": "bgdrive",
  "name": "BG DRIVE",
  "category": "amp",
  "description": "Mesa Boogie Dual Rectifier, canal vermelho (Vintage).",
  "tag": [0, 64, 2, 8],
  "status": "tested",
  "params": [
    { "name": "Gain", "page": 1, "type": "int", "min": 0, "max": 100, "default": 50,
      "pedal": true, "encoding": [[5, 124, -2], [0, 2, 2], [4, 1, 6]] }
  ]
}
```

- **`tag`**: os 4 bits que identificam o efeito no patch.
- **`type`**: `int` (faixa `min..max`), `enum` (lista `options`) ou `raw` (codificação ainda desconhecida, valor bruto).
- **`encoding`**: lista de `[byte, máscara do valor, deslocamento]`. Os bits `máscara` do valor bruto vão para o `byte`, deslocados (positivo = para a esquerda). Codificações especiais: `"max150"` (Level 0–150) e `"cab"`.
- **Conversão tela → bruto**: para `int`, bruto = valor − `min`; para `enum`, bruto = posição na lista.
- **`default`**: valor neutro usado quando o patch omite o parâmetro. Não é o padrão de fábrica da Zoom. `null` = opcional (o CAB dos amps só é gravado se for informado).
- **`status`**: `tested` (já passou pela pedaleira) ou `unverified`.

### Status atual

- **Testados numa G1Xon real:** ZNR, T Scream, BG DRIVE, GraphicEQ, Room.
- **Gerados, mas não testados:** os outros 70 efeitos (dynamics, filter, drive, amp, eq, modulation, delay, reverb).
- **Ainda não suportados:** os efeitos de pedal da G1Xon (PedalVx, PedalCry, WAH100, PDL Pitch, PDL MnPit), porque a tag deles não é conhecida.
- **Parâmetros `raw`:** 160 Comp Ratio, ParaEQ Freq1/Freq2 e CarbonDly Sync.
- **Suposições a confirmar na tela:**
  - a regra "bruto = valor − mínimo" nos parâmetros cuja escala não começa em 0 (ZNR THRSH, Room Decay/PreD, bandas do EQ etc.);
  - a ordem das opções do OUT e do CAB;
  - o comando de gravação (`--store`), que veio da MS-50G.

Para confirmar um efeito: grave um patch com ele, compare com a tela da pedaleira e, se bater, mude o `status` para `tested`. Outra forma: monte um patch na pedaleira, rode `zoomfx pull` e compare com o que a tela mostra.

### Adicionar ou corrigir efeitos

O catálogo é gerado por `tools/build_catalog.py`, a partir de três fontes:

- `tools/data/bits.json`: bits de cada parâmetro, extraídos do `effects.py` do sysex_tones por `tools/extract_bits.py`, que lê o arquivo como texto, sem executar;
- `tools/data/tags.json`: tags dos efeitos;
- `tools/official_params.json`: nomes, páginas e faixas da lista oficial de efeitos da Zoom.

```bash
python3 tools/build_catalog.py tools/data/bits.json tools/data/tags.json \
        tools/official_params.json catalog/g1on/effects
```

O gerador confere se o número de parâmetros da lista oficial bate com o da engenharia reversa e se cada faixa cabe nos bits do campo. Para um efeito novo, por exemplo um de pedal quando a tag for descoberta, também dá para escrever o JSON dele direto em `catalog/g1on/effects/<categoria>/`.

## Licença

AGPL-3.0-or-later. O codec e os dados do catálogo derivam do [sysex_tones/g1on](https://github.com/SysExTones/g1on) (AGPL-3.0). Os nomes de produtos e marcas citados pertencem aos seus donos e aparecem só para identificar os modelos simulados pela Zoom.
