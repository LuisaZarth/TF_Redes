"""
lsdb.py - Parte 2 do Trabalho Final de Fundamentos de Redes (PUCRS)
Topologia e rotas: LSDB, flooding, expiração, Dijkstra e tabela de roteamento.

ETAPA 1: leitura/validação de LSA e decisão de aceitar ou descartar.
ETAPA 2: flooding, sincronização com vizinho novo e reenvio de teste
         (LSA duplicado/antigo, para o cenário F).
ETAPA 3: expiração de LSAs remotos (30 s) e validação da sequência local.
ETAPA 4: grafo, Dijkstra, tabela de roteamento e próximo salto.

Como a Parte 1 se conecta a este arquivo:
    lsdb = LSDB(meu_ip, vizinhos_ativos, enviar, log)
    lsdb.iniciar()        # liga a thread de expiração
onde vizinhos_ativos, enviar e log são funções da Parte 1:
    vizinhos_ativos()            -> lista de IPs dos vizinhos ativos
    enviar(ip, msg)              -> envia uma mensagem UDP para ip:6000
    log(tipo, origem, descricao) -> registra um evento na tela e no arquivo

E a Parte 1 chama:
    lsdb.lsa_recebido(msg, ip_remetente)   quando chega um #
    lsdb.lsa_local_gerado(lsa)             a cada LSA próprio gerado
    lsdb.vizinho_estabelecido(ip)          quando uma vizinhança fecha
    lsdb.proximo_salto(destino)            para encaminhar um DATA
    lsdb.mostrar() / lsdb.mostrar_rotas()  comandos "lsdb" e "rotas"
    lsdb.reenviar_lsa(origem, viz, modo)   comando de teste do cenário F
"""

import heapq
import ipaddress
import random
import threading
import time

# ---------------------------------------------------------------------------
# Constantes da especificação
# ---------------------------------------------------------------------------
VALIDADE_LSA = 30          # LSA remoto expira 30 s após a última versão aceita
INTERVALO_EXPIRACAO = 1    # de quanto em quanto tempo checamos a expiração


# ---------------------------------------------------------------------------
# Validação e montagem de LSA
# ---------------------------------------------------------------------------
def ip_valido(texto):
    """True se o texto for um endereço IPv4 válido."""
    try:
        ipaddress.IPv4Address(texto)
        return True
    except ValueError:
        return False


def inteiro_positivo(texto):
    """True se o texto for um inteiro maior que zero (só dígitos)."""
    return texto.isdigit() and int(texto) > 0


def chave_ip(ip):
    """Para ordenar IPs numericamente (10.0.0.2 antes de 10.0.0.10)."""
    return ipaddress.IPv4Address(ip)


def ler_lsa(msg):
    """Interpreta um LSA no formato
        #IP_ORIGEM;SEQUENCIA*VIZINHO1;CUSTO*VIZINHO2;CUSTO

    Devolve (origem, seq, enlaces, None) se o LSA for válido, onde enlaces
    é um dicionário {vizinho: custo}. Se for inválido, devolve
    (None, None, None, motivo).
    """
    if not msg.startswith("#"):
        return None, None, None, "não começa com #"

    blocos = msg[1:].split("*")

    # Primeiro bloco: IP_ORIGEM;SEQUENCIA
    cabecalho = blocos[0].split(";")
    if len(cabecalho) != 2:
        return None, None, None, "cabeçalho deve ser IP_ORIGEM;SEQUENCIA"
    origem, seq = cabecalho
    if not ip_valido(origem):
        return None, None, None, f"IP de origem inválido: '{origem}'"
    if not inteiro_positivo(seq):
        return None, None, None, f"sequência inválida: '{seq}'"

    # Demais blocos: VIZINHO;CUSTO
    # (um LSA sem nenhum vizinho é válido: roteador sem enlaces ativos)
    enlaces = {}
    for bloco in blocos[1:]:
        partes = bloco.split(";")
        if len(partes) != 2:
            return None, None, None, f"enlace deve ser VIZINHO;CUSTO: '{bloco}'"
        vizinho, custo = partes
        if not ip_valido(vizinho):
            return None, None, None, f"IP de vizinho inválido: '{vizinho}'"
        if not inteiro_positivo(custo):
            return None, None, None, f"custo inválido: '{custo}'"
        if vizinho == origem:
            return None, None, None, "roteador anunciando enlace para si mesmo"
        if vizinho in enlaces:
            return None, None, None, f"vizinho repetido: {vizinho}"
        enlaces[vizinho] = int(custo)

    return origem, int(seq), enlaces, None


def montar_lsa(origem, seq, enlaces):
    """Monta o texto do LSA exatamente no formato do enunciado.
    Usado na sincronização e no reenvio de teste."""
    partes = [f"#{origem};{seq}"]
    partes += [f"{viz};{custo}" for viz, custo in enlaces.items()]
    return "*".join(partes)


# ---------------------------------------------------------------------------
# Grafo e Dijkstra (implementados à mão; heapq é só uma fila de prioridade)
# ---------------------------------------------------------------------------
def montar_grafo(registros, meu_ip, ativos):
    """Constrói o grafo dirigido a partir da LSDB.

    Regras do enunciado:
    - Uma aresta A -> B só existe se A e B têm LSA válido na LSDB e os DOIS
      reconhecem o enlace (A anuncia B e B anuncia A).
    - O custo de A -> B é o custo que A anunciou (pode diferir de B -> A).
    - Do meu roteador, só saio por vizinhos ATIVOS (o próximo salto tem que
      ser sempre um vizinho ativo).

    Devolve {no: {vizinho: custo}}.
    """
    grafo = {no: {} for no in registros}
    for a, reg in registros.items():
        for b, custo in reg["enlaces"].items():
            if b not in registros:
                continue                      # B não tem LSA válido
            if a not in registros[b]["enlaces"]:
                continue                      # B não reconhece o enlace
            if a == meu_ip and b not in ativos:
                continue                      # não é vizinho ativo agora
            grafo[a][b] = custo
    return grafo


def dijkstra(grafo, origem):
    """Dijkstra a partir de 'origem'.

    Devolve (dist, saltos):
      dist[no]   = custo total do menor caminho até o nó
      saltos[no] = conjunto de TODOS os primeiros saltos possíveis que levam
                   ao nó com esse custo mínimo (mais de um = empate)

    Guardar todos os primeiros saltos empatados permite escolher um de
    forma aleatória depois, como pede o enunciado.
    """
    dist = {origem: 0}
    saltos = {origem: set()}
    visitados = set()
    fila = [(0, origem)]                      # (custo acumulado, nó)

    while fila:
        d, u = heapq.heappop(fila)
        if u in visitados:
            continue                          # entrada velha da fila
        visitados.add(u)

        for v, custo in grafo.get(u, {}).items():
            if v in visitados:
                continue
            nova = d + custo
            # Se saio direto da origem, o primeiro salto é o próprio v;
            # senão, herdo os primeiros saltos de u.
            via = {v} if u == origem else saltos[u]

            if v not in dist or nova < dist[v]:
                dist[v] = nova                # caminho estritamente melhor
                saltos[v] = set(via)
                heapq.heappush(fila, (nova, v))
            elif nova == dist[v]:
                saltos[v] |= via              # caminho de mesmo custo: empate

    return dist, saltos


# ---------------------------------------------------------------------------
# A LSDB
# ---------------------------------------------------------------------------
class LSDB:
    def __init__(self, meu_ip, vizinhos_ativos, enviar, log,
                 publicar_rotas=None):
        self.meu_ip = meu_ip
        self.vizinhos_ativos = vizinhos_ativos   # função da Parte 1
        self.enviar = enviar                     # função da Parte 1
        self.log = log                           # função da Parte 1
        # Opcional: chamada com a tabela nova após cada Dijkstra, para a
        # Parte 1 usar no encaminhamento de DATA.
        #   publicar_rotas({destino: (custo_total, proximo_salto)})
        self.publicar_rotas = publicar_rotas

        # A Parte 1 chama estas funções a partir de threads diferentes
        # (receptor, timers), então todo o estado fica protegido por um lock.
        # Regra de ouro: NUNCA chamar vizinhos_ativos() ou enviar() com este
        # lock preso, para não haver deadlock com o lock da Parte 1.
        # Primeiro consultamos/decidimos, depois soltamos e enviamos.
        self.lock = threading.RLock()

        # Registros da LSDB: a versão válida mais recente de cada roteador.
        #   origem -> {"seq": int, "enlaces": {vizinho: custo}, "recebido": float}
        self.registros = {}

        # Maior sequência já aceita de cada origem. NÃO é apagada quando o
        # LSA expira: é isso que impede aceitar um anúncio antigo depois.
        self.maior_seq = {}

        # Tabela de roteamento: destino -> (custo_total, proximo_salto)
        self.tabela = {}
        # Mudanças do último cálculo, para destacar no comando "rotas":
        #   destino -> "NOVA" | "ALTERADA";  removidas guardadas à parte
        self.marcas = {}
        self.removidas = {}
        # Destinos com caminhos de mesmo custo: destino -> [saltos possíveis]
        self.empates = {}

        # Validade dos LSAs remotos (atributo para os testes poderem encurtar).
        self.validade = VALIDADE_LSA
        self.parar = threading.Event()

    # ------------------------------------------------------ ciclo de vida
    def iniciar(self):
        """Liga a thread que verifica a expiração dos LSAs a cada 1 s."""
        threading.Thread(target=self._laco_expiracao, daemon=True).start()

    def encerrar(self):
        self.parar.set()

    # ------------------------------------------------- LSA vindo da rede
    def lsa_recebido(self, msg, ip_remetente):
        """Chamado pela Parte 1 quando chega um datagrama que começa com #.
        ip_remetente é quem enviou o datagrama (pode não ser a origem).
        Devolve True se o LSA foi aceito, False se foi descartado."""

        ativos = self.vizinhos_ativos()

        # 1. O datagrama só pode ser aceito se veio de um vizinho ativo.
        if ip_remetente not in ativos:
            self.log("LSA_DESCARTADO", ip_remetente,
                     "remetente não é vizinho ativo")
            return False

        # 2. Formato.
        origem, seq, enlaces, motivo = ler_lsa(msg)
        if motivo:
            self.log("LSA_DESCARTADO", ip_remetente, f"malformado: {motivo}")
            return False

        # 3. LSA com o meu próprio IP de origem: descarta sem processar.
        if origem == self.meu_ip:
            self.log("LSA_DESCARTADO", origem, f"SEQ={seq} | LSA próprio")
            return False

        with self.lock:
            # 4. Só aceita se a sequência for MAIOR que a maior já aceita.
            #    Duplicados e antigos não fazem flooding nem renovam validade.
            ultima = self.maior_seq.get(origem, 0)
            if seq == ultima:
                self.log("LSA_DESCARTADO", origem, f"SEQ={seq} | duplicado")
                return False
            if seq < ultima:
                self.log("LSA_DESCARTADO", origem,
                         f"SEQ={seq} | antigo (último aceito: {ultima})")
                return False

            # 5. LSA novo ou mais recente: aceita e registra.
            mudou = self._registrar(origem, seq, enlaces)
            self.log("LSA_ACEITO", origem, f"SEQ={seq}")

        # 6. Flooding (fora do lock): repassa a mensagem, sem alterar,
        #    a todos os vizinhos ativos, menos quem nos mandou o datagrama.
        self._flooding(msg, origem, seq, excluir=ip_remetente, ativos=ativos)

        # 7. Só recalcula se a topologia mudou (renovação periódica não).
        if mudou:
            self.recalcular()
        return True

    def _flooding(self, msg, origem, seq, excluir, ativos):
        destinos = [ip for ip in ativos if ip != excluir]
        for ip in destinos:
            self.enviar(ip, msg)
        if destinos:
            self.log("LSA_ENCAMINHADO", origem,
                     f"SEQ={seq} | para {', '.join(destinos)}")

    # ------------------------------------------------------ LSA local
    def lsa_local_gerado(self, lsa):
        """Chamado pela Parte 1 sempre que ela gera o LSA deste roteador.
        O LSA próprio também fica na LSDB, porque o Dijkstra precisa dele.
        (O envio do LSA local aos vizinhos é feito pela Parte 1.)"""
        origem, seq, enlaces, motivo = ler_lsa(lsa)
        if motivo or origem != self.meu_ip:
            self.log("ERRO_LSA_LOCAL", self.meu_ip, motivo or "origem diferente")
            return
        with self.lock:
            # A sequência local também só pode avançar: se vier igual ou
            # menor que a já registrada, ignoramos para o LSA próprio não
            # regredir na LSDB.
            ultima = self.maior_seq.get(origem, 0)
            if seq <= ultima:
                self.log("ERRO_LSA_LOCAL", origem,
                         f"SEQ={seq} não é maior que a registrada ({ultima})")
                return
            mudou = self._registrar(origem, seq, enlaces)
        # O primeiro LSA local conta como mudança (origem nova), então o
        # primeiro cálculo acontece logo após a criação do LSA local, com o
        # que houver na LSDB, como pede o enunciado.
        if mudou:
            self.recalcular()

    # ------------------------------------------ sincronização com vizinho
    def vizinho_estabelecido(self, ip):
        """Chamado pela Parte 1 quando uma vizinhança fica ativa.
        Envia ao novo vizinho TODOS os LSAs válidos da LSDB (inclusive o
        nosso), para ele conhecer a topologia sem esperar os periódicos."""
        with self.lock:
            mensagens = [montar_lsa(origem, r["seq"], r["enlaces"])
                         for origem, r in self.registros.items()]
        for msg in mensagens:
            self.enviar(ip, msg)
        self.log("LSDB_SINCRONIZADA", ip, f"{len(mensagens)} LSA(s) enviados")

    # ------------------------------------- comando de teste (cenário F)
    def reenviar_lsa(self, origem, vizinho, modo):
        """Reenvia a um vizinho o LSA armazenado de uma origem:
            modo "dup"    -> mesma sequência (duplicado)
            modo "antigo" -> sequência menor (antigo)
        O vizinho deve descartar os dois. Devolve True se enviou."""
        if vizinho not in self.vizinhos_ativos():
            print(f"{vizinho} não é vizinho ativo.")
            return False

        with self.lock:
            r = self.registros.get(origem)
            if r is None:
                print(f"Não há LSA de {origem} na LSDB.")
                return False
            if modo == "dup":
                seq = r["seq"]
            elif modo == "antigo":
                if r["seq"] <= 1:
                    print("Sequência já é 1; não existe sequência menor válida.")
                    return False
                seq = r["seq"] - 1
            else:
                print("Modo deve ser 'dup' ou 'antigo'.")
                return False
            msg = montar_lsa(origem, seq, r["enlaces"])

        self.enviar(vizinho, msg)
        tipo = "duplicado" if modo == "dup" else "antigo"
        self.log("LSA_REENVIADO_TESTE", origem,
                 f"SEQ={seq} | {tipo} | para {vizinho}")
        return True

    # ------------------------------------------------------ expiração
    def _laco_expiracao(self):
        """Thread: a cada 1 s retira da LSDB os LSAs remotos vencidos."""
        while not self.parar.wait(INTERVALO_EXPIRACAO):
            self.verificar_expiracao()

    def verificar_expiracao(self):
        """Retira da LSDB todo LSA remoto sem versão nova aceita há mais de
        30 s. Devolve True se algum saiu (a topologia mudou).

        - O LSA LOCAL nunca expira: ele é renovado pela própria Parte 1.
        - maior_seq NÃO é apagada: um LSA antigo daquela origem continua
          sendo descartado mesmo depois da expiração.
        - Duplicados e antigos não renovam o prazo, porque só _registrar
          (chamado para LSAs aceitos) atualiza o campo "recebido"."""
        agora = time.time()
        with self.lock:
            vencidos = [origem for origem, r in self.registros.items()
                        if origem != self.meu_ip
                        and agora - r["recebido"] > self.validade]
            for origem in vencidos:
                seq = self.registros.pop(origem)["seq"]
                self.log("LSA_EXPIRADO", origem,
                         f"SEQ={seq} | sem renovação há mais de {self.validade} s")
                self.log("LSDB_ATUALIZADA", origem, "registro removido")
        if vencidos:
            self.recalcular()
        return bool(vencidos)

    # ------------------------------------------------- cálculo das rotas
    def recalcular(self):
        """Reconstrói o grafo, roda o Dijkstra a partir deste roteador e
        atualiza a tabela, registrando rotas incluídas, removidas e alteradas.
        Chamado só quando a topologia muda."""
        ativos = set(self.vizinhos_ativos())   # fora do lock (regra de ouro)

        with self.lock:
            if self.meu_ip not in self.registros:
                return                         # ainda sem LSA local

            grafo = montar_grafo(self.registros, self.meu_ip, ativos)
            dist, saltos = dijkstra(grafo, self.meu_ip)

            nova = {}
            self.empates = {}
            for destino, custo in dist.items():
                if destino == self.meu_ip:
                    continue                   # sem rota para mim mesmo
                candidatos = saltos[destino]
                if len(candidatos) > 1:
                    self.empates[destino] = sorted(candidatos, key=chave_ip)
                anterior = self.tabela.get(destino)
                if anterior and anterior[1] in candidatos:
                    # Empate continua valendo para o salto atual: mantemos,
                    # para a rota não ficar "trocando" a cada recálculo.
                    salto = anterior[1]
                else:
                    # Escolha aleatória entre os saltos de mesmo custo.
                    salto = random.choice(sorted(candidatos, key=chave_ip))
                nova[destino] = (custo, salto)

            self.log("DIJKSTRA_EXECUTADO", self.meu_ip,
                     f"{len(nova)} destino(s) alcançável(is)")
            self._comparar_tabelas(self.tabela, nova)
            self.tabela = nova

        # Fora do lock (regra de ouro): entrega a tabela à Parte 1.
        if self.publicar_rotas:
            self.publicar_rotas(dict(nova))

    def _comparar_tabelas(self, antiga, nova):
        """Registra no log e guarda as marcas das rotas que mudaram."""
        self.marcas = {}
        self.removidas = {}
        for destino in sorted(set(antiga) | set(nova), key=chave_ip):
            a, n = antiga.get(destino), nova.get(destino)
            if a is None:
                self.marcas[destino] = "NOVA"
                self.log("ROTA_INCLUIDA", destino,
                         f"custo {n[0]} via {n[1]}")
            elif n is None:
                self.removidas[destino] = a
                self.log("ROTA_REMOVIDA", destino,
                         f"era custo {a[0]} via {a[1]}")
            elif a != n:
                self.marcas[destino] = "ALTERADA"
                mudancas = []
                if a[0] != n[0]:
                    mudancas.append(f"custo {a[0]} -> {n[0]}")
                if a[1] != n[1]:
                    mudancas.append(f"via {a[1]} -> {n[1]}")
                self.log("ROTA_ALTERADA", destino, " | ".join(mudancas))

    def proximo_salto(self, destino):
        """Usado pela Parte 1 para encaminhar DATA. Devolve o IP do próximo
        salto ou None se não houver rota."""
        with self.lock:
            rota = self.tabela.get(destino)
            return rota[1] if rota else None

    # ------------------------------------------------------ auxiliares
    def _registrar(self, origem, seq, enlaces):
        """Grava o LSA na LSDB. Devolve True se a TOPOLOGIA mudou (origem nova
        ou enlaces/custos diferentes). Uma renovação periódica, que só muda
        a sequência, devolve False e não deve disparar o Dijkstra."""
        anterior = self.registros.get(origem)
        mudou = anterior is None or anterior["enlaces"] != enlaces

        self.registros[origem] = {
            "seq": seq,
            "enlaces": enlaces,
            "recebido": time.time(),
        }
        self.maior_seq[origem] = seq

        if mudou:
            self.log("LSDB_ATUALIZADA", origem,
                     "nova origem" if anterior is None else "enlaces alterados")
        return mudou

    # --------------------------------------------- comando "lsdb"
    def mostrar(self):
        """Exibe a LSDB (separada da tabela de roteamento, como pede o
        enunciado)."""
        agora = time.time()
        with self.lock:
            if not self.registros:
                print("LSDB vazia.")
                return
            print(f"\n{'Origem':<16} {'Seq':>6}  {'Idade':>7}  Enlaces anunciados")
            for origem in sorted(self.registros, key=chave_ip):
                r = self.registros[origem]
                enl = ", ".join(f"{v}({c})" for v, c in r["enlaces"].items())
                idade = f"{agora - r['recebido']:5.1f} s"
                print(f"{origem:<16} {r['seq']:>6}  {idade:>7}  {enl or '-'}")
            print()

    # --------------------------------------------- comando "rotas"
    def mostrar_rotas(self):
        """Exibe a tabela de roteamento, destacando as mudanças do último
        cálculo do Dijkstra."""
        with self.lock:
            if not self.tabela and not self.removidas:
                print("Tabela de roteamento vazia.")
                return
            print(f"\n{'Destino':<16} {'Custo total':>11}  {'Próximo salto':<16}")
            for destino in sorted(self.tabela, key=chave_ip):
                custo, salto = self.tabela[destino]
                extra = ""
                if destino in self.marcas:
                    extra += f"  <- {self.marcas[destino]}"
                if destino in self.empates:
                    extra += f"  (empate: {' / '.join(self.empates[destino])})"
                print(f"{destino:<16} {custo:>11}  {salto:<16}{extra}")
            for destino in sorted(self.removidas, key=chave_ip):
                custo, salto = self.removidas[destino]
                print(f"{destino:<16} {'-':>11}  {'-':<16}  <- REMOVIDA "
                      f"(era {custo} via {salto})")
            print()


# ---------------------------------------------------------------------------
# Teste isolado (sem rede): roda com  python lsdb.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    def log_falso(tipo, origem=None, descricao=None):
        print("  " + " | ".join(p for p in (tipo, origem, descricao) if p))

    def enviar_falso(ip, msg):
        print(f"    [ENVIARIA para {ip}] {msg}")

    def silencioso(ip, msg):
        pass

    # =======================================================================
    # Testes das etapas 1 a 3
    # =======================================================================
    ativos = ["10.0.0.2", "10.0.0.3"]

    def vizinhos_ativos_falso():
        return list(ativos)

    lsdb = LSDB("10.0.0.1", vizinhos_ativos_falso, enviar_falso, log_falso)

    def testar(titulo, msg, remetente):
        print(f"\n> {titulo}\n  recebido de {remetente}: {msg}")
        lsdb.lsa_recebido(msg, remetente)

    print("=== ETAPA 1: aceitar ou descartar ===")
    print("\n> LSA local gerado pela Parte 1")
    lsdb.lsa_local_gerado("#10.0.0.1;1*10.0.0.2;10*10.0.0.3;5")

    testar("LSA novo de vizinho ativo (faz flooding para o .3, não volta pro .2)",
           "#10.0.0.2;7*10.0.0.1;10*10.0.0.4;3", "10.0.0.2")
    testar("Mesmo LSA de novo (duplicado: sem flooding)",
           "#10.0.0.2;7*10.0.0.1;10*10.0.0.4;3", "10.0.0.2")
    testar("Sequência menor (antigo: sem flooding)",
           "#10.0.0.2;5*10.0.0.1;10*10.0.0.4;3", "10.0.0.2")
    testar("Renovação periódica: seq maior, mesmos enlaces (sem Dijkstra)",
           "#10.0.0.2;8*10.0.0.1;10*10.0.0.4;3", "10.0.0.2")
    testar("Custo alterado: topologia muda",
           "#10.0.0.2;9*10.0.0.1;10*10.0.0.4;8", "10.0.0.2")
    testar("LSA do .4 encaminhado pelo vizinho .3 (flooding só para o .2)",
           "#10.0.0.4;1*10.0.0.2;3", "10.0.0.3")
    testar("Remetente que não é vizinho ativo",
           "#10.0.0.4;2*10.0.0.2;3", "10.0.0.9")
    testar("LSA com o meu próprio IP de origem",
           "#10.0.0.1;50*10.0.0.2;10", "10.0.0.2")
    testar("Custo zero (inválido)",
           "#10.0.0.5;1*10.0.0.3;0", "10.0.0.3")
    testar("IP inválido",
           "#10.0.0.300;1*10.0.0.3;4", "10.0.0.3")
    testar("Sem sequência",
           "#10.0.0.5*10.0.0.3;4", "10.0.0.3")

    print("\n=== ETAPA 2: sincronização com vizinho novo ===")
    print("\n> Vizinhança com 10.0.0.6 estabelecida: envia a LSDB inteira")
    ativos.append("10.0.0.6")
    lsdb.vizinho_estabelecido("10.0.0.6")

    print("\n=== ETAPA 2: reenvio de teste (cenário F) ===")
    print("\n> Reenviar ao .3 o LSA do .2 com a mesma sequência (duplicado)")
    lsdb.reenviar_lsa("10.0.0.2", "10.0.0.3", "dup")
    print("\n> Reenviar ao .3 o LSA do .2 com sequência menor (antigo)")
    lsdb.reenviar_lsa("10.0.0.2", "10.0.0.3", "antigo")
    print("\n> Reenviar LSA de origem que não está na LSDB")
    lsdb.reenviar_lsa("10.0.0.8", "10.0.0.3", "dup")

    print("\n=== ETAPA 3: sequência local não pode regredir ===")
    print("\n> Parte 1 gera LSA local com seq 2 (avança: aceito)")
    lsdb.lsa_local_gerado("#10.0.0.1;2*10.0.0.2;10*10.0.0.3;5*10.0.0.6;4")
    print("\n> Parte 1 gera LSA local com seq 1 (menor: ignorado)")
    lsdb.lsa_local_gerado("#10.0.0.1;1*10.0.0.2;10")

    print("\n=== ETAPA 3: expiração ===")
    print("(validade encurtada para 2 s só neste teste; no real são 30 s)")
    lsdb.validade = 2

    print("\n> Duplicado do .4 chegando: NÃO deve renovar o prazo")
    lsdb.lsa_recebido("#10.0.0.4;1*10.0.0.2;3", "10.0.0.3")

    print("\n> Esperando 2,5 s e verificando...")
    time.sleep(2.5)
    lsdb.verificar_expiracao()

    print("\n> Depois da expiração, LSA antigo do .2 (seq 9) continua descartado")
    lsdb.lsa_recebido("#10.0.0.2;9*10.0.0.1;10*10.0.0.4;8", "10.0.0.2")
    print("\n> Mas uma versão nova do .2 (seq 10) é aceita e volta para a LSDB")
    lsdb.lsa_recebido("#10.0.0.2;10*10.0.0.1;10", "10.0.0.2")

    print("\n=== LSDB final (o LSA local nunca expira) ===")
    lsdb.mostrar()

    # =======================================================================
    # ETAPA 4: Dijkstra numa topologia conhecida (confira no papel)
    # =======================================================================
    #
    #   Eu sou R1 = 10.1.0.1. Custos anunciados em cada direção:
    #
    #        R1 --8--> R2      R2 --2--> R1     (assimétrico)
    #        R1 --5--> R3      R3 --5--> R1
    #        R3 --3--> R2      R2 --3--> R3
    #        R2 --1--> R4      R4 --1--> R2
    #        R3 --9--> R5      R5 --9--> R3
    #        R5 --2--> R6      (R6 não tem LSA: aresta não pode ser usada)
    #
    #   Rotas esperadas a partir de R1 (com todos os LSAs na LSDB):
    #     R2: custo 8, empate (direto 8  x  via R3: 5+3=8) -> .2 ou .3
    #     R3: custo 5 via .3
    #     R4: custo 9, herda o empate do R2 -> .2 ou .3
    #     R5: custo 14 via .3
    #     R6: sem rota
    #
    print("\n\n=== ETAPA 4: Dijkstra e tabela de roteamento ===")
    R1, R2, R3, R4, R5, R6 = (f"10.1.0.{i}" for i in range(1, 7))
    ativos2 = [R2, R3]
    rede = LSDB(R1, lambda: list(ativos2), silencioso, log_falso)

    print("\n> LSA local (primeiro cálculo, só me conheço)")
    rede.lsa_local_gerado(f"#{R1};1*{R2};8*{R3};5")

    print("\n> Chegam os LSAs da rede (cada um muda a topologia)")
    for msg, de in [
        (f"#{R2};1*{R1};2*{R3};3*{R4};1", R2),
        (f"#{R3};1*{R1};5*{R2};3*{R5};9", R3),
        (f"#{R4};1*{R2};1", R2),
        (f"#{R5};1*{R3};9*{R6};2", R3),
    ]:
        print(f"\n  >> {msg}")
        rede.lsa_recebido(msg, de)

    print("\n> Comando 'rotas':")
    rede.mostrar_rotas()

    print("> Renovação periódica do R4 (mesmos enlaces): NÃO roda Dijkstra")
    rede.lsa_recebido(f"#{R4};2*{R2};1", R2)

    print("\n> R3 aumenta o custo para R2 (3 -> 30): empate desaparece")
    rede.lsa_recebido(f"#{R3};2*{R1};5*{R2};30*{R5};9", R3)
    rede.mostrar_rotas()

    print("> R1 aumenta o custo para R2 (8 -> 20): agora R2 vale mais via R3?")
    print("  (via R3: 5+30=35, direto: 20 -> continua direto, só muda o custo)")
    rede.lsa_local_gerado(f"#{R1};2*{R2};20*{R3};5")
    rede.mostrar_rotas()

    print("> Vizinho R3 fica inativo (Parte 1 gera LSA local sem ele)")
    ativos2.remove(R3)
    rede.lsa_local_gerado(f"#{R1};3*{R2};20")
    rede.mostrar_rotas()

    print("> proximo_salto (usado pela Parte 1 no DATA):")
    for destino in (R4, R5, R6):
        print(f"  proximo_salto({destino}) = {rede.proximo_salto(destino)}")
