from serial.tools import list_ports

portas = list_ports.comports()

if not portas:
    print("Nenhuma porta COM encontrada. Confira o cabo USB e o driver da placa.")
else:
    for porta in portas:
        print(f"{porta.device}  ->  {porta.description}")