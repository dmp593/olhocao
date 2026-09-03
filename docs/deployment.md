# Deployment

## 1. Segredos e configuração

Crie a configuração a partir de `.env.example` num gestor de segredos. Em
produção são obrigatórios:

- `DEBUG=off` e uma `SECRET_KEY` aleatória exclusiva;
- PostgreSQL ou MariaDB com backups testados;
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` e `PUBLIC_BASE_URL` em HTTPS;
- credenciais OAuth TOConline autorizadas;
- credenciais Easypay de produção com permissões `single`, `subscription`,
  `capture` e `refund`, e Basic Auth do webhook;
- SMTP funcional e endereços `DEFAULT_FROM_EMAIL`/`SUPPORT_EMAIL`;
- cookies seguros, redirect HTTPS e HSTS ativos.

Proteja ficheiros de segredos com permissões `0600`. Rode antes da promoção:

```bash
DJANGO_ENV_FILE=/caminho/seguro/.env.prod python manage.py check --deploy
```

O cliente TOConline 2.x deriva por omissão o OAuth de
`<TOCONLINE_BASE_URL>/oauth`. Defina `TOCONLINE_OAUTH_BASE_URL` apenas se a conta
usar um endpoint OAuth separado.

## 2. Build e release

O `Dockerfile` não executa migrações no arranque, evitando corridas entre
réplicas. Execute uma única release task:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --clear --noinput
```

O release não chama fornecedores externos. Uma indisponibilidade do TOConline
não pode impedir a promoção da aplicação.

Depois inicie
`gunicorn olhocao.wsgi:application --bind=0.0.0.0:8000 --no-control-socket`.
O socket de controlo não é necessário neste serviço e desligá-lo mantém o
contentor compatível com um sistema de ficheiros read-only.

## 3. Proxy e volumes

- Termine TLS no proxy/load balancer e ative `USE_X_FORWARDED_PROTO=on` apenas
  quando este substituir corretamente `X-Forwarded-Proto`.
- Sirva `/static/` diretamente a partir do resultado de `collectstatic`.
- Sirva `/media/catalog/` a partir do volume persistente para imagens públicas.
- Nunca exponha `/media/stay_media/` nem `/media/tmp/`; media de reservas usa a
  rota autenticada do Django.
- Monte `MEDIA_ROOT` num volume persistente e inclua-o no plano de backup.
- O contentor cria `/app/media` com o utilizador não privilegiado `olhocao`.
  Se a plataforma montar um volume já existente, confirme no release que esse
  utilizador mantém permissão de escrita antes de aceitar uploads.
- Encaminhe logs stdout/stderr para o agregador da plataforma.

## 4. Integrações

Na Easypay, registe:

```text
https://www.olhocao.pt/hotel/webhooks/easypay/
```

Use as mesmas credenciais Basic Auth configuradas no ambiente. Faça primeiro um
pagamento completo no sandbox e valide tentativa, evento, reserva e FR. Valide
também uma subscrição com autorização por cartão, `subscription_create`, uma
captura `subscription_capture`, cancelamento e FR do ciclo. Só depois altere
`EASYPAY_ENVIRONMENT=production` e use as chaves de produção.

No TOConline, confirme que o OAuth tem âmbito comercial e que a série/tipos `FR`
e `NC` usados pela empresa estão disponíveis. O
`TOCONLINE_OAUTH_REDIRECT_URI` tem de coincidir exatamente com o URI registado
no cliente OAuth. O transporte atual não expõe uma callback pública: lê o
código do cabeçalho de redirecionamento do TOConline e usa por omissão a
callback Postman documentada em `.env.example`.

## 5. Jobs

Agende com lock para impedir execuções simultâneas:

- catálogo e clientes a cada 15 minutos;
- documentos, reembolsos e `maintain_activities` a cada 5 minutos.

```bash
python manage.py sync_toconline_catalog
python manage.py sync_toconline_customers
python manage.py retry_toconline_documents
python manage.py retry_easypay_refunds
python manage.py maintain_activities
```

Os comandos continuam a processar os restantes registos quando uma operação
individual falha. Alerte por ausência de execução, logs `ERROR` e estados
`failed` persistentes; use também o código de saída para falhas globais.

## 6. Verificação pós-deployment

1. `/health/live/` e `/health/ready/` respondem `200`.
2. Um artigo criado no site fica imediatamente utilizável e passa de `Waiting to
   publish` a `Synchronized` quando o job alcança o TOConline.
3. Uma divergência manual no TOConline fica visível sem substituir nome/preço
   locais.
4. Uma reserva com vários cães e extras calcula o total correto.
5. Cartão, MB WAY e Multibanco confirmam por webhook/reconciliação.
6. O documento FR e respetivo PDF ficam acessíveis ao dono.
7. Reagendamento mantém duração e cancelamento elegível solicita 85% e NC.
8. Outro utilizador não consegue abrir reserva nem media alheia.
9. Um pack ATL credita exatamente as sessões compradas e uma reserva consome um
   crédito.
10. Uma subscrição limita reservas por semana e deixa de renovar depois do
    cancelamento.
11. Uma cobrança contestada bloqueia o serviço, conserva a transação original e
    aparece na fila de atenção sem emitir automaticamente uma nota de crédito.
12. Uma conta operacional não entra em `/admin/`; apenas um superutilizador
    técnico tem esse acesso.
13. Um código de teste limitado reserva uma utilização no checkout, consome-a
    depois da captura e liberta-a quando a compra pendente é cancelada.
14. Um ajuste de hotel para `0,00 €` confirma uma cortesia sem criar pagamento
    ou FR; um link anterior pago depois do ajuste é reembolsado sem confirmar a
    reserva.

## 7. Rollback

Faça backup da base de dados e `MEDIA_ROOT` antes da primeira promoção. O
rollback de aplicação deve reutilizar esses dados; não reverta migrações depois
de terem recebido dados sem um plano específico. Desative temporariamente novos
pagamentos no proxy se houver inconsistência financeira e reconcilie tentativas
na Easypay antes de reabrir.
