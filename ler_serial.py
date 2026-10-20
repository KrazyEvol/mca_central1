import serial
import serial.tools.list_ports

BAUD = 9600


def achar_porta():
    for p in serial.tools.list_ports.comports():
        if "CH340" in p.description:
            return p.device
    return None


PORTA = achar_porta()
if PORTA is None:
    print("Placa (CH340) não encontrada. Confira o cabo USB.")
    raise SystemExit

print(f"Lendo {PORTA} a {BAUD} baud (Ctrl+C para parar)")

try:
    with serial.Serial(PORTA, BAUD, timeout=2) as conexao:
        while True:
            linha = conexao.readline().decode("utf-8", errors="ignore").strip()
            if not linha:
                print("(nada recebido)")
                continue
            try:
                valor = float(linha)
                print(f"Número lido: {valor}")
            except ValueError:
                print(f"Texto lido: {linha}")
except serial.SerialException as erro:
    print(f"Placa desconectada ou porta ocupada: {erro}")
except KeyboardInterrupt:
    print("Encerrado.")