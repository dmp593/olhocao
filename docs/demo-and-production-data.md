# Dados de demonstração e dados de produção

## Objetivo

Os dados usados para experimentar a aplicação não são fixtures JSON. São
criados pelo comando idempotente `seed_demo_data` numa base SQLite isolada. A
opção evita chaves primárias fixas, hashes de passwords versionados, datas que
expiram e referências frágeis entre modelos.

O comando recusa executar se `DEBUG` estiver desligado, se
`DEMO_DATA_ALLOWED` não estiver explicitamente ativo ou se encontrar
credenciais da Easypay/TOConline. O email usa sempre o backend em memória.

## Iniciar a demonstração

```bash
./scripts/start_demo_server.sh
```

Por omissão abre em `http://127.0.0.1:8766` e usa `.demo/db.sqlite3`. Pode ser
executado novamente sem duplicar utilizadores, cães, planos, subscrições,
sessões ou reservas.

Contas disponíveis:

| Utilizador | Papel | Cenários principais |
| --- | --- | --- |
| `demo_admin` | Administrador | Toda a gestão e Django Admin |
| `demo_manager` | Colaborador/treinador | Gestão sem superpoderes |
| `demo_ana` | Cliente | Dois cães, pack ativo, subscrição ativa e hotel |
| `demo_bruno` | Cliente | Subscrição em atraso e compra pendente |

A password predefinida é `Demo-OlhoCao-2026!`. Para a trocar sem escrever o
valor no histórico da shell, introduza-o de forma interativa:

```bash
read -s DEMO_DATA_PASSWORD
export DEMO_DATA_PASSWORD
./scripts/start_demo_server.sh
unset DEMO_DATA_PASSWORD
```

As raças dos cães são atualmente texto livre no domínio (`Pet.breed`), por
isso Labrador Retriever, Beagle e Sem raça definida exercitam esse campo sem
introduzir um catálogo de raças artificial.

Para recomeçar do zero, pare o servidor e elimine manualmente apenas
`.demo/db.sqlite3`. Nunca aponte este script para a base de desenvolvimento ou
produção.

## O que deve ser preparado em produção

### Obrigatório

Execute sempre as migrations:

```bash
python manage.py migrate --noinput
```

As migrations de dados criam os valores estruturais que pertencem ao produto,
incluindo a espécie Cão, a estrutura inicial do catálogo e o conteúdo inicial
do frontoffice. Crie o primeiro administrador com `createsuperuser` ou através
do processo seguro da plataforma, nunca através de uma fixture com password.

### Conteúdo real, após revisão

Estas duas fixtures existentes representam conteúdo jurídico real e podem ser
carregadas uma vez, depois de validação pela empresa e aconselhamento jurídico:

- `backoffice/fixtures/backoffice/terms_and_conditions.json`;
- `backoffice/fixtures/backoffice/privacy_policy.json`.

Têm chaves fixas. Não as volte a carregar depois de o conteúdo ter sido editado
em produção, pois `loaddata` pode substituir essas alterações.

### Normalmente não carregar

- `frontoffice/fixtures/frontoffice/contactsubject.json`: a migration do
  frontoffice já cria os assuntos iniciais;
- `pets/fixtures/pets/petspecies.json`: a migration de pets já garante Cão e a
  fixture acrescenta Gato, que não é necessário neste negócio.

### Nunca carregar em produção

- `seed_demo_data` e todo o conteúdo `.demo/`;
- `tests/e2e/seed_data.py` e todo o conteúdo `.e2e/`;
- utilizadores, clientes, cães, reservas, pagamentos, packs, subscrições,
  créditos, ciclos ou documentos fiscais fictícios.

Preços, IVA, capacidades, níveis, planos e serviços opcionais reais devem ser
configurados em `Gestão > Catálogo`. Assim existe uma única fonte de verdade e
a projeção no TOConline segue o mesmo fluxo auditável usado em operação.

## Limites da demonstração

Os ciclos e referências prefixados com `DEMO-` não representam dinheiro
capturado nem documentos fiscais válidos. A demonstração serve para validar UI,
permissões, capacidade, agendas e estados. Os testes automatizados com clientes
Easypay/TOConline injetados continuam a validar os caminhos transacionais sem
contactar serviços externos.
