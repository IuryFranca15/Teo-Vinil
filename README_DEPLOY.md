# Teo Vinil — Vercel + Gemini 3.8 Flash

## Arquitetura
- `public/`: interface web.
- `api/review.py`: função Python serverless que consulta o Last.fm, calcula o perfil e envia o prompt à API Gemini pela Interactions API.
- Modelo padrão: `gemini-3.8-flash` (pode ser alterado por variável de ambiente).

## 1. Criar a chave Gemini
1. Acesse https://aistudio.google.com/apikey.
2. Entre na sua conta Google e crie uma API key.
3. Copie a chave. Não a envie em conversas, não a coloque no frontend e não a publique no GitHub.

## 2. Configurar a Vercel
No projeto `teo-vinil`, abra **Settings → Environment Variables** e adicione:
- `GEMINI_API_KEY` = sua chave do Google AI Studio.
- `GEMINI_MODEL` = `gemini-3.8-flash` (opcional; esse já é o padrão).

Remova as variáveis antigas `OLLAMA_API_KEY` e `OLLAMA_MODEL`, se existirem. Salve e faça um novo Deploy (Redeploy também funciona depois de salvar as variáveis).

## 3. Gratuidade e limites
A API Gemini oferece uma faixa gratuita para modelos elegíveis, incluindo Gemini 3.8 Flash, sujeita a limites de requisições e tokens que podem variar por modelo, projeto e região. O serviço pode responder com erro 429 quando a cota for atingida. Não configure faturamento se a intenção é manter o projeto sem cobrança; nesse caso, ao atingir a cota, a geração ficará temporariamente indisponível até a renovação.

A faixa gratuita pode ter condições de uso de dados diferentes da faixa paga. Consulte a página oficial de preços e os termos antes de usar dados que não queira compartilhar com o Google: https://ai.google.dev/gemini-api/docs/pricing. Limites atuais: https://ai.google.dev/gemini-api/docs/rate-limits.

## 4. Dados do Last.fm
A API key do Last.fm é enviada pelo navegador à função `/api/review`, usada apenas para consultar o Last.fm. Ela não é incluída no prompt enviado ao Gemini nem devolvida na resposta. Não solicite senha do Last.fm nem Shared Secret.

## 5. Observações
- Não é necessário instalar Ollama nem qualquer modelo no computador.
- A Vercel Hobby também possui limites próprios de execução e uso.
- Esta versão não implementa proteção robusta contra abuso/rate limiting; antes de divulgar amplamente, considere adicionar limitação por IP e monitoramento.
- Se a cota gratuita do Gemini acabar, a API retornará uma mensagem clara para tentar novamente mais tarde.
