# Promoções e ajustes de preço

Este guia descreve as regras comerciais e financeiras implementadas pelo
OlhóCão. O objetivo é permitir ofertas flexíveis sem ligar comportamento a
nomes como “Premium” e sem reescrever compras, pagamentos ou documentos já
existentes.

## Dois mecanismos distintos

| Mecanismo | Utilização | Quem aplica | Auditoria |
| --- | --- | --- | --- |
| Código promocional | Campanha reutilizável com regras e limites | Cliente ou colaborador no checkout | configuração, reservas e utilizações |
| Ajuste de preço | Exceção individual numa reserva de hotel | Apenas staff, antes de captura | valor anterior, novo valor, motivo e autor |

Um código promocional nunca é usado para esconder uma decisão manual. Um
ajuste manual nunca altera o catálogo nem os limites de uma campanha.

## Contextos comerciais estáveis

As promoções selecionam fluxos internos, e não texto do nome comercial:

- intervalo de datas;
- pack;
- subscrição;
- compra avulsa;
- serviço opcional associado a outra reserva.

Assim, mudar “Premium” para “GOLD” ou “FreePass” não altera a elegibilidade. A
relação vem da configuração do artigo e da oferta no catálogo.

## Regras de elegibilidade

Uma promoção tem sempre pelo menos um contexto. As restantes restrições são
opcionais:

- artigos específicos do catálogo;
- clientes específicos;
- cães específicos;
- raças específicas, pesquisadas entre os cães registados e comparadas sem
  diferença entre maiúsculas e minúsculas;
- início e fim do período em que o código pode ser introduzido;
- primeiro e último dia elegível do serviço, apenas para estadias ou serviços
  opcionais ligados a uma reserva com data;
- subtotal elegível mínimo;
- limite global de utilizações;
- limite de utilizações por cliente.

Restrições preenchidas acumulam-se. Por exemplo, selecionar um cliente, uma
raça e “extras de hotel” exige que as três condições sejam verdadeiras. Numa
compra com várias linhas, apenas as linhas compatíveis recebem desconto. Uma
janela de estadia exige que todas as noites dessa linha caibam no intervalo; o
checkout é uma data exclusiva e não conta como noite.

Os campos de tipo, nível e preço/plano escolhem **o artigo que recebe o
desconto**. Para um serviço opcional, o contexto do serviço principal é uma
restrição separada: permite, por exemplo, descontar grooming apenas quando foi
adicionado a Hotel. Selecionar Hotel nesse contexto não desconta o alojamento.

As datas de disponibilidade do código respondem a “quando pode ser usado no
checkout”. As datas do serviço respondem a “quando tem de acontecer o cuidado”.
São conceitos diferentes; o segundo fica numa secção avançada e não é aceite
em packs, subscrições ou compras avulsas sem data. O subtotal mínimo está em
euros e considera apenas as linhas elegíveis, antes do desconto.

Clientes, cães e raças usam pesquisa paginada no backoffice. O formulário só
renderiza as seleções atuais e carrega candidatos em páginas de 20 registos,
evitando listas gigantes sem alterar a validação no servidor.

O desconto pode ser percentual ou um valor fixo. Nunca ultrapassa o subtotal
elegível. Quando abrange várias linhas, cada cêntimo é distribuído de forma
determinística e a soma das parcelas é sempre igual ao desconto apresentado.

## Ciclo de uma utilização

```text
checkout confirmado
        |
        v
reserved -- pagamento/cortesia confirmada --> consumed
   |
   +-- compra cancelada/abandonada de forma explícita --> released
```

Uma utilização `reserved` já conta para os limites. Isto impede duas compras
concorrentes de consumirem o último código disponível. A reserva passa a
`consumed` apenas depois de captura confirmada ou de uma reserva de hotel
gratuita confirmada. Uma compra cancelada antes de pagar liberta a utilização.

O registo de utilização é append-only. Libertar uma reserva não apaga a linha:
muda o seu estado e preserva o histórico. Uma utilização consumida nunca é
libertada nem reutilizada retroativamente.

Cada compra guarda snapshots do código, nome, tipo, valor e montante exato do
desconto. Alterar ou desativar uma promoção só afeta compras futuras.

### Subscrições

Numa subscrição de ATL ou treino, o desconto faz parte do contrato adquirido e
é aplicado ao montante recorrente de todos os ciclos dessa subscrição. Uma
alteração posterior da promoção não muda esse contrato. Para mudar o preço
contratado, termina-se a subscrição segundo a política aplicável e cria-se uma
nova compra; os ciclos anteriores permanecem imutáveis.

O total de uma compra de atividade tem de respeitar o mínimo aceite para
pagamento online. Uma campanha que o reduza abaixo desse limite é rejeitada com
uma mensagem segura para contactar a equipa.

## Ajuste manual de uma reserva de hotel

Em `Gestão > Hotel > Reserva > Ajustar preço final`, staff pode reduzir o total
até `0,00 €` ou repor um valor anterior até ao subtotal original do catálogo.
O motivo é obrigatório e fica visível apenas no histórico financeiro da
gestão.

O serviço executa estas proteções pela mesma ordem:

1. bloqueia a reserva durante a alteração;
2. rejeita reservas pagas, contestadas, canceladas ou com documento emitido;
3. rejeita valores negativos, superiores ao catálogo ou entre `0,01 €` e o
   mínimo do pagamento online;
4. desativa links de pagamento existentes antes de trocar o preço;
5. liberta um código ainda reservado;
6. distribui o ajuste pelas linhas e grava snapshots;
7. acrescenta um registo imutável com autor e motivo.

`0,00 €` confirma a reserva como cortesia. Não cria uma transação falsa, não
chama a Easypay e não emite fatura-recibo de pagamento. Se for necessário
cobrar um valor acima do catálogo, deve criar-se/configurar-se o artigo ou
extra real; um ajuste comercial não serve para inventar linhas tributáveis.

Um link antigo pode, apesar da tentativa de desativação, ser pago numa corrida
de rede. A captura é reconhecida, mas não confirma a reserva com o preço
obsoleto: fica auditada e entra em reembolso automático.

## Fronteira fiscal

Sem captura confirmada não existe emissão automática de documento fiscal. A
regra é aplicada dentro do serviço de faturação e também nos comandos de
recuperação, tanto para hotel como para packs e ciclos de subscrição.

Depois de uma captura, a compra, as linhas, o preço aplicado e o documento são
históricos. Não se apagam nem editam. Uma devolução usa o reembolso da Easypay e
uma nota de crédito emitida apenas depois da confirmação desse reembolso.

O TOConline recebe o preço bruto do snapshot e o desconto através de
`settlement_expression` na linha. Se o TOConline estiver indisponível, a compra
e o pagamento continuam válidos e o documento fica pendente para um job
idempotente.

## Recuperação e casos excecionais

| Situação | Comportamento seguro |
| --- | --- |
| Código inválido, fora da data ou limite esgotado | checkout não cria a compra |
| Falha ao criar o link | compra permanece pendente e reutiliza a referência |
| Link pago depois de ajuste de preço | não confirma; reembolso total automático |
| Link pago depois do início da estadia | não confirma; FR da captura e reembolso total |
| Reembolso temporariamente indisponível | estado `refund_failed` e retry operacional |
| TOConline indisponível | documento pendente/falhado, sem reverter a captura |
| SMTP indisponível | estado financeiro preservado; erro registado para operação |

Os comandos `retry_easypay_refunds`, `retry_toconline_documents` e
`maintain_activities` recuperam efeitos externos. Devem correr com exclusão
mútua e alertas para estados falhados persistentes.

## Alterações futuras

Toda a regra de preço deve continuar nos serviços de domínio:

- `discounts/services.py` calcula, reserva, consome e liberta promoções;
- `hotel/services/pricing.py` aplica promoções e ajustes às reservas;
- `activities/services/discounts.py` liga ofertas aos contextos comerciais;
- os módulos `*/services/invoicing.py` traduzem snapshots para documentos.

Views e templates apresentam resultados; não devem recalcular descontos. Ao
adicionar um novo tipo de venda, crie primeiro um contexto estável e um
adaptador de linhas explícito. Não use nomes comerciais, IDs externos ou
condições em templates como regra de negócio.
