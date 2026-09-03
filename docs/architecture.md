# Arquitetura

O projeto é um monólito modular Django. A lógica de negócio do hotel vive em
`hotel/services`; views, models e clientes externos não decidem regras em
duplicado.

## Propriedade dos dados

| Dado | Fonte de verdade | Cópia local |
| --- | --- | --- |
| Produto, serviço, preço, IVA, imagem e utilização | OlhóCão | `CatalogItem` autoritativo |
| Projeção de artigo para faturação | TOConline | ID, fingerprint e estado de sincronização |
| Dados fiscais do cliente | OlhóCão | `CustomerProfile` autoritativo |
| Identificador fiscal do cliente e documentos emitidos | TOConline | ID e estado de sincronização |
| Reserva, datas, extras, notas e media | OlhóCão | Dados autoritativos locais |
| Regras ATL/treino, agenda e créditos | OlhóCão | Dados autoritativos locais |
| Link, captura e reembolso | Easypay | IDs, estados e eventos auditáveis |
| Subscrição e ciclos de cobrança | Easypay | Contrato, ciclos e estado local |
| Dados e regras aplicados a uma compra | Snapshot local | Imutável após criação |
| Promoções, elegibilidade e limites | OlhóCão | configuração e utilizações auditáveis |
| Conteúdo de apresentação, contactos e imagens públicas | OlhóCão | `frontoffice` autoritativo e bilingue |

## Fluxo do catálogo

```text
Gestão > Catálogo ----> CatalogItem local ----> preço público / reserva / pagamento
                              |
                              +---- estado pending/failed
                                         |
                                         v
                              sync_toconline_catalog
                                         |
                         +---------------+----------------+
                         |                                |
                      sucesso                         indisponível
                         |                                |
                  ID + fingerprint              mantém venda local e retry
```

O código `OC-S-*`/`OC-P-*` identifica artigos publicados pelo site sem depender
de metadata não documentada. A publicação consulta primeiro esse código, por
isso recupera de uma resposta perdida sem duplicar o artigo. Uma diferença
manual no TOConline fica `diverged`, visível no backoffice, e nunca substitui
silenciosamente o preço local.

Artigos antigos podem ser importados do TOConline. Permanecem ERP-managed até o
administrador criar um sucessor local. A disponibilidade de um artigo local não
depende do ERP; a de um importado depende de continuar presente remotamente.

Uma alteração fiscal a um artigo já publicado cria uma nova versão local e uma
nova projeção remota. Reservas, contratos e documentos antigos continuam ligados
à versão e aos snapshots originais.

## Fluxo financeiro

```text
reserva pending
   |
   +--> link Easypay pending --> webhook/reconciliação --> payment paid
                                                       --> booking paid
                                                       --> FR pendente no TOConline
                                                       --> email

cancelamento elegível
   |
   +--> booking cancelled --> link pendente desativado
   |
   +--> pagamento existente --> pedido de refund 85% Easypay
                                      |
                                      +--> refund confirmado --> NC 85% TOConline
```

Pagamento, documento e email são consistências independentes. Depois de a
Easypay confirmar uma captura, uma falha no TOConline ou email nunca reverte o
pagamento. Os comandos de retry completam os efeitos pendentes.

Uma nota de crédito nunca é emitida apenas porque foi pedido um reembolso. O
documento corretivo nasce depois da confirmação da Easypay; estados pendentes,
falhas e webhooks finais perdidos são reconciliados pelos jobs.

Uma linha de venda TOConline pode ser criada com descrição, preço e IVA do
snapshot mesmo quando a projeção do artigo ainda não tem ID remoto. Assim, o job
de documentos e o job de catálogo podem recuperar em qualquer ordem.

Numa reserva ou compra assistida, o colaborador nunca entra no checkout em nome
do cliente. O site cria o link alojado sem pedir ao fornecedor que duplique a
notificação, envia-o para o email do cliente e regressa ao detalhe operacional.
O URL do checkout não é renderizado no backoffice. O cliente é sempre quem abre
o link e autoriza o pagamento.

Promoções usam contextos de venda estáveis e guardam uma utilização explícita
em `PromotionRedemption`. Uma utilização pendente reserva capacidade; uma
captura ou cortesia confirmada consome-a; um cancelamento antes de pagar
liberta-a sem apagar o histórico. Ajustes individuais de hotel são um mecanismo
separado, exigem staff e motivo, e só podem ocorrer antes de captura/documento.
Consulte o [guia de promoções](discounts-and-price-adjustments.md).

## ATL, treino e obediência

```text
CatalogType (nome dinâmico + workflow estável)
        |
        +--> CatalogLevel (opcional; capacidade/política/imagem próprias)
        |
        +--> CatalogOffer (preço ou plano final; nome interno do modelo)
                  |
                  +--> CatalogItem (projeção fiscal versionada no TOConline)
                  |
                  +--> ServiceOffering (compra avulsa/pack/subscrição)
                              |
                              +--> pack Easypay SINGLE --> ledger de créditos
                              |
                              +--> subscrição Easypay --> período + limite semanal
                                                          |
                                                          +--> ciclo pago --> FR TOConline

CatalogOffer flexível e avulso
        +--> AddOnAvailability --> tipo/nível onde surge como serviço opcional

ActivitySession
        +--> níveis elegíveis explícitos
        +--> capacidade e treinadores fixados na sessão
        +--> ActivityReservation --> eventos imutáveis de operação
```

O ledger é append-only: compras e cancelamentos acrescentam crédito; reservas
retiram crédito. Subscrições não simulam créditos: aplicam diretamente o limite
semanal contratado, sem acumular utilizações não usadas. Sessões restritas
referenciam explicitamente os níveis elegíveis; nunca inferem acesso a partir de
nomes como Premium, GOLD ou FreePass. Alterar o nome comercial não altera regras
nem reservas existentes.

Alterações de data ou hora notificam os clientes com reserva e ficam registadas
com o período anterior e o novo. Cancelar uma sessão exige motivo, cancela cada
reserva de forma auditável, devolve os créditos aplicáveis e envia notificações.
Sessões canceladas e os respetivos eventos nunca são apagados.

Os serviços opcionais são configurados numa área central do catálogo. Uma
associação ao tipo é herdada pelos níveis; uma associação ao nível aplica-se a
todos os preços e planos desse nível. A remoção desativa a relação sem eliminar
o histórico. Consulte o [guia do catálogo](catalog-and-optional-services.md).

## Limites transacionais

- A criação de reservas e respetivos snapshots é atómica.
- Só pode existir uma tentativa de pagamento pendente por reserva.
- Webhooks são deduplicados por fingerprint e voltam a consultar a Easypay antes
  de aceitar valor, moeda, referência e captura.
- O cancelamento local é persistido antes de chamar fornecedores externos. Uma
  falha externa fica visível para operação e é repetível.
- IDs externos e referências comerciais são únicos/idempotentes.
- A aplicação web apenas grava estados pendentes; nenhuma criação de catálogo ou
  cliente fica bloqueada por chamadas ao ERP.
- Lotação, crédito e limite semanal são verificados dentro de transações com
  bloqueio dos recursos relevantes.
- Uma sessão com reservas não pode mudar de tipo, excluir níveis já reservados
  nem reduzir a capacidade abaixo dos lugares ocupados.

## Segurança e privacidade

- Autorização de reserva é sempre filtrada por conta; staff tem âmbito global.
- O backoffice apenas envia pedidos de pagamento; nunca expõe o checkout do
  cliente ao colaborador.
- Webhooks exigem Basic Auth e nunca confiam apenas no payload recebido.
- Uploads validam extensão, MIME declarado, assinatura do ficheiro, tamanho,
  quantidade de chunks e nomes.
- Media de reservas é descarregada por uma view autenticada. O proxy não pode
  expor `/media/stay_media/` diretamente.
- Segredos pertencem ao ambiente, nunca à base de código.
- Django aplica HTTPS/cookies/HSTS em produção, CSP com nonce por resposta,
  `Permissions-Policy`, CSRF
  e proteção de clickjacking.

## Sistema de interface

Os templates usam três layouts finos sobre a mesma base: `public.html` para o
site comercial, `customer_portal.html` para a área autenticada e
`backoffice.html` para operações. As duas áreas de trabalho herdam de
`patterned.html`, que aplica o fundo temático uma única vez e evita diferenças
entre páginas.

Elementos recorrentes vivem em `olhocao/templates/ui`: cabeçalhos, cartões,
estados vazios, campos de formulário, indicadores, confirmações e o progresso
da reserva. Botões, badges, alertas, tabelas e campos partilham as classes
`ui-*` definidas em `styles.css`. JavaScript serve apenas interações pequenas e
progressivas; formulários e ações críticas continuam funcionais no servidor.

A família Bricolage Grotesque é servida localmente em WOFF2, com licença OFL no
repositório. É usada com tamanho ótico automático para combinar personalidade
nos títulos e legibilidade no texto, sem dependência de fontes externas em
produção.

Textos, imagens e listas de apresentação editáveis pertencem ao módulo
`frontoffice`; catálogo, preços, reservas e pagamentos continuam nos respetivos
módulos de negócio. Esta fronteira impede que uma alteração editorial possa
alterar uma transação. Consulte o [guia de conteúdo público](frontoffice-content.md).

## Estrutura relevante

```text
hotel/
  booking_views.py       adaptação HTTP e autorização
  media_views.py         upload e leitura privada
  models.py              persistência e invariantes simples
  services/
    bookings.py          regras de reserva/cancelamento
    catalog.py           publicação e reconciliação TOConline
    invoicing.py         documentos TOConline
    payments.py          Easypay e máquina de estados
  management/commands/   sincronização e recuperação operacional
  tests/                 testes por capacidade

activities/
  models.py              ofertas, contratos, ledger, sessões e reservas
  services/billing.py    Easypay, ciclos e efeitos idempotentes
  services/invoicing.py  FR no TOConline
  services/reservations.py regras de capacidade, acesso e cancelamento
  tests/                  testes do domínio ATL/treino

discounts/
  models.py              campanhas, snapshots, utilizações e auditoria
  services.py            elegibilidade, limites e alocação exata por linha
  tests/                  regras comerciais, concorrência e autorização

accounts/
  services/
    toconline_customers.py publicação assíncrona de clientes fiscais

frontoffice/
  models.py              conteúdo público bilingue e imagens geridas
  context_processors.py  contactos, parceiros e redes sociais partilhados

backoffice/
  site_content.py        registo explícito das coleções editoriais permitidas
```
