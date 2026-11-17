# Last.fm Critic — Vercel + Ollama

## Arquitetura
- `public/`: frontend estático (inclui a foto do Teo).
- `api/review.py`: função Python serverless. Busca os dados públicos do Last.fm, calcula as métricas com `critic.py` e envia somente o perfil agregado + instruções ao Ollama Cloud.
- `https://ollama.com/api/chat`: inferência na nuvem do Ollama. Não é necessário instalar Ollama nem baixar modelos no computador.

## 1. Preparar o Ollama Cloud
1. Crie uma API key em https://ollama.com/settings/keys (ou na seção API Keys da sua conta).
2. Escolha um modelo disponível no catálogo Cloud. O padrão configurado neste pacote é `gpt-oss:120b`; confirme a disponibilidade na sua conta/catálogo antes de publicar.
3. Nunca cole a chave no frontend, no código ou no GitHub.

A API oficial usa `POST https://ollama.com/api/chat` e autenticação `Authorization: Bearer SUA_CHAVE`. Consulte https://docs.ollama.com/cloud e https://docs.ollama.com/api/authentication.

## 2. Publicar na Vercel
1. Envie esta pasta para um repositório GitHub.
2. Importe o repositório na Vercel.
3. Defina a pasta raiz do projeto como a pasta que contém `vercel.json`, `api/` e `public/` (neste pacote, a própria raiz).
4. Em **Settings → Environment Variables**, adicione:
   - `OLLAMA_API_KEY` = sua chave secreta do Ollama Cloud
   - `OLLAMA_MODEL` = `gpt-oss:120b` (ou o identificador exato de outro modelo Cloud disponível)
5. Faça Deploy.

Não é necessário configurar `OLLAMA_URL`: o endpoint Cloud está definido no backend. Não é necessário configurar `LASTFM_API_KEY`: cada visitante cola sua própria API key no formulário.

## 3. Como a chave do visitante é tratada
A API key do Last.fm é enviada pelo navegador à função `/api/review`, que a usa para consultar a API do Last.fm. Ela não é colocada no prompt do Ollama, não é devolvida ao navegador na resposta e não é gravada pelo frontend. Não solicite senha do Last.fm nem o Shared Secret. Evite registrar a chave em logs.

## Limitações importantes
- O uso do Ollama Cloud pode exigir plano pago ou créditos de uso; não presuma processamento ilimitado gratuito. Consulte https://ollama.com/pricing.
- A Vercel Hobby tem limites de duração, uso e finalidade. Verifique os limites atuais antes de divulgar o site.
- Esta versão não implementa ainda proteção robusta contra abuso/rate limiting. Antes de divulgar publicamente, adicione rate limiting, monitoramento de custos e limites de uso.
- A função está configurada para 60 segundos. Se a geração exceder esse tempo, a Vercel poderá interromper a requisição.
- A foto está em `public/teo-vinil.png`. Substitua esse arquivo se quiser trocar o retrato.

## 3. Como a chave é tratada
A API key do Last.fm é enviada pelo navegador à função `/api/review`, que a usa para consultar a API do Last.fm. Ela não é colocada no prompt do Ollama, não é devolvida ao navegador na resposta e não é gravada pelo frontend. Não solicite senha do Last.fm nem o Shared Secret. A chave de API não é uma senha de conta, mas ainda assim evite registrá-la em logs.

## Limitações importantes
- O modelo Ollama precisa estar ligado e acessível durante a geração. Se o servidor desligar ou ficar lento, a função pode atingir o limite de tempo da Vercel.
- Consultas a `artist.getInfo` podem levar alguns segundos; a primeira chamada pode ser mais lenta. O cache temporário fica em `/tmp` e pode desaparecer entre execuções.
- A configuração de rede do Ollama deve permitir chamadas HTTPS originadas pela Vercel e exigir autenticação no proxy. Esta versão usa endpoint sem autenticação no código; se o proxy exigir token, será necessário adicionar uma variável `OLLAMA_AUTH_TOKEN` e cabeçalho correspondente.
- A foto está em `public/teo-vinil.png`. Substitua esse arquivo se quiser trocar o retrato.
