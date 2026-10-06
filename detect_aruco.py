import cv2
import numpy as np

# --- CONFIGURAÇÕES DO SISTEMA ---
CAMERA_INDEX = 1  # Índice da câmera USB (0 para webcam integrada/USB primária)
FRAME_WIDTH = 1080  # Largura da imagem em pixels
FRAME_HEIGHT = 920  # Altura da imagem em pixels
MARKER_SIZE_MM = 25.0  # Tamanho do lado do marcador impresso (em milímetros)

# 1. Inicializar captura de vídeo da câmera USB
cap = cv2.VideoCapture(CAMERA_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

if not cap.isOpened():
    raise RuntimeError(
        f"Não foi possível abrir a câmera USB no índice {CAMERA_INDEX}."
    )

# 2. Matriz Intrínseca Estimada (Caso não tenha executado a calibração com padrão xadrez)
# Nota: Para precisão milimétrica real no eixo Z com webcam comum, é recomendada
# a calibração prévia do sensor com cv2.calibrateCamera().
focal_length = FRAME_WIDTH  # Aproximação padrão para f_x e f_y em webcams USB
center_x = FRAME_WIDTH / 2.0
center_y = FRAME_HEIGHT / 2.0

camera_matrix = np.array(
    [[focal_length, 0, center_x], [0, focal_length, center_y], [0, 0, 1]],
    dtype=np.float64,
)

dist_coeffs = np.zeros((4, 1), dtype=np.float64)  # Assumindo distorção zero

# 3. Configurar detector ArUco (API OpenCV 4.7+)
aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
parameters = cv2.aruco.DetectorParameters()
detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)

# Coordenadas 3D dos 4 cantos do marcador em relação ao seu próprio centro
half_size = MARKER_SIZE_MM / 2.0
obj_points = np.array(
    [
        [-half_size, half_size, 0],
        [half_size, half_size, 0],
        [half_size, -half_size, 0],
        [-half_size, -half_size, 0],
    ],
    dtype=np.float32,
)

print("Iniciando detecção... Pressione 'q' para encerrar.")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Erro ao capturar frame da câmera USB.")
            break

        # Conversão para escala de cinza otimiza a detecção dos cantos
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Detectar marcadores no frame
        corners, ids, _ = detector.detectMarkers(gray)

        if ids is not None:
            # Desenhar as bordas e IDs dos marcadores detectados
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)

            # Acheita o array de IDs para garantir compatibilidade entre versões do OpenCV
            flat_ids = ids.flatten()

            for i in range(len(ids)):
                # Resolver PnP para encontrar rotação (rvec) e translação (tvec) em 3D
                _, rvec, tvec = cv2.solvePnP(
                    obj_points, corners[i][0], camera_matrix, dist_coeffs
                )

                # Distâncias X, Y, Z em milímetros relativas ao centro da câmera
                x_mm, y_mm, z_mm = tvec.flatten()

                # Centroide do marcador na tela (em pixels)
                pts = corners[i][0]
                centroid_u = int(np.mean(pts[:, 0]))
                centroid_v = int(np.mean(pts[:, 1]))

                # Desenhar ponto vermelho no centro do marcador
                cv2.circle(
                    frame, (centroid_u, centroid_v), 4, (0, 0, 255), -1
                )

                # ID extraído de forma segura
                marker_id = flat_ids[i]

                # Exibir as coordenadas tridimensionais na imagem
                info_str = (
                    f"ID {marker_id}: X={x_mm:.1f} Y={y_mm:.1f} Z={z_mm:.1f}mm"
                )
                cv2.putText(
                    frame,
                    info_str,
                    (centroid_u - 80, centroid_v - 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    2,
                )

        # Mostrar o vídeo em tempo real
        cv2.imshow("Deteccao ArUco - Camera USB", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()