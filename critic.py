#!/usr/bin/env python3
"""lastfm-critic v1

Lê o perfil de alguém no Last.fm, calcula métricas do gosto musical e pede a um
modelo (Claude) para escrever uma "review" falada do perfil, no tom definido em
persona.md. Só usa a biblioteca padrão do Python.

Uso:
  export LASTFM_API_KEY=...        # https://www.last.fm/api/account/create
  export ANTHROPIC_API_KEY=...     # https://console.anthropic.com
  python3 critic.py SEU_USUARIO --period 6month

  python3 critic.py --demo --dry-run   # sem chaves: mostra métricas e prompt
"""
import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
PERIODS = ["overall", "7day", "1month", "3month", "6month", "12month"]
CACHE_TTL = 6 * 3600

# tags que não dizem nada sobre som/gênero
STOP_TAGS = {
    "seen live", "favorites", "favorite", "favourites", "favourite", "albums i own",
    "my music", "love", "awesome", "good", "best", "spotify", "check out", "loved",
    "female vocalists", "male vocalists", "female vocalist", "male vocalist",
    "american", "british", "canadian", "australian", "swedish", "usa", "uk",
    "english", "brazilian", "under 2000 listeners", "all", "music", "singer-songwriter",
}


# ----------------------------------------------------------------- Last.fm ---
class LastFMError(Exception):
    pass


def as_list(x):
    """A API devolve dict quando há um único item; normaliza para lista."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


class LastFM:
    def __init__(self, api_key, use_cache=True):
        self.key = api_key
        self.use_cache = use_cache
        self.cache_dir = Path(os.environ.get("LASTFM_CACHE_DIR", "/tmp/lastfm-critic-cache"))
        if use_cache:
            self.cache_dir.mkdir(exist_ok=True)

    def call(self, method, **params):
        params = {k: v for k, v in params.items() if v is not None}
        ident = json.dumps({"m": method, **params}, sort_keys=True)
        cache_file = self.cache_dir / (hashlib.sha1(ident.encode()).hexdigest() + ".json")
        if self.use_cache and cache_file.exists():
            if time.time() - cache_file.stat().st_mtime < CACHE_TTL:
                return json.loads(cache_file.read_text())

        query = urllib.parse.urlencode(
            {"method": method, "api_key": self.key, "format": "json", **params}
        )
        req = urllib.request.Request(
            f"{LASTFM_URL}?{query}", headers={"User-Agent": "lastfm-critic/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise LastFMError(f"HTTP {e.code} em {method}")
        except urllib.error.URLError as e:
            raise LastFMError(f"sem conexão com o Last.fm: {e.reason}")

        if "error" in data:
            raise LastFMError(f"{method}: {data.get('message', data['error'])}")
        if self.use_cache:
            cache_file.write_text(json.dumps(data))
        time.sleep(0.2)  # educação com a API
        return data


def fetch_raw(lfm, user, period, n_details):
    info = lfm.call("user.getInfo", user=user)["user"]
    top_artists = as_list(
        lfm.call("user.getTopArtists", user=user, period=period, limit=50)
        .get("topartists", {}).get("artist")
    )
    top_albums = as_list(
        lfm.call("user.getTopAlbums", user=user, period=period, limit=25)
        .get("topalbums", {}).get("album")
    )
    top_tracks = as_list(
        lfm.call("user.getTopTracks", user=user, period=period, limit=50)
        .get("toptracks", {}).get("track")
    )
    recent = as_list(
        lfm.call("user.getRecentTracks", user=user, limit=200)
        .get("recenttracks", {}).get("track")
    )
    details = {}
    for a in top_artists[:n_details]:
        name = a["name"]
        try:
            d = lfm.call("artist.getInfo", artist=name, autocorrect=1)["artist"]
        except LastFMError:
            continue
        details[name] = {
            "listeners": int(d.get("stats", {}).get("listeners", 0) or 0),
            "tags": [t["name"] for t in as_list(d.get("tags", {}).get("tag"))],
        }
    return {
        "info": info, "period": period, "top_artists": top_artists,
        "top_albums": top_albums, "top_tracks": top_tracks,
        "recent": recent, "artist_details": details,
    }


# ----------------------------------------------------------------- análise ---
def effective_n(plays):
    """Número 'efetivo' de artistas (exp da entropia): 50 artistas iguais = 50;
    um artista dominando tudo = perto de 1."""
    total = sum(plays)
    ps = [p / total for p in plays if p > 0]
    return math.exp(-sum(p * math.log(p) for p in ps)) if ps else 0.0


def build_profile(raw):
    info = raw["info"]
    arts = [(a["name"], int(a["playcount"])) for a in raw["top_artists"]]
    if not arts:
        raise SystemExit("Esse usuário não tem artistas no período escolhido.")
    total = sum(p for _, p in arts)

    def share(n):
        return round(100 * sum(p for _, p in arts[:n]) / total, 1)

    registered = int(as_list(info.get("registered"))[0].get("unixtime", 0) or 0) \
        if isinstance(info.get("registered"), dict) else 0
    scrobbles = int(info.get("playcount", 0))
    years = round((time.time() - registered) / (365.25 * 86400), 1) if registered else None

    profile = {
        "usuario": info.get("name"),
        "periodo_analisado": raw["period"],
        "scrobbles_totais": scrobbles,
        "anos_de_conta": years,
        "scrobbles_por_dia_media": round(scrobbles / (years * 365.25), 1) if years else None,
        "concentracao_no_top50_do_periodo": {
            "top1_pct": share(1), "top5_pct": share(5), "top10_pct": share(10),
            "artistas_efetivos": round(effective_n([p for _, p in arts]), 1),
            "obs": "artistas_efetivos ~ quantos artistas 'de verdade' sustentam a escuta "
                   "(50 = tudo igualmente dividido; 1 = um artista domina tudo)",
        },
        "top_artistas": [{"nome": n, "plays": p} for n, p in arts[:15]],
    }

    # --- mainstream vs nicho (ponderado por plays) -----------------------
    det = raw["artist_details"]
    weighted = [(p, det[n]["listeners"]) for n, p in arts if n in det and det[n]["listeners"]]
    if weighted:
        tot = sum(p for p, _ in weighted)
        by_listeners = sorted(
            ((n, det[n]["listeners"]) for n, _ in arts if n in det and det[n]["listeners"]),
            key=lambda x: x[1],
        )
        profile["mainstream_vs_nicho"] = {
            "pct_plays_em_artistas_gigantes_(3M+_ouvintes)":
                round(100 * sum(p for p, l in weighted if l >= 3_000_000) / tot, 1),
            "pct_plays_em_artistas_de_nicho_(<150k_ouvintes)":
                round(100 * sum(p for p, l in weighted if l < 150_000) / tot, 1),
            "ouvintes_mediana": int(statistics.median(l for _, l in weighted)),
            "mais_obscuros": [{"nome": n, "ouvintes_lastfm": l} for n, l in by_listeners[:3]],
            "mais_populares": [{"nome": n, "ouvintes_lastfm": l} for n, l in by_listeners[-3:][::-1]],
            "obs": f"calculado sobre os {len(weighted)} artistas mais ouvidos",
        }

    # --- gêneros/tags ponderados -----------------------------------------
    tagw = Counter()
    for n, p in arts:
        for t in det.get(n, {}).get("tags", [])[:5]:
            if t.lower() not in STOP_TAGS:
                tagw[t.lower()] += p
    if tagw:
        tt = sum(tagw.values())
        profile["generos_dominantes"] = [
            {"tag": t, "pct": round(100 * w / tt, 1)} for t, w in tagw.most_common(12)
        ]

    # --- faixas e álbuns -------------------------------------------------
    tracks = [(t["name"], t["artist"]["name"], int(t["playcount"])) for t in raw["top_tracks"]]
    albums = [(a["name"], a["artist"]["name"], int(a["playcount"])) for a in raw["top_albums"]]
    if tracks:
        top10 = [p for _, _, p in tracks[:10]]
        med = statistics.median(top10)
        profile["faixas"] = {
            "top": [{"faixa": n, "artista": a, "plays": p} for n, a, p in tracks[:10]],
            "faixa_n1_vezes_a_mediana_do_top10": round(tracks[0][2] / med, 1) if med else None,
            "artista_com_mais_faixas_no_top50":
                Counter(a for _, a, _ in tracks).most_common(1)[0],
        }
    if albums:
        profile["albuns_top"] = [{"album": n, "artista": a, "plays": p} for n, a, p in albums[:10]]

    # --- escuta recente vs histórico -------------------------------------
    recent = [
        {"artist": r["artist"].get("#text") or r["artist"].get("name", ""), "track": r["name"]}
        for r in raw["recent"] if not (r.get("@attr") or {}).get("nowplaying")
    ]
    if recent:
        top_set = {n.lower() for n, _ in arts}
        rc_art = Counter(r["artist"] for r in recent)
        rc_trk = Counter((r["artist"], r["track"]) for r in recent)
        (ta, tn), tc = rc_trk.most_common(1)[0]
        profile["escuta_recente"] = {
            "ultimos_plays_analisados": len(recent),
            "artistas_distintos": len(rc_art),
            "top_artistas_recentes": [{"nome": n, "plays": c} for n, c in rc_art.most_common(5)],
            "pct_plays_fora_do_top50_do_periodo":
                round(100 * sum(1 for r in recent if r["artist"].lower() not in top_set) / len(recent), 1),
            "faixa_mais_repetida": {"faixa": tn, "artista": ta, "vezes": tc},
        }
    return profile


# ------------------------------------------------------------------ prompt ---
def load_examples(max_chars=7000):
    out = []
    for p in sorted((HERE / "examples").glob("*.txt")):
        out.append(p.read_text(encoding="utf-8", errors="replace").strip()[:max_chars])
    return out


def build_prompt(profile, lang):
    persona = (HERE / "persona.md").read_text(encoding="utf-8")
    examples = load_examples()
    system = persona
    if examples:
        refs = "\n\n".join(f"<referencia_{i+1}>\n{e}\n</referencia_{i+1}>" for i, e in enumerate(examples))
        system += (
            "\n\n# Textos de referência (só para o RITMO e a ESTRUTURA)\n"
            "Os textos abaixo são reviews de álbuns, de outra pessoa, em inglês. Estude a cadência, "
            "como a opinião é dosada com ressalvas, como as observações específicas sustentam o veredito "
            "e como o texto fecha com nota. NÃO copie frases, nomes, bordões ou aberturas deles, e não "
            "mencione que eles existem.\n\n" + refs
        )
    user = (
        f"Escreva a review do perfil abaixo, como se o gosto musical dessa pessoa fosse o álbum.\n"
        f"Idioma da resposta: {lang}.\n\n"
        f"<perfil_lastfm>\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n</perfil_lastfm>"
    )
    return system, user


def call_claude(system, user, model, max_tokens=2500):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("Defina ANTHROPIC_API_KEY (ou use --dry-run).")
    body = json.dumps({
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL, data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Erro da API da Anthropic ({e.code}): {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Sem conexão com a API da Anthropic: {e.reason}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


# -------------------------------------------------------------------- demo ---
def demo_raw():
    """Dados fictícios no mesmo formato da API, só para testar sem chaves."""
    names = [
        ("Magdalena Bay", 1840, 420_000, ["synthpop", "electropop", "indie pop", "seen live"]),
        ("Lorde", 1310, 4_800_000, ["pop", "art pop", "electropop", "female vocalists"]),
        ("Charli xcx", 980, 3_900_000, ["pop", "hyperpop", "electronic", "dance"]),
        ("Radiohead", 760, 6_200_000, ["alternative", "rock", "art rock", "electronic"]),
        ("Caroline Polachek", 640, 900_000, ["art pop", "synthpop", "electropop"]),
        ("Four Tet", 410, 1_100_000, ["electronic", "idm", "ambient", "dance"]),
        ("Tame Impala", 390, 4_500_000, ["psychedelic", "indie", "synthpop"]),
        ("Jon Hopkins", 220, 700_000, ["electronic", "ambient", "idm"]),
        ("Sufjan Stevens", 180, 2_300_000, ["indie folk", "folk", "singer-songwriter"]),
        ("Duster", 95, 90_000, ["slowcore", "space rock", "lo-fi"]),
        ("Slowdive", 160, 1_400_000, ["shoegaze", "dream pop", "indie"]),
        ("Beach House", 140, 2_000_000, ["dream pop", "indie", "shoegaze"]),
        ("Kero Kero Bonito", 120, 380_000, ["j-pop", "electropop", "indie pop"]),
        ("Sophie", 85, 800_000, ["hyperpop", "electronic", "experimental"]),
        ("Ween", 40, 450_000, ["alternative", "experimental", "rock"]),
    ]
    tr = [("Image", "Magdalena Bay", 214), ("Angel on a Satellite", "Magdalena Bay", 188),
          ("Hounds of Love", "The Futureheads", 12), ("Man of the Year", "Lorde", 101),
          ("360", "Charli xcx", 97), ("Idioteque", "Radiohead", 91),
          ("Bunny Is a Rider", "Caroline Polachek", 88), ("Killing Time", "Magdalena Bay", 83),
          ("Supercut", "Lorde", 76), ("Everything Is Embarrassing", "Sky Ferreira", 70)]
    al = [("Imaginal Disk", "Magdalena Bay", 640), ("Virgin", "Lorde", 520),
          ("Brat", "Charli xcx", 410), ("Desire, I Want to Turn Into You", "Caroline Polachek", 380),
          ("Kid A", "Radiohead", 300)]
    now = int(time.time())
    recent_seq = [("Magdalena Bay", "Image")] * 6 + [("Lorde", "Man of the Year")] * 3 + \
        [("Charli xcx", "360"), ("Four Tet", "Baby"), ("Duster", "Inside Out"), ("Mk.gee", "Are You Looking Up")] * 2 + \
        [("Slowdive", "Alison")] * 2 + [("Mk.gee", "Rockman")] * 3
    return {
        "info": {"name": "demo_user", "playcount": "58214",
                 "registered": {"unixtime": str(now - int(6.4 * 365.25 * 86400))}},
        "period": "6month",
        "top_artists": [{"name": n, "playcount": str(p)} for n, p, _, _ in names],
        "top_albums": [{"name": n, "artist": {"name": a}, "playcount": str(p)} for n, a, p in al],
        "top_tracks": [{"name": n, "artist": {"name": a}, "playcount": str(p)} for n, a, p in tr],
        "recent": [{"name": t, "artist": {"#text": a}} for a, t in recent_seq],
        "artist_details": {n: {"listeners": l, "tags": t} for n, _, l, t in names},
    }


# -------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description="Um crítico ranzinza para o seu Last.fm.")
    ap.add_argument("user", nargs="?", help="nome de usuário no Last.fm")
    ap.add_argument("--period", choices=PERIODS, default="overall")
    ap.add_argument("--lang", default="português do Brasil")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--details", type=int, default=20,
                    help="quantos artistas consultar em artist.getInfo (gêneros/popularidade)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="mostra perfil e prompt, não chama o Claude")
    ap.add_argument("--demo", action="store_true", help="usa dados fictícios, sem chave do Last.fm")
    ap.add_argument("--save-prompt", action="store_true",
                    help="grava o prompt completo em prompt.txt para colar em qualquer chat (sem API da Anthropic)")
    ap.add_argument("--out", help="arquivo de saída (padrão: reviews/<user>_<periodo>_<data>.md)")
    args = ap.parse_args()

    if args.demo:
        raw = demo_raw()
    else:
        if not args.user:
            ap.error("informe o usuário (ou use --demo)")
        key = os.environ.get("LASTFM_API_KEY")
        if not key:
            raise SystemExit("Defina LASTFM_API_KEY (https://www.last.fm/api/account/create).")
        try:
            raw = fetch_raw(LastFM(key, not args.no_cache), args.user, args.period, args.details)
        except LastFMError as e:
            raise SystemExit(f"Last.fm: {e}")

    profile = build_profile(raw)
    system, user = build_prompt(profile, args.lang)

    if args.save_prompt:
        out = HERE / "prompt.txt"
        out.write_text(
            "### INSTRUÇÕES (siga como persona e regras)\n\n" + system +
            "\n\n### PEDIDO\n\n" + user + "\n",
            encoding="utf-8",
        )
        print(f"Prompt salvo em {out}\nAbra o arquivo, copie tudo e cole num chat "
              f"(Claude, ChatGPT, Gemini...).")
        return

    if args.dry_run:
        print("=== PERFIL CALCULADO ===")
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        print(f"\n=== PROMPT: system={len(system)} chars, user={len(user)} chars ===")
        return

    review = call_claude(system, user, args.model)
    print(review)

    out = Path(args.out) if args.out else HERE / "reviews" / f"{profile['usuario']}_{profile['periodo_analisado']}_{date.today()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Review: {profile['usuario']} ({profile['periodo_analisado']})\n\n{review}\n\n---\n\n"
        f"<details><summary>Perfil usado</summary>\n\n```json\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n```\n</details>\n",
        encoding="utf-8",
    )
    print(f"\n[salvo em {out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
