# Checklist Play Store - DivideBem

## Já preparado

- App web/PWA publicado no Render.
- Cadastro/login com dados separados por usuário.
- Política de privacidade publicada.
- Projeto Android Capacitor em `android/`.
- Pacote Android `com.dividebem.app`.
- Ícones Android gerados.
- Metadados de loja em `playstore/listing-pt-BR.md`.

## Ainda necessário no PC

- Instalar Java JDK 17 ou superior.
- Instalar Android Studio.
- Instalar Android SDK e Build Tools pelo Android Studio.
- Abrir o projeto `android/` no Android Studio.
- Sincronizar Gradle.
- Gerar assinatura de release.
- Gerar arquivo `.aab`.

## Comandos após instalar Java/Android Studio

```powershell
$env:Path='C:\Users\Home\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:Path
pnpm android:sync
pnpm android:open
```

No Android Studio:

1. Abra `Build > Generate Signed Bundle / APK`.
2. Escolha `Android App Bundle`.
3. Crie ou selecione a chave de assinatura.
4. Escolha variante `release`.
5. Gere o `.aab`.

## Play Console

- Criar app no Google Play Console.
- Categoria: Finanças.
- Preencher ficha com `playstore/listing-pt-BR.md`.
- Enviar ícone 512x512.
- Enviar screenshots.
- Informar política de privacidade.
- Preencher Segurança dos dados.
- Começar por teste interno.
