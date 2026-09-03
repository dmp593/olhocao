# OlhóCão

Aplicação Django 6.1 do centro canino OlhóCão. O site é a fonte de verdade do
catálogo e das regras operacionais. A Easypay trata links, cobranças e
subscrições; o TOConline recebe projeções do catálogo e emite os documentos
fiscais de forma assíncrona.

## Princípios da integração

- Produtos, serviços, preços, IVA, imagens, publicação e regras de utilização
  são geridos em `Gestão > Catálogo`.
- Cada artigo local recebe um código reservado `OC-S-*` ou `OC-P-*`; esse é o
  marcador estável e visível da projeção criada pelo site no TOConline.
- Depois de um artigo já ter sido publicado no ERP, mudar nome, preço, tipo ou
  IVA cria uma nova versão fiscal e desativa localmente a anterior.
- Cada compra guarda snapshots imutáveis de nome, código, preço, moeda, IVA e
  regras contratadas. Alterações futuras nunca reescrevem uma transação antiga.
- A Easypay não é catálogo nem ERP. Cria links `SINGLE`, subscrições recorrentes,
  confirma cobranças por webhook e processa reembolsos.
- Nas reservas assistidas, o colaborador envia o link por email; apenas o cliente
  abre o checkout e autoriza o pagamento.
- Registos, catálogo, reservas e pagamentos não fazem chamadas ao TOConline no
  pedido web. Publicação, clientes e documentos pendentes são reconciliados por
  jobs idempotentes e podem ser repetidos após uma indisponibilidade.

Mais detalhe em [Arquitetura](docs/architecture.md), no
[guia do catálogo e serviços opcionais](docs/catalog-and-optional-services.md), no
[guia de pagamentos e subscrições](docs/payment-and-subscription-flows.md), no
[guia de promoções e ajustes de preço](docs/discounts-and-price-adjustments.md), no
[sistema de navegação e UI](docs/navigation-and-ui.md), no
[guia de conteúdo editável do frontoffice](docs/frontoffice-content.md), no
[ADR do catálogo autoritativo](docs/adr/0003-website-catalog-authority.md) e no
[ADR da integração de pagamentos](docs/adr/0001-toconline-catalog-and-easypay-links.md).

## Desenvolvimento local

Requisitos: Python 3.13, Poetry 2.4 e Node.js apenas quando for necessário
recompilar o Tailwind CSS.

```bash
cp .env.example .env
poetry install
poetry run python manage.py migrate
poetry run python manage.py createsuperuser
poetry run python manage.py runserver
```

Para recompilar estilos:

```bash
npm ci
npm run build
```

Em alternativa, `docker compose up --build` inicia a aplicação e uma base
MariaDB apenas para desenvolvimento.

## Configuração externa

Preencha no gestor de segredos do ambiente:

- `TOCONLINE_OAUTH_CLIENT_ID`, `TOCONLINE_OAUTH_CLIENT_SECRET` e o URI OAuth;
- `TOCONLINE_OAUTH_BASE_URL` apenas quando o TOConline fornecer um host OAuth
  diferente de `<TOCONLINE_BASE_URL>/oauth`;
- `EASYPAY_ACCOUNT_ID` e `EASYPAY_API_KEY`;
- credenciais Basic Auth exclusivas em `EASYPAY_WEBHOOK_USERNAME` e
  `EASYPAY_WEBHOOK_PASSWORD`;
- email, base de dados, `SECRET_KEY`, hosts e origens HTTPS.

O ficheiro [.env.example](.env.example) contém a lista completa, sem segredos.
Nunca copie credenciais reais para o repositório.

Configure na Easypay o webhook:

```text
POST https://www.olhocao.pt/hotel/webhooks/easypay/
Authorization: Basic <credenciais configuradas>
```

Depois de autorizar a aplicação OAuth no TOConline, publique as projeções
pendentes e importe uma única vez eventuais artigos antigos:

```bash
poetry run python manage.py sync_toconline_catalog
poetry run python manage.py sync_toconline_customers
```

Em `Gestão > Catálogo`, crie primeiro um tipo com o comportamento estável
adequado (intervalo de datas, dia inteiro, horário ou prestação flexível),
depois os níveis opcionais e os preços/planos finais. O nível define regras
operacionais; cada preço ou plano define imagem, preço, IVA, visibilidade e,
quando aplicável, compra avulsa, pack ou subscrição.
O acesso a sessões específicas é configurado pela relação explícita com níveis;
não depende do nome comercial. GOLD, FreePass ou qualquer outro nome não exige
alterações ao código.

Um extra, como Grooming, é criado uma única vez como serviço avulso de prestação
flexível. A página central `Gestão > Catálogo > Serviços opcionais` associa-o a
um tipo completo ou a um nível específico através de duas listas simples. A
associação reutiliza o mesmo artigo fiscal; não duplica o produto no TOConline.

Artigos antigos importados do TOConline são somente leitura quanto aos dados
fiscais. A ação de os gerir no site cria um sucessor `OC-*`, preservando o
histórico e evitando alterações silenciosas.

## Operação

Agende estes comandos de forma idempotente:

```bash
# A cada 15 minutos
python manage.py sync_toconline_catalog
python manage.py sync_toconline_customers

# A cada 5 minutos
python manage.py retry_toconline_documents
python manage.py retry_easypay_refunds
python manage.py maintain_activities
```

Endpoints operacionais:

- `/health/live/`: processo Django ativo;
- `/health/ready/`: aplicação consegue consultar a base de dados.

Consulte o [guia de deployment](docs/deployment.md) antes de promover uma
versão.

## Qualidade

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run python manage.py makemigrations --check --dry-run
poetry run python manage.py check --deploy
poetry run python manage.py test
poetry run python manage.py makemessages -l pt \
  --ignore='static/*' --ignore='node_modules/*' --ignore='.venv/*' \
  --ignore='media/*' --ignore='.e2e/*' --ignore='test-results/*' \
  --ignore='playwright-report/*'
msgfmt --check --check-format --check-header locale/pt/LC_MESSAGES/django.po
poetry run python manage.py compilemessages -l pt
npm audit
npm run build
npm run test:e2e
```

Os testes cobrem sincronização do catálogo, snapshots, reservas e extras,
autorização, reagendamento, cancelamento, pagamentos, reconciliação, webhooks,
reembolsos, faturação, media privada, packs, subscrições, créditos, capacidade,
limites semanais, códigos promocionais, ajustes auditados de preço e health
checks.
O Playwright percorre os papéis de cliente e administrador em browser real e
aplica verificações transversais de acessibilidade e consistência visual.
