# Scripts de Automação

Este diretório contém scripts para automatizar tarefas comuns do projeto, como a instalação de dependências e a execução do processo de ETL.

Compatível com **macOS** e **Linux (Debian/Ubuntu)**.

---

## Instalação

### Baixe o Projeto

Clone o repositório ou baixe o `.zip` da página de Releases:

```bash
git clone https://github.com/msantosjader/rfb-cnpj-etl.git
cd rfb-cnpj-etl
```

### Dê Permissão de Execução aos Scripts

Antes de executar, torne os scripts executáveis:

```bash
chmod +x scripts/*.sh
```

### Execute a Instalação do Ambiente

```bash
./scripts/000_preparar_ambiente.sh
```

Aguarde a mensagem **"SUCESSO!"**.

### Não tem Python instalado?

**macOS (via Homebrew):**

```bash
brew install python3
```

**Debian/Ubuntu:**

```bash
sudo apt update && sudo apt install python3 python3-venv python3-pip
```

---

## Como Executar

Execute os scripts a partir da raiz do projeto:

```bash
./scripts/run_complete.sh   # Pipeline completo
./scripts/run_download.sh   # Apenas download
./scripts/run_load.sh       # Apenas carga no banco
```

---

## Lista de Scripts

### `000_preparar_ambiente.sh`

- **Propósito:** Configura o ambiente do projeto. Cria o ambiente virtual (`.venv`) e instala as dependências do `requirements.txt`.
- **Quando usar:** Uma única vez, logo após baixar o projeto e garantir que o Python está instalado.

---

### `run_complete.sh`

- **Propósito:** Executa o ciclo **completo** do ETL — baixa os dados mais recentes da Receita Federal e os carrega no banco de dados.
- **Quando usar:** Para uso geral da ferramenta. **Este é o script principal.**

---

### `run_download.sh`

- **Propósito:** Executa apenas a **etapa de download** dos dados da Receita Federal.
- **Quando usar:** Se quiser apenas baixar os arquivos, sem carregá-los no banco imediatamente.

---

### `run_load.sh`

- **Propósito:** Executa apenas a **etapa de carga** dos dados no banco, utilizando arquivos já baixados anteriormente.
- **Quando usar:** Se os dados já foram baixados e você quer (re)carregá-los no banco.

---

## Notas

- Scripts testados em **macOS** e **Debian/Ubuntu**.
- Certifique-se de ter pelo menos **50GB de espaço livre** para o processo completo de ETL.
