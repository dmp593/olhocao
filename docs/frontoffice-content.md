# Conteúdo editável do frontoffice

O conteúdo editorial é gerido em `Gestão > Mais > Conteúdo do site`. A equipa
pode atualizar a apresentação pública sem editar templates e sem tocar no
catálogo, nas reservas ou nas transações.

## O que é editável

- imagens, títulos, descrições e ações dos carrosséis principal e de benefícios;
- cartões de serviços apresentados na página inicial;
- testemunhos reais ou princípios de cuidado;
- equipa, funções, especializações e fotografias;
- morada, email, telefones, horários, mapa e ligação para direções;
- texto de apresentação das páginas inicial, Sobre nós, Contacto e Preços;
- parceiros e respetivas ligações;
- redes sociais;
- assuntos disponíveis no formulário de contacto.

Cada campo público que contém texto editorial tem uma versão portuguesa e uma
inglesa. A publicação é imediata depois de guardar. `Ordem` controla a posição
e `Publicado` permite esconder um item sem o apagar; assim, o histórico e a
possibilidade de recuperação permanecem intactos.

## Imagens

Imagens novas são guardadas em caminhos com nomes aleatórios, não reutilizam o
nome enviado pelo browser e aceitam apenas GIF, JPEG, PNG ou WebP. O formulário
valida o conteúdo como imagem e aplica o limite configurado em
`FRONTOFFICE_IMAGE_MAX_UPLOAD_BYTES`.

Os conteúdos iniciais apontam para imagens estáticas existentes. Ao carregar
uma imagem, essa passa a ter prioridade; enquanto não houver upload, o visual
atual permanece inalterado. A descrição da imagem é obrigatória nos dois
idiomas para acessibilidade. Um conteúdo que requer imagem pode ser guardado
como rascunho, mas não publicado sem imagem.

## Guardrails editoriais

- apenas utilizadores `is_staff` entram nestas páginas;
- o conjunto global de configurações é uma única linha protegida também na base
  de dados;
- tem de existir pelo menos um slide publicado no carrossel principal, para a
  página inicial nunca ficar sem título;
- o mapa incorporado só aceita HTTPS em hosts oficiais do Google Maps;
- direções, parceiros e redes sociais exigem HTTPS;
- texto introduzido pela equipa é escapado pelos templates e não aceita HTML
  executável;
- cada alteração regista data e último editor;
- não existem ações de eliminação no backoffice editorial: itens deixam de ser
  publicados.

## Fronteira com dados de negócio

Este editor nunca altera preços, IVA, produtos, disponibilidade, capacidades,
promoções ou regras de ATL/treino. Esses dados continuam em `Gestão > Catálogo`
e nos serviços de domínio. A página pública de preços lê diretamente o catálogo
local autoritativo; o editor controla apenas o respetivo cabeçalho.

Esta separação é deliberada: uma alteração de marketing não pode modificar um
snapshot financeiro nem afetar uma compra em curso.

## Desenvolvimento

Para acrescentar uma coleção editorial:

1. crie um modelo coeso em `frontoffice/models.py`;
2. registe apenas os campos traduzíveis em `frontoffice/translation.py`;
3. crie o `ModelForm` com ambos os idiomas em `backoffice/forms.py`;
4. adicione a coleção allowlisted em `backoffice/site_content.py`;
5. consuma o modelo no template, mantendo classes e componentes `ui-*`;
6. acrescente testes de autorização, tradução, validação e rendering público.

Não transforme o registo de coleções num construtor genérico de páginas. O
layout é deliberadamente controlado pelo código para preservar consistência,
acessibilidade e identidade visual.
