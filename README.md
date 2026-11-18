# TF_Redes — Roteamento Link State

Trabalho final da disciplina de **Fundamentos de Redes de Computadores** (PUCRS, 2026/2).

O projeto implementa, em Python, um roteador com protocolo de roteamento **Link State**, inspirado no OSPF. Cada roteador é um processo que se comunica com os vizinhos por **UDP na porta 6000**. Ele:

- descobre os vizinhos com mensagens **HELLO**;
- anuncia os seus enlaces em **LSAs**, que são repassados para a rede inteira (flooding);
- guarda a topologia numa **LSDB** e calcula os menores caminhos com o **algoritmo de Dijkstra**;
- encaminha mensagens **DATA** salto a salto, usando a tabela de roteamento.

Usa apenas a biblioteca padrão do Python 3.

## Como executar

1. Crie uma pasta para o roteador com um arquivo `roteadores.txt` com os vizinhos e os custos (veja o formato abaixo).
2. Dentro dessa pasta, rode:

```
python ../src/main.py <IP_DESTE_ROTEADOR>
```

No Windows, use `python ..\src\main.py <IP>`. Em alguns sistemas o comando é `python3`.

O roteador lê e grava três arquivos na pasta onde é executado:

| Arquivo | Para que serve |
|---|---|
| `roteadores.txt` | Lista de vizinhos e custos (lido ao iniciar e atualizado pelos comandos `add`, `remover` e `custo`) |
| `seq.txt` | Último número de sequência usado nos LSAs, para continuar de onde parou depois de reiniciar |
| `roteador.log` | Log de tudo o que o roteador faz |

Para testar entre computadores diferentes, libere a porta UDP 6000 no firewall. No Windows, num cmd como administrador:

```
netsh advfirewall firewall add rule name="TF Redes UDP 6000" dir=in action=allow protocol=UDP localport=6000
```

### Formato do `roteadores.txt`

Uma linha por vizinho, no formato `IP;CUSTO`:

```
127.0.0.2;8
127.0.0.3;5
```

## Comandos

| Comando | O que faz |
|---|---|
| `enviar <ip_destino> [ttl] <mensagem>` | Envia uma mensagem DATA (TTL de 0 a 255, padrão 16) |
| `desativar <ip>` | Para de falar com o vizinho e ignora o que chega dele |
| `ativar <ip>` | Volta a falar com o vizinho; o enlace sobe no próximo HELLO válido |
| `custo <ip> <novo_custo>` | Altera o custo do enlace (1 a 65535) |
| `add <ip> <custo>` | Adiciona um vizinho e grava no `roteadores.txt` |
| `remover <ip>` | Remove um vizinho e grava no `roteadores.txt` |
| `vizinhos` | Mostra os vizinhos e a situação de cada enlace |
| `lsdb` | Mostra a LSDB |
| `rotas` | Mostra a tabela de roteamento, marcando rotas novas, alteradas, removidas e empates |
| `reenviar <origem> <vizinho> dup\|antigo` | Reenvia um LSA guardado com a mesma sequência ou uma menor (teste de descarte) |
| `ajuda` / `sair` | Mostra os comandos / encerra o roteador |

## Mensagens

Todas as mensagens são texto UTF-8, com no máximo 1200 bytes.

| Tipo | Formato | Exemplo |
|---|---|---|
| HELLO | `@IP*vizinho1*vizinho2...` | `@127.0.0.2*127.0.0.1*127.0.0.3*127.0.0.4` |
| LSA | `#IP;SEQ*vizinho;custo*...` | `#127.0.0.1;7*127.0.0.2;8*127.0.0.3;5` |
| DATA | `!origem;destino;id;ttl;mensagem` | `!127.0.0.1;127.0.0.4;2;16;oi` |

| Temporizador | Valor |
|---|---|
| Envio de HELLO | a cada 5 s |
| Vizinho considerado inativo | 15 s sem HELLO válido |
| Envio de LSA | a cada 10 s e sempre que a vizinhança muda |
| Expiração de um LSA | 30 s sem receber uma versão nova |

## Estrutura do projeto

```
src/
  main.py         ponto de entrada: inicia o estado, o socket, as threads e o terminal
  estado.py       estado compartilhado: vizinhos, tabela de rotas, sequência, log e envio
  rede.py         recepção dos pacotes, HELLO, monitor de enlaces, geração de LSA e DATA
  lsdb.py         LSDB, aceitação de LSA, flooding, expiração, Dijkstra e tabela de rotas
  integracao.py   liga o módulo de rede à LSDB
  terminal.py     comandos digitados pelo usuário
r1/ ... r4/       roteadores.txt da topologia de testes com 4 roteadores
capturas/         capturas do Wireshark (.pcapng) e logs dos testes
```

## Testes

Os testes foram feitos de duas formas:

- **Quatro roteadores na mesma máquina** (`127.0.0.1` a `127.0.0.4`), cada um na sua pasta `r1` a `r4`, com a topologia abaixo. O custo pode ser diferente em cada sentido:

| Enlace | Custo |
|---|---|
| R1 – R2 | 8 de R1 para R2, 2 de R2 para R1 |
| R1 – R3 | 5 |
| R2 – R3 | 3 |
| R2 – R4 | 1 |

- **Dois notebooks** ligados no mesmo hotspot, um roteador em cada.

Em `capturas/` estão as capturas do Wireshark de cada cenário, com os pacotes principais comentados, e os logs dos roteadores:

| Arquivo | Cenário |
|---|---|
| `cenario_A.pcapng` | Convergência inicial da rede |
| `cenario_B.pcapng` | Entrada de um novo roteador |
| `cenario_C.pcapng` | Falha de um enlace |
| `cenario_D.pcapng` | Falha de um roteador |
| `cenario_E.pcapng` | Recuperação após falha |
| `cenario_F.pcapng` | Recebimento de LSAs antigos e duplicados |
| `cenario_G.pcapng` | Alteração do custo de um enlace |
| `cenario_H.pcapng` | Encaminhamento de DATA com TTL insuficiente |
| `rede_real_*.pcapng` | Os mesmos testes entre dois computadores |
| `log_*.txt` | Logs dos roteadores durante os testes |

Para ver só um tipo de mensagem no Wireshark, use o primeiro byte do payload:

| Mensagem | Filtro |
|---|---|
| HELLO | `udp.port == 6000 && udp.payload[0:1] == 40` |
| LSA | `udp.port == 6000 && udp.payload[0:1] == 23` |
| DATA | `udp.port == 6000 && udp.payload[0:1] == 21` |

## Autoras

- Luísa Zarth([@LuisaZarth](https://github.com/LuisaZarth))
- Rosana Schreiner Budant ([@RosanaBudant](https://github.com/RosanaBudant))
