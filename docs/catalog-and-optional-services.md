# Catálogo e serviços opcionais

O catálogo é a fonte de verdade comercial do OlhóCão. Os nomes apresentados
ao cliente são livres e traduzíveis; o comportamento operacional é definido por
capacidades estáveis. Assim, renomear “Premium” para “GOLD” nunca altera uma
regra de reserva, pagamento ou acesso.

## Hierarquia

```text
Tipo de catálogo (Hotel, ATL, Treino, ...)
  └── Nível opcional (Canil interno, Normal, Internato, ...)
        └── Preço ou plano final (avulso, pack ou subscrição)
              └── CatalogItem fiscal versionado para o TOConline
```

- O **tipo** define o fluxo: intervalo de datas, dia completo, sessão com hora
  ou prestação flexível.
- O **nível** reúne regras operacionais comuns, como capacidade, antecedência
  de cancelamento e imagem herdada.
- O **preço ou plano** é o que o cliente compra. Guarda preço, IVA, formato de
  venda e, quando aplicável, número de utilizações ou periodicidade.

Os fluxos e as referências internas, não os nomes comerciais, são usados pela
lógica de negócio. Um preço já projetado no TOConline não é reescrito: uma
alteração fiscal cria um sucessor e preserva compras e documentos anteriores.

## Serviços opcionais

Um serviço reutilizável, como banho ou grooming, é criado como um preço avulso
num tipo com prestação flexível. A página `Gestão > Catálogo > Serviços
opcionais` controla onde esse serviço pode ser acrescentado.

O procedimento é deliberadamente centralizado:

1. escolher um único destino;
2. pesquisar e selecionar serviços disponíveis à esquerda;
3. movê-los para a lista da direita;
4. guardar as alterações.

Um destino pode ser o tipo completo ou um nível específico. Uma associação no
tipo é herdada por todos os níveis; uma associação no nível aplica-se a todos
os preços e planos desse nível. Isto evita repetir a mesma configuração em
planos de uma, três ou cinco utilizações semanais. Um item herdado aparece como
somente leitura e tem de ser removido no tipo onde foi configurado.

As associações têm histórico operacional: remover um serviço desativa a relação
em vez de apagar o registo. Um serviço não pode ser associado ao próprio tipo,
e apenas ofertas avulsas de prestação flexível são elegíveis. Estas regras são
validadas no serviço de domínio e novamente no modelo.

## Disponibilidade e compra

A associação apenas decide se o serviço é oferecido naquele contexto. Data,
hora e conclusão continuam a seguir as regras do próprio serviço:

- numa reserva de hotel, pode ser pedido sem data, com uma preferência dentro
  da estadia ou agendado pela equipa;
- numa reserva de ATL ou treino, fica ligado à sessão de origem;
- quando vendido isoladamente, segue o fluxo avulso configurado.

Preços e IVA vêm sempre do preço/plano do serviço opcional. Não são copiados
para a associação e não criam outro artigo no TOConline.

## Promoções aplicadas a opcionais

Para descontar o serviço opcional, a promoção seleciona o tipo, nível ou preço
do próprio opcional e inclui o fluxo “serviço opcional”. O campo de contexto do
serviço principal é uma restrição adicional: por exemplo, permite descontar
grooming apenas quando foi acrescentado ao Hotel, sem descontar o alojamento.

## Código relevante

- `catalog/models.py`: hierarquia e invariantes de associação;
- `catalog/services/add_ons.py`: substituição transacional das associações;
- `backoffice/catalog_forms.py`: seleção explícita do destino;
- `hotel/services/` e `activities/services/`: adaptação ao contexto de compra;
- `discounts/services.py`: elegibilidade sem dependência de nomes comerciais.
