# Pagamentos, subscrições e documentos fiscais

Este documento descreve as regras que não podem ser inferidas apenas a partir
dos ecrãs. A Easypay movimenta dinheiro, o TOConline emite documentos fiscais e
o OlhóCão mantém o estado operacional e o histórico imutável que liga ambos.

## Regras fundamentais

1. Um webhook nunca prova, por si só, que houve pagamento. O serviço consulta a
   API da Easypay e valida referência, valor, moeda e captura.
2. Referências comerciais e chaves de idempotência são persistidas antes da
   chamada externa. Um timeout pode, assim, ser repetido sem criar outra
   cobrança.
3. Confirmar dinheiro, emitir o documento e enviar email são efeitos
   independentes. Uma falha no TOConline ou SMTP nunca reverte um pagamento.
4. Reservas, tentativas, contratos, ciclos, movimentos de crédito e eventos
   financeiros não são apagados. Correções usam novos estados, reembolsos e
   notas de crédito.
5. Uma nota de crédito só é emitida depois de a Easypay confirmar o reembolso.
   Um pedido pendente ou falhado ainda não alterou fiscalmente a venda.
6. O backoffice nunca abre o checkout em nome do cliente. Numa operação
   assistida, envia o link alojado por email.

## Reserva de hotel

```text
reserva pendente
  -> criar/reutilizar link SINGLE
  -> cliente paga
  -> webhook ou reconciliação consulta a Easypay
  -> tentativa paga + reserva paga
  -> FR pendente no TOConline
  -> confirmação por email
```

- Só pode existir uma tentativa `pending` por reserva.
- Um link ainda válido é reutilizado. Um link expirado cria outra tentativa.
- Uma falha ou timeout durante a criação guarda `failed`; a repetição usa a
  mesma referência idempotente.
- Uma notificação de captura falhada e verificada é registada sem confirmar a
  reserva. O cliente pode tentar novamente em segurança.
- Uma captura recebida depois do cancelamento não reabre a reserva: emite a FR
  da cobrança real e solicita automaticamente o reembolso total.
- Uma captura recebida depois de a data inicial já ter passado também não
  confirma a estadia: a cobrança é documentada e reembolsada integralmente.
- Alterar o preço desativa primeiro o link anterior. Uma captura tardia nesse
  link obsoleto não confirma a reserva e entra em reembolso automático.
- Uma segunda captura para uma reserva já paga é marcada como duplicada e
  reembolsada integralmente. Não cria uma segunda nota de crédito contra a FR
  original, porque essa segunda captura não originou essa FR.

O cancelamento elegível de uma reserva paga solicita a percentagem definida em
`HOTEL_REFUND_PERCENTAGE`. Se a chamada falhar, a reserva permanece cancelada e
o reembolso fica recuperável. A nota de crédito proporcional é emitida apenas
depois da confirmação final do reembolso.

O cliente só pode reagendar ou cancelar dentro da política comercial. Um
colaborador pode ultrapassar essa janela numa exceção operacional, mas a ação
fica no histórico da reserva. Nem o cliente nem o colaborador podem alterar ou
cancelar uma reserva com pagamento contestado: primeiro é obrigatória a revisão
financeira. Reservas canceladas nunca voltam a apresentar ações de pagamento,
mesmo que exista um URL antigo na tentativa.

## Packs de ATL ou treino

Um pack usa um link `SINGLE`. Depois de uma captura validada:

1. o contrato fica ativo;
2. é criado um movimento positivo no ledger com chave idempotente;
3. a FR fica pendente para o TOConline;
4. as reservas de sessões consomem movimentos negativos.

O saldo nunca é editado. Cancelamentos dentro do prazo acrescentam um movimento
compensatório; fora do prazo mantêm o crédito consumido. Uma notificação de
pagamento falhado não ativa o contrato nem cria créditos.

## Subscrições de ATL ou treino

A subscrição é criada na Easypay com montante e frequência congelados no
snapshot da compra. O acesso só é ativado por um `subscription_capture` bem
sucedido que também tenha uma captura bem sucedida na consulta à API.

Cada captura cria um `SubscriptionCycle` imutável com o seu ID de captura,
período, valor, método e FR. Uma falha cria um ciclo falhado e nunca concede um
novo período. Se ainda existir um período anteriormente pago, esse acesso é
preservado até à respetiva data final e o erro fica imediatamente visível à
gestão. Depois dessa data, o contrato fica em `past_due`: o acesso termina, mas
o mandato recorrente continua identificado e impede uma segunda subscrição
acidental.

Por cão, só pode existir uma subscrição aberta por tipo dinâmico de catálogo.
O nome comercial não participa nesta regra. GOLD, Premium ou FreePass são
apenas nomes. O tipo e o nível comprados ficam congelados em
`catalog_type_reference` e `catalog_level_reference`; uma sessão restrita aceita
os níveis relacionados explicitamente em `ActivitySession.eligible_levels`.

Estados administrativos:

- `suspended`: pára cobranças futuras e acesso até uma reativação explícita;
- `cancel_at_period_end`: pára renovações, preservando o período já pago;
- `cancelled`: termina imediatamente e nunca apaga ciclos anteriores;
- bloqueio de acesso: suspensão operacional sem alterar a cobrança;
- reativação agendada: reativa o calendário no prestador; o acesso regressa
  apenas após a próxima captura confirmada.

O bloqueio e a reposição de acesso exigem um motivo e criam eventos de auditoria.
Estas ações controlam a utilização do serviço; nunca editam o contrato comprado,
uma captura, um ciclo ou um documento fiscal. O Django admin é manutenção
técnica; a equipa gere estas exceções nas páginas próprias do backoffice.

Uma captura inesperada depois de cancelamento, fim ou suspensão é documentada e
reembolsada automaticamente sem restaurar acesso. O administrador pode ainda
pedir reembolsos parciais por ciclo; o valor de uma repetição tem de ser igual
ao pedido original para preservar a idempotência.

## Chargebacks e disputas

Uma notificação de chargeback também não é aceite como prova isolada. O sistema
consulta a Easypay pelo ID da captura e valida o ID, a referência ao pagamento e
o total disputado antes de alterar estado.

- Hotel: a tentativa e a reserva ficam `payment_disputed`; alterações,
  cancelamentos e novas ações de pagamento ficam bloqueados para revisão.
- Pack: os créditos e a FR histórica não são alterados, mas o acesso operacional
  é bloqueado.
- Subscrição: o ciclo fica `disputed`, o acesso é bloqueado e as renovações são
  paradas no prestador. Se não for possível parar remotamente, o erro permanece
  destacado para intervenção administrativa.
- Uma reentrega do mesmo evento é idempotente e não duplica eventos.
- Um chargeback não emite automaticamente uma nota de crédito. É uma disputa
  bancária, não uma confirmação de reembolso fiscal; a regularização deve ser
  decidida e documentada pela gestão.

O administrador pode restaurar acesso após investigação, com motivo obrigatório
e evento de auditoria imutável. Restaurar acesso não altera nem elimina a
transação, o ciclo, a FR ou o chargeback.

## Recuperação de falhas

Os jobs são deliberadamente repetíveis:

| Comando | Recupera |
| --- | --- |
| `retry_easypay_refunds` | reembolsos de hotel falhados e webhooks finais perdidos |
| `maintain_activities` | expiração, FR de atividades, reembolsos/capturas pendentes e NC |
| `retry_toconline_documents` | FR e NC de reservas em falta |
| `sync_toconline_catalog` | projeções de catálogo pendentes ou divergentes |
| `sync_toconline_customers` | clientes fiscais pendentes |

Uma resposta de criação de reembolso sem ID é falha recuperável, não estado
pendente irrecuperável. Um reembolso com ID permanece pendente e é consultado
pelo job caso o webhook final não chegue.

Os jobs de documentos filtram por capturas confirmadas e o próprio serviço de
faturação volta a validar captura, referência de compra e montante. Registos
pendentes, gratuitos ou falhados nunca são enviados automaticamente ao
TOConline como vendas pagas.

Os comandos escrevem contagens em stdout e detalhes em logs estruturados. A
operação deve alertar por logs `ERROR`, por estados `failed` persistentes e por
jobs que deixem de executar; uma falha isolada recuperável pode não terminar o
comando com erro.

## Testes e limite de validação local

Os testes Django usam adaptadores falsos deterministas para validar máquinas de
estado, idempotência, concorrência, autorização e falhas externas. Os testes
Playwright validam os percursos completos do browser sem contactar prestadores.

Antes de produção é obrigatória uma aceitação real no sandbox da Easypay e na
empresa de testes do TOConline: pagamento por cada método ativo, captura
falhada, reentrega de webhook, subscrição inicial e renovação, cancelamento,
reembolso parcial/total, FR, NC e download dos PDFs. Só o prestador consegue
provar credenciais, permissões e contratos reais de resposta.
