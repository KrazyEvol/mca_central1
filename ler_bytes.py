import serial, time

PORTA = "COM6"
BAUD = 9600  # use o mesmo valor do Serial.begin

with serial.Serial(PORTA, BAUD, timeout=1) as s:
    time.sleep(3)  # espera a Mega reiniciar
    print("Lendo bytes crus (Ctrl+C para parar)...")
    while True:
        dados = s.read(64)
        print(dados if dados else "(vazio)")