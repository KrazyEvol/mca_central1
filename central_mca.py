import math
import random
import threading
import time
from collections import deque
from datetime import datetime

from dash import Dash, html, dcc, Input, Output, State, ALL, ctx
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go

# ---------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------
MODO_SIMULADO = True   # True = dados de demonstração. Com o Arduino, será False.
MAX_PONTOS = 120       # quantos pontos o gráfico guarda
INTERVALO_S = 1        # segundos entre leituras simuladas

# Cadastro dos sensores. Para adicionar um novo, acrescente um dicionário aqui.
# "casas" = quantas casas decimais mostrar no cartão.
SENSORES = [
    {"id": "mq135", "nome": "MQ-135", "descricao": "Qualidade do ar (leitura relativa)", "unidade": "", "casas": 0},
    {"id": "temperatura", "nome": "Temperatura", "descricao": "Sensor NTC", "unidade": "°C", "casas": 1},
    {"id": "luminosidade", "nome": "Luminosidade", "descricao": "Iluminância do ambiente", "unidade": "lux", "casas": 0},
    {"id": "frequencia", "nome": "Frequência cardíaca", "descricao": "MAX30102", "unidade": "bpm", "casas": 0},
    {"id": "spo2", "nome": "SpO₂", "descricao": "MAX30102 (estimativa)", "unidade": "%", "casas": 0},
    {"id": "gps", "nome": "GPS", "descricao": "Localização", "unidade": "", "casas": 0},
]

# Estados possíveis de um cartão: texto exibido e cor.
ESTADOS = {
    "sem_dados": {"texto": "Sem dados", "cor": "#9e9e9e"},
    "simulado": {"texto": "Simulado (demonstração)", "cor": "#ef6c00"},
    "normal": {"texto": "Normal", "cor": "#2e7d32"},
    "alerta": {"texto": "Alerta", "cor": "#c62828"},
}

# ---------------------------------------------------------------
# ARMAZENAMENTO DOS DADOS
# ---------------------------------------------------------------
# Uma deque por sensor. Cada item é (horário, valor).
DADOS = {sensor["id"]: deque(maxlen=MAX_PONTOS) for sensor in SENSORES}
TRAVA = threading.Lock()


def simular_mq135():
    """DEMONSTRAÇÃO: gera leituras falsas do MQ-135 (uma onda lenta com ruído)."""
    inicio = time.time()
    while True:
        t = time.time() - inicio
        valor = 350 + 60 * math.sin(t / 15) + random.gauss(0, 8)
        with TRAVA:
            DADOS["mq135"].append((datetime.now(), valor))
        time.sleep(INTERVALO_S)


if MODO_SIMULADO:
    threading.Thread(target=simular_mq135, daemon=True).start()


def ultimo_valor(sensor_id):
    """Devolve o valor mais recente do sensor, ou None se não há dados."""
    with TRAVA:
        dados = DADOS[sensor_id]
        return dados[-1][1] if dados else None


def copiar_dados(sensor_id):
    """Devolve duas listas (horários, valores) copiadas com segurança."""
    with TRAVA:
        pontos = list(DADOS[sensor_id])
    return [p[0] for p in pontos], [p[1] for p in pontos]


def estado_do_sensor(sensor):
    """Decide o estado do cartão. Por enquanto: sem dados, simulado ou normal."""
    if ultimo_valor(sensor["id"]) is None:
        return "sem_dados           "
    return "simulado" if MODO_SIMULADO else "normal"


def texto_valor(sensor):
    """Texto grande do cartão, como '352 ' ou '-- °C'."""
    valor = ultimo_valor(sensor["id"])
    if valor is None:
        return f"-- {sensor['unidade']}"
    return f"{valor:.{sensor['casas']}f} {sensor['unidade']}"


# ---------------------------------------------------------------
# COMPONENTES VISUAIS
# ---------------------------------------------------------------
def estilo_cartao(cor, aberto):
    """Estilo visual do cartão. Se o gráfico dele está aberto, ele ganha destaque."""
    estilo = {
        "border": "1px solid #ccc",
        "borderLeft": f"6px solid {cor}",z
        "borderRadius": "8px",
        "padding": "16px",
        "width": "220px",
        "cursor": "pointer",
        "backgroundColor": "white",
    }
    if aberto:
        estilo["border"] = "2px solid #1565c0"
        estilo["borderLeft"] = f"6px solid {cor}"
        estilo["backgroundColor"] = "#e3f2fd"
    return estilo


def estilo_estado(cor):
    return {"color": cor, "fontWeight": "bold", "marginTop": "8px"}


def criar_cartao(sensor):
    """Recebe a ficha de um sensor e devolve o cartão visual (clicável)."""
    estado = estado_do_sensor(sensor)
    info = ESTADOS[estado]
    return html.Div(
        [
            html.H3(sensor["nome"], style={"margin": "0"}),
            html.P(sensor["descricao"], style={"color": "#666", "fontSize": "13px", "margin": "4px 0 12px 0"}),
            html.Div(
                texto_valor(sensor),
                id={"type": "valor", "id": sensor["id"]},
                style={"fontSize": "28px", "fontWeight": "bold"},
            ),
            html.Div(
                info["texto"],
                id={"type": "estado", "id": sensor["id"]},
                style=estilo_estado(info["cor"]),
            ),
        ],
        id={"type": "cartao", "id": sensor["id"]},
        n_clicks=0,
        style=estilo_cartao(info["cor"], aberto=False),
    )


def criar_figura(sensor):
    """Monta o gráfico do sensor com os dados que existirem agora."""
    x, y = copiar_dados(sensor["id"])
    tem_dados = len(x) > 0

    titulo = sensor["nome"]
    if tem_dados and MODO_SIMULADO:
        titulo += " (DEMONSTRAÇÃO)"

    fig = go.Figure()
    if tem_dados:
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=sensor["nome"]))

    fig.update_layout(
        title=titulo,
        xaxis_title="Horário",
        yaxis_title=sensor["unidade"],
        template="plotly_white",
        height=350,
        uirevision=sensor["id"],  # mantém o zoom do usuário entre as atualizações
    )
    if not tem_dados:
        fig.add_annotation(
            text="Sem dados", x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=20, color="#9e9e9e"),
        )
    return fig


def criar_painel(sensor):
    """Painel de um sensor aberto: botão de fechar + gráfico."""
    return html.Div(
        [
            html.Div(
                html.Button(
                    "✕",
                    id={"type": "fechar", "id": sensor["id"]},
                    n_clicks=0,
                    title="Fechar gráfico",
                    style={"border": "none", "background": "transparent",
                           "fontSize": "18px", "cursor": "pointer", "color": "#666"},
                ),
                style={"textAlign": "right"},
            ),
            dcc.Graph(id={"type": "grafico", "id": sensor["id"]}, figure=criar_figura(sensor)),
        ],
        style={
            "flex": "1 1 420px",
            "minWidth": "320px",
            "border": "1px solid #ddd",
            "borderRadius": "8px",
            "padding": "8px",
        },
    )


# ---------------------------------------------------------------
# PÁGINA
# ---------------------------------------------------------------
app = Dash(__name__)

aviso_simulado = html.Div(
    "MODO DEMONSTRAÇÃO: os dados do MQ-135 são simulados e NÃO são leituras reais.",
    style={"backgroundColor": "#fff3e0", "color": "#e65100", "border": "1px solid #ef6c00",
           "borderRadius": "8px", "padding": "10px", "fontWeight": "bold", "marginBottom": "16px"},
) if MODO_SIMULADO else None

app.layout = html.Div(
    [
        html.H1("MCI", style={"marginBottom": "0"}),
        html.P("Monitoramento de Condições Individuais", style={"color": "#666", "marginTop": "4px"}),
        aviso_simulado,
        html.Div(
            [criar_cartao(sensor) for sensor in SENSORES],
            style={"display": "flex", "flexWrap": "wrap", "gap": "16px"},
        ),
        html.Div(id="painel-detalhe", style={"marginTop": "24px"}),
        dcc.Store(id="sensores-abertos", data=[]),
        dcc.Interval(id="relogio", interval=1000, n_intervals=0),  # dispara a cada 1 s
    ],
    style={"fontFamily": "Arial", "padding": "24px"},
)


# ---------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------
@app.callback(
    Output("sensores-abertos", "data"),
    Input({"type": "cartao", "id": ALL}, "n_clicks"),
    Input({"type": "fechar", "id": ALL}, "n_clicks"),
    State("sensores-abertos", "data"),
    prevent_initial_call=True,
)
def alternar_sensor(cliques_cartao, cliques_fechar, abertos):
    """Clicou no cartão ou no ✕: abre se estava fechado, fecha se estava aberto."""
    clicado = ctx.triggered_id
    if clicado is None:
        raise PreventUpdate
    if not ctx.triggered[0]["value"]:  # botão recém-criado, não é clique de verdade
        raise PreventUpdate

    abertos = list(abertos or [])
    if clicado["id"] in abertos:
        abertos.remove(clicado["id"])
    else:
        abertos.append(clicado["id"])
    return abertos


@app.callback(
    Output("painel-detalhe", "children"),
    Input("sensores-abertos", "data"),
)
def desenhar_paineis(abertos):
    """Mostra um painel para cada sensor que está na lista de abertos."""
    if not abertos:
        return html.P("Clique em um cartão para ver o gráfico.", style={"color": "#666"})
    paineis = [criar_painel(sensor) for sensor in SENSORES if sensor["id"] in abertos]
    return html.Div(paineis, style={"display": "flex", "flexWrap": "wrap", "gap": "16px"})


@app.callback(
    Output({"type": "valor", "id": ALL}, "children"),
    Output({"type": "estado", "id": ALL}, "children"),
    Output({"type": "estado", "id": ALL}, "style"),
    Output({"type": "cartao", "id": ALL}, "style"),
    Input("relogio", "n_intervals"),
    Input("sensores-abertos", "data"),
)
def atualizar_cartoes(n, abertos):
    """A cada segundo (ou quando abre/fecha um gráfico), atualiza todos os cartões."""
    abertos = abertos or []
    valores, textos, estilos_estado, estilos_cartao = [], [], [], []
    for sensor in SENSORES:
        info = ESTADOS[estado_do_sensor(sensor)]
        valores.append(texto_valor(sensor))
        textos.append(info["texto"])
        estilos_estado.append(estilo_estado(info["cor"]))
        estilos_cartao.append(estilo_cartao(info["cor"], sensor["id"] in abertos))
    return valores, textos, estilos_estado, estilos_cartao


@app.callback(
    Output({"type": "grafico", "id": ALL}, "figure"),
    Input("relogio", "n_intervals"),
)
def atualizar_graficos(n):
    """A cada segundo, redesenha os gráficos que estiverem abertos."""
    ids_abertos = [saida["id"]["id"] for saida in ctx.outputs_list]
    return [criar_figura(next(s for s in SENSORES if s["id"] == i)) for i in ids_abertos]


if __name__ == "__main__":
    app.run(debug=True)