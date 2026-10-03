# zotero-tags

Aplica no `~/Zotero/zotero.sqlite` as tags derivadas da biblioteca, e reporta o
que está mal catalogado. Stdlib pura, sem dependência.

```bash
python3 zotero_tags.py                    # dry-run (padrão): mostra o diff
python3 zotero_tags.py --aplicar
python3 zotero_tags.py --faxina           # só o relatório, sem tocar em tag
python3 zotero_tags.py --exportar-manuais # semeia regioes.json e temas.json do banco
python3 zotero_tags_test.py               # 40 testes

python3 zotero_corrigir_chaves.py         # dry-run: acha chave de colecao invalida
python3 zotero_corrigir_chaves.py --aplicar
python3 zotero_corrigir_chaves_test.py    # 13 testes
```

Irmão de [`zotero-relevancia`](../relevancia) (de onde vem o CSV e as
funções de normalização) e absorveu o antigo `zotero-chaves` (de onde veio
a guarda de Zotero aberto). O julgamento por trás das notas está em
`~/deving/zotero/tags/contexto.md`.

## Os cinco eixos

Cada prefixo vira um bloco no seletor de tags do Zotero, e eles se cruzam:
`g-brasil` + `t-doutorado` dá as teses da ESALQ; `r-a` + `n-lido` dá a fila de
leitura que mais importa.

| eixo | tags | de onde sai |
|---|---|---|
| estado | `n-lido`, `n-aprofundado` | ausência da tag positiva (`lido`, `aprofundado`) |
| tipo | `t-artigo`, `t-mestrado`, `t-doutorado`, `t-outro` | `itemTypeID` + campo `thesisType` |
| relevância | `r-a`…`r-d`, `r-sem-metrica`, `r-imaturo` | CSV mais recente do `zotero-relevancia` |
| região | `g-brasil`, `g-am-sul`, `g-am-norte`, `g-europa`, `g-asia`, `g-oceania`, `g-africa`, `g-global` | `regioes.json` |
| tema | `k-micotoxinas`, `k-bovinos`, `k-pastagem`, `k-daninhas`, `k-ml`, `k-eletronica` | `temas.json` |

**Região e tema são os eixos que o script não deduz.** Decidir que o *PointNet++* é da
China e que o *151 Use of an automated computer vision system* é do Brasil — mesmo
sendo NDSU no cabeçalho — exige ler o PDF e achar onde o gado estava. Quem
preenche o `regioes.json` é a skill `/zotero-tags`; o motor só aplica o que está
lá, e remove tag `g-` que não esteja mais no arquivo. O critério é **local do
estudo, nunca filiação dos autores**.

`--exportar-regioes` faz o caminho inverso: lê as tags `g-*` do banco e regrava o
JSON. Serve para semear o arquivo e para não perder a classificação manual.

## A poda das keywords automáticas

`itemTags.type = 1` são as keywords que o **Zotero nativo** importa do publisher ao
salvar um item — não é plugin. Nesta biblioteca eram **75 tags cobrindo 15 dos 64
itens**, 74 delas num item só: não filtravam nada, e vinham com lixo de
classificação comercial (*Electrical Contractors and Other Wiring Installation
Contractors*), duplicatas por caixa (`Computer Vision` / `computer vision`) e HTML
cru (`Kinect<sup>®</sup>`).

O motor poda todas a cada rodada, junto com a linha órfã em `tags`, e marca o item
como `synced = 0` — a tag viaja dentro do JSON do item na sincronização. Para não
voltarem, desligue também em **Editar → Configurações → Geral**, tirando "Marcar
itens automaticamente com palavras-chave e cabeçalhos de assunto"; sem isso, todo
item novo nasce com elas de novo.

`keywords_preservadas` no `configs.json` (hoje vazia) é a válvula de escape: nomeie
ali a keyword que valer a pena e ela sobrevive à poda.

## O que ele nunca toca

- **Tags protegidas** (`configs.json`): `lido`, `livro`, `premiado`. São
  marcadores pessoais fora do vocabulário de prefixos — nem cria, nem apaga.
- **A lixeira** (`deletedItems`) e itens filhos (anexo, nota, anotação).
- **Chaves.** Nenhuma é gerada; a do Zotero tem regra própria (8 caracteres
  base32 sem `0`, `1` e `O`) e quem conserta é o `zotero_corrigir_chaves.py`, aqui mesmo.

## Segurança

`--aplicar` aborta se o Zotero estiver aberto — checa o processo `zotero-bin`
(com `pgrep -x`, senão o próprio comando dá falso positivo) e o arquivo
`zotero.sqlite-journal`. Antes de escrever, copia o banco para
`zotero.sqlite.pre-tags.<timestamp>.bak`. Depois de escrever, confere que nenhum
item ficou com duas tags de um grupo exclusivo, roda `integrity_check` e compara
a contagem de `type=1`; se algo falhar, imprime o comando de restore e sai com 1.

Todo item tocado recebe `synced = 0` — sem isso o zotero.org não sobe a mudança.

## Faxina

Relatório, nunca conserto automático: quem decide é você (ou a skill, que
pergunta). Detecta:

- **mesmo DOI** em mais de um item, e **mesmo título normalizado** entre itens sem DOI;
- **página web ou revista de divulgação com PDF anexado** — assinatura de artigo
  salvo pelo connector com a página aberta em vez do registro. Aconteceu 3× nesta
  biblioteca (*Affordable 3D*, *Dung beetles*, *Maize intercropped*);
- **item sem nenhum anexo** e **artigo sem DOI**.

## configs.json

Todo o vocabulário mora lá: os grupos (e quais são exclusivos), os pares
negativa/positiva, as protegidas, os typos a remover (`aprodundado`), e as
palavras que separam mestrado de doutorado no `thesisType`. Mexa no JSON, não no
`.py`.

