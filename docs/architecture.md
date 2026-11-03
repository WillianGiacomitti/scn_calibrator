# Scanner de Insetos: referencial de arquitetura (etapa 1)

Versão 1.0, 03/11/2026. Este documento delimita o que se desenvolve na etapa 1 e como as partes conversam. Ele é a referência para o desenvolvimento com o Claude Code nos dois repositórios:

| Frente | Repositório | Guia do Claude Code |
|---|---|---|
| Firmware (ESP32-C6) | `scn_firmware` | `scn_firmware/CLAUDE.md` |
| Aquisição de imagens (programa do PC) | `scn_calibrator` | `scn_calibrator/CLAUDE.md` |

Este arquivo existe em cópias iguais em `docs/architecture.md` nos dois repositórios. A cópia mestre fica na pasta `scanner/` do projeto. **Qualquer mudança em uma cópia (principalmente no protocolo da seção 5) tem que ser feita nas duas.**

Base: `sistema_simples.md` (referência principal), `comutador_vbus_conexoes.md` (Solução D) e `plano_de_acao.md`. `modulos.md` e a pasta `sysml/` são só detalhamento para o TCC e não mandam no código.

---

## 1. Objetivo e escopo

**Objetivo da etapa 1 (até 30/11/2026):** o operador coloca um inseto alfinetado sobre a mesa, digita o código do espécime, e o sistema centraliza a câmera sobre o inseto (erro ≤ 1 mm), tira a foto e salva no PC com o código do espécime, sem intervenção durante o ciclo.

**Dentro da etapa 1**
- Firmware do ESP32-C6: disparo, zoom por pulsos e relé do USB, com protocolo serial padronizado.
- Programa do PC: mover a Ender, comandar o ESP32, tirar a foto e baixar, calibrar (pixels → mm) e centralizar foto a foto, salvar a foto com metadados.

**Fora da etapa 1** (não implementar, mesmo que pareça fácil)
- Vídeo ao vivo (a L120 não dá imagem de captura pela HDMI) e qualquer uso da HDMI.
- Wi-Fi, servidor web, banco de dados, tratamento das imagens.
- Câmera de profundidade, webcam auxiliar, empilhamento de foco, várias fotos por inseto, bandeja com vários insetos.
- Ajuste de foco por varredura de Z e zoom automático. O foco é o **autofoco da própria L120** com a Ender numa altura fixa. O zoom pelo servo só entra se sobrar tempo no fim (o firmware já oferece os comandos).

## 2. Visão geral

```
            G-code (USB serial)
   PC  ─────────────────────────────▶  Ender 5 Plus  (move a câmera em X, Y, Z)
 (Python)
   │        texto ALVO:ACAO (USB serial)
   ├────────────────────────────────▶  ESP32-C6
   │                                    ├─ servo SHOT ─▶ botão de disparo da L120
   │                                    ├─ servo ZOOM ─▶ alavanca de zoom da L120
   │                                    └─ relé (GND do USB da câmera)
   │        fotos (USB, só com o relé fechado)
   └◀───────────────────────────────── Nikon Coolpix L120 (alimentada por fonte 5 V)
```

**Quem decide o quê**
- O **PC orquestra tudo**. Toda decisão (quando disparar, quanto mover, quando ligar o USB) fica no Python.
- O **ESP32 só executa** comandos curtos e responde. Ele não guarda estado do ciclo, não sabe se a foto deu certo e nunca age sozinho, exceto deixar o USB desligado ao ligar.
- A **Ender** só recebe G-code do PC. O firmware dela (Marlin) não é alterado.

## 3. Hardware e ligações

| Peça | Detalhe |
|---|---|
| Máquina | Ender 5 Plus, área 350 × 350 mm, a mesa desce em Z. Firmware Marlin, serial 115200. Fim de curso de X deslocado ~45 mm pelo suporte da câmera (medir; ver seção 11). |
| Microcontrolador | **ESP32-C6** (placa de desenvolvimento tipo ESP32-C6-DevKitC-1). |
| Câmera | Nikon Coolpix L120, alimentada pela entrada 5 V (fonte própria). VID 04B0, PID 0185. |
| Servos | Servo comum (SG92R ou similar), 50 Hz, pulso 500–2400 µs. Alimentação 5 V com GND comum ao ESP32. |
| Comutador USB | **Solução D**: um módulo relé "ESP-01 Relay" (sem o ESP-01) cortando **só o GND** do cabo USB da câmera. D+ e D− direto, VBUS desligado. |

**Pinos do ESP32-C6** (padrão; ficam como constantes no topo do firmware)

| Função | GPIO | Observação |
|---|---|---|
| Servo ZOOM | 18 | PWM 50 Hz |
| Servo SHOT | 19 | PWM 50 Hz |
| Relé do USB | 2 | Ligado no furo GPIO0 do soquete ESP-01 do módulo. **Ativo em nível baixo.** |

Evitar no C6: GPIO 4, 5, 8, 9 e 15 (modo de boot), 12 e 13 (USB nativo), 16 e 17 (UART0). Se os pinos 18/19 não estiverem livres na placa usada, trocar só as constantes e atualizar esta tabela.

**Lógica do relé**

| GPIO2 | Relé | GND do USB | Câmera |
|---|---|---|---|
| BAIXO | fechado | ligado | **conectada** (modo armazenamento, aparece no Windows) |
| ALTO | aberto | aberto | **desconectada** (modo foto, pode disparar) |

A câmera **não fotografa** enquanto está em modo USB. Por isso todo disparo exige `USB:OFF` antes.

## 4. Fluxos principais

### 4.1 Tirar a foto e baixar (`capture_and_download`)
1. PC → ESP32 `USB:OFF` (garante modo foto) → espera `OK`.
2. PC → ESP32 `SHOT:CLICK` → espera `OK` (o ESP32 só responde depois de o servo voltar ao repouso).
3. PC espera `write_wait_s` (padrão 3 s) para a foto ser gravada no cartão.
4. PC → ESP32 `USB:ON` → espera `OK`.
5. PC espera a câmera aparecer no Windows (até `enumerate_timeout_s`, padrão 20 s). Se não aparecer: `USB:OFF`, espera 2 s, uma nova tentativa; se falhar de novo, erro.
6. PC copia **o arquivo mais novo** de `DCIM` para a pasta de trabalho. Confere que o nome é diferente da última foto copiada (senão a foto não foi tirada).
7. PC → ESP32 `USB:OFF` (sempre, inclusive se algo falhar: usar `try/finally`).

Como a L120 aparece no Windows (unidade de disco com letra ou dispositivo MTP sem letra) **ainda não foi verificado**. O módulo `camera.py` tem que tratar os dois casos (seção 6.3), com unidade de disco como padrão.

### 4.2 Calibração (feita uma vez, **sem inseto**, com o ArUco na mesa)
1. `home()` e ir para a posição aproximada do centro da mesa na altura de trabalho `z_work`.
2. Foto → centro do ArUco `p0` (pixels).
3. Mover +`jog_mm` (padrão 10 mm) em X, foto → `px`. Voltar. Mover +`jog_mm` em Y, foto → `py`. Voltar.
4. Montar a matriz **J** (2 × 2, pixels por mm): coluna 1 = `(px − p0) / jog_mm`, coluna 2 = `(py − p0) / jog_mm`. Ela contém escala, sinal e rotação entre a imagem e os eixos da máquina.
5. Conferir: a escala de J deve bater com `lado_do_ArUco_em_px / 25 mm` com diferença de até 5%. Se não bater, a calibração é recusada.
6. Centralizar no ArUco com o algoritmo de 4.3 e salvar a posição X/Y final como **centro da mesa**.
7. Salvar tudo em `calibration.json` (seção 7.2).

A calibração vale para uma altura `z_work` e para o zoom mínimo. Se `z_work` ou o zoom mudarem, recalibrar.

### 4.3 Centralização foto a foto
```
c = centro da imagem (pixels)
para tentativa de 1 a max_photos (padrão 3):
    foto → p = centro do alvo (inseto ou ArUco)
    d_mm = J⁻¹ · (c − p)          # quanto a câmera deve andar em X e Y
    se |d_mm| ≤ tolerance_mm (1 mm): fim, centralizado
    se |d_mm| > max_step_mm (20 mm) ou destino fora da área: ERRO, não move
    move_relative(d_mm) e esperar parar (M400)
se acabar as tentativas: ERRO
```
A análise da imagem pode ser feita numa cópia reduzida (ex.: largura 1600 px), desde que o resultado volte para a escala da foto original, que é a escala em que J foi medida.

### 4.4 Ciclo de um inseto (`scan --id <espécime>`)
1. Carregar `config.json` e `calibration.json`. Sem calibração válida: parar com mensagem pedindo a calibração.
2. ESP32: `PING`, `USB:OFF`.
3. Ender: `home()` e ir para o centro da mesa em `z_work`.
4. Centralizar no **inseto** (4.3).
5. A foto que confirmou o erro ≤ 1 mm é a foto final (não há movimento depois dela).
6. Salvar a foto e os metadados (seção 7.3).
7. Ender vai para a posição de estacionamento. Fim.

Em qualquer erro: `USB:OFF`, parar o movimento (não enviar mais G-code), estacionar se for seguro, mostrar a mensagem e sair com código diferente de zero.

## 5. Protocolo serial PC ↔ ESP32 (contrato)

Esta seção é o contrato entre as duas frentes. O firmware implementa exatamente isto, e o `esp32.py` usa exatamente isto.

**Transporte:** USB serial (USB-CDC do C6 ou ponte UART da placa), 115200 baud, 8N1. Comandos em texto ASCII, uma linha por comando, terminada em `\n` (aceitar também `\r\n`). Maiúsculas e minúsculas são equivalentes. Linha com mais de 64 caracteres é descartada com `ERRO FORMATO`.

**Formato do comando:** `ALVO:ACAO[:VALOR]`, ou só `ALVO` para `PING` e `STATUS`.

**Formato da resposta:**
- Para **cada comando**, o ESP32 envia **exatamente uma linha final**, que começa com `OK` ou `ERRO`, seguida de um espaço e de um texto curto sem acentos.
- A linha final só é enviada **depois** que a ação terminou (servo parado, relé comutado).
- Linhas informativas (depuração) são permitidas e começam com `#`. O PC ignora linhas com `#`.
- Os comandos são processados um de cada vez, na ordem de chegada.

| Comando | Ação | Resposta de sucesso |
|---|---|---|
| `PING` | Nada | `OK PONG` |
| `STATUS` | Informa o estado | `OK FW=<versao> USB=<ON\|OFF> ZOOM_MS=<n>` |
| `SHOT:CLICK` | Pressiona e solta o disparador (sequência de ângulos atual: 140° por 1000 ms, 145° por 500 ms, repouso 90°) | `OK SHOT` |
| `ZOOM:IN:<ms>` | Alavanca em zoom-in por `<ms>` e volta ao neutro | `OK ZOOM IN <ms>` |
| `ZOOM:OUT:<ms>` | Alavanca em zoom-out por `<ms>` e volta ao neutro | `OK ZOOM OUT <ms>` |
| `ZOOM:IN` / `ZOOM:OUT` | Igual, com o tempo padrão de 500 ms | `OK ZOOM IN 500` |
| `ZOOM:HOME` | Zoom-out longo (padrão 6000 ms) até o batente, zera o contador | `OK ZOOM HOME` |
| `USB:ON` | Fecha o relé (câmera conectada ao PC) | `OK USB ON` |
| `USB:OFF` | Abre o relé (câmera em modo foto) | `OK USB OFF` |

`<ms>` é inteiro de 50 a 5000. Fora disso: `ERRO VALOR`.

`ZOOM_MS` no `STATUS` é a soma com sinal dos tempos de zoom-in menos zoom-out desde o último `ZOOM:HOME` (com piso em 0). É só uma estimativa; o zoom real não é medido.

| Erro | Quando |
|---|---|
| `ERRO FORMATO` | Linha longa demais ou fora do formato `ALVO:ACAO[:VALOR]` |
| `ERRO ALVO` | Alvo desconhecido |
| `ERRO ACAO` | Ação desconhecida para o alvo |
| `ERRO VALOR` | Valor ausente, não numérico ou fora da faixa |

**Inicialização do ESP32**
1. A **primeira instrução** do `setup()` coloca o pino do relé como saída em nível ALTO (USB desligado), para a câmera nunca conectar durante o boot.
2. Servos em repouso (ZOOM 95°, SHOT 90°).
3. Envia uma linha `# PRONTO FW=<versao>`.
4. Sem `delay()` longo no boot. O PC sincroniza enviando `PING` até receber `OK PONG` (até 5 tentativas, 1 s cada).

**Valores padrão do lado do PC:** timeout de resposta de 3 s, mais a duração da ação quando for longa (`SHOT:CLICK` ≈ 1,5 s, `ZOOM:HOME` ≈ 6 s). Resposta `ERRO` ou timeout levanta exceção.

Mudou o protocolo? Aumente a versão do firmware (`FW`), atualize esta seção nos dois repositórios e o `esp32.py` no mesmo dia.

## 6. Programa do PC (`scn_calibrator`)

### 6.1 Estrutura alvo
```
scn_calibrator/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── config.example.json        # modelo; o config.json real não vai para o git
├── docs/
│   ├── architecture.md
│   └── sysml/                 # diagramas (cópia de scanner/sysml e scanner/simples)
├── scanner/
│   ├── __init__.py
│   ├── __main__.py            # linha de comando (python -m scanner ...)
│   ├── config.py              # carregar/validar config.json e calibration.json
│   ├── printer.py             # Ender 5 Plus por G-code
│   ├── esp32.py               # cliente do protocolo da seção 5
│   ├── camera.py              # tirar a foto e baixar
│   ├── vision.py              # ArUco, inseto, centro na imagem
│   └── scanner.py             # calibração, centralização, ciclo, salvar foto
├── tests/                     # pytest, sem hardware
└── legacy/                    # scripts antigos, só para consulta
```
São os 4 arquivos da versão simples (`impressora.py` → `printer.py`, `esp32.py`, `camera.py`, `scanner.py`), com nomes em inglês, mais `vision.py` separado para poder ser testado com imagens salvas e `config.py`.

**Regra de dependências** (de cima para baixo; nunca o contrário):
```
__main__ → scanner → printer, esp32, camera, vision, config
camera → esp32
printer, esp32, vision, config → (nada do projeto)
```

### 6.2 Interfaces públicas

`printer.py`
```python
class Printer:
    def __init__(self, cfg: PrinterConfig, serial_port=None): ...   # serial_port injetável nos testes
    def connect(self) -> None
    def close(self) -> None
    def home(self) -> None                               # G28 + correção do X + estacionar
    def move_to(self, x: float, y: float, z: float) -> None      # absoluto, sempre espera M400
    def move_relative(self, dx: float, dy: float) -> None        # valida limite e max_step_mm
    def get_position(self) -> tuple[float, float, float]         # M114
    def park(self) -> None
```
Movimento fora da área ou passo maior que `max_step_mm` levanta `UnsafeMoveError` **antes** de enviar qualquer G-code. Não usar `clamp` silencioso: um destino fora da área é erro, não é para ser "corrigido".

`esp32.py`
```python
class Esp32:
    def __init__(self, cfg: Esp32Config, serial_port=None): ...
    def connect(self) -> None                            # abre a porta e sincroniza com PING
    def close(self) -> None
    def ping(self) -> None
    def status(self) -> dict
    def shoot(self) -> None
    def zoom_in(self, ms: int = 500) -> None
    def zoom_out(self, ms: int = 500) -> None
    def zoom_home(self) -> None
    def usb_on(self) -> None
    def usb_off(self) -> None
```
Resposta `ERRO ...` levanta `Esp32Error`; falta de resposta levanta `Esp32Timeout`.

`camera.py`
```python
class Camera:
    def __init__(self, cfg: CameraConfig, esp32: Esp32): ...
    def capture_and_download(self, dest_dir: Path) -> Path      # fluxo 4.1, devolve o caminho da foto
    def find_camera_storage(self, timeout_s: float) -> CameraStorage   # acha a câmera no Windows
```
`CameraStorage` tem duas implementações:
- `DriveStorage` (padrão): procura em todas as letras de unidade uma pasta `DCIM` com subpasta de nome contendo `NIKON`; copia com `shutil`.
- `MtpStorage`: usa o Shell do Windows (`pywin32`, `Shell.Application`) para achar o dispositivo "COOLPIX L120" em "Este Computador" e copiar o arquivo.
`camera.mode` no `config.json` escolhe `drive`, `mtp` ou `auto` (tenta `drive` e depois `mtp`).

`vision.py` (funções puras: recebem imagem, devolvem números; não falam com hardware)
```python
def load_image(path: Path, max_width: int = 1600) -> tuple[np.ndarray, float]   # imagem e fator de escala
def find_aruco(image) -> ArucoResult | None          # centro (u, v), lado médio em px, ângulo, id
def find_insect(image, ignore_aruco: bool = True) -> tuple[float, float] | None   # centro em px
def image_center(image) -> tuple[float, float]
```
ArUco: dicionário `DICT_4X4_50`, lado impresso de 25 mm. Inseto: limiar (Otsu) sobre o fundo da mesa, maior contorno escuro perto do centro, ignorando a região do ArUco quando ele estiver visível.

`scanner.py`
```python
def calibrate(printer, camera, cfg) -> Calibration     # fluxo 4.2
def center_on(target: str, printer, camera, calib, cfg) -> CenterResult   # target: "aruco" ou "insect"
def scan_specimen(specimen_id: str, printer, esp32, camera, calib, cfg) -> Path   # fluxo 4.4
def save_capture(photo: Path, specimen_id: str, metadata: dict, cfg) -> Path
```

### 6.3 Linha de comando
```
python -m scanner ping                    # testa Ender e ESP32
python -m scanner usb-test --cycles 20    # USB:ON → câmera aparece → USB:OFF → disparo; mede tempos
python -m scanner photo                   # uma foto baixada para a pasta de trabalho
python -m scanner calibrate               # fluxo 4.2
python -m scanner center-test             # grade de 9 posições (±10 mm) e relatório do erro
python -m scanner scan --id <especime>    # fluxo 4.4
```

## 7. Arquivos de configuração e de saída

### 7.1 `config.json` (editado à mão; fica fora do git, o modelo é `config.example.json`)
```json
{
  "printer": {
    "port": "COM3", "baudrate": 115200,
    "x_min": 0, "x_max": 350, "y_min": 0, "y_max": 350, "z_min": 0, "z_max": 140,
    "x_home_correction": 0.0,
    "feed_xy": 3000, "feed_z": 600,
    "max_step_mm": 20.0,
    "park": [180.0, 180.0, 12.2]
  },
  "esp32": { "port": "COM8", "baudrate": 115200, "timeout_s": 3.0 },
  "camera": { "mode": "auto", "write_wait_s": 3.0, "enumerate_timeout_s": 20.0, "work_dir": "work" },
  "scan": {
    "z_work": 50.0, "jog_mm": 10.0, "tolerance_mm": 1.0, "max_photos": 3,
    "analysis_width": 1600, "output_dir": "capturas"
  }
}
```
Os limites de X/Y/Z e `z_work` são provisórios até serem medidos na máquina (seção 11).

### 7.2 `calibration.json` (escrito só pelo `calibrate`)
```json
{
  "date": "2026-11-10T14:00:00",
  "z_work": 50.0,
  "zoom": "home",
  "image_size": [4320, 3240],
  "J_px_per_mm": [[0.0, 0.0], [0.0, 0.0]],
  "aruco_px_per_mm": 0.0,
  "table_center_xy": [175.0, 175.0]
}
```
Fica separado do `config.json` para o programa nunca reescrever o arquivo que é editado à mão.

### 7.3 Saída de cada espécime
```
capturas/<especime>/<especime>_<AAAAMMDD-HHMMSS>.jpg
capturas/<especime>/<especime>_<AAAAMMDD-HHMMSS>.json
```
Metadados: `specimen_id`, `datetime`, `x`, `y`, `z`, `photos_used`, `final_error_mm`, `original_filename`, `calibration_date`, `fw_version`. Nunca sobrescrever uma captura existente.

## 8. Regras de segurança (valem para as duas frentes)
1. Nenhum G-code de movimento sai sem passar pela validação de área e de `max_step_mm`. Destino inválido é erro, não é corrigido.
2. Sempre esperar `M400` depois de mover e antes de tirar foto ou ler posição.
3. Não descer Z abaixo de `z_min` do config. Não mudar `z_work` durante o ciclo.
4. O relé do USB começa desligado no boot do ESP32 e é desligado pelo PC em qualquer erro (`try/finally`).
5. Nunca ligar VBUS no cabo da câmera; a Solução D funciona com o VBUS desconectado.
6. O programa do PC não envia comandos ao ESP32 nem à Ender sem antes confirmar a conexão (`PING` / resposta do Marlin).
7. Em teste novo de movimento, rodar primeiro com a mesa vazia e a câmera alta.

## 9. Convenções de código

**Gerais**
- Nomes de funções, classes, variáveis e arquivos **em inglês**, claros e descritivos.
- Comentários e docstrings **em português**, só o essencial (o porquê, não o óbvio).
- Mensagens de commit em português, curtas, no imperativo ("Adiciona comando USB:ON").
- Mensagens para o operador (terminal) em português.

**Python**
- Python 3.11+, PEP 8, linhas de até 99 caracteres, `type hints` nas funções públicas.
- Dependências em `requirements.txt`: `pyserial`, `numpy`, `opencv-contrib-python` (ArUco), `pywin32` (só Windows, para MTP), `pytest`.
- Sem variáveis globais com porta serial aberta; tudo passa por classes com `connect()`/`close()`.
- Testes com `pytest`, sem hardware: porta serial falsa (objeto com `write`/`readline`) e imagens sintéticas (ArUco gerado com `cv2.aruco.generateImageMarker`). O que depende do Windows (`camera.py`) é testado com uma pasta temporária no lugar da unidade da câmera.

**Firmware**
- C++ com Arduino core 3.x (plataforma pioarduino) e PlatformIO.
- Nomes em inglês (`handleCommand`, `PIN_USB_RELAY`), constantes em `MAIÚSCULAS`.
- O interpretador do protocolo fica separado do acesso ao hardware, para ser testado no PC com `pio test -e native`.

## 10. Ordem de desenvolvimento e critérios de pronto

Em 03/11 os dois repositórios ainda estão no primeiro commit. Ordem sugerida (datas do plano, ajustadas):

| # | Entrega | Frente | Pronto quando |
|---|---|---|---|
| 1 | Firmware no C6 com o protocolo da seção 5 | firmware | Compila para o C6; testes do interpretador passam; `PING`, `USB:ON/OFF`, `SHOT:CLICK` respondem no monitor serial |
| 2 | `esp32.py` + `camera.py` + `usb-test` | aquisição | **20 fotos seguidas** chegam no PC sem mexer em nada; tempo de aparecer no Windows anotado |
| 3 | `printer.py` com a área real | aquisição | Move para qualquer ponto válido e recusa ponto fora da mesa sem enviar G-code |
| 4 | `vision.py` + `calibrate` | aquisição | J coerente (escala ±5% do ArUco); `calibration.json` salvo |
| 5 | Centralização + `center-test` | aquisição | 9/9 posições com erro ≤ 1 mm, nenhum movimento recusado |
| 6 | `scan --id` | aquisição | 5 insetos seguidos sem intervenção, fotos salvas com metadados |
| 7 | Testes de robustez, README, diagramas | as duas | Etapa 1 entregue (30/11) |

## 11. Pontos em aberto (medir e registrar aqui)
| Ponto | Como resolver | Afeta |
|---|---|---|
| A L120 aparece como unidade de disco ou MTP? | `usb-test` / Explorer do Windows | `camera.mode` |
| Tempo até a câmera aparecer no Windows | `usb-test --cycles 20` | `enumerate_timeout_s` |
| Limites reais de X/Y/Z e deslocamento do fim de curso de X | `M114` nos cantos + régua | `printer` no `config.json` |
| Altura de trabalho `z_work` em que o autofoco foca o inseto | teste manual | `scan.z_work` |
| Pinos 18/19 livres na placa C6 usada | serigrafia da placa | tabela da seção 3 |
| Tempo de `ZOOM:HOME` até o batente | teste manual | firmware |

## 12. Histórico de decisões
| Data | Decisão |
|---|---|
| 06/10 | A L120 não manda imagem ao vivo pela HDMI: centralização **foto a foto** via USB. |
| 13/10 | **Solução D**: o relé corta só o GND do USB; D+/D− direto, VBUS desligado (fuga medida de 0,1 µA). |
| 13/10 | Sistema simplificado (`sistema_simples.md`): 4 arquivos Python, autofoco, zoom opcional. |
| 03/11 | ESP32-C6 já na etapa 1; protocolo padronizado com uma resposta `OK`/`ERRO` por comando; a frente de aquisição cobre todo o programa do PC. |
