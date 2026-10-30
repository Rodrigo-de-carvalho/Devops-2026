# Pipeline DevOps — Dashboard + Aiven.io (100% navegador, sem instalação local)

Laboratório completo: um dashboard Flask que mostra métricas de acesso,
com dados persistidos em um **PostgreSQL gerenciado pela Aiven.io**, e um
pipeline de CI/CD em **GitHub Actions** que:

1. Provisiona a infraestrutura na Aiven via **Terraform** (rodando no runner do GitHub, não na máquina do aluno).
2. Testa e builda a aplicação.
3. Publica a imagem Docker no GitHub Container Registry (GHCR).
4. Dispara o deploy da aplicação no **Render** (plataforma com deploy via hook HTTP, sem CLI local).

Todo o fluxo é feito pela interface web do GitHub, da Aiven e do Render —
nenhuma ferramenta local é necessária, compatível com ambientes de sala de
aula sem permissão de instalação.

## Estrutura do repositório

```
aiven-devops-pipeline/
├── Dockerfile               # Builda a partir da raiz do repo, copia de app/
├── app/                    # Aplicação Flask (o dashboard)
│   ├── app.py
│   ├── bcb_client.py       # Conexão com Postgres (pool) + client da API do BCB
│   ├── requirements.txt
│   ├── .env.example
│   ├── templates/dashboard.html
│   └── static/style.css
├── terraform/               # Provisionamento da infra na Aiven
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
└── .github/workflows/
    ├── infra.yml            # Provisiona o Postgres na Aiven
    └── ci-cd.yml             # Testa, builda, publica e faz deploy do app
```

> **Nota:** o `Dockerfile` fica na **raiz** do repositório (não em `app/`),
> mesmo a aplicação estando na pasta `app/`. Isso é proposital — ver
> [Troubleshooting](#troubleshooting--problemas-comuns-no-deploy) abaixo
> para o motivo.

## Passo a passo (tudo pelo navegador)

### 1. Criar o repositório no GitHub
- Crie um repositório novo e envie estes arquivos (pode usar o próprio
  editor web do GitHub: **Add file → Upload files**).

### 2. Criar a conta e o projeto na Aiven
- Acesse [aiven.io](https://aiven.io) e crie uma conta (tem free trial/plano gratuito para estudo).
- Crie um **Project** no console da Aiven — anote o nome do projeto.
- Vá em **Profile → Authentication tokens** e gere um **API token**.

### 3. Configurar os Secrets do repositório no GitHub
Em **Settings → Secrets and variables → Actions → New repository secret**,
crie:

| Secret | Descrição |
|---|---|
| `AIVEN_API_TOKEN` | Token gerado no passo 2 |
| `AIVEN_PROJECT` | Nome do projeto na Aiven |
| `RENDER_DEPLOY_HOOK_URL` | Deploy hook do serviço no Render (passo 5) |
| `APP_HEALTHCHECK_URL` | URL pública do app no Render, ex: `https://seu-app.onrender.com` |
| `AIVEN_DB_URL` | Service URI do Postgres (necessária no job de consumer em lote) |
| `KAFKA_BOOTSTRAP_SERVERS` | Saída `kafka_bootstrap_servers` do Terraform |
| `KAFKA_USERNAME` | Saída `kafka_username` do Terraform |
| `KAFKA_PASSWORD` | Saída `kafka_password` do Terraform |
| `KAFKA_CA_CERT_PEM` | Saída `kafka_ca_cert` do Terraform (cole o certificado inteiro) |
| `KAFKA_TOPIC` | `bcb-indicadores` (ou o valor de `kafka_topic_name`) |

Os 5 secrets de Kafka só são necessários se você for usar a extensão de
streaming (seção [Streaming com Kafka](#streaming-com-kafka) abaixo) — o
laboratório básico funciona só com os 4 primeiros.

### 4. Provisionar o banco na Aiven
- Vá na aba **Actions** do repositório e rode manualmente o workflow
  **"Infra - Provisionar Aiven (Terraform)"** (botão *Run workflow*).
- Isso cria um serviço PostgreSQL gerenciado na Aiven. Acompanhe o
  progresso e a URI de conexão no console da Aiven
  (**Services → dashboard-pg → Connection information**).

### 5. Criar o serviço no Render (deploy do app)
- Crie uma conta em [render.com](https://render.com).
- **New → Web Service → Build and deploy from a Git repository**, aponte
  para o repositório.
- Em **Settings**, confirme (valores padrão, não precisa mexer se o
  `Dockerfile` estiver na raiz do repo como neste projeto):
  - **Root Directory**: vazio.
  - **Dockerfile Path**: `./Dockerfile`.
  - **Docker Build Context Directory**: `.`.
- Em **Environment**, adicione:
  - `AIVEN_DB_URL` — a string de conexão copiada do console da Aiven.
  - `PORT` — **`8080`**, criada manualmente (o Render às vezes não detecta
    sozinho a porta exposta pelo container; ver
    [Troubleshooting](#troubleshooting--problemas-comuns-no-deploy)).
- Em **Settings → Deploy Hook**, copie a URL e cole no secret
  `RENDER_DEPLOY_HOOK_URL` do GitHub (passo 3).

### 6. Rodar o pipeline de CI/CD
- Qualquer `push` na pasta `app/` na branch `main` dispara o workflow
  **"CI/CD - Dashboard App"**, que:
  1. Roda lint e um smoke test de import.
  2. Builda e publica a imagem em `ghcr.io/<usuário>/<repo>/dashboard-app`.
  3. Chama o deploy hook do Render.
  4. Verifica `/healthz` após o deploy.

### 7. Acessar o dashboard
- Abra a URL pública do Render — o dashboard mostra visitas totais,
  uptime, um gráfico (Chart.js) de acessos por minuto e os últimos
  acessos registrados, tudo lido do Postgres da Aiven.

## Fonte de dados: trabalho original + Banco Master, juntos

O dashboard consome a **API de Dados Abertos do Banco Central do Brasil**
([dadosabertos.bcb.gov.br](https://dadosabertos.bcb.gov.br/)) — pública,
sem necessidade de chave ou cadastro — e mostra **duas fontes de dados
convivendo no mesmo gráfico**:

**1. Trabalho original — SGS** (Sistema Gerenciador de Séries Temporais).
Linha **tracejada** no gráfico:

| Código | Indicador | Unidade |
|---|---|---|
| 1 | Dólar comercial (venda) | R$ |
| 432 | Meta Selic definida pelo Copom | % a.a. |
| 433 | IPCA — variação mensal | % |

As séries diárias (Dólar, Selic) são guardadas com o último valor de cada
mês, para cobrirem o mesmo período das séries do Banco Master.

**2. Trabalho novo — Banco Master (IF.data)**, conglomerado prudencial
`C0080367`, relatório Resumo, trimestral. Linha **contínua** no gráfico:

| Conta | Indicador | Unidade |
|---|---|---|
| 78182 | Ativo Total | R$ bi |
| 78186 | Patrimônio Líquido | R$ bi |
| 78187 | Lucro Líquido | R$ bi |
| 79664 | Índice de Basileia | % |

As duas fontes ficam na mesma tabela `bcb_series` (os códigos não se
repetem) e são declaradas juntas no dicionário `SERIES` de `bcb_client.py`,
onde o campo `fonte` (`original` ou `master`) diz de onde buscar cada série.
No gráfico (eixo de tempo), cada unidade tem seu próprio eixo Y — R$ bi à
esquerda, % e R$ à direita — e os botões acima do gráfico filtram
"Todas", só o trabalho original ou só o Banco Master.

### Como funciona o cache

Chamar a API do BCB a cada carregamento da página seria lento e
desnecessário (os dados mudam no máximo uma vez por dia). Por isso:

1. Cada série é salva na tabela `bcb_series` do Postgres da Aiven
   (`series_code`, `ref_date`, `value`, `fetched_at`).
2. A cada requisição ao dashboard, o app confere se o cache de cada série
   tem mais de `CACHE_HOURS` (padrão: 6h). Se sim, busca dados novos na API
   do BCB e faz um `UPSERT`; se não, usa o que já está no banco.
3. Se a API do BCB estiver fora do ar, o app **não quebra** — continua
   servindo os últimos dados em cache e registra um aviso no log.
4. Opcionalmente, o workflow `.github/workflows/refresh-data.yml` chama
   `POST /api/refresh` a cada 6h via `cron`, mantendo o cache quente mesmo
   sem visitas — configure o secret `APP_HEALTHCHECK_URL` (o mesmo já usado
   no health check) para habilitá-lo.

### Trocando a fonte de dados

O padrão de `app.py` (`SERIES`, `fetch_series_from_bcb`, `bcb_series`) foi
escrito para ser fácil de adaptar a outras APIs de dados abertos do governo:

- **Outras séries do próprio BCB**: basta adicionar entradas ao dicionário
  `SERIES` com o código da série (consulte o
  [catálogo do SGS](https://www3.bcb.gov.br/sgspub)).
- **IBGE (SIDRA/localidades)**: trocar `fetch_series_from_bcb` por uma
  chamada a `https://servicodados.ibge.gov.br/api/v1/...` — o formato de
  resposta é diferente, então o parsing precisa ser ajustado, mas a
  estrutura de cache (tabela, `is_cache_stale`, `upsert`) pode ser reaproveitada.
- **Portal da Transparência**: requer cadastro de token gratuito
  ([api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br/)),
  que deve ser adicionado como um novo secret/variável de ambiente.

## Streaming com Kafka

Extensão opcional que transforma a ingestão de "polling batelado" (o app
busca dados sozinho, de tempos em tempos) em **arquitetura orientada a
eventos**: um *producer* publica os dados novos do BCB num tópico Kafka, e
um *consumer* — dissociado do app — assina esse tópico e grava no mesmo
Postgres. O dashboard Flask não muda nada na leitura.

```
GitHub Actions (cron 15min)          Kafka (Aiven)              Consumer
┌────────────────┐   publica    ┌──────────────────┐  assina  ┌──────────┐
│  producer.py    │ ───────────▶ │ bcb-indicadores  │ ───────▶ │consumer.py│
│ busca no BCB    │              │     (tópico)     │          │ grava no  │
└────────────────┘               └──────────────────┘          │ Postgres  │
                                                                  └──────────┘
```

**Atenção de custo:** ao contrário do Postgres (plano `hobbyist`, gratuito
em contas elegíveis), o Aiven for Apache Kafka normalmente exige um plano
pago (`startup-2` por padrão no Terraform). Confirme disponibilidade e
custo na sua conta antes de rodar o `terraform apply`.

### Onde o consumer roda

O `consumer.py` foi escrito para funcionar dos dois jeitos, ao mesmo tempo
se você quiser (ambos compartilham o mesmo `KAFKA_GROUP_ID`, então o Kafka
nunca entrega a mesma mensagem duas vezes):

1. **Worker contínuo no Render** — sempre escutando o tópico:
   - No Render, crie um novo serviço **Background Worker** (não Web
     Service), apontando para o mesmo repositório/Dockerfile.
   - Em **Settings → Docker Command**, sobrescreva o comando padrão para
     `python consumer.py`.
   - Configure as mesmas variáveis de ambiente do app (`AIVEN_DB_URL`) mais
     as 5 variáveis de Kafka (mesmos valores dos secrets do GitHub).

2. **Job em lote no GitHub Actions** — `.github/workflows/kafka-consume-batch.yml`,
   roda a cada 20 min por 30 segundos e sai. Funciona como **rede de
   segurança**: se o worker do Render cair ou dormir, os dados não ficam
   represados por muito tempo.

Para o producer, o workflow `.github/workflows/kafka-produce.yml` já cuida
de tudo — roda a cada 15 min, sem precisar de nenhum serviço always-on.

### Testando manualmente

Pelo botão **Run workflow** na aba Actions, você pode disparar o
`Kafka - Producer` manualmente e depois checar no Grafana (ou direto no
Postgres, via PG Studio) se o `fetched_at` da tabela `bcb_series` mudou.

## Exploração com Grafana

Além do dashboard Flask "caseiro", o Terraform provisiona um serviço
**Aiven for Grafana**, para explorar os mesmos dados visualmente, sem
escrever código:

1. Depois do `terraform apply`, acesse a saída `grafana_uri` (ou o console
   da Aiven → serviço `bcb-grafana` → Overview) para pegar a URL e as
   credenciais de acesso.
2. Em **Connections → Data sources → Add data source → PostgreSQL**,
   preencha host/porta/usuário/senha do serviço `dashboard-pg` (mesmas
   informações de **Connection information** no console da Aiven).
3. Crie um painel apontando para a tabela `bcb_series` — por exemplo, uma
   série temporal filtrando por `series_code`.

Isso deixa nítida, em aula, a diferença entre um dashboard sob medida
(controle total do código, mais trabalho) e um dashboard gerenciado
(configuração via UI, menos código).

## Variáveis de ambiente da aplicação

Veja `app/.env.example`. Em produção (Render), são configuradas na aba
**Environment** do serviço — nunca commitadas no repositório.

| Variável | Obrigatória? | Descrição |
|---|---|---|
| `AIVEN_DB_URL` | Sim | String de conexão do Postgres, copiada do console da Aiven |
| `PORT` | Sim, no Render | `8080` — precisa bater com o `--bind` do `CMD` no Dockerfile (ver [Troubleshooting](#troubleshooting--problemas-comuns-no-deploy), item 8) |
| `CACHE_HOURS` | Não | Horas antes de buscar dados novos na API do BCB (padrão: 6) |

## Diagrama do pipeline

```
 GitHub (push)
      │
      ▼
 ┌───────────────┐        ┌──────────────────────┐
 │ infra.yml     │        │ ci-cd.yml            │
 │ Terraform     │        │ test → build → push  │
 │ → PG + Kafka  │        │ imagem (GHCR)         │
 │   + Grafana   │        └──────────┬───────────┘
 └──────┬────────┘                   │
        │                            ▼
        ▼                     Render (deploy hook)
  Aiven.io (PostgreSQL               │
  gerenciado, SSL)                   ▼
        ▲               App Flask (dashboard) ── lê ──▶ Aiven for Grafana
        │                            ▲
        │ upsert            AIVEN_DB_URL
        │
  consumer.py ◀── assina ── bcb-indicadores ◀── publica ── producer.py
  (Render worker                (Kafka)              (GH Actions, cron 15min)
   ou GH Actions --once)
```

## Troubleshooting — problemas comuns no deploy

Esta seção documenta os problemas reais enfrentados ao colocar este
projeto no ar pela primeira vez no Render, na ordem em que costumam
aparecer. Se o seu deploy estiver falhando, comece por aqui.

### 1. Build falha em `COPY requirements.txt .` / `not found`

```
ERROR: failed to calculate checksum ... "/requirements.txt": not found
```

Causa: o `Dockerfile` estava em `app/Dockerfile`, mas o **contexto de
build** do Render (campo "Docker Build Context Directory") apontava para
a raiz do repositório — então o `COPY requirements.txt .` procurava o
arquivo em `/requirements.txt`, que não existe (ele está em
`app/requirements.txt`).

Ajustar o campo "Docker Build Context Directory" para `app` no painel
pode resolver, mas em alguns testes esse campo **não surtiu efeito** de
forma consistente (builds seguintes usavam o mesmo contexto de antes,
mesmo salvando a mudança). A solução mais robusta e que não depende
desse campo específico: **mover o `Dockerfile` para a raiz do repo** e
usar `COPY app/requirements.txt .` / `COPY app/ .` — assim o contexto
padrão (raiz) já é o correto, sem precisar configurar nada extra.

### 2. Build falha mesmo com o Dockerfile "certo"

Se o erro persistir mostrando o **conteúdo antigo** do Dockerfile
mesmo após você editá-lo, adicione temporariamente um passo de
diagnóstico que lista o conteúdo do contexto de build:

```dockerfile
RUN find . -maxdepth 3 -not -path '*/.git*'
```

No nosso caso, isso revelou que o arquivo `requirements.txt` tinha sido
commitado com um **espaço no início do nome** (`" requirements.txt"`),
provavelmente por um erro ao renomear pelo editor web do GitHub — por
isso o `COPY app/requirements.txt .` nunca encontrava o arquivo, mesmo
o caminho estando certo. Vale a pena checar nomes de arquivo com espaços
acidentais sempre que um `COPY` falhar sem motivo aparente.

### 3. Build passa, mas o container crasha: `relation "..." does not exist`

Causa: o código que cria as tabelas (`init_db()`) só rodava dentro de
`if __name__ == "__main__":`, bloco que **nunca executa** quando o app
sobe via `gunicorn app:app` (usado em produção). Solução: mover a
chamada de `init_db()` para o nível do módulo, fora do bloco
`__main__`, para que rode sempre que o módulo é importado — inclusive
pelo gunicorn.

### 4. `ModuleNotFoundError: No module named 'bcb_client'`

Mesma causa-raiz do item 2: outro arquivo (`bcb_client.py`, nesse caso)
também tinha sido commitado com um nome ligeiramente errado. Sempre que
um `import` falhar apesar do arquivo "existir", confira o nome exato no
GitHub (não confie só na aparência visual — copie e cole o nome).

### 5. `psycopg2.errors.UniqueViolation: ... pg_type_typname_nsp_index`

Com mais de um worker do gunicorn, todos chamam `init_db()` quase ao
mesmo tempo na subida. `CREATE TABLE IF NOT EXISTS` não é perfeitamente
atômico entre transações concorrentes no Postgres, então dois workers
podem colidir tentando criar a mesma tabela — um deles recebe esse erro,
que é inofensivo (a tabela já existe). `bcb_client.py` já trata esse
caso especificamente (ver `init_db()`).

### 6. API do BCB retornando 400 Bad Request

O endpoint `/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}` da API
do BCB passou a responder 400 em testes recentes. `bcb_client.py` usa o
endpoint alternativo `/dados` com `dataInicial`/`dataFinal`, que
funciona normalmente, e recorta os últimos N pontos localmente.

### 7. Página fica carregando pra sempre / demora muito (mas sem erro)

Duas causas possíveis, ambas relacionadas a conexões com o Postgres:

- **Vazamento de conexões**: `with psycopg2_connection:` controla só
  commit/rollback, **não fecha** a conexão. Sem fechar explicitamente,
  cada requisição deixava conexões penduradas até estourar o limite do
  plano da Aiven, e novas conexões passavam a travar. `bcb_client.py`
  agora usa um **pool de conexões** (`ThreadedConnectionPool`), que
  reaproveita conexões entre requisições em vez de abrir/fechar uma nova
  a cada consulta — também deixa o carregamento da página bem mais
  rápido, já que abrir uma conexão nova custa ~1s (latência de rede até
  a Aiven + handshake SSL).
- Sem timeout explícito, uma conexão que trava (rede instável, banco
  fora do ar) trava o worker inteiro **silenciosamente**, sem nenhum
  erro no log. `connect_timeout=10` no `psycopg2.connect()` garante que,
  se isso acontecer, o erro apareça rápido e de forma clara.

### 8. "Your service is live 🎉" no log, mas o navegador dá 502 / fica carregando pra sempre

Esse é o mais sutil: o gunicorn confirma estar escutando em
`0.0.0.0:8080`, o Render até declara "live", mas nenhuma requisição
externa chega no container (nem `/healthz`, que só faz uma query
simples). O log mostra `No open HTTP ports detected on 0.0.0.0,
continuing to scan...` repetidamente.

Causa: o mecanismo de detecção automática de porta do Render às vezes
falha em identificar a porta exposta pelo container. A correção
(confirmada em vários relatos no fórum oficial do Render) é criar
**manualmente** a variável de ambiente `PORT` em Settings → Environment,
com o **mesmo valor** que o `CMD` do Dockerfile usa para o
`--bind 0.0.0.0:PORTA` (`8080` neste projeto). Atenção ao digitar o
valor — um erro comum é deixar `10000` (sugestão padrão do Render) em
vez de `8080`, o que causa o mesmo sintoma por um motivo diferente
(Render escuta na porta errada).

### Checklist rápido se o deploy não sobe

1. O `Dockerfile` está na raiz do repo, e `Docker Build Context
   Directory` é `.`?
2. Todos os nomes de arquivo em `app/` estão exatamente certos (sem
   espaço antes/depois)?
3. `AIVEN_DB_URL` está preenchida em Settings → Environment?
4. `PORT=8080` está preenchida em Settings → Environment?
5. O IP allowlist do Postgres na Aiven permite `0.0.0.0/0` (ou pelo
   menos os IPs do Render)?

## Possíveis extensões para a turma

- Adicionar um ambiente de *staging* com `workflow_dispatch` manual antes
  do deploy em produção.
- Um segundo consumer "fan-out" do mesmo tópico Kafka — por exemplo, um
  alerta quando o dólar varia mais de 1% num dia, mostrando que múltiplos
  consumidores podem reagir ao mesmo evento de formas diferentes.
- Trocar o Aiven for Grafana por **Aiven for OpenSearch**, indexando os
  eventos do Kafka para busca full-text e análise de logs.
