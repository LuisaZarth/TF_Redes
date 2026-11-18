"""
terminal.py - Interface de comandos (roda na thread principal).

"""
import estado
import integracao
import rede
from estado import registrar_log

AJUDA = """Comandos:
  enviar <ip_destino> [ttl] <mensagem>   envia DATA (ttl 0-255, padrão 16)
  desativar <ip>                         para de falar com o vizinho
  ativar <ip>                            volta a enviar HELLO ao vizinho
  custo <ip> <novo_custo>                altera custo (1-65535)
  add <ip> <custo>                       adiciona vizinho e grava roteadores.txt
  remover <ip>                           remove vizinho e grava roteadores.txt
  vizinhos                               mostra vizinhos e situação do enlace
  lsdb                                   mostra a LSDB
  rotas                                  mostra a tabela de rotas
  reenviar <origem> <vizinho> dup|antigo reenvia LSA guardado (teste do cenário F)
  ajuda | sair"""


def _ip_arg(args, qtd, uso):
    """Confere quantidade de argumentos e se o primeiro é um IP válido."""
    if len(args) != qtd or not estado.ip_valido(args[0]):
        print(f"Uso: {uso}")
        return None
    return args[0]


def _ip_e_custo(resto, uso):
    """Para comandos '<ip> <custo>'. Retorna (ip, custo) ou None (já imprimiu o erro)."""
    args = resto.split()
    if len(args) != 2 or not estado.ip_valido(args[0]):
        print(f"Uso: {uso}")
        return None
    custo = estado.inteiro_em_faixa(args[1], estado.CUSTO_MIN, estado.CUSTO_MAX)
    if custo is None:
        print(f"Custo inválido: use inteiro de {estado.CUSTO_MIN} a {estado.CUSTO_MAX}.")
        return None
    return args[0], custo


# ------------------------------------------------------------- comandos ----
def cmd_enviar(resto):
    # 'enviar <ip> [ttl] <mensagem>': se o 1º token após o IP for inteiro E ainda
    # houver texto depois dele, esse token é o TTL; caso contrário faz parte da mensagem.
    partes = resto.split(None, 1)
    if len(partes) < 2 or not estado.ip_valido(partes[0]):
        return print("Uso: enviar <ip_destino> [ttl] <mensagem>")
    destino, texto = partes
    ttl = estado.TTL_PADRAO

    token = texto.split(None, 1)
    if len(token) == 2 and token[0].isascii() and token[0].isdigit():
        ttl = estado.inteiro_em_faixa(token[0], 0, estado.TTL_MAX)
        if ttl is None:
            return print(f"TTL inválido: use 0 a {estado.TTL_MAX}.")
        texto = token[1]

    ok, motivo = rede.enviar_data(destino, ttl, texto)
    if not ok:
        print(f"Não foi possível enviar: {motivo}")


def cmd_desativar(resto):
    ip = _ip_arg(resto.split(), 1, "desativar <ip>")
    if ip is None:
        return
    existe, estava_ativo = estado.desativar_vizinho(ip)
    if not existe:
        return print("Vizinho não configurado.")
    registrar_log("CLI", ip, "vizinho desativado manualmente")
    if estava_ativo:
        registrar_log("ENLACE_INATIVO", ip, "enlace desativado manualmente")
        estado.solicitar_lsa()


def cmd_ativar(resto):
    ip = _ip_arg(resto.split(), 1, "ativar <ip>")
    if ip is None:
        return
    if not estado.ativar_vizinho(ip):
        return print("Vizinho não configurado.")
    registrar_log("CLI", ip, "vizinho reativado; enlace só fica ATIVO após novo HELLO válido")


def cmd_custo(resto):
    lido = _ip_e_custo(resto, "custo <ip> <novo_custo>")
    if lido is None:
        return
    ip, custo = lido
    existe, ativo = estado.alterar_custo(ip, custo)
    if not existe:
        return print("Vizinho não configurado.")
    registrar_log("CLI", ip, f"custo alterado para {custo}")
    if ativo:
        estado.solicitar_lsa()


def cmd_add(resto):
    lido = _ip_e_custo(resto, "add <ip> <custo>")
    if lido is None:
        return
    ip, custo = lido
    try:
        ok, motivo = estado.adicionar_vizinho(ip, custo)
    except OSError as erro:
        return print(f"Vizinho adicionado em memória, mas falhou gravar roteadores.txt: {erro}")
    if ok:
        registrar_log("CLI", ip, f"vizinho adicionado com custo {custo} (roteadores.txt atualizado)")
    else:
        print(f"Não foi possível adicionar: {motivo}")


def cmd_remover(resto):
    ip = _ip_arg(resto.split(), 1, "remover <ip>")
    if ip is None:
        return
    try:
        existe, estava_ativo = estado.remover_vizinho(ip)
    except OSError as erro:
        return print(f"Removido da memória, mas falhou gravar roteadores.txt: {erro}")
    if not existe:
        return print("Vizinho não configurado.")
    registrar_log("CLI", ip, "vizinho removido (roteadores.txt atualizado)")
    if estava_ativo:
        registrar_log("ENLACE_INATIVO", ip, "vizinho removido")
        estado.solicitar_lsa()


def cmd_vizinhos(_resto):
    snap = estado.snapshot_vizinhos()
    if not snap:
        return print("(nenhum vizinho configurado)")
    for ip, viz in snap.items():
        situacao = "DESATIVADO" if viz["desativado"] else ("ATIVO" if viz["ativo"] else "INATIVO")
        print(f"  {ip:<15} custo={viz['custo']:<6} {situacao}")


def cmd_lsdb(_resto):
    integracao.mostrar_lsdb()


def cmd_rotas(_resto):
    integracao.mostrar_rotas()


def cmd_reenviar(resto):
    args = resto.split()
    if (len(args) != 3 or not estado.ip_valido(args[0])
            or not estado.ip_valido(args[1]) or args[2] not in ("dup", "antigo")):
        return print("Uso: reenviar <ip_origem> <ip_vizinho> dup|antigo")
    integracao.reenviar_lsa(args[0], args[1], args[2])


def cmd_ajuda(_resto):
    print(AJUDA)


def cmd_sair(_resto):
    estado.encerrar.set()


COMANDOS = {
    "enviar": cmd_enviar, "desativar": cmd_desativar, "ativar": cmd_ativar,
    "custo": cmd_custo, "add": cmd_add, "remover": cmd_remover,
    "vizinhos": cmd_vizinhos, "lsdb": cmd_lsdb, "rotas": cmd_rotas,
    "reenviar": cmd_reenviar,
    "ajuda": cmd_ajuda, "sair": cmd_sair,
}


def loop():
    print(AJUDA)
    while not estado.encerrar.is_set():
        try:
            linha = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not linha:
            continue
        nome, _, resto = linha.partition(" ")
        funcao = COMANDOS.get(nome.lower())
        if funcao is None:
            print("Comando desconhecido. Digite 'ajuda'.")
            continue
        try:
            funcao(resto.strip())
        except Exception as erro:  # noqa: BLE001 - a CLI nunca deve cair
            registrar_log("ERRO", nome, repr(erro))