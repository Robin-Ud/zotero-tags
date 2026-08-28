#!/usr/bin/env python3
"""Testes do zotero_corrigir_chaves. Rode: python3 zotero_corrigir_chaves_test.py"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zotero_corrigir_chaves as z


class TestChaveValida(unittest.TestCase):
    """A regra que quebrou o sync em 18/08/2026: 8 chars, sem 0, 1 e O."""

    def test_aceita_chave_do_zotero(self):
        for boa in ("3QNVNBI7", "5NCTNH28", "XI6MEZV5", "ZZZZZZZZ", "22222222"):
            self.assertTrue(z.chave_valida(boa), boa)

    def test_recusa_as_chaves_que_quebraram_o_sync(self):
        # as cinco colecoes reais do incidente
        for ruim in ("FRRG2026A", "GEE20026A", "EXTR2026A", "1T15IHCE", "61KQLYIF"):
            self.assertFalse(z.chave_valida(ruim), ruim)

    def test_recusa_tamanho_errado(self):
        self.assertFalse(z.chave_valida("ABCDEFG"))       # 7
        self.assertFalse(z.chave_valida("ABCDEFGHI"))     # 9
        self.assertFalse(z.chave_valida(""))

    def test_recusa_os_tres_caracteres_proibidos(self):
        for proibido in ("0", "1", "O"):
            self.assertFalse(z.chave_valida("ABCDEFG" + proibido), proibido)

    def test_recusa_minuscula(self):
        self.assertFalse(z.chave_valida("abcdefgh"))


class TestGerarChave(unittest.TestCase):
    def test_gera_sempre_chave_valida(self):
        for _ in range(300):
            self.assertTrue(z.chave_valida(z.gerar_chave(set())))

    def test_nunca_gera_L(self):
        """ALFABETO_VALIDO aceita L, mas nao geramos: e confundivel com 1 e I.
        Aceitar o que o Zotero aceita, gerar so o que e seguro."""
        self.assertIn("L", z.ALFABETO_VALIDO)
        self.assertNotIn("L", z.ALFABETO_GERACAO)
        juntas = "".join(z.gerar_chave(set()) for _ in range(300))
        self.assertNotIn("L", juntas)

    def test_nao_repete_chave_ja_usada(self):
        todas = set()
        for _ in range(50):
            nova = z.gerar_chave(todas)
            self.assertNotIn(nova, todas)
            todas.add(nova)


def banco_falso(caminho, chaves):
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, "
                "key TEXT, collectionName TEXT)")
    con.execute("CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER)")
    for i, k in enumerate(chaves, 1):
        con.execute("INSERT INTO collections VALUES (?,?,?)", (i, k, "Col%d" % i))
        con.execute("INSERT INTO collectionItems VALUES (?,?)", (i, 100 + i))
    con.commit()
    return con


class TestColetar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.caminho = os.path.join(self.dir, "z.sqlite")

    def test_separa_quebradas_das_boas(self):
        con = banco_falso(self.caminho, ["3QNVNBI7", "FRRG2026A", "5NCTNH28"])
        linhas, usadas, quebradas = z.coletar(con)
        self.assertEqual(len(linhas), 3)
        self.assertEqual([q[1] for q in quebradas], ["FRRG2026A"])
        self.assertIn("3QNVNBI7", usadas)
        con.close()

    def test_banco_saudavel_nao_tem_quebrada(self):
        con = banco_falso(self.caminho, ["3QNVNBI7", "5NCTNH28"])
        _, _, quebradas = z.coletar(con)
        self.assertEqual(quebradas, [])
        con.close()

    def test_conta_itens_por_colecao(self):
        con = banco_falso(self.caminho, ["3QNVNBI7", "5NCTNH28"])
        self.assertEqual(z.contar_itens(con), {1: 1, 2: 1})
        con.close()

    def test_chave_nova_nao_colide_com_as_existentes(self):
        con = banco_falso(self.caminho, ["3QNVNBI7", "FRRG2026A"])
        _, usadas, _ = z.coletar(con)
        for _ in range(100):
            self.assertNotIn(z.gerar_chave(usadas), usadas)
        con.close()


class TestGuarda(unittest.TestCase):
    def test_journal_presente_impede_escrita(self):
        d = tempfile.mkdtemp()
        banco = os.path.join(d, "z.sqlite")
        open(banco + "-journal", "w").close()
        self.assertIn("journal", z.zotero_rodando(banco))


if __name__ == "__main__":
    unittest.main(verbosity=2)
