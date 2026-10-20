import math
import random
import threading
import time
from collections import deque
from datetime import datetime

import serial
import serial.tools.list_ports
from dash import Dash, html, dcc, Input, Output, ALL, ctx
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go

# ---------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------
MAX_PONTOS = 120       # quantos pontos o gráfico guarda
MAX_OCORRENCIAS = 50   # quantas ocorrências o histórico guarda
MAX_TRILHA = 100       # quantas posições recentes do GPS guardar no trajeto
VALIDADE_GPS_S = 10    # segundos até considerar os dados do GPS desatualizados
INTERVALO_S = 1        # segundos entre leituras simuladas
BAUD = 9600            # precisa ser igual ao Serial.begin() do sketch da Mega

# MQ-135: conversão LINEAR PROVISÓRIA de ADC para uma escala ppm de demonstração.
# ATENÇÃO: isto NÃO é uma calibração física do MQ-135. Os pontos 0->350 e 1023->5000
# são apenas uma escala ilustrativa solicitada para a interface; não usar para segurança.
MQ135_ADC_MIN = 0
MQ135_ADC_MAX = 1023
MQ135_PPM_MIN = 350.0
MQ135_PPM_MAX = 5000.0
MQ135_LIMITE_BAIXO_PPM = 350.0
MQ135_LIMITE_ALTO_PPM = 1000.0

# Traduz o nome curto que a placa envia para o id do sensor no cadastro.
MAPA_SERIAL = {
    "mq135": "mq135",
    "lux": "luminosidade",
    "bpm": "frequencia",
    "spo2": "spo2",
}

# Cadastro dos sensores. Para adicionar um novo, acrescente um dicionário aqui.
# "casas"      = quantas casas decimais mostrar no cartão.
# "origem"     = "simulado" (demonstração), "serial" (vem da Mega), "gps" (campos do GPS)
#                ou None (ainda não ligado).
# "faixa"      = (mínimo, máximo) fixo do eixo Y do gráfico (opcional).
# "limites"    = {"min": valor ou None, "max": valor ou None} (opcional).
#                Fora desse intervalo, o sensor entra em alerta.
# "validade_s" = depois de quantos segundos sem leitura nova o valor deixa de valer (opcional).
# "ocioso"     = valor mostrado no cartão quando não há leitura válida (opcional).
#                Não é medição: o cartão fica em "Sem leitura" e nunca gera alerta.
SENSORES = [
    {"id": "luminosidade", "nome": "Luminosidade", "descricao": "BH1750 (limites provisórios)",
     "unidade": "lux", "casas": 0, "origem": "serial",
     # PROVISÓRIO: troque pelos valores definidos com seu professor / norma de iluminação.
     "limites": {"min": 100, "max": 10000}},
    {"id": "frequencia", "nome": "Frequência cardíaca",
     "descricao": "MAX30102 (estimativa, apoie o dedo)",
     "unidade": "bpm", "casas": 0, "origem": "serial", "faixa": (40, 200),
     "validade_s": 5, "ocioso": {"valor": 0}},
    {"id": "spo2", "nome": "SpO₂",
     "descricao": "MAX30102 (estimativa, não calibrado)",
     "unidade": "%", "casas": 0, "origem": "serial", "faixa": (70, 100),
     "validade_s": 5, "ocioso": {"valor": 0}},
    {"id": "mq135", "nome": "Qualidade do ar",
     "descricao": "MQ-135 • escala linear ilustrativa, requer calibração real",
     "unidade": "ppm*", "casas": 0, "origem": "serial", "faixa": (350, 5000),
     "limites": {"min": 350, "max": 1000}, "validade_s": 5},
    {"id": "temperatura", "nome": "Temperatura", "descricao": "Sensor NTC",
     "unidade": "°C", "casas": 1, "origem": None},
    {"id": "gps", "nome": "GPS", "descricao": "NEO-6M (precisa de céu aberto)",
     "unidade": "", "casas": 0, "origem": "gps"},
]

# Estados possíveis de um cartão: texto exibido e cor.
ESTADOS = {
    "sem_dados": {"texto": "Sem dados", "cor": "#94a3b8"},
    "ocioso": {"texto": "Sem leitura (sem dedo ou medindo)", "cor": "#94a3b8"},
    "simulado": {"texto": "Simulado (demonstração)", "cor": "#D4AF37"},
    "real": {"texto": "Dados reais", "cor": "#D98B55"},
    "normal": {"texto": "Dentro do limite", "cor": "#D98B55"},
    "alerta_baixo": {"texto": "ALERTA: abaixo do mínimo", "cor": "#fb7185"},
    "alerta_alto": {"texto": "ALERTA: acima do máximo", "cor": "#fb7185"},
    "desconectado": {"texto": "Placa desconectada", "cor": "#fb7185"},
    "aquecendo": {"texto": "Aquecendo (não avalie ainda)", "cor": "#f59e0b"},
    "aprendendo": {"texto": "Aguardando leitura", "cor": "#f59e0b"},
    "ar_normal": {"texto": "Qualidade do ar: aceitável", "cor": "#34d399"},
    "ar_atencao": {"texto": "ATENÇÃO: ventilação inadequada", "cor": "#D4AF37"},
    "ar_alerta": {"texto": "ALERTA: concentração elevada", "cor": "#fb7185"},
    "gps_ok": {"texto": "Posição válida", "cor": "#D98B55"},
    "gps_sem_sinal": {"texto": "Sem sinal de satélite", "cor": "#D4AF37"},
    "gps_sem_modulo": {"texto": "Módulo GPS sem dados", "cor": "#fb7185"},
}
ESTADOS_DE_ALERTA = ("alerta_baixo", "alerta_alto", "ar_atencao", "ar_alerta")
ESTADOS_ATENCAO = ("ar_atencao",)
ESTADOS_NORMAIS = ("normal", "ar_normal")

# Estados em que o sensor NÃO está enviando dados: o cartão some e ele sai da lista de conexão.
# ("ocioso" não entra aqui: a placa está falando, só não há leitura, como BPM sem dedo.)
ESTADOS_INATIVOS = ("desconectado", "sem_dados", "gps_sem_modulo")

# Cada estado vira uma categoria; o CSS (.est-ok, .est-alerta...) decide a cor.
CATEGORIA = {
    "normal": "ok", "ar_normal": "ok", "real": "ok", "gps_ok": "ok",
    "ar_atencao": "atencao", "aquecendo": "atencao", "aprendendo": "atencao", "gps_sem_sinal": "atencao",
    "alerta_baixo": "alerta", "alerta_alto": "alerta", "ar_alerta": "alerta",
    "simulado": "demo",
}  # o que não está aqui (sem_dados, ocioso, desconectado...) cai em "neutro" (cinza)

ESTIMADOS = {"mq135", "frequencia", "spo2"}   # ganham o selo EST. no cartão
SENSOR_INICIAL = "mq135"                      # sensor mostrado no gráfico grande ao abrir

# ---------------------------------------------------------------
# ARMAZENAMENTO DOS DADOS
# ---------------------------------------------------------------
# Uma deque por sensor. Cada item é (horário, valor).
DADOS = {sensor["id"]: deque(maxlen=MAX_PONTOS) for sensor in SENSORES}
TRAVA = threading.Lock()

# Quanto tempo (segundos) um valor continua valendo. None = vale para sempre.
VALIDADE = {sensor["id"]: sensor.get("validade_s") for sensor in SENSORES}

# Histórico de ocorrências (alertas e retornos ao normal). Fica só na memória.
HISTORICO = deque(maxlen=MAX_OCORRENCIAS)
TRAVA_HIST = threading.Lock()

# Situação da conexão com a Mega (a thread leitora atualiza, a página lê).
STATUS_SERIAL = {"conectado": False, "mensagem": "Procurando a placa..."}

# Aquecimento e referência automática do MQ-135.
PRIMEIRO_DADO = {sensor["id"]: None for sensor in SENSORES}
BASE_AR = {"valor": None, "amostras": [], "inicio": None}
TRAVA_AR = threading.Lock()

# Última situação do GPS (a thread leitora atualiza, a página lê).
GPS = {"estado": None, "satelites": None, "lat": None, "lon": None, "horario": None}
TRILHA = deque(maxlen=MAX_TRILHA)   # posições válidas recentes: (lat, lon)
TRAVA_GPS = threading.Lock()


def adc_para_ppm(adc):
    """Mapeamento linear de demonstração; não substitui calibração MQ-135."""
    adc = max(MQ135_ADC_MIN, min(MQ135_ADC_MAX, float(adc)))
    proporcao = (adc - MQ135_ADC_MIN) / (MQ135_ADC_MAX - MQ135_ADC_MIN)
    return MQ135_PPM_MIN + proporcao * (MQ135_PPM_MAX - MQ135_PPM_MIN)


def simular_mq135():
    """DEMONSTRAÇÃO: gera valores ADC falsos e armazena a escala ppm ilustrativa."""
    inicio = time.time()
    while True:
        t = time.time() - inicio
        adc = 350 + 60 * math.sin(t / 15) + random.gauss(0, 8)
        ppm = adc_para_ppm(adc)
        with TRAVA:
            DADOS["mq135"].append((datetime.now(), ppm))
        time.sleep(INTERVALO_S)


def reiniciar_referencia_ar():
    """Mantida para compatibilidade; a versão linear não aprende referência automática."""
    return


def recalibrar_ar():
    """Não calibra fisicamente o MQ-135; apenas informa a limitação na interface."""
    return


def acumular_base_ar(valor, agora):
    # Compatibilidade com o leitor serial legado. O valor armazenado já é ppm ilustrativo.
    return


def classificar_ar():
    """Classifica a escala ppm provisória com os intervalos solicitados pelo usuário."""
    valor = ultimo_valor("mq135")
    if valor is None:
        return "aprendendo", None, None
    if valor < 450:
        return "ar_normal", valor, None  # Excelente
    if valor < 700:
        return "ar_normal", valor, None  # Bom
    if valor < 1000:
        return "ar_normal", valor, None  # Aceitável
    if valor < 2000:
        return "ar_atencao", valor, None  # Ruim
    return "ar_alerta", valor, None  # Muito ruim / faixa elevada


def estado_mq135():
    if not STATUS_SERIAL["conectado"]:
        return "desconectado"
    valor = ultimo_valor("mq135")
    if valor is None:
        return "sem_dados"
    return classificar_ar()[0]


def achar_porta():
    """Procura a placa pelo nome do conversor (CH340). Devolve a COM ou None."""
    for p in serial.tools.list_ports.comports():
        if "CH340" in p.description:
            return p.device
    return None


def interpretar_linha(linha):
    """Converte 'pot:512,lux:180.0,bpm:72,spo2:97' em {'potenciometro': 512.0, ...}.
    Ignora o que não entender."""
    resultado = {}
    for parte in linha.split(","):
        if ":" not in parte:
            continue
        nome, texto = parte.split(":", 1)
        sensor_id = MAPA_SERIAL.get(nome.strip())
        if sensor_id is None:
            continue  # nome que não está no mapa
        try:
            resultado[sensor_id] = float(texto)
        except ValueError:
            continue  # valor que não é número
    return resultado


def interpretar_gps(linha):
    """Extrai só os campos do GPS de uma linha como
    'pot:38,gps:2,sat:7,lat:-22.1,lon:-43.1'. Ignora o que não entender."""
    campos = {}
    for parte in linha.split(","):
        if ":" not in parte:
            continue
        nome, texto = parte.split(":", 1)
        nome = nome.strip()
        if nome in ("gps", "sat", "lat", "lon"):
            try:
                campos[nome] = float(texto)
            except ValueError:
                continue
    return campos


def atualizar_gps(linha):
    """Atualiza a situação do GPS. Só age em linhas que trazem o campo 'gps'."""
    campos = interpretar_gps(linha)
    if "gps" not in campos:
        return  # linha de erro, linha cortada ou placa sem GPS
    lat, lon = campos.get("lat"), campos.get("lon")
    # Posição só vale se o módulo disse 2 E as duas coordenadas existem e fazem sentido
    posicao_valida = (
        campos["gps"] == 2
        and lat is not None and lon is not None
        and -90 <= lat <= 90 and -180 <= lon <= 180
    )
    with TRAVA_GPS:
        GPS["estado"] = int(campos["gps"])
        GPS["satelites"] = int(campos["sat"]) if "sat" in campos else None
        GPS["lat"] = lat if posicao_valida else None   # sem posição: apaga a antiga
        GPS["lon"] = lon if posicao_valida else None
        GPS["horario"] = datetime.now()
        if posicao_valida:
            # Guarda apenas posições novas, evitando repetir o mesmo ponto no trajeto.
            nova_posicao = (lat, lon)
            ultima_posicao = TRILHA[-1] if TRILHA else None
            if ultima_posicao is None or abs(ultima_posicao[0] - lat) > 0.00001 or abs(ultima_posicao[1] - lon) > 0.00001:
                TRILHA.append(nova_posicao)


def ler_serial():
    """Lê a Mega pela serial. Se a placa sumir, avisa e tenta de novo."""
    while True:
        porta = achar_porta()
        if porta is None:
            STATUS_SERIAL.update(conectado=False, mensagem="Placa (CH340) não encontrada. Confira o cabo USB.")
            reiniciar_referencia_ar()
            time.sleep(2)
            continue
        try:
            with serial.Serial(porta, BAUD, timeout=2) as conexao:
                STATUS_SERIAL.update(conectado=True, mensagem=f"Conectado em {porta} a {BAUD} baud")
                while True:
                    linha = conexao.readline().decode("utf-8", errors="ignore").strip()
                    atualizar_gps(linha)
                    leituras = interpretar_linha(linha)
                    if not leituras:
                        continue  # linha vazia ou inválida
                    # A leitura mq135 recebida é ADC; convertemos para escala ppm ilustrativa.
                    # Não é ppm real sem calibração com referência conhecida.
                    if "mq135" in leituras:
                        leituras["mq135"] = adc_para_ppm(leituras["mq135"])
                    agora = datetime.now()
                    with TRAVA:
                        for sensor_id, valor in leituras.items():
                            DADOS[sensor_id].append((agora, valor))
                            if PRIMEIRO_DADO.get(sensor_id) is None:
                                PRIMEIRO_DADO[sensor_id] = agora
        except serial.SerialException as erro:
            STATUS_SERIAL.update(conectado=False, mensagem=f"Falha na porta: {erro}")
            reiniciar_referencia_ar()
            time.sleep(2)


def ultimo_valor(sensor_id):
    """Devolve o valor mais recente do sensor, ou None se não há dados
    (ou se o último valor é velho demais, conforme 'validade_s')."""
    with TRAVA:
        dados = DADOS[sensor_id]
        if not dados:
            return None
        horario, valor = dados[-1]
    validade = VALIDADE[sensor_id]
    if validade is not None and (datetime.now() - horario).total_seconds() > validade:
        return None
    return valor


def copiar_dados(sensor_id):
    """Devolve duas listas (horários, valores) copiadas com segurança."""
    with TRAVA:
        pontos = list(DADOS[sensor_id])
    return [p[0] for p in pontos], [p[1] for p in pontos]


def avaliar_limite(sensor, valor):
    """Compara o valor com os limites do sensor.
    Devolve 'baixo', 'alto', 'ok' ou None (se o sensor não tem limites)."""
    limites = sensor.get("limites")
    if not limites or valor is None:
        return None
    minimo, maximo = limites.get("min"), limites.get("max")
    if minimo is not None and valor < minimo:
        return "baixo"
    if maximo is not None and valor > maximo:
        return "alto"
    return "ok"


def estado_do_gps():
    """Decide em qual das 5 situações o GPS está."""
    if not STATUS_SERIAL["conectado"]:
        return "desconectado"
    with TRAVA_GPS:
        estado, horario, lat = GPS["estado"], GPS["horario"], GPS["lat"]
    if horario is None or (datetime.now() - horario).total_seconds() > VALIDADE_GPS_S:
        return "sem_dados"
    if estado == 0:
        return "gps_sem_modulo"
    if estado == 2 and lat is not None:
        return "gps_ok"
    return "gps_sem_sinal"


def estado_do_sensor(sensor):
    """Decide o estado do cartão com base na origem, nos dados e nos limites."""
    if sensor["origem"] == "gps":
        return estado_do_gps()
    if sensor["id"] == "mq135":
        return estado_mq135()
    if sensor["origem"] == "serial" and not STATUS_SERIAL["conectado"]:
        return "desconectado"
    valor = ultimo_valor(sensor["id"])
    if valor is None:
        # Sem leitura válida: se o sensor tem valor "ocioso", é um estado neutro (nunca alerta).
        return "ocioso" if sensor.get("ocioso") else "sem_dados"
    if sensor["origem"] == "simulado":
        return "simulado"
    resultado = avaliar_limite(sensor, valor)
    if resultado == "baixo":
        return "alerta_baixo"
    if resultado == "alto":
        return "alerta_alto"
    if resultado == "ok":
        return "normal"
    return "real"  # sensor sem limites cadastrados


def sensor_ativo(sensor):
    """True se o sensor está enviando dados agora."""
    return estado_do_sensor(sensor) not in ESTADOS_INATIVOS


def texto_estado(sensor, estado):
    """Texto pequeno do cartão. Para o MQ-135, mostra a faixa da escala demonstrativa."""
    if sensor["id"] == "mq135" and estado.startswith("ar_"):
        valor = ultimo_valor("mq135")
        if valor is not None:
            for limite, rotulo in ((450, "Excelente"), (700, "Bom"), (1000, "Aceitável"), (2000, "Ruim")):
                if valor < limite:
                    return f"{rotulo} • escala demonstrativa"
            return "Muito ruim • escala demonstrativa"
    return ESTADOS[estado]["texto"]


def texto_gps():
    """Texto do cartão do GPS (com quebras de linha)."""
    estado = estado_do_gps()
    with TRAVA_GPS:
        lat, lon, sat = GPS["lat"], GPS["lon"], GPS["satelites"]
    if estado == "gps_ok" and lat is not None:
        return f"Lat {lat:.5f}\nLon {lon:.5f}\n{sat or 0} satélites"
    if estado == "gps_sem_sinal":
        return f"--\n{sat or 0} satélites"
    return "--"


def texto_valor(sensor):
    """Texto grande do cartão, como '352 ' ou '-- °C'."""
    if sensor["origem"] == "gps":
        return texto_gps()
    if sensor["id"] == "mq135":
        valor = ultimo_valor("mq135")
        if valor is None or estado_mq135() in ("desconectado", "sem_dados"):
            return "-- ppm*"
        return f"{valor:.0f} ppm*"
    if estado_do_sensor(sensor) == "desconectado":
        return f"-- {sensor['unidade']}"
    valor = ultimo_valor(sensor["id"])
    if valor is None:
        ocioso = sensor.get("ocioso")
        if not ocioso:
            return f"-- {sensor['unidade']}"
        valor = ocioso["valor"]  # valor de espera, não é medição
    return f"{valor:.{sensor['casas']}f} {sensor['unidade']}"


def lista_de_alertas():
    """Devolve as frases de todos os sensores que estão fora do limite agora."""
    frases = []
    for sensor in SENSORES:
        estado = estado_do_sensor(sensor)
        if estado not in ESTADOS_DE_ALERTA:
            continue
        if sensor["id"] == "mq135":
            valor = ultimo_valor("mq135")
            if valor is not None:
                faixa = "ruim" if valor < 2000 else "muito ruim / concentração elevada"
                frases.append(f"MQ-135: {valor:.0f} ppm* na escala linear ilustrativa ({faixa}). Não é uma medição real de ppm.")
            continue
        limites = sensor["limites"]
        if estado == "alerta_baixo":
            detalhe = f"abaixo do mínimo configurado ({limites['min']} {sensor['unidade']})"
        else:
            detalhe = f"acima do máximo configurado ({limites['max']} {sensor['unidade']})"
        frases.append(f"{sensor['nome']}: {texto_valor(sensor).strip()}, {detalhe}")
    return frases


# ---------------------------------------------------------------
# HISTÓRICO DE OCORRÊNCIAS
# ---------------------------------------------------------------
def registrar_ocorrencia(sensor, estado):
    """Guarda uma linha no histórico. 'estado' é um alerta, 'normal', 'ativo' ou um estado inativo."""
    if estado == "normal":
        texto = "Voltou ao normal"
    elif estado == "ativo":
        texto = "Sensor ativo"
    else:
        texto = ESTADOS[estado]["texto"]
    ocorrencia = {
        "horario": datetime.now(),
        "sensor": sensor["nome"],
        "texto": texto,
        "valor": f"{texto_valor(sensor).strip()}",
        "alerta": estado in ESTADOS_DE_ALERTA,
    }
    with TRAVA_HIST:
        HISTORICO.append(ocorrencia)


def vigiar_alertas():
    """Compara o estado de cada sensor com o anterior e registra só quando MUDA."""
    anterior = {}
    while True:
        for sensor in SENSORES:
            estado = estado_do_sensor(sensor)
            antes = anterior.get(sensor["id"])
            if estado in ESTADOS_DE_ALERTA and estado != antes:
                registrar_ocorrencia(sensor, estado)
            elif estado in ESTADOS_NORMAIS and antes in ESTADOS_DE_ALERTA:
                registrar_ocorrencia(sensor, "normal")
            elif estado in ESTADOS_INATIVOS and antes is not None and antes not in ESTADOS_INATIVOS:
                registrar_ocorrencia(sensor, estado)      # o sensor parou de enviar dados
            elif estado not in ESTADOS_INATIVOS and antes in ESTADOS_INATIVOS:
                registrar_ocorrencia(sensor, "ativo")     # o sensor voltou a enviar dados
            anterior[sensor["id"]] = estado
        time.sleep(0.5)


# Liga as threads conforme a origem cadastrada nos sensores.
if any(s["origem"] == "simulado" for s in SENSORES):
    threading.Thread(target=simular_mq135, daemon=True).start()
if any(s["origem"] in ("serial", "gps") for s in SENSORES):
    threading.Thread(target=ler_serial, daemon=True).start()
threading.Thread(target=vigiar_alertas, daemon=True).start()


# ---------------------------------------------------------------
# COMPONENTES VISUAIS
# ---------------------------------------------------------------
COR_FUNDO = "#0E1116"
COR_SUPERFICIE = "#151A21"
COR_SUPERFICIE_2 = "#1C222B"
COR_BORDA = "#262D38"
COR_TEXTO = "#E6E8EB"
COR_SECUNDARIA = "#8B95A3"
COR_DESTAQUE = "#D98B55"   # cobre


def copiar_historico():
    with TRAVA_HIST:
        return list(HISTORICO)


def estilo_grafico_historico(fig, titulo, uirevision):
    """Aplica o visual do painel (fundo transparente, grade discreta) a um gráfico do histórico."""
    fig.update_layout(
        title=dict(text=titulo, x=0, xanchor="left", font=dict(size=14, color=COR_SECUNDARIA)),
        height=300, margin=dict(l=40, r=16, t=44, b=56),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COR_SECUNDARIA, family="Segoe UI"),
        legend=dict(orientation="h", y=-0.25), hovermode="x unified", uirevision=uirevision,
    )
    fig.update_xaxes(gridcolor=COR_BORDA, zeroline=False)
    fig.update_yaxes(gridcolor=COR_BORDA, zeroline=False, rangemode="tozero")
    return fig


def criar_graficos_historico():
    """Dois gráficos feitos só com as ocorrências registradas nesta sessão:
    barras por hora (hoje) e linhas dos últimos 7 dias. Duas categorias: alerta e informativo."""
    ocorrencias = copiar_historico()
    hoje = datetime.now().date()
    dias = [datetime.fromordinal(hoje.toordinal() - i).date() for i in range(6, -1, -1)]

    alertas_hora, info_hora = [0] * 24, [0] * 24
    alertas_dia, info_dia = [0] * 7, [0] * 7
    for o in ocorrencias:
        quando = o["horario"]
        if quando.date() == hoje:
            (alertas_hora if o["alerta"] else info_hora)[quando.hour] += 1
        if quando.date() in dias:
            (alertas_dia if o["alerta"] else info_dia)[dias.index(quando.date())] += 1

    horas = list(range(24))
    fig_hora = go.Figure()
    fig_hora.add_bar(x=horas, y=alertas_hora, name="Alertas", marker_color="#F0616D")
    fig_hora.add_bar(x=horas, y=info_hora, name="Informativos", marker_color=COR_DESTAQUE)
    fig_hora.update_layout(barmode="stack")
    estilo_grafico_historico(fig_hora, "Ocorrências por hora • hoje", "hist-horas")
    fig_hora.update_xaxes(tickmode="linear", dtick=2)

    rotulos = [d.strftime("%d/%m") for d in dias]
    fig_dias = go.Figure()
    fig_dias.add_scatter(x=rotulos, y=alertas_dia, name="Alertas", mode="lines+markers",
                         line=dict(color="#F0616D", width=2), marker=dict(size=6))
    fig_dias.add_scatter(x=rotulos, y=info_dia, name="Informativos", mode="lines+markers",
                         line=dict(color=COR_DESTAQUE, width=2), marker=dict(size=6))
    estilo_grafico_historico(fig_dias, "Ocorrências • últimos 7 dias", "hist-dias")

    return fig_hora, fig_dias


def criar_tabela_historico():
    """Tabela de eventos recentes, do mais novo ao mais antigo. O estilo vem do CSS (.tabela, .sev)."""
    ocorrencias = copiar_historico()
    if not ocorrencias:
        return html.Div("Nenhuma ocorrência registrada ainda. Os eventos aparecem aqui quando um sensor muda de estado.",
                        className="vazio")
    cabecalho = html.Tr([html.Th(t) for t in ("Horário", "Sensor", "Ocorrência", "Valor", "Severidade")])
    linhas = []
    for o in reversed(ocorrencias):
        classe_sev = "sev sev-alerta" if o["alerta"] else "sev sev-info"
        linhas.append(html.Tr([
            html.Td(o["horario"].strftime("%d/%m/%Y %H:%M:%S"), className="num"),
            html.Td(o["sensor"]),
            html.Td(o["texto"]),
            html.Td(o["valor"], className="num"),
            html.Td(html.Span("Alerta" if o["alerta"] else "Informativo", className=classe_sev)),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], className="tabela")


def criar_indicador(titulo, valor, id_valor, classe=""):
    """Cartão de número do histórico. O id é o mesmo que o callback atualizar_tela_historico usa."""
    return html.Div([html.Div(titulo, className="ind-titulo"),
                     html.Div(str(valor), id=id_valor, className=f"ind-valor {classe}".strip())],
                    className="indicador")


def criar_tela_historico():
    """Tela completa da aba Histórico de eventos."""
    ocorrencias = copiar_historico()
    total = len(ocorrencias)
    alertas = sum(1 for o in ocorrencias if o["alerta"])
    fig_hora, fig_dias = criar_graficos_historico()
    return html.Div([
        html.Div([html.H1("Histórico de eventos"),
                  html.Div("Alertas e mudanças de estado desta sessão", className="hora")],
                 className="titulo-linha"),
        html.Div([
            criar_indicador("Ocorrências", total, "historico-total"),
            criar_indicador("Alertas", alertas, "historico-alertas-total", "alerta"),
            criar_indicador("Informativos", total - alertas, "historico-info-total", "cobre"),
        ], className="grade-indicadores"),
        html.Div([
            html.Section(dcc.Graph(id="historico-grafico-horas", figure=fig_hora, config={"displayModeBar": False}), className="painel"),
            html.Section(dcc.Graph(id="historico-grafico-dias", figure=fig_dias, config={"displayModeBar": False}), className="painel"),
        ], className="grade-graficos"),
        html.Section([
            html.Div("Registro detalhado", className="rotulo"),
            html.Div(criar_tabela_historico(), id="tabela-historico-aba", className="tabela-wrap"),
        ], className="painel"),
    ])


def criar_tela_gps():
    """Tela completa da aba GPS / Localização."""
    with TRAVA_GPS:
        lat, lon, sat = GPS["lat"], GPS["lon"], GPS["satelites"]
    if lat is not None and lon is not None:
        resumo, classe = f"Latitude {lat:.6f} • Longitude {lon:.6f} • {sat or 0} satélites", "faixa-alerta ok"
    else:
        resumo, classe = "Aguardando posição GPS válida. O módulo normalmente precisa de céu aberto.", "faixa-alerta ok"
    return html.Div([
        html.Div([html.H1("GPS / Localização"),
                  html.Div("Posição atual e trajeto recente", className="hora")],
                 className="titulo-linha"),
        html.Div(resumo, className=classe),
        html.Section(dcc.Graph(id="mapa-gps-separado", figure=criar_mapa(), className="mapa",
                               config={"displayModeBar": True, "scrollZoom": True, "displaylogo": False}),
                     className="painel"),
        html.P("Para vários coletes simultâneos, cada dispositivo precisará enviar um identificador único junto com a latitude e a longitude.",
               className="nota"),
    ])


def classe_cartao(estado, aberto=False):
    """Monta o class do cartão. Sensor sem dados recebe 'oculto' e some do painel."""
    classes = ["cartao", "est-" + CATEGORIA.get(estado, "neutro")]
    if aberto:
        classes.append("aberto")
    if estado in ESTADOS_INATIVOS:
        classes.append("oculto")
    return " ".join(classes)


def criar_spark(sensor):
    """Minigráfico dos últimos pontos, sem eixos, para dentro do cartão."""
    x, y = copiar_dados(sensor["id"])
    fig = go.Figure(go.Scatter(x=x[-60:], y=y[-60:], mode="lines",
                               line=dict(color=COR_DESTAQUE, width=1.5)))
    fig.update_layout(height=48, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


def criar_cartao(sensor, aberto=False):
    """Cartão de sensor (clicável): título, número grande, unidade, estado e minigráfico."""
    estado = estado_do_sensor(sensor)
    numero, _, unidade = texto_valor(sensor).rpartition(" ")
    selo = html.Span("EST.", className="selo", title="Valor estimado, não calibrado") if sensor["id"] in ESTIMADOS else None
    return html.Div(
        [
            html.Div([html.Span(sensor["nome"]), selo, html.Span(className="ponto")], className="cartao-topo"),
            html.Div([html.Span(numero), html.Span(unidade, className="unidade")],
                     id={"type": "valor", "id": sensor["id"]}, className="cartao-valor"),
            html.Div(texto_estado(sensor, estado), id={"type": "estado", "id": sensor["id"]}, className="cartao-estado"),
            dcc.Graph(id={"type": "spark", "id": sensor["id"]}, figure=criar_spark(sensor),
                      config={"staticPlot": True}, className="spark", style={"height": "48px"}),
        ],
        id={"type": "cartao", "id": sensor["id"]},
        n_clicks=0,
        className=classe_cartao(estado, aberto),
        title=sensor["descricao"],   # a descrição longa aparece ao passar o mouse
    )


def criar_mapa():
    """Mapa do GPS: posição atual (ponto vermelho) e trajeto recente (linha âmbar).
    Sem posição válida, mostra só um aviso (nunca uma posição antiga)."""
    with TRAVA_GPS:
        lat, lon = GPS["lat"], GPS["lon"]
        trilha = list(TRILHA)
    fig = go.Figure()

    if estado_do_gps() == "gps_ok" and lat is not None:
        fig.add_trace(go.Scattermap(
            lat=[p[0] for p in trilha], lon=[p[1] for p in trilha],
            mode="lines", line=dict(width=3, color="#D4AF37"), name="Trajeto",
        ))
        fig.add_trace(go.Scattermap(
            lat=[lat], lon=[lon], mode="markers",
            marker=dict(size=14, color="#fb7185"), name="Posição atual",
        ))
        fig.update_layout(
            title=f"GPS: Lat {lat:.6f}, Lon {lon:.6f}",
            map=dict(style="open-street-map", center=dict(lat=lat, lon=lon), zoom=16),
            margin=dict(l=0, r=0, t=40, b=0),
        )
    else:
        fig.update_layout(title="GPS", template="plotly_dark")
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.add_annotation(
            text="Sem posição válida", x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=20, color=COR_SECUNDARIA),
        )

    fig.update_layout(height=650, uirevision="gps-estavel", autosize=True)  # preserva zoom e estado visual entre atualizações
    return fig


def criar_figura(sensor):
    """Gráfico grande do sensor, com as linhas de limite."""
    if sensor["id"] == "gps":
        return criar_mapa()
    x, y = copiar_dados(sensor["id"])
    tem_dados = len(x) > 0

    titulo = sensor["nome"]
    if tem_dados and sensor["origem"] == "simulado":
        titulo += " (DEMONSTRAÇÃO)"

    fig = go.Figure()
    if tem_dados:
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=sensor["nome"],
                                 line=dict(color=COR_DESTAQUE, width=2)))
        limites = sensor.get("limites") or {}
        for chave, rotulo in (("min", "Limite mínimo"), ("max", "Limite máximo")):
            valor_limite = limites.get(chave)
            if valor_limite is None:
                continue
            fig.add_trace(go.Scatter(
                x=[x[0], x[-1]], y=[valor_limite, valor_limite], mode="lines",
                name=f"{rotulo} ({valor_limite} {sensor['unidade']})".strip(),
                line=dict(color="#F0616D", dash="dash", width=1.5)))

    fig.update_layout(
        title=dict(text=titulo, x=0, xanchor="left", font=dict(size=14, color=COR_SECUNDARIA)),
        height=360, margin=dict(l=48, r=16, t=44, b=60),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COR_SECUNDARIA, family="Segoe UI"),
        showlegend=True, legend=dict(orientation="h", y=-0.2),
        hovermode="x unified", uirevision=sensor["id"],
    )
    fig.update_xaxes(gridcolor=COR_BORDA, zeroline=False)
    fig.update_yaxes(gridcolor=COR_BORDA, zeroline=False, title=sensor["unidade"])
    if "faixa" in sensor:
        fig.update_yaxes(range=list(sensor["faixa"]))
    if not tem_dados:
        fig.add_annotation(text="Sem dados", x=0.5, y=0.5, xref="paper", yref="paper",
                           showarrow=False, font=dict(size=20, color=COR_SECUNDARIA))
    return fig


# ---------------------------------------------------------------
# PÁGINA
# ---------------------------------------------------------------
app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "MCI | Central de Monitoramento"

TITULO_SECAO = {"margin": "0", "fontSize": "20px", "color": COR_TEXTO}
TEXTO_AUX = {"margin": "5px 0 14px", "fontSize": "12px", "color": COR_SECUNDARIA, "lineHeight": "1.6"}

NOTA_TECNICA = ("Nota técnica: a conversão ADC→ppm do MQ-135 é apenas uma escala linear demonstrativa, não uma medição calibrada. "
                "Não usar para decisões de segurança industrial. BPM e SpO₂ são estimativas e não substituem equipamento médico.")

nomes_simulados = [s["nome"] for s in SENSORES if s["origem"] == "simulado"]
aviso_simulado = html.Div(
    f"MODO DEMONSTRAÇÃO: os dados de {', '.join(nomes_simulados)} são simulados e NÃO são leituras reais.",
    className="faixa-alerta demo",
) if nomes_simulados else None


def criar_aba(id_botao, icone, texto, ativa=False):
    return html.Button([html.Span(className=f"icone icone-{icone}"), html.Span(texto)],
                       id=id_botao, n_clicks=0, className="aba ativa" if ativa else "aba")


sensor_inicial = next(s for s in SENSORES if s["id"] == SENSOR_INICIAL)

app.layout = html.Div(
    [
        html.Div([
            html.Header([
                html.Div([
                    html.Div([html.Div("MCI", className="marca-nome"),
                              html.Div("Monitoramento de condições individuais", className="marca-sub")]),
                    html.Div("Central ativa", className="central-status"),
                ], className="topo-linha"),
                html.Nav([
                    criar_aba("nav-painel", "painel", "Painel principal", ativa=True),
                    criar_aba("nav-gps", "gps", "GPS / Localização"),
                    criar_aba("nav-historico", "hist", "Histórico de eventos"),
                ], className="abas"),
            ], className="cabecalho"),

            html.Div([
                html.Aside([html.Div("Conexão", className="rotulo"),
                            html.Div(id="painel-conexao")], className="lateral"),

                html.Main([
                    html.Div([html.H1("Painel principal"),
                              html.Div(datetime.now().strftime("%d/%m/%Y • %H:%M:%S"), id="hora-painel", className="hora")],
                             className="titulo-linha"),
                    aviso_simulado,
                    html.Div("Verificando limites...", id="painel-alertas", className="faixa-alerta ok"),
                    html.Div([criar_cartao(s) for s in SENSORES if s["id"] != "gps"], className="grade-cartoes"),
                    html.Div("Nenhum sensor enviando dados no momento.", id="aviso-sem-sensores", className="vazio oculto"),
                    html.Section(dcc.Graph(id="grafico-principal", figure=criar_figura(sensor_inicial),
                                           config={"displayModeBar": False}), className="painel"),
                    html.P(NOTA_TECNICA, className="nota"),
                ], id="main-screen", className="conteudo"),

                html.Main(id="pagina-secundaria", children=[], className="conteudo oculto"),
            ], className="corpo"),
        ], className="pagina"),

        dcc.Store(id="sensor-selecionado", data=SENSOR_INICIAL),
        dcc.Interval(id="relogio", interval=1000, n_intervals=0),
        dcc.Interval(id="gps-relogio", interval=5000, n_intervals=0),
        dcc.Interval(id="graficos-relogio", interval=3000, n_intervals=0),
    ]
)


# ---------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------
@app.callback(
    Output("main-screen", "className"),
    Output("pagina-secundaria", "children"),
    Output("pagina-secundaria", "className"),
    Output("nav-painel", "className"),
    Output("nav-gps", "className"),
    Output("nav-historico", "className"),
    Input("nav-painel", "n_clicks"),
    Input("nav-gps", "n_clicks"),
    Input("nav-historico", "n_clicks"),
)
def navegar_telas(click_painel, click_gps, click_historico):
    """Mantém GPS e histórico em telas próprias, ocupando toda a área de conteúdo."""
    acionado = ctx.triggered_id
    if acionado == "nav-gps":
        pagina = "gps"
    elif acionado == "nav-historico":
        pagina = "historico"
    elif acionado == "nav-painel":
        pagina = "painel"
    else:
        # A atualização periódica não deve retirar o usuário da tela atual.
        pagina = getattr(navegar_telas, "pagina_atual", "painel")

    navegar_telas.pagina_atual = pagina
    classe_principal = "conteudo" if pagina == "painel" else "conteudo oculto"
    classe_secundaria = "conteudo oculto" if pagina == "painel" else "conteudo"
    abas = ["aba ativa" if pagina == p else "aba" for p in ("painel", "gps", "historico")]

    if pagina == "gps":
        conteudo = criar_tela_gps()
    elif pagina == "historico":
        conteudo = criar_tela_historico()
    else:
        conteudo = []
    return classe_principal, conteudo, classe_secundaria, *abas


@app.callback(Output("mapa-gps-separado", "figure"), Input("gps-relogio", "n_intervals"), prevent_initial_call=True)
def atualizar_mapa_gps(n):
    # Atualiza a figura no componente existente; a página não é desmontada a cada segundo.
    return criar_mapa()


@app.callback(
    Output("tabela-historico-aba", "children"),
    Output("historico-total", "children"),
    Output("historico-alertas-total", "children"),
    Output("historico-info-total", "children"),
    Output("historico-grafico-horas", "figure"),
    Output("historico-grafico-dias", "figure"),
    Input("relogio", "n_intervals"),
    prevent_initial_call=True,
)
def atualizar_tela_historico(n):
    ocorrencias = copiar_historico()
    fig_horas, fig_dias = criar_graficos_historico()
    return (criar_tabela_historico(), str(len(ocorrencias)),
            str(sum(1 for o in ocorrencias if o.get("alerta"))),
            str(sum(1 for o in ocorrencias if not o.get("alerta"))), fig_horas, fig_dias)


@app.callback(
    Output("sensor-selecionado", "data"),
    Input({"type": "cartao", "id": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def selecionar_sensor(cliques):
    """Clicou num cartão: esse sensor passa a ser o do gráfico grande."""
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate   # cartão recém-criado, não é clique de verdade
    return ctx.triggered_id["id"]


@app.callback(
    Output({"type": "valor", "id": ALL}, "children"),
    Output({"type": "estado", "id": ALL}, "children"),
    Output({"type": "cartao", "id": ALL}, "className"),
    Output("aviso-sem-sensores", "className"),
    Input("relogio", "n_intervals"),
    Input("sensor-selecionado", "data"),
)
def atualizar_cartoes(n, selecionado):
    """A cada segundo atualiza número, estado e classe (cor / visível ou oculto) de cada cartão."""
    valores, textos, classes = [], [], []
    algum_ativo = False
    for sensor in SENSORES:
        if sensor["id"] == "gps":
            continue  # o GPS tem aba própria
        estado = estado_do_sensor(sensor)
        numero, _, unidade = texto_valor(sensor).rpartition(" ")
        valores.append([html.Span(numero), html.Span(unidade, className="unidade")])
        textos.append(texto_estado(sensor, estado))
        classes.append(classe_cartao(estado, sensor["id"] == selecionado))
        algum_ativo = algum_ativo or estado not in ESTADOS_INATIVOS
    return valores, textos, classes, "vazio oculto" if algum_ativo else "vazio"


@app.callback(
    Output({"type": "spark", "id": ALL}, "figure"),
    Input("graficos-relogio", "n_intervals"),
)
def atualizar_sparklines(n):
    return [criar_spark(s) for s in SENSORES if s["id"] != "gps"]


@app.callback(
    Output("grafico-principal", "figure"),
    Input("graficos-relogio", "n_intervals"),
    Input("sensor-selecionado", "data"),
)
def atualizar_grafico_principal(n, selecionado):
    sensor = next(s for s in SENSORES if s["id"] == selecionado)
    return criar_figura(sensor)


@app.callback(
    Output("painel-alertas", "children"),
    Output("painel-alertas", "className"),
    Input("relogio", "n_intervals"),
)
def atualizar_painel_alertas(n):
    """Faixa discreta quando está tudo bem; destacada quando há sensor fora do limite."""
    frases = lista_de_alertas()
    if not frases:
        return "Nenhum alerta ativo", "faixa-alerta ok"
    return [html.Strong("Atenção"), *[html.Span(f) for f in frases]], "faixa-alerta alerta"


@app.callback(
    Output("painel-conexao", "children"),
    Input("relogio", "n_intervals"),
)
def atualizar_conexao(n):
    """Mostra só quem está enviando dados. Temperatura (origem None) ainda não está
    ligada, então não entra na contagem de 'previstos'."""
    previstos = [s for s in SENSORES if s["origem"]]
    ativos = [s for s in previstos if sensor_ativo(s)]
    contador = html.Div([html.Span(str(len(ativos)), className="conexao-numero"),
                         html.Span(f" de {len(previstos)} sensores ativos", className="conexao-texto")])
    if not STATUS_SERIAL["conectado"]:
        return [html.Div(STATUS_SERIAL["mensagem"], className="conexao-aviso"), contador]
    return [html.Div(STATUS_SERIAL["mensagem"], className="conexao-info"), contador,
            html.Ul([html.Li(s["nome"]) for s in ativos], className="conexao-lista")]


@app.callback(
    Output("hora-painel", "children"),
    Input("relogio", "n_intervals"),
)
def atualizar_hora_painel(n):
    return datetime.now().strftime("%d/%m/%Y • %H:%M:%S")


if __name__ == "__main__":
    # use_reloader=False: evita rodar o arquivo duas vezes (duas threads na mesma COM).
    app.run(debug=True, use_reloader=False)