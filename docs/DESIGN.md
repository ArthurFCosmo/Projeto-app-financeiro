# Obsidian — High-Contrast Dark

## Guia Norteador (North Star): "Precisão na Escuridão"
Interface escura de nível profissional (developer-grade). Superfícies quase pretas (near-black), tipografia de alto contraste e cores de destaque cirúrgicas e precisas. Visual limpo, ágil e estritamente funcional.

## Cores
- **Primária (`#a78bfa`):** Violeta suave — elementos interativos, links, estados ativos e anéis de foco.
- **Fundo (`#09090b`):** Fundo base quase preto absoluto (*true near-black*).
- **Terciária (`#34d399`):** Verde esmeralda — estados de sucesso, receitas, saldos positivos e destaques contextuais.
- **Escala de Superfície:** Tons de cinza baseados em zinc (`#0c0c0f` → `#27272a`), com gradações muito sutis.
- **Erro (`#ef4444`):** Vermelho reservado estritamente para erros, alertas críticos e despesas/atrasos. Sem uso decorativo de cores.

## Tipografia
- **Família de Fontes:** Geist (Geist Sans e Geist Mono) — moderna, nítida e voltada para produtividade.
- Espaçamento entre letras (*letter-spacing*) reduzido nos títulos (-0.02em) para máxima sobriedade. Espaçamento padrão no corpo do texto.
- `#fafafa` para o texto primário e `#a1a1aa` para o texto secundário, assegurando alto contraste e legibilidade constante.

## Elevação e Profundidade
- Sombras mínimas ou ausentes. Separação visual fundamentada em bordas finas e precisas: `1px solid #27272a`.
- Estados ativos e ao passar o mouse (*hover*): transições sutis para o nível seguinte da escala de superfície.
- Anéis de foco acessíveis: `2px solid #a78bfa` com deslocamento de `2px` (*2px offset*).

## Componentes
- **Botões:**
  - Primário: preenchimento violeta sólido com texto escuro de alto contraste.
  - Secundário: fundo transparente com borda sutil.
  - Fantasma (*Ghost*): apenas texto, com realce de fundo visível no *hover*.
- **Cartões (*Cards*):** Fundo em `surface_container`, borda fina em `outline_variant` e raio de arredondamento de 8px (`border-radius: 8px`).
- **Campos de Entrada (*Inputs*):** Preenchimento em `surface_container`, borda fina e anel de foco em violeta.
- **Blocos de Código e Números Mono:** Fundo mais profundo (`surface_container_lowest`) e fonte monoespaçada (Geist Mono).

## Regras Fundamentais
- Nunca utilizar fundos claros no tema padrão. Manter consistência rigorosa na escala de cinzas zinc.
- Priorizar bordas sutis sobre sombras decorativas para separação de planos. A interface deve ser plana, nítida e precisa.
- Cores de destaque devem possuir significado funcional, nunca meramente decorativo.

## Logotipo e Identidade Visual
- **Proporção e Arquivos:** Imagens quadradas 1:1 (`logo_tema_escuro.png` para tema escuro e `logo_tema_claro.png` para tema claro).
- **Telas de Autenticação (Login, Cadastro, Recuperação de Senha):**
  - Ocupa de **25% a 30% da viewheight** horizontal padrão (`height: 26vh; min-height: 120px; max-width: 100%; object-fit: contain;`).
  - Posicionamento centralizado com margem inferior harmônica antes do título.
- **Barra Lateral / Dashboard (Sidebar de 260px):**
  - Dimensionamento proporcional para encaixar na sidebar com margens de respiro (`max-width: 160px; height: clamp(100px, 16vh, 150px); object-fit: contain;`).
  - Margem lateral e vertical em relação aos limites da barra lateral (`.sidebar`), preservando a usabilidade e o espaçamento dos links de navegação.