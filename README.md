# AutoClicker DU

Auto click em janela especifica, com foco em clique em background para nao atrapalhar o uso normal da maquina.

## Para usar (usuario final)

1. Abra `AutoClicker.exe`.
2. Clique em `Atualizar` e selecione o jogo/app alvo.
3. Clique em `Validar alvo`.
4. Clique em `Capturar e testar`.
5. Se funcionar, clique em `Iniciar autoclick`.

Atalhos:
- `F6`: testar 1 clique
- `F7`: iniciar/parar
- `F8`: capturar ponto

## Gerar release para distribuir

1. Execute `build_release.bat`.
2. Aguarde o fim do processo.
3. Envie o `.zip` gerado na pasta `release`.

O pacote gerado inclui:
- `AutoClicker.exe`
- `Executar AutoClicker.bat`
- `LEIA-ME.txt`
- `SHA256.txt`

## Requisitos de build

- Windows
- Python 3.11+
- Dependencias em `requirements.txt`

## Observacoes importantes

- Se o jogo roda como Administrador, rode o AutoClicker como Administrador tambem.
- Alguns jogos com anti-cheat podem bloquear mensagens de clique em background.
