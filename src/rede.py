"""
rede.py - Comunicação UDP: recepção, HELLO, expiração de enlaces, LSA local e DATA.

Threads iniciadas por iniciar_threads():
  loop_recepcao : bloqueia em recvfrom() e trata @HELLO, #LSA e !DATA
  loop_hello    : a cada 5s envia HELLO aos vizinhos não desativados
  loop_monitor  : a cada 1s derruba enlaces sem HELLO há > 15s
  loop_lsa      : gera o LSA local a cada 10s OU quando acordada por evento_lsa

Quem muda a topologia local (recepção, monitor, CLI) NÃO gera LSA diretamente:
apenas chama estado.solicitar_lsa(), que acorda loop_lsa. Assim só uma thread
incrementa o seq.txt e envia LSAs.
"""

import socket
import threading

import estado
import parte2
from estado import registrar_log

BUFFER_RECV = 4096   # maior que 1200 para conseguir DETECTAR pacotes grandes


def _seguro(funcao, *args):
    """Uma exceção em um tratamento vira log; a thread continua viva."""
    try:
        funcao(*args)
    except Exception as erro:  # noqa: BLE001
        registrar_log("ERRO", funcao.__name__, repr(erro))


def descartar(motivo, origem, conteudo):
    registrar_log("DESCARTE", origem, f"{motivo}: {conteudo[:100]!r}")


# ==========================================================================
# RECEPÇÃO
# ==========================================================================
def loop_recepcao():
    while not estado.encerrar.is_set():
        try:
            dados, (ip_remetente, _porta) = estado.sock.recvfrom(BUFFER_RECV)
        except socket.timeout:
            continue
        except ConnectionResetError:   # Windows: ICMP "porta inacessível" após sendto
            continue
        except OSError as erro:
            if estado.encerrar.is_set():
                break
            registrar_log("ERRO_RX", "-", str(erro))
            continue
        _seguro(tratar_pacote, dados, ip_remetente)


def tratar_pacote(dados, ip_remetente):
    """Validação comum (tamanho, UTF-8, sem quebra de linha) e despacho por tipo."""
    if len(dados) > estado.TAM_MAX_PACOTE:
        return descartar(f"pacote maior que {estado.TAM_MAX_PACOTE} bytes ({len(dados)})",
                         ip_remetente, dados)
    try:
        msg = dados.decode("utf-8")
    except UnicodeDecodeError:
        return descartar("não é UTF-8", ip_remetente, dados)
    if not msg or "\n" in msg or "\r" in msg:
        return descartar("vazio ou com quebra de linha", ip_remetente, msg)

    tipo = msg[0]
    if tipo == "@":
        tratar_hello(msg, ip_remetente)
    elif tipo == "#":
        tratar_lsa(msg, ip_remetente)
    elif tipo == "!":
        tratar_data(msg, ip_remetente)
    else:
        descartar("tipo desconhecido", ip_remetente, msg)


# ---------------------------------------------------------------- HELLO ----
def tratar_hello(msg, ip_remetente):
    """@IP_ORIGEM*VIZINHO1*VIZINHO2"""
    partes = msg[1:].split("*")
    if not all(estado.ip_valido(p) for p in partes):
        return descartar("HELLO malformado", ip_remetente, msg)

    ip_origem, lista = partes[0], partes[1:]
    resultado = estado.processar_hello(ip_origem, lista)

    if resultado == "DESCONHECIDO":
        descartar("HELLO de roteador não configurado", ip_origem, msg)
    elif resultado == "DESATIVADO":
        descartar("HELLO de vizinho desativado manualmente", ip_origem, msg)
    elif resultado == "UNILATERAL":
        registrar_log("HELLO_RX", ip_origem, "ignorado: ele não me tem na lista dele (enlace segue INATIVO)")
    elif resultado == "UNILATERAL_CAIU":
        registrar_log("HELLO_RX", ip_origem, "ele removeu meu IP da lista")
        registrar_log("ENLACE_INATIVO", ip_origem, "enlace deixou de ser bidirecional")
        estado.solicitar_lsa()
    elif resultado == "SUBIU":
        registrar_log("HELLO_RX", ip_origem, "HELLO válido")
        registrar_log("ENLACE_ATIVO", ip_origem, "enlace subiu")
        estado.solicitar_lsa()
    else:  # OK
        registrar_log("HELLO_RX", ip_origem, "HELLO válido")


# ------------------------------------------------------------------ LSA ----
def validar_lsa(msg):
    """#IP_ORIGEM;SEQ*VIZ1;CUSTO*VIZ2;CUSTO  (vizinhos opcionais)."""
    partes = msg[1:].split("*")
    cabecalho = partes[0].split(";")
    if (len(cabecalho) != 2 or not estado.ip_valido(cabecalho[0])
            or estado.inteiro_em_faixa(cabecalho[1], 1, estado.SEQ_MAX) is None):
        return False
    for par in partes[1:]:
        viz = par.split(";")
        if (len(viz) != 2 or not estado.ip_valido(viz[0])
                or estado.inteiro_em_faixa(viz[1], estado.CUSTO_MIN, estado.CUSTO_MAX) is None):
            return False
    return True


def tratar_lsa(msg, ip_remetente):
    if not validar_lsa(msg):
        return descartar("LSA malformado", ip_remetente, msg)
    if estado.vizinho_desativado(ip_remetente):
        return descartar("LSA de vizinho desativado manualmente", ip_remetente, msg)
    registrar_log("LSA_RX", ip_remetente, msg)
    parte2.processar_lsa_recebido(msg, ip_remetente)     # <- Parte 2


# ----------------------------------------------------------------- DATA ----
def validar_data(msg):
    """!ORIGEM;DESTINO;ID;TTL;MENSAGEM -> (origem, destino, id_txt, ttl, texto) ou None.
    split(';', 4) garante que ';' dentro da MENSAGEM não quebra o pacote."""
    partes = msg[1:].split(";", 4)
    if len(partes) != 5:
        return None
    origem, destino, id_txt, ttl_txt, texto = partes
    ttl = estado.inteiro_em_faixa(ttl_txt, 0, estado.TTL_MAX)
    if (not estado.ip_valido(origem) or not estado.ip_valido(destino)
            or estado.inteiro_em_faixa(id_txt, 0, estado.ID_MAX) is None
            or ttl is None or texto == ""):
        return None
    return origem, destino, id_txt, ttl, texto


def tratar_data(msg, ip_remetente):
    dados = validar_data(msg)
    if dados is None:
        return descartar("DATA malformado", ip_remetente, msg)
    origem, destino, ident, ttl, texto = dados

    if destino == estado.meu_ip:
        registrar_log("DATA_RECEBIDA", origem, f"id={ident} ttl={ttl} mensagem: {texto}")
        return
    if ttl == 0:
        return registrar_log("DATA_DESCARTADA", origem, f"id={ident}: TTL zerado (destino {destino})")
    rota = estado.obter_rota(destino)
    if rota is None:
        return registrar_log("DATA_DESCARTADA", origem, f"id={ident}: sem rota para {destino}")

    novo = f"!{origem};{destino};{ident};{ttl - 1};{texto}"
    estado.enviar_pacote(novo, rota["proximo_salto"], "DATA_ENCAMINHADA")


def enviar_data(destino, ttl, texto):
    """Chamada pela CLI (origem do pacote: TTL vai como informado, sem decrementar).
    Retorna (ok, motivo)."""
    if destino == estado.meu_ip:
        return False, "o destino é este próprio roteador"
    rota = estado.obter_rota(destino)
    if rota is None:
        registrar_log("DATA_DESCARTADA", destino, "sem rota para o destino (envio local)")
        return False, "sem rota para o destino"
    ident = estado.proximo_id()
    msg = f"!{estado.meu_ip};{destino};{ident};{ttl};{texto}"
    if estado.enviar_pacote(msg, rota["proximo_salto"], "DATA_ENVIADA"):
        return True, ""
    return False, "falha no envio (veja o log)"


# ==========================================================================
# HELLO (envio periódico)
# ==========================================================================
def enviar_hellos():
    destinos, configurados = estado.dados_para_hello()
    msg = "@" + "*".join([estado.meu_ip] + configurados)
    for ip in destinos:
        estado.enviar_pacote(msg, ip)


def loop_hello():
    while not estado.encerrar.is_set():
        _seguro(enviar_hellos)
        estado.encerrar.wait(estado.INTERVALO_HELLO)   # sleep interrompível
  

# ==========================================================================
# MONITOR DE ENLACES (timeout de 15s)
# ==========================================================================
def verificar_expiracao():
    for ip in estado.expirar_hellos():
        registrar_log("ENLACE_INATIVO", ip, f"sem HELLO válido há mais de {estado.TIMEOUT_HELLO}s")
        estado.solicitar_lsa()


def loop_monitor():
    while not estado.encerrar.is_set():
        _seguro(verificar_expiracao)
        estado.encerrar.wait(1)


# ==========================================================================
# LSA LOCAL
# ==========================================================================
def gerar_e_enviar_lsa():
    """#IP;SEQ*VIZ;CUSTO... só com vizinhos ATIVOS."""
    ativos = estado.vizinhos_ativos()
    seq = estado.proximo_seq()            # grava seq.txt em disco ANTES de qualquer envio
    msg = f"#{estado.meu_ip};{seq}" + "".join(f"*{ip};{custo}" for ip, custo in ativos.items())

    registrar_log("LSA_LOCAL", estado.meu_ip, f"gerado seq={seq} com {len(ativos)} vizinho(s) ativo(s)")
    parte2.registrar_lsa_local(msg)       # <- Parte 2 guarda o LSA local na LSDB
    for ip in ativos:
        estado.enviar_pacote(msg, ip)


def loop_lsa():
    while not estado.encerrar.is_set():
        estado.evento_lsa.clear()         # limpa ANTES de gerar: gatilhos novos não se perdem
        _seguro(gerar_e_enviar_lsa)
        estado.evento_lsa.wait(estado.INTERVALO_LSA)   # acorda por timeout (10s) ou por evento


# ==========================================================================
def iniciar_threads():
    for alvo in (loop_recepcao, loop_hello, loop_monitor, loop_lsa):
        threading.Thread(target=alvo, name=alvo.__name__, daemon=True).start()