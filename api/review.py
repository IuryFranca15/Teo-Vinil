import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import critic
from critic import LastFM, LastFMError, fetch_raw, build_profile, build_prompt

critic.THROTTLE = 0.05  # em serverless cada segundo conta; a escuta é sequencial

PERIODS = {"overall", "7day", "1month", "3month", "6month", "12month"}
# A função tem maxDuration=60 s na Vercel; paramos de tentar antes disso.
BUDGET_SECONDS = float(os.environ.get("TIME_BUDGET", "54"))
RETRYABLE = {429, 500, 502, 503, 504}

DEFAULT_GEMINI_MODELS = "gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"


class LLMUnavailable(Exception):
    def __init__(self, message, codes):
        super().__init__(message)
        self.codes = codes


# ------------------------------------------------------------------ provedores
def _post_json(url, headers, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def gemini_text(system_prompt, user_prompt, model, api_key, timeout):
    """Interactions API (a mesma que você já estava usando)."""
    result = _post_json(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        {"x-goog-api-key": api_key},
        {
            "model": model,
            "system_instruction": system_prompt,
            "input": user_prompt,
            "generation_config": {"temperature": 0.75, "max_output_tokens": 2800},
            "store": False,
        },
        timeout,
    )
    parts = []
    for step in result.get("steps", []):
        if step.get("type") == "model_output":
            for item in step.get("content", []):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(item["text"])
    return "\n".join(parts).strip()


def openai_compat_text(system_prompt, user_prompt, base_url, api_key, model, timeout):
    """Qualquer provedor com API estilo OpenAI (Groq, OpenRouter, Mistral, etc.)."""
    result = _post_json(
        base_url.rstrip("/") + "/chat/completions",
        {"Authorization": "Bearer " + api_key},
        {
            "model": model,
            "temperature": 0.8,
            "max_tokens": 2800,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout,
    )
    return (result.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def build_attempts(system_prompt, user_prompt):
    attempts = []
    gkey = os.environ.get("GEMINI_API_KEY", "").strip()
    models = os.environ.get("GEMINI_MODELS") or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODELS
    if gkey:
        for m in [x.strip() for x in models.split(",") if x.strip()]:
            attempts.append(("gemini:" + m,
                             lambda t, m=m: gemini_text(system_prompt, user_prompt, m, gkey, t)))

    fb_url = os.environ.get("FALLBACK_BASE_URL", "").strip()
    fb_key = os.environ.get("FALLBACK_API_KEY", "").strip()
    fb_model = os.environ.get("FALLBACK_MODEL", "").strip()
    if fb_url and fb_key and fb_model:
        attempts.append(("fallback:" + fb_model,
                         lambda t: openai_compat_text(system_prompt, user_prompt, fb_url, fb_key, fb_model, t)))
    return attempts


def generate(system_prompt, user_prompt, deadline):
    """Tenta cada modelo em ordem; 2 tentativas por modelo em erros temporários."""
    attempts = build_attempts(system_prompt, user_prompt)
    if not attempts:
        raise LLMUnavailable("Nenhum provedor de IA configurado (defina GEMINI_API_KEY na Vercel).", set())

    codes = set()
    for label, call in attempts:
        for attempt in range(2):
            remaining = deadline - time.time()
            if remaining < 6:
                raise LLMUnavailable("Tempo esgotado antes de algum modelo responder.", codes)
            try:
                text = call(min(30.0, remaining - 2))
                if text:
                    return text, label
                break  # resposta vazia: próximo modelo
            except urllib.error.HTTPError as exc:
                codes.add(exc.code)
                if exc.code in RETRYABLE and attempt == 0:
                    time.sleep(min(1.5, max(0.0, deadline - time.time() - 8)))
                    continue
                break  # 400/403/404 ou tentativas esgotadas: próximo modelo
            except (urllib.error.URLError, OSError, ValueError):
                codes.add(0)  # timeout/conexão/JSON inválido
                break
    raise LLMUnavailable("Todos os modelos falharam.", codes)


def friendly_llm_error(codes):
    if codes and codes <= {401, 403}:
        return "O Gemini recusou a chave. Confira GEMINI_API_KEY nas variáveis da Vercel."
    if 429 in codes and not (codes & {500, 502, 503, 504}):
        return "A cota gratuita da IA acabou por agora. Tente de novo mais tarde."
    return "Os modelos de IA estão sobrecarregados neste momento (isso costuma passar em minutos)."


# ---------------------------------------------------------------------- handler
class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        started = time.time()
        if self.path != "/api/review":
            return self._send(404, {"error": "Rota não encontrada."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                return self._send(413, {"error": "Requisição muito grande."})
            body = json.loads(self.rfile.read(length) or b"{}")
            username = str(body.get("username", "")).strip()
            api_key = str(body.get("apiKey", "")).strip() or os.environ.get("LASTFM_API_KEY", "").strip()
            period = str(body.get("period", "overall"))
            lang = " ".join(str(body.get("language", "Português do Brasil")).split())[:30] or "Português do Brasil"
            if not username or len(username) > 80:
                return self._send(400, {"error": "Informe um usuário válido do Last.fm."})
            if not api_key or len(api_key) > 300:
                return self._send(400, {"error": "Cole sua API key do Last.fm."})
            if period not in PERIODS:
                return self._send(400, {"error": "Período inválido."})

            # use_cache=False: o disco da Vercel é somente leitura
            raw = fetch_raw(LastFM(api_key, use_cache=False), username, period, 12)
            profile = build_profile(raw)
            system_prompt, user_prompt = build_prompt(profile, lang)

            try:
                review, used = generate(system_prompt, user_prompt, started + BUDGET_SECONDS)
            except LLMUnavailable as exc:
                # Último recurso: devolve o prompt pronto para o visitante colar em qualquer chat.
                return self._send(503, {
                    "error": friendly_llm_error(exc.codes) if exc.codes else str(exc),
                    "prompt": "### INSTRUÇÕES\n\n" + system_prompt + "\n\n### PEDIDO\n\n" + user_prompt,
                })

            return self._send(200, {
                "username": profile.get("usuario"),
                "period": period,
                "review": review,
                "model": used,
                "profile": profile,
            })
        except LastFMError as exc:
            return self._send(422, {"error": "Last.fm: " + str(exc)})
        except Exception as exc:
            return self._send(500, {"error": "Falha ao gerar a review: " + str(exc)[:400]})

    def do_GET(self):
        if self.path == "/api/health":
            return self._send(200, {"ok": True, "service": "Last.fm Critic"})
        return self._send(404, {"error": "Rota não encontrada."})
