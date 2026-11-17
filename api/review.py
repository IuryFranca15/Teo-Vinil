import json
import os
import sys
import urllib.request
import urllib.error
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

            ollama_api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
            model = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b").strip()
            if not ollama_api_key:
                return self._send(503, {
                    "error": "O Ollama Cloud ainda não foi configurado. Defina OLLAMA_API_KEY nas variáveis da Vercel."
                })
            payload = json.dumps({
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "options": {"temperature": 0.75, "num_predict": 2800}
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://ollama.com/api/chat", data=payload, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + ollama_api_key
                }
            )
            with urllib.request.urlopen(req, timeout=240) as response:
                result = json.loads(response.read().decode("utf-8"))
            review = result.get("message", {}).get("content", "").strip()
            if not review:
                return self._send(502, {"error": "O Ollama respondeu sem texto."})
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
            return self._send(502, {"error": "Erro ao consultar o Ollama Cloud: HTTP %s — %s" % (exc.code, detail)})
        except Exception as exc:
            return self._send(500, {"error": "Falha ao gerar a review: " + str(exc)[:400]})

    def do_GET(self):
        if self.path == "/api/health":
            return self._send(200, {"ok": True, "service": "Last.fm Critic"})
        return self._send(404, {"error": "Rota não encontrada."})
