#!/usr/bin/env python3
"""Testes do zotero_tags. So stdlib: python3 zotero_tags_test.py"""

import json
import os
import sqlite3
import tempfile
import unittest

import zotero_tags as z

ESQUEMA = """
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, key TEXT,
                    synced INT DEFAULT 1, clientDateModified TEXT, dateModified TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT, PRIMARY KEY (itemID, fieldID));
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE itemTags (itemID INT, tagID INT, type INT DEFAULT 0, PRIMARY KEY (itemID, tagID));
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, contentType TEXT);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);
"""

TIPOS = ["journalArticle", "thesis", "webpage", "book", "attachment", "note", "annotation"]
CAMPOS = ["title", "DOI", "thesisType"]


def banco_falso():
    con = sqlite3.connect(":memory:")
    con.executescript(ESQUEMA)
    for i, nome in enumerate(TIPOS, 1):
        con.execute("INSERT INTO itemTypes VALUES (?, ?)", (i, nome))
    for i, nome in enumerate(CAMPOS, 1):
        con.execute("INSERT INTO fields VALUES (?, ?)", (i, nome))
    return con


def por_nome(con, tabela, coluna, nome):
    return con.execute("SELECT %s FROM %s WHERE %s = ?"
                       % ({"itemTypes": "itemTypeID", "fields": "fieldID"}[tabela],
                          tabela, coluna), (nome,)).fetchone()[0]


def add_item(con, item_id, chave, tipo, titulo="Titulo", doi="", thesis=""):
    con.execute("INSERT INTO items (itemID, itemTypeID, key) VALUES (?, ?, ?)",
                (item_id, por_nome(con, "itemTypes", "typeName", tipo), chave))
    for campo, valor in (("title", titulo), ("DOI", doi), ("thesisType", thesis)):
        if not valor:
            continue
        cur = con.execute("INSERT INTO itemDataValues (value) VALUES (?)", (valor,))
        con.execute("INSERT INTO itemData VALUES (?, ?, ?)",
                    (item_id, por_nome(con, "fields", "fieldName", campo), cur.lastrowid))


def add_tag(con, item_id, nome, tipo=0):
    linha = con.execute("SELECT tagID FROM tags WHERE name = ?", (nome,)).fetchone()
    tag = linha[0] if linha else con.execute(
        "INSERT INTO tags (name) VALUES (?)", (nome,)).lastrowid
    con.execute("INSERT INTO itemTags VALUES (?, ?, ?)", (item_id, tag, tipo))


def linha_csv(**extra):
    linha = {"item_key": "AAAA1111", "tier": "B", "flags": "", "fwci": "2.0"}
    linha.update(extra)
    return linha


class TestTagDeTipo(unittest.TestCase):
    def setUp(self):
        self.cfg = z.carregar_config(z.CONFIGS_PADRAO)

    def tipo(self, **extra):
        item = {"tipo": "thesis", "thesis": ""}
        item.update(extra)
        return z.tag_de_tipo(item, self.cfg)

    def test_artigo(self):
        self.assertEqual(self.tipo(tipo="journalArticle"), "t-artigo")

    def test_livro_e_pagina_caem_em_outro(self):
        self.assertEqual(self.tipo(tipo="book"), "t-outro")
        self.assertEqual(self.tipo(tipo="webpage"), "t-outro")

    def test_tese_separa_mestrado_de_doutorado(self):
        self.assertEqual(self.tipo(thesis="tese de doutorado"), "t-doutorado")
        self.assertEqual(self.tipo(thesis="dissertacao de mestrado"), "t-mestrado")

    def test_thesistype_em_branco_nao_chuta(self):
        """Melhor devolver None e pedir olho humano que classificar errado."""
        self.assertIsNone(self.tipo(thesis=""))

    def test_tcc_de_graduacao_cai_em_outro(self):
        self.assertEqual(self.tipo(thesis="trabalho de conclusao de curso"), "t-outro")


class TestRelevancia(unittest.TestCase):
    def test_tier_vira_tag(self):
        self.assertEqual(z.tag_de_relevancia(linha_csv(tier="A")), "r-a")

    def test_sem_registro_nao_e_tier_d(self):
        """'nao mensuravel' e diferente de 'medido e fraco'."""
        self.assertEqual(
            z.tag_de_relevancia(linha_csv(tier="D", flags="sem_registro_openalex")),
            "r-sem-metrica")

    def test_item_fora_do_csv_nao_recebe_tag(self):
        self.assertIsNone(z.tag_de_relevancia(None))

    def test_imaturo_so_quando_o_fwci_foi_descartado(self):
        baixo = linha_csv(flags="fwci_imaturo", fwci="0.0")
        alto = linha_csv(flags="fwci_imaturo", fwci="63.0")
        self.assertTrue(z.marcador_imaturo(baixo))
        self.assertFalse(z.marcador_imaturo(alto))


class TestPlano(unittest.TestCase):
    def setUp(self):
        self.cfg = z.carregar_config(z.CONFIGS_PADRAO)
        self.con = banco_falso()

    def planejar(self, regioes=None):
        itens = z.coletar_itens(self.con, self.cfg)
        csv = {"AAAA1111": linha_csv()}
        return z.planejar(self.con, itens, csv, regioes or {}, self.cfg)

    def test_item_novo_recebe_os_quatro_eixos(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle", doi="10.1/x")
        plano = self.planejar({"AAAA1111": {"g-brasil"}})
        (item, inserir, remover) = plano[0]
        self.assertEqual(set(inserir),
                         {"n-lido", "n-aprofundado", "t-artigo", "r-b", "g-brasil"})
        self.assertEqual(remover, [])

    def test_idempotente(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        for nome in ("n-lido", "n-aprofundado", "t-artigo", "r-b"):
            add_tag(self.con, 1, nome)
        self.assertEqual(self.planejar(), [])

    def test_tag_lido_cancela_a_negativa(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "lido")
        (_, inserir, _) = self.planejar()[0]
        self.assertNotIn("n-lido", inserir)
        self.assertIn("n-aprofundado", inserir)

    def test_tier_antigo_e_removido_ao_mudar(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "r-a")
        (_, inserir, remover) = self.planejar()[0]
        self.assertIn("r-b", inserir)
        self.assertIn("r-a", remover)

    def test_protegidas_ficam_fora_do_diff(self):
        """'livro' e 'premiado' sao marcadores pessoais dele: nao se toca."""
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "premiado")
        (_, _, remover) = self.planejar()[0]
        self.assertNotIn("premiado", remover)

    def test_typo_conhecido_e_removido(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "aprodundado")
        (_, _, remover) = self.planejar()[0]
        self.assertIn("aprodundado", remover)

    def test_keyword_do_publisher_nao_entra_no_diff(self):
        """itemTags.type=1 vem do metadado da editora e nunca e mexido."""
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "aflatoxins", tipo=1)
        (_, _, remover) = self.planejar()[0]
        self.assertNotIn("aflatoxins", remover)

    def test_lixeira_e_ignorada(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        self.con.execute("INSERT INTO deletedItems VALUES (1)")
        self.assertEqual(z.coletar_itens(self.con, self.cfg), [])

    def test_anexo_nao_e_item_de_topo(self):
        add_item(self.con, 1, "AAAA1111", "attachment")
        self.assertEqual(z.coletar_itens(self.con, self.cfg), [])


class TestAplicar(unittest.TestCase):
    def setUp(self):
        self.cfg = z.carregar_config(z.CONFIGS_PADRAO)
        self.con = banco_falso()
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "aflatoxins", tipo=1)

    def rodar(self):
        itens = z.coletar_itens(self.con, self.cfg)
        plano = z.planejar(self.con, itens, {"AAAA1111": linha_csv()}, {}, self.cfg)
        z.aplicar(self.con, plano)
        return plano

    def test_escreve_sempre_type_zero(self):
        self.rodar()
        tipos = {t for (t,) in self.con.execute(
            """SELECT it.type FROM itemTags it JOIN tags t ON t.tagID = it.tagID
               WHERE t.name LIKE 'r-%' OR t.name LIKE 't-%'""")}
        self.assertEqual(tipos, {0})

    def test_tagueamento_nao_mexe_nas_automaticas(self):
        """Quem apaga keyword e a poda, nao o passo de taguear."""
        antes = z.contar_automaticas(self.con)
        self.rodar()
        self.assertEqual(z.contar_automaticas(self.con), antes)

    def test_marca_synced_zero(self):
        self.rodar()
        self.assertEqual(
            self.con.execute("SELECT synced FROM items WHERE itemID = 1").fetchone()[0], 0)

    def test_rodar_duas_vezes_nao_muda_nada(self):
        self.rodar()
        self.assertEqual(self.rodar(), [])

    def test_conferir_pega_dois_tiers_no_mesmo_item(self):
        add_tag(self.con, 1, "r-a")
        add_tag(self.con, 1, "r-c")
        self.assertTrue(z.conferir(self.con, self.cfg))


class TestPoda(unittest.TestCase):
    def setUp(self):
        self.cfg = z.carregar_config(z.CONFIGS_PADRAO)
        self.con = banco_falso()
        add_item(self.con, 1, "AAAA1111", "journalArticle")
        add_tag(self.con, 1, "aflatoxins", tipo=1)
        add_tag(self.con, 1, "Warehousing & storage", tipo=1)
        add_tag(self.con, 1, "t-artigo")

    def podar(self, cfg=None):
        cfg = cfg or self.cfg
        alvo = z.keywords_a_podar(self.con, cfg)
        return alvo, z.podar_keywords(self.con, alvo)

    def test_apaga_as_automaticas(self):
        self.podar()
        self.assertEqual(z.contar_automaticas(self.con), 0)

    def test_nao_encosta_no_vocabulario(self):
        """A poda e so do type=1; as tags dos eixos sao type=0 e ficam."""
        self.podar()
        nomes = {n for (n,) in self.con.execute(
            "SELECT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID")}
        self.assertEqual(nomes, {"t-artigo"})

    def test_preservada_sobrevive(self):
        cfg = dict(self.cfg, keywords_preservadas=["aflatoxins"])
        self.podar(cfg)
        nomes = {n for (n,) in self.con.execute(
            """SELECT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID
               WHERE it.type = 1""")}
        self.assertEqual(nomes, {"aflatoxins"})

    def test_desligada_no_configs_nao_apaga(self):
        cfg = dict(self.cfg, podar_automaticas=False)
        alvo, _ = self.podar(cfg)
        self.assertEqual(alvo, [])
        self.assertEqual(z.contar_automaticas(self.con), 2)

    def test_linha_orfa_em_tags_some(self):
        self.podar()
        sobrou = {n for (n,) in self.con.execute("SELECT name FROM tags")}
        self.assertNotIn("aflatoxins", sobrou)
        self.assertIn("t-artigo", sobrou)

    def test_item_que_perdeu_keyword_fica_synced_zero(self):
        _, tocados = self.podar()
        self.assertEqual(tocados, {1})
        self.assertEqual(self.con.execute(
            "SELECT synced FROM items WHERE itemID = 1").fetchone()[0], 0)

    def test_conferir_reclama_se_sobrou_automatica(self):
        self.assertTrue(z.conferir(self.con, self.cfg))
        self.podar()
        self.assertEqual(z.conferir(self.con, self.cfg), [])


class TestArquivos(unittest.TestCase):
    def test_pega_o_csv_mais_recente(self):
        with tempfile.TemporaryDirectory() as pasta:
            for nome in ("zotero_relevancia_2026-08-01.csv",
                         "zotero_relevancia_2026-08-19.csv",
                         "zotero_relevancia_2026-07-05.csv"):
                open(os.path.join(pasta, nome), "w").close()
            cfg = {"dir_csv": pasta, "padrao_csv": "zotero_relevancia_*.csv"}
            self.assertTrue(z.csv_mais_recente(cfg).endswith("2026-08-19.csv"))

    def test_sem_csv_devolve_none(self):
        with tempfile.TemporaryDirectory() as pasta:
            cfg = {"dir_csv": pasta, "padrao_csv": "zotero_relevancia_*.csv"}
            self.assertIsNone(z.csv_mais_recente(cfg))

    def test_mapa_indexa_por_chave_e_ignora_comentario(self):
        with tempfile.TemporaryDirectory() as pasta:
            os.mkdir(os.path.join(pasta, "pessoal"))
            with open(os.path.join(pasta, "pessoal", "regioes.json"), "w") as arq:
                json.dump({"_comentario": "nao e tag",
                           "g-brasil": ["AAAA1111", "BBBB2222"],
                           "g-am-sul": ["AAAA1111"]}, arq)
            antigo = z.DIR_SCRIPT
            try:
                z.DIR_SCRIPT = pasta
                mapa, _ = z.carregar_mapa("regioes.json")
            finally:
                z.DIR_SCRIPT = antigo
        self.assertEqual(mapa["AAAA1111"], {"g-brasil", "g-am-sul"})
        self.assertNotIn("_comentario", str(mapa))

    def test_eixos_manuais_se_somam_no_mesmo_item(self):
        """regiao e tema convivem: o item recebe as duas familias."""
        with tempfile.TemporaryDirectory() as pasta:
            os.mkdir(os.path.join(pasta, "pessoal"))
            with open(os.path.join(pasta, "pessoal", "regioes.json"), "w") as arq:
                json.dump({"g-brasil": ["AAAA1111"]}, arq)
            with open(os.path.join(pasta, "pessoal", "temas.json"), "w") as arq:
                json.dump({"k-bovinos": ["AAAA1111"], "k-ml": ["AAAA1111"]}, arq)
            antigo = z.DIR_SCRIPT
            try:
                z.DIR_SCRIPT = pasta
                juntos, caminhos = z.carregar_manuais(
                    {"eixos_manuais": {"regiao": "regioes.json", "tema": "temas.json"}})
            finally:
                z.DIR_SCRIPT = antigo
        self.assertEqual(juntos["AAAA1111"], {"g-brasil", "k-bovinos", "k-ml"})
        self.assertEqual(len(caminhos), 2)

    def test_eixo_manual_ausente_nao_quebra(self):
        with tempfile.TemporaryDirectory() as pasta:
            antigo = z.DIR_SCRIPT
            try:
                z.DIR_SCRIPT = pasta
                juntos, caminhos = z.carregar_manuais(
                    {"eixos_manuais": {"tema": "temas.json"}})
            finally:
                z.DIR_SCRIPT = antigo
        self.assertEqual(juntos, {})
        self.assertFalse(caminhos["tema"][1])

    def test_chave_morta_em_regioes_nao_quebra(self):
        """Item apagado no Zotero nao pode derrubar o run inteiro."""
        cfg = z.carregar_config(z.CONFIGS_PADRAO)
        con = banco_falso()
        add_item(con, 1, "AAAA1111", "journalArticle")
        itens = z.coletar_itens(con, cfg)
        plano = z.planejar(con, itens, {}, {"MORTA999": {"g-asia"}}, cfg)
        self.assertEqual(len(plano), 1)
        self.assertNotIn("g-asia", plano[0][1])


class TestFaxina(unittest.TestCase):
    def setUp(self):
        self.cfg = z.carregar_config(z.CONFIGS_PADRAO)
        self.con = banco_falso()

    def achar(self):
        return z.faxina(z.coletar_itens(self.con, self.cfg), self.cfg)

    def test_mesmo_doi_em_dois_itens(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle", titulo="A", doi="10.1/x")
        add_item(self.con, 2, "BBBB2222", "journalArticle", titulo="B", doi="10.1/x")
        self.assertEqual(len(self.achar()["duplicado_doi"]), 1)

    def test_titulo_igual_sem_doi(self):
        add_item(self.con, 1, "AAAA1111", "journalArticle", titulo="Capim capeta")
        add_item(self.con, 2, "BBBB2222", "journalArticle", titulo="CAPIM, CAPETA!")
        self.assertEqual(len(self.achar()["duplicado_titulo"]), 1)

    def test_pagina_web_com_pdf_e_suspeita(self):
        """Foi assim que 3 artigos entraram catalogados como pagina web."""
        add_item(self.con, 1, "AAAA1111", "webpage", titulo="Frontiers | Maize")
        self.con.execute("INSERT INTO itemAttachments VALUES (9, 1, 'application/pdf')")
        self.assertEqual(len(self.achar()["tipo_suspeito"]), 1)

    def test_pagina_web_sem_pdf_nao_e_suspeita(self):
        add_item(self.con, 1, "AAAA1111", "webpage", titulo="Perfil no Escavador")
        self.assertEqual(self.achar()["tipo_suspeito"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

