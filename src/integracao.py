"""
integracao.py - PONTO DE ENCAIXE com a Parte 2 (LSDB, flooding, expiração, Dijkstra).

Liga a classe LSDB (lsdb.py) às funções da Parte 1, mantendo os nomes e
assinaturas que a Parte 1 já chama.

Este módulo importa somente `estado` e `lsdb` (nunca `rede`/`terminal`) para
não criar import circular.

O que a Parte 2 usa da Parte 1:
  estado.meu_ip                       IP deste roteador
  estado.vizinhos_ativos()            {ip: custo} dos enlaces ATIVOS (flooding)
  estado.enviar_pacote(msg, ip)       envia UDP (já valida tamanho/quebra de linha)
  estado.substituir_tabela_rotas(t)   t = {destino: {"proximo_salto": ip, "custo": n}}
  estado.registrar_log(tipo, info, descricao)
  estado.encerrar                     Event: termine suas threads quando estiver setado
"""

import threading

import estado
from lsdb import LSDB

_lsdb = None

# Vizinhos ativos na última geração do LSA local. Serve para detectar
# vizinhança NOVA e sincronizar a LSDB com ela (ver registrar_lsa_local).
_ativos_anteriores = set()


# --------------------------------------------------------------------------
# Adaptadores: a LSDB espera estas assinaturas
# --------------------------------------------------------------------------
def _vizinhos_ativos():
    return list(estado.vizinhos_ativos())          # só os IPs


def _enviar(ip, msg):
    estado.enviar_pacote(msg, ip)                  # ordem dos argumentos invertida


def _log(tipo, origem=None, descricao=None):
    estado.registrar_log(tipo, origem or "-", descricao or "")


def _publicar_rotas(tabela):
    """{destino: (custo, salto)} -> formato que a Parte 1 usa no DATA."""
    estado.substituir_tabela_rotas(
        {destino: {"proximo_salto": salto, "custo": custo}
         for destino, (custo, salto) in tabela.items()})


# --------------------------------------------------------------------------
# Funções chamadas pela Parte 1 (nomes e assinaturas mantidos)
# --------------------------------------------------------------------------
def iniciar():
    """Chamada uma vez pelo main.py, antes da CLI: cria a LSDB e liga a
    thread de expiração de LSAs (30 s)."""
    global _lsdb
    _lsdb = LSDB(estado.meu_ip, _vizinhos_ativos, _enviar, _log,
                 publicar_rotas=_publicar_rotas)

    def laco_expiracao():
        # Usa o Event da Parte 1 para encerrar junto com o resto do programa.
        while not estado.encerrar.wait(1):
            try:
                _lsdb.verificar_expiracao()
            except Exception as erro:  # noqa: BLE001 - a thread não pode morrer
                estado.registrar_log("ERRO", "expiracao", repr(erro))

    threading.Thread(target=laco_expiracao, name="loop_expiracao",
                     daemon=True).start()
    estado.registrar_log("PARTE2", estado.meu_ip, "LSDB iniciada")


def registrar_lsa_local(msg):
    """Chamada pela thread de LSA (Parte 1) toda vez que o LSA LOCAL é gerado,
    ANTES de enviá-lo aos vizinhos.

    Também é aqui que detectamos vizinhança nova: quando um enlace sobe, a
    Parte 1 pede um LSA imediato, e o vizinho aparece pela primeira vez em
    vizinhos_ativos(). Para cada vizinho novo, enviamos a LSDB inteira."""
    global _ativos_anteriores
    _lsdb.lsa_local_gerado(msg)

    ativos = set(estado.vizinhos_ativos())
    novos = ativos - _ativos_anteriores
    _ativos_anteriores = ativos
    for ip in sorted(novos):
        _lsdb.vizinho_estabelecido(ip)


def processar_lsa_recebido(msg, ip_remetente):
    """Chamada pela thread de recepção (Parte 1) para cada LSA recebido.
    A Parte 1 já validou o formato e descartou vizinho desativado; a LSDB
    confere vizinho ativo, sequência, LSA próprio, faz flooding e recalcula."""
    _lsdb.lsa_recebido(msg, ip_remetente)


def reenviar_lsa(origem, vizinho, modo):
    """Comando de teste do cenário F: modo 'dup' ou 'antigo'."""
    return _lsdb.reenviar_lsa(origem, vizinho, modo)


def mostrar_lsdb():
    """Comando 'lsdb' da CLI."""
    _lsdb.mostrar()


def mostrar_rotas():
    """Comando 'rotas' da CLI."""
    _lsdb.mostrar_rotas()
