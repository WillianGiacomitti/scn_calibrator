#!/usr/bin/env python3
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
import serial


# ============================================================================
# CONFIGURAÇÃO DA MÁQUINA (ajuste conforme sua Ender 5 adaptada)
# ============================================================================

@dataclass
class MachineConfig:
    port: str = "COM3"
    baudrate: int = 115200

    # Limites de trabalho reais da máquina (mm)
    x_min: float = 0.0
    x_max: float = 350.0
    y_min: float = 0.0
    y_max: float = 350.0
    z_min: float = 0.0
    z_max: float = 140.0

    # Sequência observada após G28: a máquina finaliza em (350,350,0) e,
    # segundo seu firmware/start-gcode, depois vai para (180,180,12.2).
    park_position: tuple = (180.0, 180.0, 12.2)

    # O fim de curso de X está sendo acionado 45mm adiantado (adaptação da
    # câmera desloca o gatilho do endstop). Se precisar reativar a correção,
    # descomente a linha correspondente em home() e ajuste o sinal:
    #   1) rode o home
    #   2) meça fisicamente a posição real do bico/câmera em X
    #   3) compare com o M114 reportado
    #   4) ajuste o sinal abaixo até M114 bater com a régua
    x_home_correction: float = 0.0

    feed_rate_xy: int = 3000   # mm/min
    feed_rate_z: int = 600     # mm/min

    # Margem de segurança para não bater a câmera nas bordas/estrutura
    safety_margin: float = 20.0


@dataclass
class CameraProfile:
    """Parâmetros específicos de cada câmera (varia por modelo)."""
    name: str
    z_focus_min: float   # menor Z (mais perto do alvo) onde ainda foca
    z_focus_max: float   # maior Z (mais longe) onde ainda foca
    z_samples: int = 5   # quantas alturas amostrar dentro da faixa de foco
    xy_grid_size: int = 3     # grade NxN de posições XY por altura
    xy_grid_span: float = 60.0  # extensão total da grade (mm), centrada
    backend: str = "opencv"   # "opencv" (webcam/USB) ou "gphoto2" (DSLR tetherada)
    device_index: int = 1     # índice da câmera para backend opencv


# ============================================================================
# COMUNICAÇÃO G-CODE
# ============================================================================

class GCodeSender:
    def __init__(self, cfg: MachineConfig):
        self.cfg = cfg
        self.ser = None

    def connect(self):
        self.ser = serial.Serial(self.cfg.port, self.cfg.baudrate, timeout=5)
        time.sleep(2)  # tempo para o firmware inicializar
        self.ser.reset_input_buffer()
        self._send_raw("M115")  # apenas para "acordar" e checar comunicação

    def disconnect(self):
        if self.ser:
            self.ser.close()

    def _send_raw(self, cmd: str) -> str:
        self.ser.write((cmd + "\n").encode())
        response = ""
        while True:
            line = self.ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            response += line + "\n"
            if line.startswith("ok") or line.startswith("Error"):
                break
        return response

    def home(self):
        print("[GCODE] Homing...")
        self._send_raw("G28")
        # Corrige o offset do fim de curso de X adaptado para a câmera
        # self._send_raw(f"G92 X{self.cfg.x_home_correction}")
        self.move_to(*self.cfg.park_position, wait=True)

    def move_to(self, x: float, y: float, z: float, wait: bool = True):
        x = self._clamp(x, self.cfg.x_min, self.cfg.x_max)
        y = self._clamp(y, self.cfg.y_min, self.cfg.y_max)
        z = self._clamp(z, self.cfg.z_min, self.cfg.z_max)
        cmd = f"G1 X{x:.2f} Y{y:.2f} Z{z:.2f} F{self.cfg.feed_rate_xy}"
        self._send_raw(cmd)
        if wait:
            self._send_raw("M400")  # espera terminar todos os movimentos
        return x, y, z

    def get_position(self) -> str:
        return self._send_raw("M114")

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))


# ============================================================================
# BACKENDS DE CAPTURA DE IMAGEM (abstrai diferentes câmeras)
# ============================================================================

class CameraBackend(ABC):
    @abstractmethod
    def open(self):
        ...

    @abstractmethod
    def capture(self, filepath: str) -> bool:
        ...

    @abstractmethod
    def close(self):
        ...


class OpenCVBackend(CameraBackend):
    """Para webcams USB genéricas ou câmeras que expõem V4L2 (ex: RealSense RGB)."""

    def __init__(self, device_index: int = 0, warmup_frames: int = 5):
        self.device_index = device_index
        self.warmup_frames = warmup_frames
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.device_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir a câmera índice {self.device_index}")

    def capture(self, filepath: str) -> bool:
        # descarta alguns frames para estabilizar exposição/foco automático
        for _ in range(self.warmup_frames):
            self.cap.read()
        ok, frame = self.cap.read()
        if not ok:
            return False
        cv2.imwrite(filepath, frame)
        return True

    def close(self):
        if self.cap:
            self.cap.release()


class GPhoto2Backend(CameraBackend):
    """Para DSLRs/bridge cameras tetheradas via USB (ex: Nikon L120), usando gphoto2 CLI."""

    def open(self):
        pass

    def capture(self, filepath: str) -> bool:
        import subprocess
        result = subprocess.run(
            ["gphoto2", "--capture-image-and-download", f"--filename={filepath}", "--force-overwrite"],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def close(self):
        pass


def make_backend(profile: CameraProfile) -> CameraBackend:
    if profile.backend == "opencv":
        return OpenCVBackend(device_index=profile.device_index)
    elif profile.backend == "gphoto2":
        return GPhoto2Backend()
    raise ValueError(f"Backend desconhecido: {profile.backend}")


# ============================================================================
# GERAÇÃO DA GRADE DE POSIÇÕES DE CAPTURA (varredura em zig-zag / boustrophedon)
# ============================================================================

def generate_positions(machine: MachineConfig, cam: CameraProfile) -> list:
    """
    Gera lista de posições (x, y, z) para UM pass de inclinação.
    Varre em zig-zag (boustrophedon) para que o próximo ponto seja sempre
    adjacente ao anterior, minimizando deslocamento total e desgaste mecânico.
    """
    cx, cy = 210.0, 210.0  # centro geométrico de trabalho
    margin = machine.safety_margin

    z_values = np.linspace(cam.z_focus_min, cam.z_focus_max, cam.z_samples)

    half_span = cam.xy_grid_span / 2.0
    offsets = np.linspace(-half_span, half_span, cam.xy_grid_size)

    positions = []
    reverse_x = False
    reverse_y = False

    for z in z_values:
        current_dx_offsets = offsets[::-1] if reverse_x else offsets

        for dx in current_dx_offsets:
            current_dy_offsets = offsets[::-1] if reverse_y else offsets

            for dy in current_dy_offsets:
                x = cx + dx
                y = cy + dy
                x = max(margin, min(machine.x_max - margin, x))
                y = max(margin, min(machine.y_max - margin, y))
                positions.append((round(float(x), 2), round(float(y), 2), round(float(z), 2)))

            reverse_y = not reverse_y

        reverse_x = not reverse_x

    return positions


# ============================================================================
# ROTINA DE CAPTURA (com múltiplos passes de inclinação do alvo)
# ============================================================================

def run_capture_routine(machine_cfg: MachineConfig, cam_profile: CameraProfile,
                         num_tilt_passes: int, output_dir: Path) -> list:
    """Executa a varredura completa e retorna a lista de arquivos de imagem gerados."""

    gcode = GCodeSender(machine_cfg)
    gcode.connect()
    gcode.home()

    backend = make_backend(cam_profile)
    backend.open()

    positions = generate_positions(machine_cfg, cam_profile)
    print(f"[INFO] {len(positions)} posições geradas.")
    input("\n>>> Pressione ENTER para iniciar a captura automática...")

    log_path = output_dir / "capture_log.csv"
    image_paths = []

    with open(log_path, "w") as log:
        log.write("filename,tilt_pass,x,y,z\n")

        for tilt_pass in range(num_tilt_passes):
            input(
                f"\n>>> Pass de inclinação {tilt_pass + 1}/{num_tilt_passes}.\n"
                f"    Ajuste o jig/inclinação do alvo ChArUco agora e pressione ENTER "
                f"para iniciar a varredura automática..."
            )

            for i, (x, y, z) in enumerate(positions):
                gcode.move_to(x, y, z, wait=True)
                time.sleep(0.3)  # pequeno assentamento mecânico

                filename = f"tilt{tilt_pass}_pos{i:03d}_x{x:.0f}_y{y:.0f}_z{z:.0f}.jpg"
                filepath = str(output_dir / filename)

                ok = backend.capture(filepath)
                status = "OK" if ok else "FALHA"
                print(f"  [{tilt_pass}][{i+1}/{len(positions)}] "
                      f"pos=({x},{y},{z}) -> {filename} [{status}]")

                if ok:
                    log.write(f"{filename},{tilt_pass},{x},{y},{z}\n")
                    image_paths.append(filepath)

    backend.close()
    gcode.move_to(*machine_cfg.park_position)
    gcode.disconnect()

    return image_paths


# ============================================================================
# CALIBRAÇÃO AUTOMÁTICA A PARTIR DAS IMAGENS CAPTURADAS
# ============================================================================

@dataclass
class BoardConfig:
    squares_x: int = 7
    squares_y: int = 5
    square_length_m: float = 0.030   # MEÇA o alvo físico e ajuste aqui


def run_calibration(image_paths: list, board_cfg: BoardConfig,
                     debug_dir: Path = None) -> dict:
    """
    Calibração via chessboard clássico (cv2.findChessboardCornersSB +
    cv2.calibrateCamera). O board físico é um ChArUco (ideal para robustez de
    detecção parcial), mas para a calibração usamos só o padrão preto/branco
    por baixo, ignorando os marcadores ArUco desenhados nos quadrados.

    Motivo de não usar ArUco/ChArUco para a calibração: o CharucoDetector do
    OpenCV não fechava nenhum corner neste ambiente (testado em 5.0.0 e
    4.10.0, com/sem legacy pattern, passando marcadores manualmente — sempre
    zero), e uma calibração via cantos de marcador implementada manualmente
    produziu parâmetros fisicamente inconsistentes ao introduzir rotação real
    da câmera. O chessboard clássico usa o pipeline mais maduro e testado do
    OpenCV e já validado como correto (RMS < 1px, fx≈fy, cx/cy plausíveis).

    Limitação: exige que o board INTEIRO apareça no frame (sem ArUco não há
    como "completar" com visão parcial). Imagens com o board cortado são
    descartadas automaticamente.
    """
    pattern_size = (board_cfg.squares_x - 1, board_cfg.squares_y - 1)  # nº de corners internos

    # pontos 3D do padrão, no plano Z=0, em metros reais (usa square_length_m)
    objp = np.zeros((pattern_size[0] * pattern_size[1], 3), dtype=np.float32)
    objp[:, :2] = np.mgrid[0:pattern_size[0], 0:pattern_size[1]].T.reshape(-1, 2)
    objp *= board_cfg.square_length_m

    all_obj_points = []
    all_img_points = []
    image_size = None
    used_images = 0
    fail_read = 0
    fail_not_found = 0

    flags = (cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)

    for path in image_paths:
        img = cv2.imread(path)
        if img is None:
            fail_read += 1
            print(f"  [FALHA-LEITURA] cv2.imread não conseguiu abrir: {path}")
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        image_size = gray.shape[::-1]

        found, corners = cv2.findChessboardCornersSB(gray, pattern_size, flags)

        if not found:
            fail_not_found += 1
            print(f"  [{Path(path).name}] chessboard NÃO encontrado (board incompleto/cortado?)")
            continue

        all_obj_points.append(objp)
        all_img_points.append(corners.reshape(-1, 2).astype(np.float32))
        used_images += 1
        print(f"  [{Path(path).name}] OK — {pattern_size[0] * pattern_size[1]} corners")

    total = len(image_paths)
    print(f"\n[DIAGNÓSTICO] Total: {total} | OK: {used_images} | "
          f"falha leitura arquivo: {fail_read} | "
          f"chessboard não encontrado (provavelmente cortado): {fail_not_found}")

    if used_images < 8:
        raise RuntimeError(
            f"Poucas imagens válidas para calibrar ({used_images}). "
            f"Precisa de fotos com o board INTEIRO visível no frame — "
            f"recapture garantindo que nenhuma borda do board saia do quadro."
        )

    rms_error, camera_matrix, dist_coeffs, _, _ = cv2.calibrateCamera(
        all_obj_points, all_img_points, image_size, None, None
    )

    return {
        "rms_error": float(rms_error),
        "camera_matrix": camera_matrix.tolist(),
        "dist_coeffs": dist_coeffs.flatten().tolist(),
        "image_size": list(image_size),
        "images_used": used_images,
        "images_discarded": fail_read + fail_not_found,
        "method": "chessboard_classic",
    }


# ============================================================================
# SALVAR ARQUIVO DE CONFIGURAÇÃO FINAL
# ============================================================================

def save_config(output_dir: Path, cam_profile: CameraProfile,
                 board_cfg: BoardConfig, calib_result: dict):
    config = {
        "camera_name": cam_profile.name,
        "calibration_date": datetime.now().isoformat(),
        "board": {
            "squares_x": board_cfg.squares_x,
            "squares_y": board_cfg.squares_y,
            "square_length_m": board_cfg.square_length_m,
        },
        "calibration_result": calib_result,
    }

    config_path = output_dir / "camera_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n[OK] Configuração salva em: {config_path}")
    print(f"     Erro de reprojeção (RMS): {calib_result['rms_error']:.4f} px")
    print(f"     Imagens usadas: {calib_result['images_used']} "
          f"(descartadas: {calib_result['images_discarded']})")

    if calib_result["rms_error"] > 1.0:
        print("     [ATENÇÃO] RMS acima de 1.0px — considere recapturar com mais "
              "variação angular (novos passes de inclinação) ou checar foco/iluminação.")


# ============================================================================
# MAIN — configuração direta em variáveis (sem argparse)
# ============================================================================

def main():
    # ---- EDITE AQUI ----
    output_base = "calibration"
    camera_name = "camera1"

    port = "COM3"
    baudrate = 115200

    z_focus_min = 50.0
    z_focus_max = 120.0
    z_samples = 2

    xy_grid_size = 3
    xy_grid_span = 260.0

    backend = "opencv"
    device_index = 1

    tilt_passes = 3

    skip_capture = False  # True para recalibrar sem mover a máquina de novo

    # Se skip_capture=True, aponte aqui a pasta EXATA da sessão já capturada
    # (o nome inclui a data em que a captura foi feita, não a de hoje).
    # Exemplo: "calibration/camera1_20260910"
    existing_session_dir = "calibration/camera1_manual"
    # ---------------------

    if skip_capture:
        output_dir = Path(existing_session_dir)
        if not output_dir.exists():
            raise FileNotFoundError(
                f"Pasta '{output_dir}' não existe. Confirme o caminho em existing_session_dir."
            )
    else:
        day_str = datetime.now().strftime("%Y%m%d")
        output_dir = Path(output_base) / f"{camera_name}_{day_str}"
        output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Diretório de saída: {output_dir}")

    machine_cfg = MachineConfig(port=port, baudrate=baudrate)
    cam_profile = CameraProfile(
        name=camera_name,
        z_focus_min=z_focus_min,
        z_focus_max=z_focus_max,
        z_samples=z_samples,
        xy_grid_size=xy_grid_size,
        xy_grid_span=xy_grid_span,
        backend=backend,
        device_index=device_index,
    )
    board_cfg = BoardConfig()

    if skip_capture:
        image_paths = sorted(str(p) for p in output_dir.glob("*.jpg"))
        print(f"[INFO] Pulando captura. Encontradas {len(image_paths)} imagens existentes.")
    else:
        image_paths = run_capture_routine(machine_cfg, cam_profile, tilt_passes, output_dir)

    print(f"\n[INFO] Rodando calibração com {len(image_paths)} imagens...")
    calib_result = run_calibration(image_paths, board_cfg, debug_dir=output_dir)
    save_config(output_dir, cam_profile, board_cfg, calib_result)


if __name__ == "__main__":
    main()