# Roadmap de autonomia e personalização

## Objetivo

Permitir que a gerência mantenha o site e opere o centro canino sem depender de
alterações ao código para tarefas editoriais ou operacionais normais. Essa
autonomia não pode permitir quebrar o design, contornar regras financeiras ou
reescrever o histórico de uma transação.

Este documento é um plano, não uma lista de funcionalidades já entregues. Cada
fase deve ser implementada e validada isoladamente antes de iniciar a seguinte.

## O que já existe

`Gestão > Mais > Conteúdo do site` já permite editar, em português e inglês:

- carrosséis, imagens, textos e ações;
- cartões de serviços da página inicial;
- testemunhos, equipa, parceiros e redes sociais;
- contactos, morada, mapa, horários e assuntos do formulário;
- conteúdos principais de Início, Sobre nós, Contacto e Preços.

O catálogo, hotel, ATL, treino, serviços opcionais, capacidade, calendários,
treinadores, promoções, clientes, pagamentos, reembolsos, documentos e
subscrições têm áreas operacionais próprias. Não se devem duplicar estes dados
num editor de páginas.

## Princípios de produto e arquitetura

1. **Blocos seguros em vez de uma página em branco.** A equipa escolhe e ordena
   componentes aprovados. Não edita HTML, CSS, JavaScript, cores ou dimensões.
2. **Conteúdo separado de transações.** O CMS nunca altera preços capturados,
   compras, faturas, pagamentos ou snapshots fiscais.
3. **Rascunho antes de publicação.** Alterar conteúdo público exige
   pré-visualização; revisões anteriores continuam recuperáveis.
4. **Uma fonte de verdade.** Serviços apresentados no site referenciam o
   catálogo existente em vez de copiar nomes, preços ou disponibilidade.
5. **Tradução visível.** Cada ecrã mostra o estado de completude PT/EN e impede
   publicar uma versão obrigatória incompleta.
6. **Permissões e auditoria.** Editor, publicador e gestor têm responsabilidades
   distintas; todas as alterações indicam autor, instante e motivo quando a
   operação for sensível.
7. **Progressive enhancement.** Formulários permanecem funcionais no servidor;
   JavaScript melhora pré-visualização, pesquisa e ordenação sem conter regras
   de negócio.

## Fase 1 — Publicação segura e biblioteca de media

Esta é a prioridade mais alta, porque reduz o risco quando o cliente ganha mais
controlo.

### Entregas

- estados `Rascunho`, `Agendado` e `Publicado`;
- pré-visualização autenticada por idioma, viewport e tema;
- histórico de revisões com comparação e reposição de uma versão anterior;
- publicação e retirada de publicação agendadas;
- biblioteca de imagens reutilizáveis, pesquisa, texto alternativo obrigatório,
  ponto focal e variantes WebP responsivas;
- proteção contra apagar uma imagem ainda utilizada;
- perfis `Editor de conteúdo` e `Publicador`, além do gestor;
- registo de auditoria pesquisável.

### Decisão técnica antes da implementação

Fazer um spike curto e documentado que compare:

- continuar o editor Django atual com um modelo simples de revisões; e
- usar Wagtail apenas para conteúdo público não transacional.

Wagtail já fornece revisões, rascunhos, pré-visualização, agendamento, bloqueio,
permissões e workflows. Só deve ser adotado se puder ficar integrado na
navegação de Gestão, preservar a tradução PT/EN e migrar o conteúdo atual sem
criar um segundo backoffice confuso. Catálogo, reservas, pagamentos e restantes
domínios transacionais ficam sempre nos módulos atuais.

### Critérios de conclusão

- nenhuma edição fica pública por acidente;
- uma versão publicada consegue ser restaurada sem intervenção técnica;
- uma imagem só é publicada com descrição acessível;
- permissões e ações são cobertas por testes Django e E2E.

## Fase 2 — Conteúdo e descoberta comercial

### Entregas

- anúncios temporários configuráveis, com início/fim, idioma, severidade e CTA;
- páginas de serviço ligadas a cada tipo de catálogo, com galeria, FAQ e equipa;
- galeria das instalações e casos/testemunhos destacados;
- FAQ reutilizável por página e por serviço;
- editor controlado de navegação e rodapé, limitado a destinos seguros;
- SEO por página: título, descrição, imagem social, canonical e indexação;
- dados estruturados `LocalBusiness`/`Organization` gerados a partir dos
  contactos, horários, redes sociais e catálogo já existentes;
- redirecionamentos permanentes quando um endereço público muda;
- verificador editorial antes de publicar: links inválidos, tradução em falta,
  imagem sem texto alternativo e conteúdo vazio.

Não incluir um construtor visual genérico. Novos padrões de layout continuam a
ser componentes revistos em código; o cliente apenas escolhe conteúdo, ordem e
visibilidade dentro desses padrões.

## Fase 3 — Autonomia operacional do dia a dia

Estas funcionalidades tendem a evitar mais chamadas do que novas opções
puramente visuais.

### Portal e ficha do cliente

- onboarding guiado por etapas;
- contactos de emergência, veterinário, autorizações e instruções de cuidado;
- vacinas e documentos com data de validade, alertas e estado de aprovação;
- histórico cronológico único de reservas, atividades, compras, pagamentos,
  documentos, mensagens e ocorrências;
- pedidos de atualização de dados sujeitos a validação quando necessário.

### Agenda e comunicação

- fila de pedidos e lista de espera por sessão/serviço, com promoção automática
  controlada quando surge capacidade;
- templates PT/EN para email e, apenas se houver necessidade confirmada, SMS;
- lembretes configuráveis de pagamento, vacinação, chegada, recolha e sessão;
- notas e conversas associadas ao cliente, cão ou reserva;
- relatório pós-serviço com texto, fotos e visibilidade explícita para o cliente;
- quadro de check-in/check-out e checklist de entrega do cão.

Configurações de mensagens devem ter limites e exemplos claros. Eventos
financeiros críticos continuam a usar templates testados e não aceitam HTML
arbitrário.

## Fase 4 — Decisão e controlo da gerência

- dashboard de ocupação e utilização real versus capacidade por dia, sessão,
  tipo, nível, treinador e instalação;
- receitas, dívida, reembolsos, cortesia, descontos e desempenho de promoções;
- adesão, renovação, suspensão e cancelamento de subscrições;
- utilização contratada versus utilizada, sem transportar créditos semanais por
  omissão;
- no-shows, cancelamentos tardios e procura recusada por falta de capacidade;
- exportações CSV/PDF acessíveis a partir dos filtros visíveis;
- tarefas operacionais pendentes: pagamentos, documentos fiscais, sincronização,
  vacinas, pedidos e reembolsos.

As métricas devem ser derivadas dos eventos e snapshots já auditados. Nunca se
alteram transações antigas para tornar um relatório mais conveniente.

## Fase 5 — Privacidade, qualidade e suporte preventivo

- área para exportação dos dados do próprio cliente;
- fluxo auditado para pedidos de acesso, correção e apagamento aplicável;
- políticas explícitas de retenção e anonimização, sem apagar obrigações fiscais;
- centro de ajuda contextual no backoffice, com instruções curtas junto à ação;
- página de estado das integrações e fila de novas tentativas, sem expor segredos;
- alertas de configuração incompleta e checklist de lançamento;
- auditoria periódica WCAG, teclado, leitores de ecrã, contraste e zoom;
- backup/restauro ensaiado para conteúdo e media.

## Funcionalidades a não priorizar agora

- escolha livre de tema, cores ou CSS por parte do cliente;
- criação arbitrária de campos ou regras transacionais;
- aplicação móvel nativa antes de medir limitações reais do portal responsivo;
- chatbot ou geração automática de conteúdo sem um caso de uso mensurável;
- SMS, WhatsApp ou múltiplos fornecedores antes de validar custo e consentimento;
- automações que confirmem reservas, emitam documentos ou façam reembolsos sem
  regras determinísticas, idempotência e auditoria.

## Ordem recomendada

| Ordem | Incremento | Resultado observável |
| --- | --- | --- |
| 1 | Revisões, rascunhos, preview e media | editar sem risco nem suporte técnico |
| 2 | Anúncios, páginas de serviço, FAQ e SEO | campanhas e conteúdo autónomos |
| 3 | Onboarding, documentos e mensagens | menos trabalho manual e menos dúvidas |
| 4 | Espera, lembretes e check-in/out | operação diária previsível |
| 5 | Analytics e tarefas pendentes | decisões baseadas em capacidade e receita |
| 6 | Privacidade e centro de ajuda | conformidade e suporte preventivo |

Cada incremento exige modelo de permissões, migração reversível, documentação,
testes de domínio, autorização e browser, além de uma demonstração com dados
realistas antes de avançar.

## Referências da investigação

- [Wagtail: funcionalidades avançadas de snippets](https://docs.wagtail.org/en/stable/topics/snippets/features.html)
- [Time To Pet Facility: portal, pedidos, banners e recorrência](https://facility-help.timetopet.com/en/articles/14036664-facility-frequently-asked-questions)
- [Time To Pet: calendário, mensagens, relatórios e operação](https://www.timetopet.com/pricing)
- [Gingr: reservas, check-in, report cards e analytics](https://www.gingrapp.com/solutions)
- [Google: dados estruturados de negócios locais](https://developers.google.com/search/docs/appearance/structured-data/local-business)
- [Comissão Europeia: European Accessibility Act](https://commission.europa.eu/strategy-and-policy/policies/justice-and-fundamental-rights/disability/european-accessibility-act-eaa_en)
