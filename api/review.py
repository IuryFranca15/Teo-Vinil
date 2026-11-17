import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from critic import LastFM, LastFMError, fetch_raw, build_profile, build_prompt

PERIODS = {"overall", "7day", "1month", "3month", "6month", "12month"}

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
        if self.path != "/api/review":
            return self._send(404, {"error": "Rota não encontrada."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                return self._send(413, {"error": "Requisição muito grande."})
            body = json.loads(self.rfile.read(length) or b"{}")
            username = str(body.get("username", "")).strip()
            api_key = str(body.get("apiKey", "")).strip()
            period = str(body.get("period", "overall"))
            lang = str(body.get("language", "Português do Brasil"))
            if not username or len(username) > 80:
                return self._send(400, {"error": "Informe um usuário válido do Last.fm."})
            if not api_key or len(api_key) > 300:
                return self._send(400, {"error": "Cole sua API key do Last.fm."})
            if period not in PERIODS:
                return self._send(400, {"error": "Período inválido."})

            raw = fetch_raw(LastFM(api_key), username, period, 20)
            profile = build_profile(raw)
            system_prompt, user_prompt = build_prompt(profile, lang)

            gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
            if not gemini_api_key:
                return self._send(503, {
                    "error": "O Gemini ainda não foi configurado. Defina GEMINI_API_KEY nas variáveis da Vercel."
                })

            payload = json.dumps({
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.75,
                    "maxOutputTokens": 2800
                }
            }).encode("utf-8")
            endpoint = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                + model + ":generateContent?key=" + urllib.parse.quote(gemini_api_key, safe="")
            )
            req = urllib.request.Request(
                endpoint, data=payload, method="POST",
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=240) as response:
                result = json.loads(response.read().decode("utf-8"))

            candidates = result.get("candidates", [])
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            review = "\n".join(
                part.get("text", "") for part in parts if part.get("text")
            ).strip()
            if not review:
                feedback = candidates[0].get("finishReason", "sem detalhes") if candidates else "sem candidatos"
                return self._send(502, {
                    "error": "O Gemini não retornou uma resenha em texto (" + str(feedback) + "). Tente novamente."
                })
            return self._send(200, {
                "username": profile.get("usuario"),
                "period": period,
                "review": review,
                "profile": profile
            })
        except LastFMError as exc:
            return self._send(422, {"error": "Last.fm: " + str(exc)})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 429:
                message = "Limite gratuito do Gemini atingido. Aguarde a renovação da cota e tente novamente."
            elif exc.code == 403:
                message = "O Gemini recusou a chave ou o acesso ao modelo. Confira GEMINI_API_KEY e o projeto no Google AI Studio."
            elif exc.code == 400:
                message = "O Gemini rejeitou a solicitação. Verifique o modelo e os dados enviados."
            else:
                message = "Erro ao consultar o Gemini: HTTP %s — %s" % (exc.code, detail)
            return self._send(502, {"error": message})
        except Exception as exc:
            return self._send(500, {"error": "Falha ao gerar a review: " + str(exc)[:400]})

    def do_GET(self):
        if self.path == "/api/health":
            return self._send(200, {"ok": True, "service": "Last.fm Critic"})
        return self._send(404, {"error": "Rota não encontrada."})
