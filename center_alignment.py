import json
import time
import cv2
import numpy as np
from calibration import MachineConfig, GCodeSender, OpenCVBackend

def load_config(json_path="calibration\\camera1_manual\\camera_config.json"):
    with open(json_path, "r") as f:
        return json.load(f)

def main():
    config = load_config()
    
    # Inicializa detecção do ArUco (Dicionário 4X4_50 = ID 8)
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)

    machine_cfg = MachineConfig() 
    gcode = GCodeSender(machine_cfg)
    gcode.connect()
    
    # Abre a câmera usando sua classe
    cam = OpenCVBackend(device_index=1)
    cam.open()
    cap = cam.cap  # Acessa o VideoCapture diretamente para o stream

    print("Movendo para posição inicial...")
    gcode.home()
    current_x, current_y, current_z = 210.0, 210.0, 70.0  # Z ajustado para dar campo de visão
    gcode.move_to(current_x, current_y, current_z, wait=True)

    # Centro teórico da imagem baseado na calibração
    img_w, img_h = config["calibration_result"]["image_size"]
    center_x_px, center_y_px = int(img_w / 2), int(img_h / 2)

    tolerance_px = 3.0  
    aligned = False

    print("Iniciando stream e busca do centro... Pressione 'q' na janela do vídeo para cancelar.")

    while not aligned:
        # Lê o frame diretamente para o stream não travar
        ret, frame = cap.read()
        if not ret:
            print("Falha ao capturar imagem da câmera.")
            break
            
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = detector.detectMarkers(gray)
        
        # Desenha uma mira no centro da imagem (em azul)
        cv2.drawMarker(frame, (center_x_px, center_y_px), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)

        if ids is not None:
            # Desenha o contorno do ArUco encontrado
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)
            
            c = corners[0][0]
            marker_cx = int(np.mean(c[:, 0]))
            marker_cy = int(np.mean(c[:, 1]))
            
            error_x = -(center_x_px - marker_cx)
            error_y = -(center_y_px - marker_cy)
            
            # Desenha uma linha do centro da tela ao centro do ArUco (em vermelho)
            cv2.line(frame, (center_x_px, center_y_px), (marker_cx, marker_cy), (0, 0, 255), 2)
            cv2.putText(frame, f"Erro: X={error_x} Y={error_y}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            
            if abs(error_x) < tolerance_px and abs(error_y) < tolerance_px:
                aligned = True
                print(f"\n[SUCESSO] Alinhado! Coordenadas: X: {current_x:.2f} | Y: {current_y:.2f} | Z: {current_z:.2f}")
                cv2.putText(frame, "ALINHADO!", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                cv2.imshow("Stream de Alinhamento", frame)
                cv2.waitKey(3000) # Mostra a mensagem de sucesso por 3 segundos
                break
            
            # Controle de movimento
            current_x += error_x * 0.015
            current_y += error_y * -0.015
            gcode.move_to(current_x, current_y, current_z, wait=False)
        else:
            cv2.putText(frame, "Buscando ArUco...", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        # Mostra o frame
        cv2.imshow("Stream de Alinhamento", frame)
        
        # Atualiza a interface do OpenCV e checa se o usuário quer sair
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\nCancelado pelo usuário.")
            break

    # Limpeza
    cam.close()
    gcode.disconnect()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()