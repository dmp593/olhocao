# ADR 0001: TOConline autoritativo e Easypay Pay by Link

- Estado: parcialmente substituído pelo ADR 0003
- Data: 2026-09-01

## Contexto

O projeto duplicava produtos manualmente no Stripe para obter imagens e
pagamento. Isto criava dois catálogos comerciais, mapeamento por metadata e
risco de preço divergente. O cliente final ainda não tinha criado uma conta
Stripe de produção. O TOConline não guarda as imagens e classificações de UI
necessárias ao site, nem processa pagamentos online.

## Decisão

Esta decisão escolheu a Easypay e retirou o catálogo do fornecedor de pagamentos.
A propriedade do catálogo foi posteriormente revista no ADR 0003. A reserva
continua a congelar os valores efetivamente aceites pelo cliente.

A Easypay fornece links de pagamento alojados com cartão, MB WAY e Multibanco.
Não se mantém catálogo no fornecedor de pagamentos. O código integra a sua API
através de um cliente isolado e confirma cada webhook consultando o pagamento na
API.

Em operações assistidas, o colaborador cria a reserva ou adesão e envia o link
para o email do cliente. O backoffice não abre nem apresenta esse URL: a
autorização do pagamento pertence sempre ao cliente.

Stripe e `dj-stripe` são removidos por completo. Como a aplicação nunca teve
dados de produção, as migrações do hotel são uma baseline limpa, sem campos de
compatibilidade.

## Consequências

Positivas:

- preços, IVA e artigos são editados num só sistema;
- imagens sobrevivem a atualizações e remoções no ERP;
- novos artigos aparecem automaticamente e exigem classificação explícita antes
  de poderem entrar numa reserva;
- o fornecedor de pagamentos pode ser substituído sem mudar catálogo ou regras
  de reserva;
- reservas antigas continuam legíveis e faturáveis através de snapshots.

Custos:

- o catálogo local tem de ser sincronizado periodicamente;
- o backoffice OlhóCão continua responsável por imagens e classificação visual;
- pagamentos, documentos e emails exigem reconciliação e retries, porque não
  existe uma transação distribuída entre fornecedores.

## Alternativas consideradas

- Manter Stripe Products: rejeitado por duplicar o catálogo e exigir mapeamento.
- Usar apenas TOConline: rejeitado porque não oferece checkout online.
- Integrar um checkout próprio: rejeitado pelo custo de segurança e manutenção;
  o link alojado reduz o âmbito de dados de pagamento tratado pelo site.
