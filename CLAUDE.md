# CLAUDE.md: scn_calibrator (aquisição de imagens, programa do PC)

Programa em Python que roda no PC Windows e comanda todo o Scanner de Insetos: move a Ender 5 Plus, comanda o ESP32 (disparo, zoom, relé do USB), baixa as fotos da Nikon L120, calibra pixels → mm, centraliza a câmera no inseto foto a foto e salva a foto final com o código do espécime.

**Leia antes de qualquer mudança:** [docs/architecture.md](docs/architecture.md). As seções mais usadas aqui: 4 (fluxos), 5 (protocolo do ESP32), 6 (módulos e interfaces), 7 (arquivos de configuração e saída) e 8 (segurança). Se algo aqui conflitar com o architecture.md, o architecture.md vale.

## Escopo desta frente
- Faz: todo o programa do PC, de `printer.py` até `scan --id`.
- Não faz (etapa 1): vídeo ao vivo/HDMI, webcam, câmera de profundidade, varredura de foco em Z, zoom automático, banco de dados, interface gráfica ou web, tratamento das imagens.
- O foco é o autofoco da L120, com a Ender numa altura fixa (`scan.z_work`).
- Não alterar o firmware daqui. Se o protocolo precisar mudar, propor a mudança no `docs/architecture.md` e avisar o Willian; o firmware é outro repositório (`scn_firmware`).

## Ambiente e comandos
- Python 3.11+ no Windows (alvo). O Claude Code pode rodar os testes no Linux.
- Instalar: `python -m venv .venv`, ativar, `pip install -r requirements.txt`.
- Testes: `pytest` (sem hardware). Estilo: `python -m flake8 --max-line-length 99 scanner tests` se o flake8 estiver instalado.
- Rodar (só o Willian, com a máquina): `python -m scanner <comando>`, com os comandos da seção 6.3 do architecture.md (`ping`, `usb-test`, `photo`, `calibrate`, `center-test`, `scan --id`).

O Claude Code não tem a máquina nem a câmera. Ele escreve o código e os testes com hardware simulado. Cada entrega lista o comando que o Willian deve rodar e o que ele deve ver.

## Estrutura alvo
```
scanner/
  __main__.py   # linha de comando (argparse)
  config.py     # carrega e valida config.json e calibration.json (dataclasses)
  printer.py    # Printer: G-code, limites, M400
  esp32.py      # Esp32: cliente do protocolo
  camera.py     # Camera: capture_and_download, DriveStorage, MtpStorage
  vision.py     # find_aruco, find_insect, image_center (funções puras)
  scanner.py    # calibrate, center_on, scan_specimen, save_capture
tests/
config.example.json
requirements.txt
legacy/         # scripts antigos, só consulta
```
Dependências só de cima para baixo: `__main__ → scanner → printer, esp32, camera, vision, config`; `camera → esp32`. `vision.py` nunca importa serial nem fala com hardware.

## Regras de segurança (não negociáveis)
1. Toda movimentação passa pela validação de área e de `max_step_mm` do `Printer`. Destino inválido levanta `UnsafeMoveError` **antes** de enviar G-code. Não usar `clamp` silencioso (o `calibration.py` antigo faz isso; não copiar).
2. Sempre `M400` depois de mover e antes de fotografar ou ler posição. Nunca mover sem esperar (`wait=False` foi uma causa das falhas antigas).
3. `usb_off()` em `finally` em todo fluxo que chama `usb_on()`, e em qualquer erro do ciclo.
4. A centralização usa a matriz J da calibração. **Nunca** um ganho px → mm fixo (0,1 ou 0,015 dos scripts antigos): é o que fazia a câmera ir para o lado errado.
5. Em erro: parar de enviar G-code, desligar o USB, mostrar a mensagem em português e sair com código diferente de zero.

## Pontos ainda não medidos (o código tem que aceitar os dois casos)
- Se a L120 aparece no Windows como **unidade com letra** ou como **dispositivo MTP**: implementar `DriveStorage` (padrão) e `MtpStorage` (`pywin32`), escolhidos por `camera.mode` (`auto` tenta os dois).
- Tempo até a câmera aparecer: `enumerate_timeout_s` (padrão 20 s) configurável; o `usb-test` mede e imprime esse tempo.
- Limites reais da Ender, correção do fim de curso de X e `z_work`: tudo no `config.json`, nada fixo no código.
Quando o Willian informar um desses valores, atualizar a seção 11 do architecture.md.

## Convenções
- PEP 8, linhas de até 99 caracteres, `type hints` nas funções públicas.
- Funções, classes, variáveis e arquivos **em inglês**, com nomes claros (`capture_and_download`, `move_relative`).
- Comentários e docstrings em **português**, só o essencial.
- Mensagens para o operador em português.
- Portas seriais sempre injetáveis no construtor (`serial_port=None`) para os testes usarem uma porta falsa.
- `config.json` e `calibration.json` reais ficam no `.gitignore`; versionar só `config.example.json`.
- Não versionar `__pycache__`, `.venv`, `work/`, `capturas/`.
- Commits em português, curtos, no imperativo.

## Testes esperados
- `esp32.py`: porta falsa que responde `OK ...`, `ERRO ...`, linhas `#` e silêncio (timeout).
- `printer.py`: porta falsa que responde `ok`; conferir que um destino fora da área não gera nenhum `write`.
- `vision.py`: imagens sintéticas com ArUco gerado (`cv2.aruco.generateImageMarker`, `DICT_4X4_50`) em posição conhecida e um "inseto" (elipse escura).
- `scanner.py`: centralização com `Printer` e `Camera` simulados por um modelo J conhecido (inclusive com rotação e sinal trocado), convergindo em até 3 fotos.
- `camera.py`: `DriveStorage` sobre uma pasta temporária com `DCIM/100NIKON/`.

## Tarefas da etapa 1 (em ordem; critério de pronto no architecture.md, seção 10)
1. Estrutura do pacote, `requirements.txt`, `config.example.json`, `config.py`, `.gitignore`.
2. `esp32.py` + `camera.py` + comandos `ping`, `photo` e `usb-test`. Meta: 20 fotos seguidas no PC.
3. `printer.py` (reaproveitar a lógica de `GCodeSender` do `calibration.py`, com validação em vez de clamp).
4. `vision.py` + `calibrate`.
5. `center_on` + `center-test` (grade de 9 posições, erro ≤ 1 mm).
6. `scan --id` e `save_capture`.

## Código antigo
`calibration.py`, `center_alignment.py`, `deteccao.py`, `detect_aruco.py` e `send_cmd_serial.py` são de testes com webcam e ganho fixo. Servem só de consulta (porta da Ender, `M114`, parâmetros do ArUco). Mover para `legacy/` quando a tarefa 1 for feita, junto com a remoção do `__pycache__` versionado. Não importar nada deles no pacote novo.
