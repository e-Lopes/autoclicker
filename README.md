# AutoClicker

Autoclick em background para uma janela alvo, sem usar o mouse real do usuario.
Interface moderna feita com `customtkinter`.

## Uso rapido

1. Abra `AutoClicker.exe`.
2. Escolha o processo alvo.
3. Clique em `VALIDAR`.
4. Clique em `CAPTURAR` e marque o ponto.
5. Clique em `TESTE`.
6. Clique em `INICIAR`.

Atalhos:
- `F6`: teste
- `F7`: iniciar
- `F8`: parar
- `F9`: capturar ponto

## Gerar release

1. Execute `build_release.bat`.
2. Use os arquivos em `release`:
   - `AutoClicker.exe`
   - `CriarAtalhoDesktop.bat`
   - `AutoClicker-win64.zip`
3. No computador da outra pessoa:
   - extraia o `.zip`;
   - execute `CriarAtalhoDesktop.bat` (ele cria o atalho no Desktop automaticamente).

O script limpa a pasta `release` antes de gerar novos arquivos.
