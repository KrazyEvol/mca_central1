import serial
import serial.tools.list_ports
from importlib import import_module
from collections import deque

try:
    plt = import_module("matplotlib.pyplot")
except ModuleNotFoundError as erro:
    raise SystemExit("Matplotlib não encontrado. Instale com: python -m pip install matplotlib") from erro

BAUD = 9600
PONTOS = 100


def achar_porta():
    for p in serial.tools.list_ports.comports():
        if "CH340" in p.description:
            return p.device
    return None


porta = achar_porta()
if porta is None:
    print("Placa (CH340) não encontrada. Confira o cabo USB.")
    raise SystemExit

dados = deque([0] * PONTOS, maxlen=PONTOS)

plt.ion()
fig, ax = plt.subplots()
linha, = ax.plot(list(dados))
ax.set_ylim(0, 1023)
ax.set_title("Potenciômetro (A0)")
ax.set_xlabel("Leituras recentes")
ax.set_ylabel("Valor (0 a 1023)")

print(f"Lendo {porta} a {BAUD} baud. Feche a janela do gráfico para parar.")

try:
    with serial.Serial(porta, BAUD, timeout=0.2) as conexao:
        while plt.fignum_exists(fig.number):
            texto = conexao.readline().decode("utf-8", errors="ignore").strip()
            try:
                dados.append(float(texto))
            except ValueError:
                pass  # linha vazia ou texto: ignora
            linha.set_ydata(list(dados))
            plt.pause(0.01)
except serial.SerialException as erro:
    print(f"Placa desconectada ou porta ocupada: {erro}")
except KeyboardInterrupt:
    print("Encerrado.")