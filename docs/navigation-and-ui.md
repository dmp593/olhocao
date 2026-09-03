# Navegação e sistema de interface

O produto tem quatro contextos separados. Misturá-los volta a criar a confusão
que este sistema de navegação pretende evitar.

| Contexto | Layout | Navegação |
| --- | --- | --- |
| Site público | `layouts/public.html` | serviços, preços, ATL/treino, contacto e informação |
| Área do cliente | `layouts/customer_portal.html` | início, cães, hotel, atividades, documentos e conta |
| Gestão operacional | `layouts/backoffice.html` | dashboard, clientes, reservas, atividades, subscrições, catálogo e operações |
| Django admin | templates nativos do Django | manutenção técnica exclusiva de superusers |

O Django admin não é uma continuação do backoffice. Funcionalidades usadas pela
equipa — reservar, agendar, gerir clientes, catálogo, pagamentos, subscrições e
documentos legais — têm páginas próprias em Gestão. Mesmo uma conta operacional
com `is_staff=True` não entra em `/admin/`; essa área e o respetivo link técnico
são exclusivos de `is_superuser=True`.

## Componentes

Os componentes reutilizáveis estão em `olhocao/templates/ui/`. Antes de criar
HTML novo, procure aí uma composição existente:

- `page_header.html`: título, subtítulo, eyebrow e ações da página;
- `form_field.html`, `form_errors.html` e `form_actions.html`: formulários;
- `stat_card.html` e `status_card.html`: indicadores e estados operacionais;
- `empty_state.html`: listas vazias com próximo passo;
- `booking_steps.html`: progresso do checkout;
- `confirmation_panel.html`: ações destrutivas ou irreversíveis.
- `pet_identity.html`: identificação consistente de um cão;
- `service_card.html` e `catalog_family_card.html`: cartões comerciais.

Os cartões, badges, alertas, tabelas, campos e botões simples são intencionalmente
classes `ui-*`, em vez de templates que apenas passariam classes e texto. As
páginas de detalhe de reserva compõem os blocos de domínio em
`hotel/templates/hotel/partials/`: resumo, estadia, cliente e pagamento. Esta
separação evita duplicar regras de estado em templates sem criar componentes
sem comportamento ou significado próprio.

Os elementos de navegação ficam em `olhocao/templates/components/`. O seletor
de idioma é partilhado pelos três contextos e conserva o caminho e a query
string atuais.

## Aspeto claro, escuro e automático

O seletor reutilizável `components/theme_switcher.html` está disponível no site
público, área de cliente e Gestão. Existem três preferências:

- `Claro` e `Escuro` mantêm explicitamente o aspeto escolhido;
- `Automático` segue `prefers-color-scheme` do dispositivo e reage a alterações
  do sistema sem recarregar a página.

A preferência é local a cada browser, guardada em `localStorage` sob a chave
`olhocao-theme`; não é um dado da conta e não requer consentimento de cookies.
O script pequeno `assets/js/components/theme.js` é carregado antes do CSS para
aplicar o tema sem um clarão inicial. Também atualiza `color-scheme`, a cor da
barra do browser e os estados acessíveis do seletor. Uma alteração emite o
evento `olhocao:themechange`, que futuros componentes visuais podem observar
sem acoplamento ao seletor.

Em desktop, o seletor explica as três opções num menu. No menu móvel é um
controlo segmentado compacto com ícones de sol, lua e sol/lua. Os dois formatos
partilham o mesmo estado e nomes acessíveis.

As páginas não devem implementar variantes escuras próprias. Use os tokens e
componentes `ui-*` definidos em `styles.css`; a camada central de tema é a única
responsável pelas cores. O tema respeita `prefers-reduced-motion`, mantém foco
visível e não permite ao editor alterar livremente a paleta ou o CSS da marca.

## CSS e JavaScript

O ficheiro fonte é `styles.css`; `assets/css/styles.css` é o build distribuído.
Classes `ui-*` formam a pequena API visual do projeto. Não duplique cores,
raios, sombras ou tamanhos numa página. Quando um padrão se repete, corrija o
componente ou a classe partilhada.

JavaScript vive em `assets/js/`, usa atributos `data-*` e melhora
progressivamente HTML funcional no servidor. Não use handlers inline nem faça
renumeração client-side de formsets Django. Formulários críticos mantêm CSRF,
validação e autorização no servidor.

Os menus responsivos são visíveis por defeito em desktop através de CSS e o
JavaScript apenas controla o estado móvel. Não volte a colocar `hidden` no menu
renderizado pelo servidor: uma versão de CSS/HTML em cache ou JavaScript tardio
não pode produzir um cabeçalho desktop vazio.

A fonte Bricolage Grotesque é servida localmente em WOFF2. Não acrescente uma
dependência externa de fontes ou ícones sem necessidade operacional.

## Regras para páginas novas

1. Escolha o layout pelo contexto, não pela aparência desejada.
2. Use um único `h1`, conteúdo dentro de `main` e landmarks semânticos.
3. Comece com `page_header`; componha cartões, estados vazios e formulários.
4. Todo o controlo precisa de label e todo o botão apenas com ícone precisa de
   nome acessível.
5. A ação principal é visualmente única. Ações destrutivas exigem confirmação e
   explicação do efeito.
6. Preserve teclado, foco visível, contraste e mensagens de erro junto ao campo.
7. Teste 390 px e desktop; não esconda ações essenciais no hover.
8. Acrescente um teste de comportamento Django e, para percursos essenciais, um
   cenário Playwright.

## Verificação

```bash
npm ci
npm run build
npm run test:e2e
```

O servidor E2E usa `.e2e/db.sqlite3`, recria a base e semeia utilizadores e
dados deterministas. Nunca aponta para a base local normal ou de produção. Os
testes percorrem público, cliente, gestão, criação de cliente, password de
emergência, reserva de uma noite, cortesia documentada, edição do frontoffice,
documentos legais, catálogo central de serviços opcionais, pesquisa paginada de
audiências de promoções, idiomas e viewport móvel.
Também verificam overflow horizontal, labels, checkboxes gigantes, botões
excessivos, padding de cartões, padrão de fundo, temas claro/escuro/automático,
bandeiras de idioma sem deformação e erros de JavaScript.
