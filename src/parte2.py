"""
parte2.py - PONTO DE ENCAIXE com a Parte 2 (LSDB, flooding, expiração, Dijkstra).

Hoje contém apenas STUBS. A dupla substitui o corpo das funções abaixo,
SEM mudar os nomes/assinaturas, e o resto do programa continua funcionando.

Este módulo deve importar somente `estado` (nunca `rede`/`cli`) para não
criar import circular. Para enviar pacotes use estado.enviar_pacote().

O que a Parte 2 pode usar da Parte 1:
  estado.meu_ip                       IP deste roteador
  estado.vizinhos_ativos()            {ip: custo} dos enlaces ATIVOS (flooding)
  estado.enviar_pacote(msg, ip)       envia UDP (já valida tamanho/quebra de linha)
  estado.substituir_tabela_rotas(t)   t = {destino: {"proximo_salto": ip, "custo": n}}
  estado.registrar_log(tipo, info, descricao)
  estado.encerrar                     Event: termine suas threads quando estiver setado
"""

import estado


def iniciar():
    """Chamada uma vez pelo main.py, antes da CLI. Inicie aqui as threads da Parte 2
    (ex.: expiração de LSAs de 30s). Use daemon=True e cheque estado.encerrar."""
    estado.registrar_log("PARTE2", "-", "stub: iniciar() ainda não implementado")


def registrar_lsa_local(msg):
    """Chamada pela thread de LSA (Parte 1) toda vez que o LSA LOCAL é gerado,
    ANTES de enviá-lo aos vizinhos. `msg` já é o pacote completo, ex.:
    '#10.0.0.1;7*10.0.0.2;10'. Guarde-o na LSDB (o próprio roteador também
    faz parte do grafo) e, se quiser, recalcule o Dijkstra."""
    estado.registrar_log("PARTE2", "-", f"stub: registrar_lsa_local({msg})")


def processar_lsa_recebido(msg, ip_remetente):
    """Chamada pela thread de recepção (Parte 1) para cada LSA recebido.
    Garantias da Parte 1: formato validado e remetente NÃO está desativado.
    `ip_remetente` é o IP UDP de quem enviou (o vizinho que repassou).
    A Parte 2 deve: comparar sequência, atualizar LSDB, fazer flooding aos
    vizinhos ativos (exceto o remetente) e recalcular rotas.
    Atenção: roda na thread de recepção; não bloqueie por muito tempo."""
    estado.registrar_log("PARTE2", ip_remetente, "stub: processar_lsa_recebido não implementado")


def mostrar_lsdb():
    """Comando 'lsdb' da CLI: imprimir a LSDB."""
    print("[stub] lsdb: Parte 2 ainda não implementada")


def mostrar_rotas():
    """Comando 'rotas' da CLI: imprimir a tabela de rotas."""
    print("[stub] rotas: Parte 2 ainda não implementada")