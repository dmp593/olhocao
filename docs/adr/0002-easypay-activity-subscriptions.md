# ADR 0002: subscrições Easypay para ATL e treino

- Estado: aceite; propriedade do catálogo atualizada pelo ADR 0003
- Data: 2026-09-02

## Contexto

Os artigos ATL, treino e obediência podem representar packs de sessões ou acesso
recorrente com um número máximo de utilizações por semana. O TOConline não gere
agenda, utilização nem pagamento online.

## Decisão

O OlhóCão mantém a configuração operacional explícita (`ServiceOffering`),
agenda sessões e mantém contratos, reservas e um ledger de créditos append-only.
O ADR 0003 passou também a autoria do artigo e do preço para o site.

Packs usam Pay by Link `SINGLE`. Planos recorrentes usam o endpoint oficial
Easypay `/subscription`, que devolve uma página alojada para autorização do
cartão. O site nunca recebe dados de cartão. Cada webhook é deduplicado e
confirmado por consulta à API antes de conceder acesso ou emitir uma FR.

## Consequências

- não existe um segundo catálogo no fornecedor de pagamentos;
- limites semanais, capacidade e acesso exclusivo são regras locais
  testáveis;
- cada renovação paga origina um ciclo local e uma FR idempotente;
- é obrigatório configurar na Easypay permissões de subscrição e os webhooks
  `subscription_create` e `subscription_capture`.

O Pay by Link genérico documenta o objeto `SUBSCRIPTION`, mas o contrato OpenAPI
assinala-o como não implementado. Por isso a recorrência usa `/subscription` e
o URL de autorização devolvido pela própria Easypay.
