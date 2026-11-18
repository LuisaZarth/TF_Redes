"""
integracao.py - PONTO DE ENCAIXE com flooding, expiração, Dijkstra.

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
    estado.substituir_tabela_rotas(
        {destino: {"proximo_salto": salto, "custo": custo}
         for destino, (custo, salto) in tabela.items()})

def iniciar():
    """Chamada uma vez pelo main.py, antes da CLI: cria a LSDB e liga a
    thread de expiração de LSAs (30 s)."""
    global _lsdb
    _lsdb = LSDB(estado.meu_ip, _vizinhos_ativos, _enviar, _log,
                 publicar_rotas=_publicar_rotas)

    def laco_expiracao():
        # Usa o Event para encerrar junto com o resto do programa.
        while not estado.encerrar.wait(1):
            try:
                _lsdb.verificar_expiracao()
            except Exception as erro:  # noqa: BLE001 - a thread não pode morrer
                estado.registrar_log("ERRO", "expiracao", repr(erro))

    threading.Thread(target=laco_expiracao, name="loop_expiracao",
                     daemon=True).start()
    estado.registrar_log("LSDB", estado.meu_ip, "LSDB iniciada")


def registrar_lsa_local(msg):
    """Chamada pela thread de LSA toda vez que o LSA LOCAL é gerado,
    ANTES de enviá-lo aos vizinhos."""
    global _ativos_anteriores
    _lsdb.lsa_local_gerado(msg)

    ativos = set(estado.vizinhos_ativos())
    novos = ativos - _ativos_anteriores
    _ativos_anteriores = ativos
    for ip in sorted(novos):
        _lsdb.vizinho_estabelecido(ip)

def processar_lsa_recebido(msg, ip_remetente):
    """Chamada pela thread de recepção para cada LSA recebido."""
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
