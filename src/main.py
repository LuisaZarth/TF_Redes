"""
main.py - Ponto de entrada.   Uso: python main.py <IP_DESTE_ROTEADOR>

Ordem de inicialização:
  1. estado.inicializar -> guarda meu_ip e lê roteadores.txt
  2. estado.abrir_socket -> bind em 0.0.0.0:6000
  3. parte2.iniciar     -> (Parte 2) threads de expiração etc.
  4. rede.iniciar_threads -> recepção, HELLO, monitor de enlaces, LSA
  5. cli.loop           -> a thread principal vira a CLI
"""

import sys

import terminal
import estado
import parte2
import rede


def main():
    if len(sys.argv) != 2 or not estado.ip_valido(sys.argv[1]):
        print("Uso: python main.py <IP_DESTE_ROTEADOR>")
        sys.exit(1)

    estado.inicializar(sys.argv[1])
    try:
        estado.abrir_socket()
    except OSError as erro:
        print(f"Não foi possível abrir UDP {estado.PORTA}: {erro}")
        sys.exit(1)

    estado.registrar_log("INICIO", estado.meu_ip, f"roteador iniciado na porta {estado.PORTA}")
    parte2.iniciar()
    rede.iniciar_threads()
    try:
        terminal.loop()
    finally:
        estado.encerrar.set()
        estado.registrar_log("FIM", estado.meu_ip, "roteador encerrado")


if __name__ == "__main__":
    main()