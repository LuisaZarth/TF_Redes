"""
estado.py - Estado compartilhado do roteador.

É o ÚNICO módulo que a Parte 2 precisa importar para conversar com a Parte 1:
  - estruturas protegidas por Lock (vizinhos, tabela_rotas)
  - registrar_log(), enviar_pacote(), vizinhos_ativos(), substituir_tabela_rotas()

Regras de lock (para evitar deadlock):
  - cada função pega UM lock, faz algo curto e solta;
  - lock_log é "folha": pode ser pego por dentro de outro lock,
    mas quem o segura nunca pega outro lock.
"""

import ipaddress
import os
import socket
import threading
import time

# --------------------------------------------------------------------------
# Constantes do protocolo
# --------------------------------------------------------------------------
PORTA = 6000
TAM_MAX_PACOTE = 1200          # bytes
INTERVALO_HELLO = 5            # s
INTERVALO_LSA = 10             # s
TIMEOUT_HELLO = 15             # s sem HELLO válido -> enlace INATIVO
TTL_PADRAO = 16
TTL_MAX = 255
CUSTO_MIN = 1
CUSTO_MAX = 65535
SEQ_MAX = 2147483647
ID_MAX = 2**63 - 1

ARQ_ROTEADORES = "roteadores.txt"
ARQ_SEQ = "seq.txt"
ARQ_LOG = "roteador.log"

# --------------------------------------------------------------------------
# Estado global (acessar sempre como estado.xxx, nunca "from estado import xxx",
# senão você captura uma cópia do valor antigo, ex.: meu_ip = None)
# --------------------------------------------------------------------------
meu_ip = None
sock = None

encerrar = threading.Event()     # sinaliza fim do programa para todas as threads
evento_lsa = threading.Event()   # "gere um LSA agora" (acorda a thread de LSA)

# Vizinhos configurados: ip -> {"custo", "desativado", "ativo", "ultimo_hello"}
#   desativado : o usuário rodou 'desativar' (não envia/aceita nada dele)
#   ativo      : enlace bidirecional confirmado por HELLO válido recente
lock_vizinhos = threading.Lock()
vizinhos = {}

# Tabela de rotas (preenchida pela Parte 2):
#   destino -> {"proximo_salto": ip, "custo": int}
lock_rotas = threading.Lock()
tabela_rotas = {}

lock_seq = threading.Lock()
_ultimo_seq = None

lock_id = threading.Lock()
_ultimo_id = 0

lock_log = threading.Lock()


# --------------------------------------------------------------------------
# Log: "TIPO_EVENTO | ORIGEM_OU_INFO | DESCRICAO" (tela + arquivo)
# --------------------------------------------------------------------------
def registrar_log(tipo, info, descricao):
    linha = f"{tipo} | {info} | {descricao}"
    with lock_log:
        print(linha, flush=True)
        try:
            with open(ARQ_LOG, "a", encoding="utf-8") as arq:
                arq.write(linha + "\n")
        except OSError:
            pass  # falha de log nunca pode derrubar o roteador


# --------------------------------------------------------------------------
# Validações reutilizáveis
# --------------------------------------------------------------------------
def ip_valido(texto):
    try:
        ipaddress.IPv4Address(texto)
        return True
    except ValueError:
        return False


def inteiro_em_faixa(texto, minimo, maximo):
    """Converte texto -> int se for só dígitos ASCII e estiver na faixa; senão None.
    Atenção: 0 é um retorno válido, compare sempre com 'is None'."""
    if not (texto.isascii() and texto.isdigit()):
        return None
    valor = int(texto)
    return valor if minimo <= valor <= maximo else None


def _gravar_atomico(caminho, conteudo):
    """Grava em arquivo temporário, força ir ao disco e troca o original."""
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as arq:
        arq.write(conteudo)
        arq.flush()
        os.fsync(arq.fileno())
    os.replace(tmp, caminho)


# --------------------------------------------------------------------------
# Inicialização
# --------------------------------------------------------------------------
def inicializar(ip_proprio):
    global meu_ip
    meu_ip = ip_proprio
    carregar_roteadores()


def abrir_socket():
    """UDP em 0.0.0.0:6000. Timeout de 1s para a thread de recepção
    conseguir checar 'encerrar' periodicamente."""
    global sock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", PORTA))
    s.settimeout(1.0)
    sock = s


# --------------------------------------------------------------------------
# roteadores.txt  (formato IP;CUSTO)
# --------------------------------------------------------------------------
def _novo_vizinho(custo):
    return {"custo": custo, "desativado": False, "ativo": False, "ultimo_hello": 0.0}


def carregar_roteadores():
    lidos = {}
    if not os.path.exists(ARQ_ROTEADORES):
        registrar_log("ARQUIVO", ARQ_ROTEADORES, "não encontrado; iniciando sem vizinhos")
        return
    with open(ARQ_ROTEADORES, encoding="utf-8") as arq:
        for num, linha in enumerate(arq, 1):
            linha = linha.strip()
            if not linha:
                continue
            partes = [p.strip() for p in linha.split(";")]
            custo = inteiro_em_faixa(partes[1], CUSTO_MIN, CUSTO_MAX) if len(partes) == 2 else None
            if len(partes) != 2 or not ip_valido(partes[0]) or custo is None:
                registrar_log("ARQUIVO", ARQ_ROTEADORES, f"linha {num} inválida ignorada: {linha!r}")
            elif partes[0] == meu_ip:
                registrar_log("ARQUIVO", ARQ_ROTEADORES, f"linha {num}: próprio IP ignorado")
            elif partes[0] in lidos:
                registrar_log("ARQUIVO", ARQ_ROTEADORES, f"linha {num}: IP duplicado ignorado")
            else:
                lidos[partes[0]] = custo
    with lock_vizinhos:
        for ip, custo in lidos.items():
            vizinhos[ip] = _novo_vizinho(custo)
    registrar_log("ARQUIVO", ARQ_ROTEADORES, f"{len(lidos)} vizinho(s) carregado(s)")


def _salvar_roteadores_locked():
    """Reescreve roteadores.txt. Chamar com lock_vizinhos já preso. Pode lançar OSError."""
    linhas = [f"{ip};{viz['custo']}" for ip, viz in vizinhos.items()]
    _gravar_atomico(ARQ_ROTEADORES, "".join(l + "\n" for l in linhas))


# --------------------------------------------------------------------------
# Operações sobre vizinhos (todas atômicas, sob lock_vizinhos)
# --------------------------------------------------------------------------
def snapshot_vizinhos():
    """Cópia segura (pode iterar sem lock)."""
    with lock_vizinhos:
        return {ip: dict(viz) for ip, viz in vizinhos.items()}


def vizinhos_ativos():
    """{ip: custo} dos enlaces ATIVOS. Usado no LSA e pela Parte 2 (flooding)."""
    with lock_vizinhos:
        return {ip: viz["custo"] for ip, viz in vizinhos.items() if viz["ativo"]}


def vizinho_desativado(ip):
    with lock_vizinhos:
        viz = vizinhos.get(ip)
        return bool(viz and viz["desativado"])


def dados_para_hello():
    """(destinos que recebem HELLO, lista de todos os configurados para o corpo do HELLO)."""
    with lock_vizinhos:
        destinos = [ip for ip, viz in vizinhos.items() if not viz["desativado"]]
        return destinos, list(vizinhos)


def processar_hello(ip_origem, lista_origem):
    """Aplica um HELLO já com formato válido. Retorna o que aconteceu:
    DESCONHECIDO | DESATIVADO | UNILATERAL | UNILATERAL_CAIU | SUBIU | OK"""
    with lock_vizinhos:
        viz = vizinhos.get(ip_origem)
        if viz is None:
            return "DESCONHECIDO"
        if viz["desativado"]:
            return "DESATIVADO"
        if meu_ip not in lista_origem:          # ele não me tem configurado
            if viz["ativo"]:
                viz["ativo"] = False
                return "UNILATERAL_CAIU"
            return "UNILATERAL"
        viz["ultimo_hello"] = time.monotonic()
        if viz["ativo"]:
            return "OK"
        viz["ativo"] = True
        return "SUBIU"


def expirar_hellos():
    """Marca INATIVO quem passou de TIMEOUT_HELLO sem HELLO. Retorna os IPs que caíram."""
    agora = time.monotonic()
    caidos = []
    with lock_vizinhos:
        for ip, viz in vizinhos.items():
            if viz["ativo"] and agora - viz["ultimo_hello"] > TIMEOUT_HELLO:
                viz["ativo"] = False
                caidos.append(ip)
    return caidos


def desativar_vizinho(ip):
    """Retorna (existe, estava_ativo)."""
    with lock_vizinhos:
        viz = vizinhos.get(ip)
        if viz is None:
            return False, False
        estava_ativo = viz["ativo"]
        viz["desativado"] = True
        viz["ativo"] = False
        return True, estava_ativo


def ativar_vizinho(ip):
    """Só reabilita o envio de HELLO; o enlace volta a ATIVO apenas com novo HELLO válido."""
    with lock_vizinhos:
        viz = vizinhos.get(ip)
        if viz is None:
            return False
        viz["desativado"] = False
        return True


def alterar_custo(ip, custo):
    """Retorna (existe, esta_ativo)."""
    with lock_vizinhos:
        viz = vizinhos.get(ip)
        if viz is None:
            return False, False
        viz["custo"] = custo
        return True, viz["ativo"]


def adicionar_vizinho(ip, custo):
    """Retorna (ok, motivo). Reescreve roteadores.txt (pode lançar OSError)."""
    with lock_vizinhos:
        if ip == meu_ip:
            return False, "não pode ser o próprio IP"
        if ip in vizinhos:
            return False, "já configurado (use 'custo' para alterar)"
        vizinhos[ip] = _novo_vizinho(custo)
        _salvar_roteadores_locked()
        return True, ""


def remover_vizinho(ip):
    """Retorna (existe, estava_ativo). Reescreve roteadores.txt (pode lançar OSError)."""
    with lock_vizinhos:
        viz = vizinhos.pop(ip, None)
        if viz is None:
            return False, False
        _salvar_roteadores_locked()
        return True, viz["ativo"]


def solicitar_lsa():
    """Pede à thread de LSA para gerar um LSA imediatamente."""
    evento_lsa.set()


# --------------------------------------------------------------------------
# Tabela de rotas (escrita pela Parte 2, lida pela Parte 1 no encaminhamento)
# --------------------------------------------------------------------------
def obter_rota(destino):
    """{"proximo_salto": ip, "custo": n} ou None se não houver rota."""
    with lock_rotas:
        rota = tabela_rotas.get(destino)
        return dict(rota) if rota else None


def substituir_tabela_rotas(nova_tabela):
    """Para a Parte 2: troca a tabela inteira de forma atômica após rodar Dijkstra."""
    with lock_rotas:
        tabela_rotas.clear()
        tabela_rotas.update(nova_tabela)


# --------------------------------------------------------------------------
# seq.txt e ID de DATA
# --------------------------------------------------------------------------
def _ler_seq_do_disco():
    if not os.path.exists(ARQ_SEQ):
        return 0                                   # primeiro LSA será 1
    with open(ARQ_SEQ, encoding="utf-8") as arq:
        valor = inteiro_em_faixa(arq.read().strip(), 1, SEQ_MAX)
    if valor is None:
        registrar_log("ARQUIVO", ARQ_SEQ, "conteúdo inválido; reiniciando em 1")
        return 0
    return valor


def proximo_seq():
    """Incrementa, GRAVA EM DISCO e só então devolve o número.
    Como o envio acontece depois desta função, a regra 'gravar antes de enviar'
    fica garantida. Se a gravação falhar, lança OSError e nada é enviado."""
    global _ultimo_seq
    with lock_seq:
        if _ultimo_seq is None:
            _ultimo_seq = _ler_seq_do_disco()
        novo = _ultimo_seq + 1
        if novo > SEQ_MAX:
            novo = 1   # DECISÃO: ao estourar o limite, volta a 1 (confirmar com o professor)
            registrar_log("SEQ", "-", "limite 2147483647 atingido; voltando para 1")
        _gravar_atomico(ARQ_SEQ, str(novo))
        _ultimo_seq = novo
        return novo


def proximo_id():
    """ID único por origem durante esta execução (contador simples)."""
    global _ultimo_id
    with lock_id:
        _ultimo_id += 1
        return _ultimo_id


# --------------------------------------------------------------------------
# Envio UDP (único ponto de saída: Parte 1 e Parte 2 usam esta função)
# --------------------------------------------------------------------------
_TIPOS_TX = {"@": "HELLO_TX", "#": "LSA_TX", "!": "DATA_TX"}


def enviar_pacote(msg, ip_destino, tipo_log=None):
    """Valida (<=1200 bytes, sem \\n/\\r) e envia. Retorna True/False."""
    dados = msg.encode("utf-8")
    if len(dados) > TAM_MAX_PACOTE or b"\n" in dados or b"\r" in dados:
        registrar_log("ERRO_TX", ip_destino, f"pacote inválido não enviado ({len(dados)} bytes)")
        return False
    try:
        sock.sendto(dados, (ip_destino, PORTA))
    except OSError as erro:
        registrar_log("ERRO_TX", ip_destino, f"falha no sendto: {erro}")
        return False
    registrar_log(tipo_log or _TIPOS_TX.get(msg[:1], "TX"), ip_destino, msg)
    return True