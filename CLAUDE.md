# zotero-tags — notas para o agente

Leia `~/deving/zotero-tags/contexto.md` antes de mexer em qualquer coisa de relevância.
Ele guarda o julgamento (por que o FWCI pesa 35, por que `literatura_cinzenta`
não é demérito) que nem o CSV nem este código carregam.

## Regras que não se negociam

- **`itemTags.type = 1` é podado, não preservado.** Regra invertida em 19/08/2026:
  eram 75 keywords do publisher cobrindo 15 dos 64 itens, 74 num item só. O motor
  apaga todas a cada rodada. Tag que você criar tem que ser `type = 0` e estar no
  vocabulário do `configs.json`.
- **Região e tema não se adivinham.** Item novo fora do `regioes.json` ou do
  `temas.json`: leia o PDF e ache o local do experimento — filiação do autor não
  serve — e o assunto. Na dúvida, pergunte em vez de chutar.
- **Nunca invente `key`.** 8 caracteres base32 sem `0`, `1` e `O`; chave
  inventada quebra a sincronização com `'<chave>' is not a valid collection key`.
  O corretor mora aqui: `zotero_corrigir_chaves.py` (dry-run por padrão).
  Detalhe que parece bug e não é: `ALFABETO_VALIDO` aceita `L`, mas
  `ALFABETO_GERACAO` não gera — aceitamos o que o Zotero aceita e geramos só o
  que é seguro sob qualquer versão do regex dele.
- **A guarda de Zotero aberto usa `pgrep -x zotero-bin`**, não `zotero`. Já
  errei isso: `pgrep -x zotero` nunca dispara, e a proteção vira decoração.

## Ordem certa de execução

1. `cd ../zotero-relevancia && python3 zotero_relevancia.py` — regera o CSV.
   Com o TTL, só bate na rede o que venceu.
2. `python3 zotero_tags.py` — dry-run, mostra o diff e a faxina.
3. **Resolva a faxina antes de aplicar** — passo obrigatório, não opcional.
   Consertar o tipo de um item muda a tag `t-` dele, e um item que sai de
   `webpage` para `journalArticle` passa a ter DOI, entra no OpenAlex e ganha `r-`
   de verdade. Aplicar antes de consertar é garantir retrabalho.
4. Classifique região e tema dos itens que o dry-run listar como faltando.
5. `python3 zotero_tags.py --aplicar`.

## Ao adicionar tag nova ao vocabulário

Declare no `configs.json` (grupo certo, `exclusivo` certo) e escreva o teste
junto. Tag fora do `configs.json` é invisível para o motor: ele não cria, e
também não remove — o que é proposital, para não pisar nas tags manuais dele.

