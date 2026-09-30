# Windows Optimizer

Ferramenta de **diagnóstico, limpeza, manutenção e otimização** para Windows 10/11, em Python,
com a filosofia **segurança primeiro**:

> **analisar → recomendar → confirmar → executar → registrar → permitir desfazer**

Nada é alterado sem que você escolha, veja exatamente o que será feito e confirme.

---

## O que o programa faz

| Área | Recursos |
|---|---|
| **Saúde do PC** | CPU, RAM, discos, tempo ligado, temporários, temperaturas (quando confiáveis), antivírus/firewall, Windows Update, inicialização, programas instalados, processos mais pesados. **Pontuação de saúde explicada** fator a fator e **recomendações adaptadas ao hardware**. |
| **Limpeza** | `%TEMP%`, `C:\Windows\Temp`, TEMP de outros usuários, cache do Windows/relatórios de erro, dumps antigos, miniaturas, shaders, downloads antigos do Windows Update, cache de Otimização de Entrega, logs antigos do Windows, Prefetch, caches de apps e de navegadores. Mostra "**X GB podem ser liberados**" com as opções **[1] Limpar tudo que for seguro**, **[2] Escolher categorias** e **[3] Cancelar**. Lixeira (com confirmação) e cache de DNS. |
| **Inicialização** | Nome, caminho, fabricante, local, impacto aproximado, estado. Desativa pelo mesmo mecanismo do Gerenciador de Tarefas (`StartupApproved`), sem apagar nada. |
| **Processos** | Nome, PID, CPU, RAM e caminho; ordenar, filtrar e finalizar (processos críticos protegidos). |
| **Serviços** | Explica a função dos serviços, sugere mudanças conservadoras (em geral "Manual") e só aplica com confirmação. |
| **Armazenamento** | Espaço por unidade, SSD/HDD, sistema de arquivos, TRIM, otimização nativa (ReTrim em SSD, nunca desfragmentação), pastas que mais ocupam espaço. |
| **Rede** | Adaptadores, flush DNS, renovar IP, liberar/renovar DHCP, testes de conexão e DNS. |
| **Windows** | Status do Windows Update (e busca de atualizações pendentes, só leitura), Defender/Firewall, plano de energia, ponto de restauração, SFC, DISM, CHKDSK (só leitura / `/scan`) e limpeza de componentes. |
| **Navegadores** | Chrome, Edge, Firefox, Brave, Opera, Opera GX, Vivaldi, Chromium: tamanho e limpeza **somente do cache**. |
| **Ferramentas** | Arquivos grandes/antigos/ISOs/instaladores/dumps, duplicados por hash, apps em segundo plano, programas instalados, relatório. |
| **Histórico** | Data/hora, alteração, estado anterior, novo estado e **Desfazer**. |

**Modo Seguro** (botão *OTIMIZAÇÃO SEGURA*): apenas limpeza de temporários e caches do sistema;
nenhuma configuração do Windows é alterada.
**Modo Avançado**: mostra otimizações adicionais (inicialização, serviços, caches extras, TRIM,
limpeza de componentes, ponto de restauração); cada uma é listada e só roda se você marcar e confirmar.
Não há "modo milagroso".

---

## Segurança — o que o programa **nunca** faz

- Não desativa **Windows Defender**, **Firewall**, **Windows Update** nem serviços de segurança. Eles estão
  numa lista protegida e o programa se recusa a alterá-los.
- Não altera políticas de segurança e não faz alterações em massa no Registro. As únicas escritas no
  Registro são os valores `StartupApproved` de itens que você escolheu desativar, com backup `.reg` prévio.
- Não apaga arquivos pessoais: Desktop, Documentos, Downloads, Imagens, Vídeos, Músicas e OneDrive são
  protegidos na limpeza.
- Não apaga nada em `C:\Windows\System32`, `SysWOW64`, `WinSxS`, `Program Files` etc.
- Não segue links simbólicos/junctions (proteção contra sair da pasta autorizada; revalidado no momento da
  exclusão).
- Não remove arquivos em uso. Eles são ignorados e registrados no log.
- Não finaliza processos críticos (`lsass`, `csrss`, `winlogon`, `svchost`, Defender…).
- Não desativa itens de inicialização de segurança, do Windows ou de drivers/hardware (🔒).
- Não usa desfragmentação em SSD, `DISM /ResetBase`, `CHKDSK /f` automático, formatação, `rd /s` ou
  comandos via shell.
- Arquivos grandes e duplicados escolhidos por você vão para a **Lixeira**, de onde podem ser restaurados.
  Em duplicados, pelo menos uma cópia de cada grupo é sempre mantida.
- Não fica rodando em segundo plano, não cria serviço nem tarefa agendada. Só executa enquanto está aberto.
  A única conexão com a internet é a verificação de atualizações no GitHub (somente leitura); nada é
  baixado ou instalado sem a sua confirmação.

Outras camadas: idade mínima por categoria (ex.: `%TEMP%` só remove arquivos com mais de 24 h, contando
também a data de **criação**), extensões executáveis protegidas nas pastas do Windows, erros tratados
item a item (um arquivo com problema nunca interrompe a operação) e apenas uma operação que altera o
sistema por vez.

---

## Instalação

Requisitos: **Windows 10/11** e **Python 3.10+** (testado com 3.14).

```bat
cd windows-optimizer
python -m pip install -r requirements.txt
```

Dependências: `psutil` e `customtkinter`. Todo o resto usa a biblioteca padrão e ferramentas nativas do
Windows (`ipconfig`, `powercfg`, `sc`, `fsutil`, `defrag`, `sfc`, `dism`, `chkdsk`, PowerShell).

## Como executar

| Forma | Comando |
|---|---|
| Modo usuário (recomendado) | `python main.py` ou duplo clique em `run.bat` |
| Como Administrador | duplo clique em `run_as_admin.bat` (mostra o UAC) |
| Relatório no console, sem interface | `python main.py --report` |
| Limpeza pelo console (analisa, mostra, pede `[S/N]`) | `python main.py --clean` |
| Só analisar, sem apagar nada | `python main.py --clean --dry-run` |
| Instruções para agendar a limpeza | `python main.py --schedule` |

Opções de `--clean`: `--yes` (não pergunta; necessário em execução agendada e também esvazia a Lixeira),
`--no-recycle-bin`, `--all` (inclui as categorias opcionais, não só as recomendadas) e `--quiet`.
Sem Administrador, as categorias que o exigem são puladas. O programa **não** cria a tarefa agendada:
`--schedule` apenas mostra o passo a passo para você configurar no Agendador de Tarefas.
Com o `.exe` (sem console), use `--clean --yes` para agendamento; o resultado fica no log.

O programa abre **sem** privilégios elevados. Quando uma operação exige Administrador (limpar
`C:\Windows\Temp`, serviços, itens de inicialização de todos os usuários, SFC/DISM/CHKDSK, ponto de
restauração, otimização de unidade, TRIM), a interface avisa e oferece **Reiniciar como Administrador**.

## Gerar o .exe

```bat
build_exe.bat
```

Gera `dist\WindowsOptimizer\WindowsOptimizer.exe` com PyInstaller (`--onedir --windowed`).
Equivalente manual:

```bat
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --onedir --name WindowsOptimizer --collect-data customtkinter main.py
```

Se a pasta do `.exe` não for gravável (por exemplo, em `Program Files`), logs e backups vão para
`%LOCALAPPDATA%\WindowsOptimizer`.

Instalador: `build_installer.bat` gera `installer_output\WindowsOptimizer-Setup-<versão>.exe`
(Inno Setup 6), com a versão lida de `APP_VERSION` em `utils/helpers.py`.

## Atualizações

No menu lateral, **⟳ Verificar atualizações** consulta o último Release do GitHub. Ao abrir, o
programa também verifica em silêncio e, se houver versão nova, mostra **⬆ Atualizar para X.Y.Z**
no menu (nunca instala sozinho).

Ao confirmar, o programa:

1. baixa `WindowsOptimizer-Setup-<versão>.exe` do Release (só HTTPS e só de endereços do GitHub, até 300 MB);
2. confere o **SHA-256** publicado no Release; se não bater, ou se não houver hash, o arquivo é descartado e nada é instalado;
3. abre o instalador em modo silencioso e fecha; o instalador espera o programa terminar, instala por cima (mesma pasta
   e mesmo modo, só para você ou para todos) e **reabre o programa**. Histórico, backups e logs são mantidos.

A atualização automática só funciona na cópia instalada pelo instalador. Rodando pelo Python ou pela
pasta `dist`, o botão avisa da versão nova e abre a página de download.

**Configurar (uma vez):** crie um repositório no GitHub e coloque `"dono/repositorio"` em `UPDATE_REPO`
(`utils/helpers.py`); para testar sem editar o código, use a variável de ambiente `WO_UPDATE_REPO`.
Sem isso, o botão avisa que a origem não foi configurada e a checagem ao abrir fica desligada.

**Publicar uma versão nova:**

```bat
rem 1. aumente APP_VERSION em utils\helpers.py (ex.: 1.0.0 -> 1.1.0)
build_exe.bat
build_installer.bat
python publish_release.py "O que mudou nesta versão"
```

O `publish_release.py` usa o GitHub CLI (`winget install GitHub.cli`, depois `gh auth login`), cria o
Release `v<versão>` e envia o instalador e o arquivo `.sha256`. Os programas instalados passam a
encontrar a versão nova na próxima verificação.

---

## Logs, histórico e desfazer

- `logs/optimization_YYYY-MM-DD.log`: início e fim de cada operação, arquivos removidos e ignorados,
  alterações de configuração (com estado anterior), erros e resultado final.
- `logs/report_YYYY-MM-DD_HHMMSS.txt`: relatórios gerados.
- `backups/history.json`: histórico de ações (data/hora, descrição, estado anterior, novo estado).
- `backups/reg_*.reg`: backup das chaves do Registro antes de alterá-las.

| Ação | Pode desfazer? |
|---|---|
| Ativar/desativar item de inicialização | ✔ pelo Histórico |
| Tipo de inicialização de serviço | ✔ pelo Histórico |
| Plano de energia | ✔ pelo Histórico |
| Ativar TRIM | ✔ pelo Histórico |
| Arquivos grandes/duplicados | ✔ pela Lixeira do Windows |
| Temporários e caches | ✘ (são recriados pelos programas; informado antes da confirmação) |
| Esvaziar Lixeira | ✘ (confirmação reforçada) |
| Finalizar processo | ✘ |

Para mudanças maiores (serviços/inicialização), o Modo Avançado oferece criar um **ponto de restauração**
antes.

---

## Estrutura

```
windows-optimizer/
├── main.py                  ponto de entrada (GUI ou --report)
├── gui/
│   ├── app.py               janela, menu, cabeçalho, barra de status
│   ├── widgets.py           tabela, diálogos, executor de tarefas em thread
│   ├── optimization_dialog.py   Modo Seguro / Avançado
│   └── pages/               uma página por item do menu
├── core/
│   ├── system_info.py       diagnóstico (somente leitura)
│   ├── temp_cleaner.py      temporários/caches, Lixeira
│   ├── cli_cleanup.py       limpeza pelo console (--clean) e instruções de agendamento
│   ├── startup_manager.py   inicialização (reversível)
│   ├── process_manager.py   processos e apps em segundo plano
│   ├── service_manager.py   serviços (base de conhecimento + sugestões)
│   ├── storage_analyzer.py  unidades, TRIM, pastas, arquivos grandes, duplicados
│   ├── browser_cleaner.py   cache de navegadores
│   ├── network_tools.py     DNS/IP/testes
│   ├── windows_tools.py     plano de energia, SFC/DISM/CHKDSK
│   ├── restore_manager.py   histórico, desfazer, backups, ponto de restauração
│   ├── updater.py           atualização pelo GitHub Releases (hash SHA-256 obrigatório)
│   ├── recommendations.py   análise, pontuação, recomendações, plano
│   ├── report.py            relatório
│   └── security.py          listas protegidas e validação de caminhos
├── utils/  logger.py · admin.py · helpers.py
├── tests/  test_safety.py
├── logs/ · backups/
├── run.bat · run_as_admin.bat · build_exe.bat · build_installer.bat · installer.iss
├── publish_release.py       publica uma versão nova no GitHub Releases
└── requirements.txt · README.md
```

## Testes

```bat
python -m unittest discover -s tests -v
```

Os testes rodam em pastas temporárias próprias e **não alteram o sistema**. Eles cobrem:
- recusa de raízes críticas e pessoais; nenhuma categoria embutida aponta para pasta pessoal;
- arquivos bloqueados (em uso) são ignorados sem interromper a limpeza;
- pastas inexistentes e pastas sem permissão de acesso;
- somente leitura, filtro de idade, extensões protegidas;
- TEMP de outros usuários: nunca o usuário atual, perfis especiais ou aliases (junctions);
- Prefetch, logs do Windows e TEMP de outros usuários exigem Administrador e ficam fora da limpeza segura;
- limpeza pelo console: `--dry-run` e ausência de confirmação nunca apagam nada;
- junctions não são seguidas, inclusive se criadas **entre a análise e a exclusão**;
- ausência de navegadores; navegador falso: só o cache é apagado (Cookies, Login Data, Bookmarks e
  History ficam);
- duplicados (nunca apaga; impede remover todas as cópias), arquivos grandes, múltiplas unidades;
- histórico/desfazer e histórico corrompido;
- no modo sem Administrador, operações privilegiadas são recusadas com mensagem clara;
- comandos inexistentes e timeout nunca lançam exceção.

## Limitações conhecidas

- **Temperatura de CPU**: o Windows não a expõe de forma padronizada. São mostradas apenas a GPU NVIDIA
  (via `nvidia-smi`) e a zona térmica ACPI (com Administrador, marcada como aproximada).
- **Impacto na inicialização** é *estimado* pelo uso atual de RAM do programa. O Windows mede o impacto
  real apenas durante o boot.
- **Ponto de restauração**: o Windows cria no máximo 1 a cada 24 h por padrão, e o programa não altera
  essa política. Exige que a Proteção do Sistema esteja ativa.
- Limpeza de cookies/histórico de navegadores **não** é oferecida, de propósito.
