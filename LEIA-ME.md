# Atelier Inove Financeiro

Aplicativo interno para cadastrar despesas do laboratorio, dividir automaticamente entre os socios e controlar producao, creditos, debitos e saldo por socio.

## Rodar local

```bash
pip install -r requirements.txt
python server.py
```

Abra `http://127.0.0.1:8798`.

## O que faz

- Cadastro de socios sem lista fixa.
- Cadastro de despesas sem lista fixa.
- Botoes para escolher quem participa de cada despesa.
- Divisao automatica do valor entre os selecionados.
- Debito e credito no cadastro de lancamentos.
- Despesa recorrente mensal com um clique.
- Geracao das recorrentes do proximo mes para facilitar o fechamento.
- Admin com PIN para controlar producao, creditos e debitos por socio.
- Saldo por socio: producao + creditos - debitos - despesas rateadas.
- Graficos simples de producao, creditos e debitos.
- Totais por socio no mes.
- Historico de despesas.
- Editar, excluir e tornar lancamento recorrente.
- Exportar CSV.
- PWA instalavel no celular.

## Admin

O PIN padrao local e `inove2026`. No Render, configure a variavel `ADMIN_PIN` para trocar esse PIN.
