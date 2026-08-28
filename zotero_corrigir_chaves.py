#!/usr/bin/env python3
"""Corrige chaves de coleção inválidas no zotero.sqlite.

O Zotero exige que a chave de todo objeto tenha exatamente 8 caracteres do
alfabeto base32 dele (sem 0, 1 e O). Coleções criadas por script com chave
inventada à mão (ex.: 'FRRG2026A') quebram a sincronização com o zotero.org:
    '1T15IHCE' is not a valid collection key

Este script acha essas coleções e troca a chave por uma aleatória válida,
mantendo version=0/synced=0 para o Zotero subir cada uma como coleção nova.
Vínculos de item (collectionItems) e hierarquia (parentCollectionID) usam
collectionID, não a chave, então nada se perde.

Uso:
    python3 zotero_corrigir_chaves.py            # dry-run (padrão)
    python3 zotero_corrigir_chaves.py --aplicar
"""

import argparse
import os
import re
import secrets
import sqlite3
import subprocess
import sys

BANCO_PADRAO = os.path.expanduser("~/Zotero/zotero.sqlite")

# Alfabeto aceito pela validação do Zotero (sem 0, 1 e O).
ALFABETO_VALIDO = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
# Alfabeto usado para gerar chaves novas: subconjunto conservador, sem L
# também, seguro sob qualquer versão do regex do Zotero.
ALFABETO_GERACAO = "23456789ABCDEFGHIJKMNPQRSTUVWXYZ"

TAMANHO_CHAVE = 8
CHAVE_VALIDA = re.compile(f"^[{ALFABETO_VALIDO}]{{{TAMANHO_CHAVE}}}$")


def chave_valida(chave):
    return bool(CHAVE_VALIDA.match(chave))


def gerar_chave(usadas):
    while True:
        chave = "".join(secrets.choice(ALFABETO_GERACAO) for _ in range(TAMANHO_CHAVE))
        if chave not in usadas:
            return chave


def zotero_rodando(banco):
    if os.path.exists(banco + "-journal"):
        return "existe %s-journal (Zotero aberto ou fechado sujo)" % banco
    try:
        # -x casa o nome exato do processo; -f casaria com o próprio comando
        # que invocou este script, dando falso positivo.
        achou = subprocess.run(
            ["pgrep", "-x", "zotero-bin"], capture_output=True
        ).returncode == 0
    except FileNotFoundError:
        achou = False
    if achou:
        return "processo zotero-bin em execução"
    return None


def coletar(con):
    linhas = con.execute(
        "SELECT collectionID, key, collectionName FROM collections ORDER BY collectionID"
    ).fetchall()
    usadas = {linha[1] for linha in linhas}
    quebradas = [linha for linha in linhas if not chave_valida(linha[1])]
    return linhas, usadas, quebradas


def contar_itens(con):
    return dict(
        con.execute(
            "SELECT collectionID, COUNT(*) FROM collectionItems GROUP BY collectionID"
        ).fetchall()
    )


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--banco", default=BANCO_PADRAO, help="caminho do zotero.sqlite")
    p.add_argument("--aplicar", action="store_true",
                   help="grava as mudanças (sem isso, só mostra o que faria)")
    args = p.parse_args()

    if not os.path.exists(args.banco):
        print("banco não encontrado: %s" % args.banco, file=sys.stderr)
        return 1

    if args.aplicar:
        motivo = zotero_rodando(args.banco)
        if motivo:
            print("ABORTADO: feche o Zotero antes de aplicar — %s" % motivo,
                  file=sys.stderr)
            return 1

    con = sqlite3.connect(args.banco)
    try:
        _, usadas, quebradas = coletar(con)
        itens = contar_itens(con)

        if not quebradas:
            print("Nenhuma chave de coleção inválida. Nada a fazer.")
            return 0

        print("Coleções com chave inválida (%d):\n" % len(quebradas))
        plano = []
        for cid, chave_antiga, nome in quebradas:
            nova = gerar_chave(usadas)
            usadas.add(nova)
            plano.append((cid, chave_antiga, nova, nome))
            print("  #%-3d %-24s %-10s -> %-8s (%d itens)"
                  % (cid, nome, chave_antiga, nova, itens.get(cid, 0)))

        if not args.aplicar:
            print("\n[dry-run] nada foi gravado. Rode com --aplicar para valer.")
            return 0

        with con:
            for cid, _, nova, _ in plano:
                con.execute(
                    "UPDATE collections SET key = ?, version = 0, synced = 0,"
                    " clientDateModified = CURRENT_TIMESTAMP WHERE collectionID = ?",
                    (nova, cid),
                )

        print("\n%d coleções atualizadas." % len(plano))

        integridade = con.execute("PRAGMA integrity_check").fetchone()[0]
        print("integrity_check: %s" % integridade)
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        print("foreign_key_check: %s" % ("ok" if not fk else fk))

        _, _, ainda = coletar(con)
        depois = contar_itens(con)
        if ainda:
            print("ATENÇÃO: ainda há chaves inválidas: %s" % ainda, file=sys.stderr)
            return 1
        if depois != itens:
            print("ATENÇÃO: contagem de itens por coleção mudou!", file=sys.stderr)
            return 1
        print("Todas as chaves válidas e contagem de itens intacta.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

