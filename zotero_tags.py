#!/usr/bin/env python3
"""Aplica no zotero.sqlite as tags derivaveis da biblioteca.

Cinco eixos, todos com prefixo proprio para o seletor de tags do Zotero
agrupar sozinho:

    n-lido / n-aprofundado    estado de leitura
    t-*                       tipo de documento (artigo, mestrado, doutorado, outro)
    r-*                       relevancia, lida do CSV do zotero-relevancia
    g-*                       regiao do ESTUDO (nao a filiacao dos autores)
    k-*                       tema grosso (micotoxinas, bovinos, pastagem...)

Os tres primeiros saem de dado que ja esta no banco ou no CSV. Os dois ultimos
nao: decidir que um paper de autores chineses medindo Nelore e do Brasil exige
ler o PDF. Por isso vem de regioes.json e temas.json, que a skill preenche —
este script nunca adivinha nenhum dos dois.

Ele TAMBEM poda as keywords automaticas (itemTags.type = 1), que o tradutor do
Zotero importa do publisher. Nesta biblioteca eram 75 tags cobrindo 15 dos 64
itens, 74 delas num item so — nao filtram nada, e ainda vem com lixo de
classificacao comercial. Para nao voltarem, desligue tambem em
Editar > Configuracoes > Geral > "Marcar itens automaticamente com palavras-chave".

Uso:
    python3 zotero_tags.py                 # dry-run (padrao)
    python3 zotero_tags.py --aplicar
    python3 zotero_tags.py --faxina        # so o relatorio de catalogacao
    python3 zotero_tags.py --exportar-manuais  # semeia regioes.json e temas.json
"""

import argparse
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone

DIR_SCRIPT = os.path.dirname(os.path.abspath(__file__))
CONFIGS_PADRAO = os.path.join(DIR_SCRIPT, "configs.json")

# normalizar_titulo/limpar_doi ja existem no projeto irmao; duplicar as regras
# de normalizacao seria criar duas verdades sobre o que e "o mesmo titulo".
DIR_RELEVANCIA = os.path.expanduser("~/deving/zotero-relevancia")


def carregar_relevancia():
    sys.path.insert(0, DIR_RELEVANCIA)
    try:
        import zotero_relevancia
        return zotero_relevancia
    except ImportError:
        sys.exit("nao achei zotero_relevancia.py em %s" % DIR_RELEVANCIA)


rel = carregar_relevancia()


# --------------------------------------------------------------------------
# Guardas e infraestrutura
# --------------------------------------------------------------------------

def zotero_rodando(banco):
    """Copia fiel da guarda do zotero-chaves: o processo chama-se zotero-bin,
    e -x casa o nome exato (com -f o proprio comando daria falso positivo)."""
    if os.path.exists(banco + "-journal"):
        return "existe %s-journal (Zotero aberto ou fechado sujo)" % banco
    try:
        achou = subprocess.run(
            ["pgrep", "-x", "zotero-bin"], capture_output=True
        ).returncode == 0
    except FileNotFoundError:
        achou = False
    if achou:
        return "processo zotero-bin em execucao"
    return None


def carregar_config(caminho):
    with open(caminho) as arq:
        cfg = json.load(arq)
    for chave in ("banco", "dir_csv"):
        cfg[chave] = os.path.expanduser(cfg[chave])
    return cfg


def csv_mais_recente(cfg):
    """O CSV tem a data no nome; pegar o mais novo evita taguear com uma rodada
    velha depois de regerar."""
    achados = sorted(glob.glob(os.path.join(cfg["dir_csv"], cfg["padrao_csv"])))
    return achados[-1] if achados else None


def sem_acento(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


# --------------------------------------------------------------------------
# Leitura do banco
# --------------------------------------------------------------------------

def campo(con, item_id, nome):
    linha = con.execute(
        """SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID = d.valueID
           WHERE d.itemID = ? AND d.fieldID = (SELECT fieldID FROM fields WHERE fieldName = ?)""",
        (item_id, nome),
    ).fetchone()
    return linha[0] if linha else ""


def coletar_itens(con, cfg):
    """Itens de topo vivos. A lixeira do Zotero fica de fora."""
    ignorados = ",".join("?" * len(cfg["tipos_ignorados"]))
    linhas = con.execute(
        """SELECT i.itemID, i.key, t.typeName FROM items i
           JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
           WHERE t.typeName NOT IN (%s)
             AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
           ORDER BY i.itemID""" % ignorados,
        cfg["tipos_ignorados"],
    ).fetchall()
    itens = []
    for item_id, chave, tipo in linhas:
        itens.append({
            "id": item_id, "key": chave, "tipo": tipo,
            "titulo": campo(con, item_id, "title"),
            "doi": rel.limpar_doi(campo(con, item_id, "DOI")),
            "thesis": sem_acento(campo(con, item_id, "thesisType")),
            "anexos": con.execute(
                "SELECT COUNT(*) FROM itemAttachments WHERE parentItemID = ?", (item_id,)
            ).fetchone()[0],
            "pdfs": con.execute(
                """SELECT COUNT(*) FROM itemAttachments
                   WHERE parentItemID = ? AND contentType = 'application/pdf'""",
                (item_id,),
            ).fetchone()[0],
        })
    return itens


def tags_manuais(con, item_id):
    return {
        nome for (nome,) in con.execute(
            """SELECT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID
               WHERE it.itemID = ? AND it.type = 0""",
            (item_id,),
        )
    }


# --------------------------------------------------------------------------
# Alvo de tags
# --------------------------------------------------------------------------

def tag_de_tipo(item, cfg):
    if item["tipo"] == "journalArticle":
        return "t-artigo"
    if item["tipo"] != "thesis":
        return "t-outro"
    if any(p in item["thesis"] for p in cfg["tese_doutorado"]):
        return "t-doutorado"
    if any(p in item["thesis"] for p in cfg["tese_mestrado"]):
        return "t-mestrado"
    if any(p in item["thesis"] for p in cfg["tese_graduacao"]):
        return "t-outro"
    return None          # thesisType em branco ou estranho: quem decide e a skill


def tag_de_relevancia(linha):
    if linha is None:
        return None
    # sem_registro_openalex: o OpenAlex nao conhece o item.
    # fora_da_regua: conhece, mas e literatura cinzenta ou base de dados — a
    # regua bibliometrica nao sabe medir. Nos dois casos a resposta honesta e
    # "nao consigo avaliar", que e diferente de "avaliei e e fraco" (r-d).
    if ("sem_registro_openalex" in linha["flags"]
            or "fora_da_regua" in linha["flags"]):
        return "r-sem-metrica"
    tier = linha["tier"].strip().lower()
    return "r-" + tier if tier else None


def marcador_imaturo(linha):
    """Nota provisoria: o FWCI existe mas foi descartado por imaturidade."""
    if linha is None or "fwci_imaturo" not in linha["flags"]:
        return False
    try:
        return float(linha["fwci"] or 0.0) < rel.CORTE_FWCI_IMATURO_UTIL
    except ValueError:
        return False


def alvo_do_item(item, atuais, linha_csv, manuais, cfg):
    """Conjunto de tags do vocabulario que este item DEVE ter no fim."""
    alvo = set()

    for negativa, positiva in cfg["pares"].items():
        if positiva not in atuais:
            alvo.add(negativa)

    tipo = tag_de_tipo(item, cfg)
    if tipo:
        alvo.add(tipo)

    relev = tag_de_relevancia(linha_csv)
    if relev:
        alvo.add(relev)
    if marcador_imaturo(linha_csv):
        alvo.add("r-imaturo")

    alvo |= manuais.get(item["key"], set())
    return alvo


def vocabulario(cfg):
    return {t for g in cfg["grupos"].values() for t in g["tags"]}


def planejar(con, itens, csv_por_key, manuais, cfg):
    """Devolve [(item, a_inserir, a_remover)] só para o que muda."""
    vocab = vocabulario(cfg)
    remover_sempre = set(cfg["remover"])
    plano = []
    for item in itens:
        atuais = tags_manuais(con, item["id"])
        alvo = alvo_do_item(item, atuais, csv_por_key.get(item["key"]), manuais, cfg)
        # So o vocabulario entra na conta: 'lido', 'livro', 'premiado' e as
        # keywords do publisher ficam de fora do diff.
        atuais_vocab = (atuais & vocab) | (atuais & remover_sempre)
        inserir = alvo - atuais_vocab
        remover = atuais_vocab - alvo
        if inserir or remover:
            plano.append((item, sorted(inserir), sorted(remover)))
    return plano


# --------------------------------------------------------------------------
# Faxina de catalogacao (so relatorio: quem decide e voce)
# --------------------------------------------------------------------------

def faxina(itens, cfg):
    achados = {"duplicado_doi": [], "duplicado_titulo": [], "tipo_suspeito": [],
               "sem_pdf": [], "sem_doi": []}

    por_doi, por_titulo = {}, {}
    for item in itens:
        if item["doi"]:
            por_doi.setdefault(item["doi"], []).append(item)
        chave = rel.normalizar_titulo(item["titulo"])
        if chave:
            por_titulo.setdefault(chave, []).append(item)

    for doi, grupo in por_doi.items():
        if len(grupo) > 1:
            achados["duplicado_doi"].append((doi, grupo))
    for titulo, grupo in por_titulo.items():
        if len(grupo) > 1 and not any(i["doi"] for i in grupo):
            achados["duplicado_titulo"].append((titulo, grupo))

    for item in itens:
        # Pagina web com PDF anexado costuma ser artigo salvo pelo connector
        # com a pagina aberta em vez do registro. Aconteceu 3x na biblioteca.
        if item["tipo"] in cfg["tipos_suspeitos_de_paper"] and item["pdfs"]:
            achados["tipo_suspeito"].append(item)
        if not item["anexos"]:
            achados["sem_pdf"].append(item)
        if not item["doi"] and item["tipo"] == "journalArticle":
            achados["sem_doi"].append(item)
    return achados


def imprimir_faxina(achados):
    rotulos = {
        "duplicado_doi": "MESMO DOI em mais de um item",
        "duplicado_titulo": "MESMO TITULO, sem DOI para desempatar",
        "tipo_suspeito": "pagina web com PDF anexado (artigo salvo errado?)",
        "sem_pdf": "sem nenhum anexo",
        "sem_doi": "artigo sem DOI",
    }
    total = 0
    for chave, rotulo in rotulos.items():
        grupo = achados[chave]
        if not grupo:
            continue
        total += len(grupo)
        print("\n--- %s (%d) ---" % (rotulo, len(grupo)))
        for entrada in grupo:
            if chave.startswith("duplicado"):
                valor, itens_dup = entrada
                print("  %s" % valor[:60])
                for item in itens_dup:
                    print("      #%-4d %s" % (item["id"], item["titulo"][:60]))
            else:
                print("  #%-4d %-14s %s" % (entrada["id"], entrada["tipo"],
                                            entrada["titulo"][:56]))
    if not total:
        print("\nFaxina: nada a reportar.")
    return total


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------

def tag_id(con, nome):
    linha = con.execute("SELECT tagID FROM tags WHERE name = ?", (nome,)).fetchone()
    if linha:
        return linha[0]
    cur = con.execute("INSERT INTO tags (name) VALUES (?)", (nome,))
    return cur.lastrowid


def aplicar(con, plano):
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for item, inserir, remover in plano:
        for nome in remover:
            con.execute(
                "DELETE FROM itemTags WHERE itemID = ? AND tagID = ? AND type = 0",
                (item["id"], tag_id(con, nome)),
            )
        for nome in inserir:
            con.execute(
                "INSERT OR IGNORE INTO itemTags (itemID, tagID, type) VALUES (?, ?, 0)",
                (item["id"], tag_id(con, nome)),
            )
        # Sem synced=0 o zotero.org nao sobe a mudanca.
        con.execute(
            """UPDATE items SET synced = 0, clientDateModified = ?, dateModified = ?
               WHERE itemID = ?""",
            (agora, agora, item["id"]),
        )


def keywords_a_podar(con, cfg):
    """itemTags.type = 1 sao as keywords que o tradutor do Zotero importa do
    publisher. Nesta biblioteca cobriam 15 dos 64 itens, 74 delas num item so:
    nao filtram nada e ainda trazem lixo de classificacao comercial.

    Devolve [(tagID, nome, [itemID...])] do que sairia.
    """
    if not cfg.get("podar_automaticas"):
        return []
    preservadas = set(cfg.get("keywords_preservadas", []))
    por_tag = {}
    for tag_id, nome, item_id in con.execute(
        """SELECT t.tagID, t.name, it.itemID FROM itemTags it
           JOIN tags t ON t.tagID = it.tagID WHERE it.type = 1 ORDER BY t.name"""
    ):
        if nome in preservadas:
            continue
        por_tag.setdefault((tag_id, nome), []).append(item_id)
    return [(tid, nome, itens) for (tid, nome), itens in por_tag.items()]


def podar_keywords(con, alvo):
    """Apaga as automaticas e a linha orfa em tags. O item recebe synced = 0
    porque a tag viaja dentro do JSON dele na sincronizacao."""
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    tocados = set()
    for tag_id, _, itens in alvo:
        con.execute("DELETE FROM itemTags WHERE tagID = ? AND type = 1", (tag_id,))
        sobrou = con.execute(
            "SELECT COUNT(*) FROM itemTags WHERE tagID = ?", (tag_id,)
        ).fetchone()[0]
        if not sobrou:
            con.execute("DELETE FROM tags WHERE tagID = ?", (tag_id,))
        tocados.update(itens)
    con.executemany(
        """UPDATE items SET synced = 0, clientDateModified = ?, dateModified = ?
           WHERE itemID = ?""",
        [(agora, agora, i) for i in sorted(tocados)],
    )
    return tocados


def conferir(con, cfg):
    """Invariantes que nao podem quebrar depois de escrever."""
    problemas = []
    for nome, grupo in cfg["grupos"].items():
        if not grupo["exclusivo"]:
            continue
        marcas = ",".join("?" * len(grupo["tags"]))
        conflitos = con.execute(
            """SELECT it.itemID FROM itemTags it JOIN tags t ON t.tagID = it.tagID
               WHERE t.name IN (%s) GROUP BY it.itemID HAVING COUNT(*) > 1""" % marcas,
            grupo["tags"],
        ).fetchall()
        if conflitos:
            problemas.append("%s: itens com mais de uma tag: %s"
                             % (nome, [c[0] for c in conflitos]))
    if cfg.get("podar_automaticas"):
        preservadas = set(cfg.get("keywords_preservadas", []))
        sobraram = {
            nome for (nome,) in con.execute(
                """SELECT DISTINCT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID
                   WHERE it.type = 1"""
            )
        } - preservadas
        if sobraram:
            problemas.append("keywords automaticas nao podadas: %s" % sorted(sobraram)[:5])
    integridade = con.execute("PRAGMA integrity_check").fetchone()[0]
    if integridade != "ok":
        problemas.append("integrity_check: %s" % integridade)
    return problemas


def contar_automaticas(con):
    return con.execute("SELECT COUNT(*) FROM itemTags WHERE type = 1").fetchone()[0]


# --------------------------------------------------------------------------
# Regioes
# --------------------------------------------------------------------------

def carregar_mapa(arquivo):
    """Le um eixo manual (regiao, tema): JSON de tag -> [item_key]."""
    caminho = os.path.join(DIR_SCRIPT, arquivo)
    if not os.path.exists(caminho):
        return {}, caminho
    with open(caminho) as arq:
        bruto = json.load(arq)
    por_key = {}
    for tag, chaves in bruto.items():
        if tag.startswith("_"):
            continue
        for chave in chaves:
            por_key.setdefault(chave, set()).add(tag)
    return por_key, caminho


def carregar_manuais(cfg):
    """Junta todos os eixos manuais num mapa so: item_key -> {tags}."""
    juntos, caminhos = {}, {}
    for grupo, arquivo in cfg["eixos_manuais"].items():
        mapa, caminho = carregar_mapa(arquivo)
        caminhos[grupo] = (caminho, bool(mapa))
        for chave, tags in mapa.items():
            juntos.setdefault(chave, set()).update(tags)
    return juntos, caminhos


COMENTARIO_MANUAL = {
    "regiao": ("Regiao do ESTUDO, nao da filiacao dos autores. A skill preenche "
               "lendo o PDF; o motor nunca adivinha."),
    "tema": ("Assunto grosso do item. Serve para cruzar com os outros eixos; o "
             "recorte fino ja esta nas colecoes."),
}


def exportar_manuais(con, cfg):
    """Semeia os JSONs dos eixos manuais com o que ja esta no banco."""
    feitos = []
    for grupo, arquivo in cfg["eixos_manuais"].items():
        saida = {}
        for tag in cfg["grupos"][grupo]["tags"]:
            chaves = [
                k for (k,) in con.execute(
                    """SELECT i.key FROM items i JOIN itemTags it ON it.itemID = i.itemID
                       JOIN tags t ON t.tagID = it.tagID
                       WHERE t.name = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
                       ORDER BY i.key""",
                    (tag,),
                )
            ]
            if chaves:
                saida[tag] = chaves
        caminho = os.path.join(DIR_SCRIPT, arquivo)
        saida["_comentario"] = COMENTARIO_MANUAL.get(grupo, "")
        with open(caminho, "w") as arq:
            json.dump(saida, arq, indent=2, ensure_ascii=False)
        feitos.append((caminho, sum(len(v) for k, v in saida.items()
                                    if not k.startswith("_"))))
    return feitos


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--configs", default=CONFIGS_PADRAO)
    p.add_argument("--banco", default=None, help="sobrepoe o banco do configs.json")
    p.add_argument("--csv", default=None, help="sobrepoe o CSV mais recente")
    p.add_argument("--aplicar", action="store_true",
                   help="grava as mudancas (sem isso, so mostra o que faria)")
    p.add_argument("--faxina", action="store_true",
                   help="so o relatorio de catalogacao, sem mexer em tag")
    p.add_argument("--exportar-manuais", action="store_true",
                   help="escreve os JSONs dos eixos manuais a partir das tags do banco")
    args = p.parse_args()

    cfg = carregar_config(args.configs)
    banco = os.path.expanduser(args.banco) if args.banco else cfg["banco"]
    if not os.path.exists(banco):
        sys.exit("banco nao encontrado: %s" % banco)

    if args.aplicar:
        motivo = zotero_rodando(banco)
        if motivo:
            sys.exit("ABORTADO: feche o Zotero antes de aplicar — %s" % motivo)

    if args.aplicar:
        con = sqlite3.connect(banco)
    else:
        # Dry-run tem que rodar com o Zotero aberto — so o --aplicar exige ele
        # fechado. Conexao normal daria "database is locked"; immutable ignora o
        # lock e o journal, ao custo de nao ver escrita ainda nao commitada.
        con = sqlite3.connect("file:%s?immutable=1" % banco, uri=True)
    try:
        if args.exportar_manuais:
            for caminho, quantos in exportar_manuais(con, cfg):
                print("%d itens -> %s" % (quantos, caminho))
            return 0

        itens = coletar_itens(con, cfg)
        print("%d itens de topo (lixeira fora)" % len(itens))

        if args.faxina:
            imprimir_faxina(faxina(itens, cfg))
            return 0

        caminho_csv = args.csv or csv_mais_recente(cfg)
        if not caminho_csv:
            sys.exit("nenhum CSV em %s — rode o zotero_relevancia.py antes"
                     % cfg["dir_csv"])
        print("CSV: %s" % caminho_csv)
        import csv as _csv
        with open(caminho_csv, encoding="utf-8") as arq:
            csv_por_key = {l["item_key"]: l for l in _csv.DictReader(arq)}
        vivos = {i["key"] for i in itens}
        orfas = sorted(set(csv_por_key) - vivos)
        if orfas:
            print("aviso: %d chaves do CSV sem item vivo (%s...)"
                  % (len(orfas), ", ".join(orfas[:3])))

        manuais, caminhos = carregar_manuais(cfg)
        for grupo, (caminho, tem) in caminhos.items():
            if not tem:
                print("aviso: %s vazio ou ausente — nenhuma tag de %s sera aplicada"
                      % (caminho, grupo))
        mortas = sorted(set(manuais) - vivos)
        if mortas:
            print("aviso: %d chaves nos eixos manuais sem item vivo (%s...)"
                  % (len(mortas), ", ".join(mortas[:3])))
        sem_tema = [i["key"] for i in itens if not (manuais.get(i["key"], set())
                    & set(cfg["grupos"]["tema"]["tags"]))]
        if sem_tema:
            print("%d itens sem tema em temas.json (ok se nao se encaixam)"
                  % len(sem_tema))

        sem_tipo = [i for i in itens if tag_de_tipo(i, cfg) is None]
        for item in sem_tipo:
            print("  ! #%d tem thesisType %r: classifique a mao"
                  % (item["id"], item["thesis"]))

        plano = planejar(con, itens, csv_por_key, manuais, cfg)
        poda = keywords_a_podar(con, cfg)
        if poda:
            itens_poda = {i for _, _, lista in poda for i in lista}
            print("\npoda: %d keywords automaticas de %d itens"
                  % (len(poda), len(itens_poda)))
            print("  %s%s" % (", ".join(n for _, n, _ in poda[:8]),
                              ", ..." if len(poda) > 8 else ""))

        if not plano and not poda:
            print("\nTags em dia: nada a mudar.")
            imprimir_faxina(faxina(itens, cfg))
            return 0

        print("\n%d itens mudariam:" % len(plano))
        for item, inserir, remover in plano:
            marcas = " ".join(["+" + t for t in inserir] + ["-" + t for t in remover])
            print("  #%-4d %-42.42s %s" % (item["id"], item["titulo"], marcas))

        imprimir_faxina(faxina(itens, cfg))

        if not args.aplicar:
            print("\n[dry-run] nada foi gravado. Rode com --aplicar para valer.")
            return 0

        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = "%s.pre-tags.%s.bak" % (banco, carimbo)
        shutil.copy2(banco, backup)
        print("\nbackup -> %s" % backup)

        with con:
            aplicar(con, plano)
            podados = podar_keywords(con, poda)

        problemas = conferir(con, cfg)
        if problemas:
            print("\nATENCAO:", *problemas, sep="\n  ")
            print("restaure com: cp %s %s" % (backup, banco))
            return 1

        print("%d itens com tag atualizada; %d keywords podadas de %d itens."
              % (len(plano), len(poda), len(podados)))
        print("automaticas restantes: %d" % contar_automaticas(con))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

