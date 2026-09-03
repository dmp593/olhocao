# ADR 0003: catálogo autoritativo no site e TOConline assíncrono

- Estado: aceite
- Data: 2026-09-02

## Contexto

O TOConline não representa imagens nem as relações necessárias entre hotel,
extras, ATL, treino, packs, subscrições e acesso a sessões. Usá-lo como catálogo
autoritativo obrigava a regras externas ou hardcodes como “Premium”. Além disso,
uma indisponibilidade do ERP não pode impedir uma reserva ou cobrança.

A [API de produtos e serviços](https://api-docs.toconline.pt/apis/empresa/produtos-e-servicos)
documenta a criação de artigos, mas não oferece metadata arbitrária nem um
contrato geral para atualizar todos os seus dados comerciais. A
[API de documentos de venda](https://api-docs.toconline.pt/apis/vendas/documentos-de-venda)
aceita linhas sem `item_id`.

## Decisão

A hierarquia local `CatalogType -> CatalogLevel -> CatalogOffer` é a fonte de
verdade comercial. O tipo tem um nome dinâmico e um `CatalogWorkflow` estável
(`DATE_RANGE`, `FULL_DAY_SLOT`, `TIMED_SLOT` ou `FLEXIBLE`) que descreve apenas
o comportamento. O nível é opcional; a oferta é o produto ou plano final.
Na interface de gestão esta folha vendável chama-se `preço ou plano`, uma vez
que `oferta` seria ambíguo para quem gere o catálogo.

Cada `CatalogOffer` aponta para um `CatalogItem`, que é somente a sua projeção
fiscal versionada. Imagens, visibilidade, relações entre serviços, capacidade e
políticas operacionais continuam locais. `ServiceOffering` acrescenta os termos
de compra avulsa, pack ou subscrição. Nomes comerciais nunca controlam regras.
`AddOnAvailability` associa um serviço flexível avulso a um tipo ou nível de
destino, permitindo reutilizar extras sem duplicar o artigo fiscal.

Cada artigo criado pelo site recebe um código reservado `OC-S-*` ou `OC-P-*`.
O código funciona como origem explícita no TOConline. A publicação é idempotente:
procura primeiro o código e só depois cria.

Alterar nome, preço, IVA ou tipo fiscal de uma versão já publicada cria um
sucessor e desativa localmente a versão anterior. Transações mantêm snapshots e
nunca são apagadas ou reescritas. Alterar apenas a designação de um tipo, nível
ou plano não altera o workflow nem a elegibilidade operacional.

Pedidos web só gravam estado local. Clientes, catálogo e documentos fiscais são
sincronizados por jobs. Falhas ficam registadas como pendentes/falhadas e são
repetidas. Documentos usam linhas livres com os snapshots quando o artigo ainda
não tem ID remoto.

## Consequências

- existe um único backoffice para toda a configuração comercial e operacional;
- o site, reservas e Easypay continuam disponíveis durante falhas do TOConline;
- divergências remotas são visíveis e não substituem dados locais;
- cada alteração fiscal publicada acrescenta uma versão em vez de mutar o
  histórico;
- a plataforma precisa de executar e monitorizar os jobs de reconciliação;
- a emissão fiscal pode ficar temporariamente pendente após um pagamento, mas o
  estado pago nunca é revertido.
