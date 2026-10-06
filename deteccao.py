import cv2
import numpy as np
import serial
import time

# ================= CONFIGURAÇÕES =================
# Câmera e Visão
CAMERA_ID = 1
MM_POR_PIXEL = 0.1  # IMPORTANTE: Quantos mm equivale a 1 pixel? (Calibre na prática)
TOLERANCIA_PIXELS = 10 # Margem de erro aceitável para considerar "centralizado"

# Configurações da Impressora (Ender 5)
PORTA_SERIAL = 'COM3' # No Linux/Mac seria algo como '/dev/ttyUSB0'
BAUDRATE = 115200     # Padrão Marlin (pode ser 250000)
CENTRO_MESA_X = 110.0 # Centro geométrico da Ender 5 (220x220)
CENTRO_MESA_Y = 110.0

# Offset Físico (Distância do centro da lente da câmera até onde ficava o bico)
# Exemplo: Se a câmera está 30mm à direita e 15mm à frente do antigo bico
OFFSET_CAMERA_X = 30.0 
OFFSET_CAMERA_Y = -15.0

# Direção dos Eixos da Imagem em relação à Impressora (1 ou -1)
# Depende de como a câmera está montada. Se mover X pra direita faz a imagem ir pra esquerda, mude para -1.
DIR_X = -1
DIR_Y = -1

# ================= COMUNICAÇÃO SERIAL =================
try:
    impressora = serial.Serial(PORTA_SERIAL, BAUDRATE, timeout=2)
    time.sleep(2) # Aguarda o reset do Arduino/Placa ao conectar
    print("Impressora conectada!")
except Exception as e:
    print(f"Erro ao conectar na impressora: {e}")
    exit()

def enviar_gcode(comando):
    """Envia o comando para a impressora e aguarda a confirmação (ok)"""
    print(f"Enviando: {comando}")
    impressora.write((comando + '\n').encode('utf-8'))
    while True:
        resposta = impressora.readline().decode('utf-8').strip()
        if resposta == 'ok':
            break

# ================= DETECTORES =================
def detectar_aruco(frame):
    """Retorna as coordenadas (cX, cY) do centro do ArUco na imagem"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parametros = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(dicionario, parametros)
    
    cantos, ids, _ = detector.detectMarkers(gray)
    
    if ids is not None and len(cantos) > 0:
        # Pega o primeiro ArUco detectado
        canto = cantos[0][0]
        cX = int((canto[0][0] + canto[2][0]) / 2.0)
        cY = int((canto[0][1] + canto[2][1]) / 2.0)
        return cX, cY
    return None, None

def detectar_objeto_canny(frame):
    """Retorna as coordenadas (cX, cY) do objeto escuro usando Filtro de Bordas"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.bilateralFilter(gray, 9, 75, 75)
    edges = cv2.Canny(blur, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges_closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    
    contornos, _ = cv2.findContours(edges_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if contornos:
        maior_contorno = max(contornos, key=cv2.contourArea)
        if cv2.contourArea(maior_contorno) > 500:
            M = cv2.moments(maior_contorno)
            if M["m00"] != 0:
                return int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
    return None, None

# ================= LÓGICA DE CENTRALIZAÇÃO =================
def centralizar_em(cap, metodo_detecao, nome_alvo):
    """Loop de Visual Servoing: move a impressora até alinhar a câmera com o alvo"""
    print(f"\n--- Iniciando centralização no {nome_alvo} ---")
    
    # Pega resolução da câmera para saber onde é o centro exato da tela
    _, frame = cap.read()
    altura_frame, largura_frame = frame.shape[:2]
    centro_tela_x = largura_frame // 2
    centro_tela_y = altura_frame // 2

    # Configura impressora para movimento Relativo
    enviar_gcode("G91") 

    while True:
        # Descartar frames antigos do buffer da câmera para pegar uma foto atualizada após o movimento
        for _ in range(5): cap.read() 
        ret, frame = cap.read()
        
        if not ret: continue

        cX, cY = metodo_detecao(frame)

        if cX is not None and cY is not None:
            # Desenha os eixos na tela
            cv2.circle(frame, (cX, cY), 5, (0, 0, 255), -1)
            cv2.line(frame, (centro_tela_x, 0), (centro_tela_x, altura_frame), (255, 0, 0), 1)
            cv2.line(frame, (0, centro_tela_y), (largura_frame, centro_tela_y), (255, 0, 0), 1)

            # Calcula o erro em pixels
            erro_x_pixels = cX - centro_tela_x
            erro_y_pixels = cY - centro_tela_y
            
            cv2.putText(frame, f"Erro: X={erro_x_pixels} Y={erro_y_pixels}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("Processo de Centralizacao", frame)
            cv2.waitKey(1)

            # Condição de parada (alvo centralizado)
            if abs(erro_x_pixels) < TOLERANCIA_PIXELS and abs(erro_y_pixels) < TOLERANCIA_PIXELS:
                print(f"{nome_alvo} centralizado com sucesso!")
                break

            # Converte pixels para milímetros
            move_x = erro_x_pixels * MM_POR_PIXEL * DIR_X
            move_y = erro_y_pixels * MM_POR_PIXEL * DIR_Y

            # Manda a impressora mover os eixos para compensar (Velocidade F1500)
            enviar_gcode(f"G1 X{move_x:.2f} Y{move_y:.2f} F1500")
            
            # Aguarda todos os movimentos físicos terminarem antes de tirar a próxima foto
            enviar_gcode("M400") 
            time.sleep(0.5) # Pausa extra para a câmera focar/estabilizar
        else:
            print(f"Buscando {nome_alvo}...")
            cv2.imshow("Processo de Centralizacao", frame)
            cv2.waitKey(1)

# ================= EXECUÇÃO PRINCIPAL =================
def main():
    cap = cv2.VideoCapture(CAMERA_ID)
    
    # 1. Homing Inicial
    print("Iniciando Homing (G28)...")
    enviar_gcode("G28")
    
    # 2. Ir para o centro da mesa COMPENSANDO o Offset da Câmera
    # Se a câmera está fora do bico, o carro (bico fantasma) precisa ir para uma posição diferente
    # para que a LENTE fique em (110, 110)
    pos_carro_x = CENTRO_MESA_X - OFFSET_CAMERA_X
    pos_carro_y = CENTRO_MESA_Y - OFFSET_CAMERA_Y
    
    # G90 = Movimento Absoluto
    enviar_gcode("G90")
    print(f"Movendo carro para compensar offset. Destino: X{pos_carro_x} Y{pos_carro_y}")
    enviar_gcode(f"G1 X{pos_carro_x} Y{pos_carro_y} Z20 F3000") # Z20 para focar, ajuste conforme sua lente
    enviar_gcode("M400")

    # 3. Centralizar perfeitamente no ArUco (Ajuste Fino Inicial)
    centralizar_em(cap, detectar_aruco, "Marcador ArUco")

    # 4. Agora procura e centraliza no Objeto Escuro (usando Canny)
    centralizar_em(cap, detectar_objeto_canny, "Objeto Escuro")

    print("\nPROCESSO CONCLUÍDO! A câmera está perfeitamente sobre o objeto.")
    
    # Mantém o stream aberto para visualização final
    while True:
        ret, frame = cap.read()
        if ret:
            cv2.imshow("Processo de Centralizacao", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    impressora.close()

if __name__ == "__main__":
    main()