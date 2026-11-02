"""일본 서버 공지 → 한국 서버 공식 공지 대응 판정과 공지 번역 맵(onp_notice_map.bin) 생성.

사용: python notice_map.py [--out out/notice] [--map final/onp_notice_map.bin] [--min-sim 0.40] [--no-fetch]
      데이터 저장소 Actions(매시간): --main-map onp_master_map.bin --map onp_notice_map.bin --out .github/notice
        --update-json update.json --base-url <raw 주소/>  → 맵이 바뀌면 update.json의 noticeMap·seq를 고치고 NOTICE_CHANGED 출력

출처: 팬 사이트 Moenotes의 공지 API(게임 서버 응답을 그대로 옮김, 약관상 과도한 수집 금지 → 상세는 (서버, id, 수정 시각)별로 캐시).
판정(모두 통과해야 '같은 공지'):
  1) 날짜: 본문의 'YYYY/MM/DD HH:MM' 집합이 같다(둘 다 없으면 통과하되 유사도 기준을 높인다)
  2) 숫자: 날짜·시각을 뺀 두 자리 이상 숫자 중 한국 공지에만 있는 것은 없고, 일본 공지에만 있는 것은 1개 이하
     (한국 공지가 예시 하나를 빼고 옮긴 경우는 허용, 값이 다르면 양쪽에 차이가 생겨 떨어진다)
  3) 이름: 일본어 괄호(「」『』[]) 안 이름 중 일반 번역 맵에 있는 것의 한국어가 한국 공지에 80% 이상 들어 있다
  4) 기계번역 유사도: 일본 공지(제목+본문)를 Google 번역으로 한국어로 옮긴 글과 한국 공지의 글자 3-gram F1이 기준 이상,
     그리고 두 번째로 비슷한 한국 공지보다 0.1 이상 높다(날짜·숫자·이름을 모두 통과한 후보가 하나뿐이면 차이는 보지 않음)
  기계번역은 짝을 찾는 데만 쓰고, 맵에는 한국 서버 공식 제목·본문과 배너 그림 주소(bannerUrl·pickupBannerUrl, 둘 다 있을 때)만 넣는다.
맵 형식 ONPN: "ONPN" u32 버전(1) u32 개수 { u32 일본어 길이, u32 한국어 길이, 일본어 UTF-16LE, 한국어 UTF-16LE }
  패처는 공지 제목·본문 문자열이 일본어와 글자 단위로 똑같을 때만 바꾼다(일본 공지가 고쳐지면 일본어 그대로).
"""
import argparse, hashlib, html, json, os, re, struct, sys, time, urllib.error, urllib.parse, urllib.request
from typing import Any

WORK = os.path.dirname(os.path.abspath(__file__))
API = "https://api.bdon.moe/api/v1"
UA = {"User-Agent": "OurNotesPatch-notice/1.0 (+https://github.com/ournotes-ko)", "Accept": "application/json"}
MAIN_MAP = WORK + "/native/onp_master_map.bin"


def _get_json(url) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def get_json(url, tries=3) -> dict[str, Any]:
    for k in range(tries - 1):
        try:
            return _get_json(url)
        except OSError:
            time.sleep(2 + 3 * k)
    return _get_json(url)


PINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notice_pins.json")


def fetch_region(region, cache_dir, no_fetch):
    lst_path = f"{cache_dir}/{region}_list.json"
    if no_fetch:
        lst = json.load(open(lst_path, encoding="utf-8"))
    else:
        lst = get_json(f"{API}/{region}/announcements")
        json.dump(lst, open(lst_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out = []
    for a in lst["announcements"]:
        p = f"{cache_dir}/{region}_{a['id']}_{a.get('lastUpdatedAt', '0')}.json"
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
        elif no_fetch:
            raise RuntimeError(f"캐시 없음: {p}")
        else:
            d = get_json(f"{API}/{region}/announcements/{a['id']}")["announcement"]
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            time.sleep(0.5)
        out.append(d)
    return out


def body_text(h):
    h = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h).replace("\u3000", " ")
    return "\n".join(s for s in (re.sub(r"[ \t]+", " ", x).strip() for x in h.splitlines()) if s)


DT = re.compile(r"(20\d\d)/(\d{1,2})/(\d{1,2})(?:\s*[（(][^)）]{1,4}[)）])?\s*(?:(\d{1,2}):(\d\d))?")


def dates(t):
    s = set()
    for y, mo, d, hh, mm in DT.findall(t):
        s.add(f"{y}/{int(mo)}/{int(d)}" + (f" {int(hh)}:{mm}" if hh else ""))
    return s


def numbers(t):
    t = DT.sub(" ", t)
    t = re.sub(r"UTC\s*\+\s*9|GMT\s*\+\s*9", " ", t)
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return {n for n in re.findall(r"\d+", t) if len(n) >= 2}


NAME = re.compile(r"「([^「」]{1,60})」|『([^『』]{1,60})』|\[([^\[\]]{1,60})\]")


def names(t):
    return {next(g for g in m if g) for m in NAME.findall(t)}


BOILER = re.compile(r"^(いつも.*(バンドリ|アワーノーツ).*(ありがとう|感謝)|(引き続き|今後とも).*よろしくお願い|"
                    r"항상.*아워 ?노트.*감사|앞으로도.*아워 ?노트.*부탁)")


def strip_boiler(t):
    # 모든 공지에 들어가는 첫·끝 인사 줄은 서로 다른 공지끼리의 유사도만 올리므로 유사도 계산에서 뺀다.
    return "\n".join(s for s in t.split("\n") if not BOILER.match(s))


def official_names(t, mm):
    # 괄호 안 이름(카드·멤버·곡·이벤트)을 공식 번역으로 먼저 바꿔 기계번역이 한국 공지 표기와 같아지게 한다.
    # 짧은 일반어까지 바꾸면 문장이 깨져(バンドリ→밴드リ) 괄호 안만 바꾼다.
    def rep(m):
        n = next(g for g in m.groups() if g)
        return m.group(0).replace(n, mm.get(n, n))
    return NAME.sub(rep, t)


# 2026-10-18 사용자 결정: 기계번역 표현 → 한국 공식 공지 표현(대응 찾기의 유사도 계산에만 쓰고 화면 글에는 쓰지 않음).
# 제목의 '개최!'는 한국 공지도 그대로 쓰므로 본문 표현(개최했습니다·개최 기간 등)만 바꾼다.
TERMS = [(re.compile(p), r) for p, r in (
    (r"번들리|반도리", "뱅드림"), (r"아워 ?노츠", "아워 노트"), (r"가챠", "뽑기"),
    (r"개최(?=했|합|하|중|기간|일정)", "진행"), (r"개시", "시작"), (r"스케줄", "일정"),
    (r"이용 ?순서", "이용 방법"), (r"교환 ?기한", "교환 기간"), (r"배포 ?기간", "수령 기간"),
    (r"보수", "보상"), (r"호화(로운|롭게|스러운)?", "풍성한"), (r"(선물|프레젠트) ?박스", "선물함"),
    (r"노멀 ?보상", "일반 보상"), (r"프리즘", "젬"), (r"크리스탈", "크리스털"), (r"유상 ?스타", "유료 스타"),
    (r"레어리티", "희귀도"), (r"악곡", "곡"), (r"해방", "해금"), (r"새로운|신등장", "신규"), (r"고객", "이용자"))]


def official_terms(t):
    for p, r in TERMS:
        t = p.sub(r, t)
    return t


def load_main_map(path):
    b = open(path, "rb").read()
    assert b[:4] == b"ONPM", "일반 번역 맵 형식이 다름"
    pc, mc = struct.unpack_from("<II", b, 4)
    p = 12 + 4 * mc
    m = {}
    for _ in range(pc):
        jl, kl = struct.unpack_from("<HH", b, p); p += 4
        ja = b[p:p + 2 * jl].decode("utf-16-le", "surrogatepass"); p += 2 * jl
        ko = b[p:p + 2 * kl].decode("utf-16-le", "surrogatepass"); p += 2 * kl
        m.setdefault(ja, ko)
    return m


def mt_ko(text, cache):
    k = hashlib.sha1(text.encode()).hexdigest()
    if k in cache:
        return cache[k]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) > 1500 and buf:
            parts.append(buf); buf = ""
        buf += line + "\n"
    if buf:
        parts.append(buf)
    out = []
    for s in parts:
        data = urllib.parse.urlencode({"q": s}).encode()
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=ja&tl=ko&dt=t"
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
            j = json.load(r)
        out.append("".join(seg[0] for seg in j[0] if seg and seg[0]))
        time.sleep(0.3)
    cache[k] = "".join(out)
    return cache[k]


def grams(t, n=3):
    t = re.sub(r"\s+", "", t)
    g = {}
    for i in range(len(t) - n + 1):
        g[t[i:i + n]] = g.get(t[i:i + n], 0) + 1
    return g


def f1(a, b):
    if not a or not b:
        return 0.0
    inter = sum(min(v, b.get(k, 0)) for k, v in a.items())
    pa, pb = inter / sum(a.values()), inter / sum(b.values())
    return 0.0 if inter == 0 else 2 * pa * pb / (pa + pb)


def write_map(path, pairs):
    blob = bytearray(b"ONPN" + struct.pack("<II", 1, len(pairs)))
    for ja, ko in pairs:
        jb, kb = ja.encode("utf-16-le", "surrogatepass"), ko.encode("utf-16-le", "surrogatepass")
        blob += struct.pack("<II", len(jb) // 2, len(kb) // 2) + jb + kb
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    open(path, "wb").write(bytes(blob))
    return hashlib.sha256(blob).hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=WORK + "/out/notice")
    ap.add_argument("--map", default=WORK + "/final/onp_notice_map.bin")
    ap.add_argument("--min-sim", type=float, default=0.40)
    ap.add_argument("--min-sim-nodate", type=float, default=0.60)
    ap.add_argument("--no-fetch", action="store_true", help="캐시만 사용")
    ap.add_argument("--main-map", default=MAIN_MAP, help="이름 대조에 쓰는 일반 번역 맵")
    ap.add_argument("--update-json", default="", help="맵이 바뀌면 이 update.json의 noticeMap 항목을 고친다")
    ap.add_argument("--base-url", default="https://raw.githubusercontent.com/ournotes-ko/patch-data/main/")
    ap.add_argument("--min-app-code", type=int, default=34, help="공지 맵을 받는 패치 앱(1.33)의 versionCode")
    a = ap.parse_args()
    cache_dir = a.out + "/cache"
    os.makedirs(cache_dir, exist_ok=True)
    jp, kr = fetch_region("jp", cache_dir, a.no_fetch), fetch_region("kr", cache_dir, a.no_fetch)
    mm = load_main_map(a.main_map)
    mt_path = a.out + "/mt_cache.json"
    mt_cache = json.load(open(mt_path, encoding="utf-8")) if os.path.exists(mt_path) else {}

    krf = []
    for k in kr:
        t = body_text(k.get("body", ""))
        krf.append({"a": k, "text": t, "dates": dates(t), "nums": numbers(t),
                    "g": grams(k["title"] + "\n" + strip_boiler(t)), "flat": re.sub(r"\s+", "", k["title"] + t)})
    pins = json.load(open(PINS, encoding="utf-8")) if os.path.exists(PINS) else {}
    report, pairs, used, src = [], [], {}, {}
    for j in jp:
        t = body_text(j.get("body", ""))
        jd, jn = dates(t), numbers(t)
        try:
            mt = mt_ko(official_names(j["title"] + "\n" + strip_boiler(t), mm), mt_cache)
        except (urllib.error.URLError, TimeoutError) as e:
            # Actions 서버에서 Google 번역이 429로 막히면 이 공지만 건너뛴다(다음 실행에서 다시 시도).
            print(f"MT_FAIL jp{j['id']} {e}", flush=True)
            report.append({"jp": j["id"], "jp_title": j["title"], "category": j.get("category"), "jp_dates": sorted(jd),
                           "matched": False, "best": None, "second_sim": 0.0, "why": ["기계번역 실패"]})
            continue
        jg = grams(official_terms(mt))
        known = [(n, mm[n]) for n in names(t) if n in mm and mm[n] != n]
        cands = []
        for i, k in enumerate(krf):
            hit = sum(1 for _, ko in known if re.sub(r"\s+", "", ko) in k["flat"])
            c = {"kr": k["a"]["id"], "kr_title": k["a"]["title"], "sim": round(f1(jg, k["g"]), 3),
                 "dates_ok": jd == k["dates"], "nums_ok": not (k["nums"] - jn) and len(jn - k["nums"]) <= 1,
                 "names": f"{hit}/{len(known)}", "names_ok": not known or hit / len(known) >= 0.8, "i": i}
            c["need"] = a.min_sim if jd else a.min_sim_nodate
            c["pass"] = c["dates_ok"] and c["nums_ok"] and c["names_ok"] and c["sim"] >= c["need"]
            cands.append(c)
        cands.sort(key=lambda c: -c["sim"])
        best = cands[0] if cands else None
        second = cands[1]["sim"] if len(cands) > 1 else 0.0
        # 2026-10-18 사용자 결정: 날짜·숫자·이름을 모두 통과한 후보가 하나뿐이면 2등과의 차이를 보지 않는다
        # (같은 틀로 쓴 지난 이벤트·가챠 공지가 유사도 2등으로 붙어 같은 공지를 놓치던 문제).
        hard = sum(1 for c in cands if c["dates_ok"] and c["nums_ok"] and c["names_ok"])
        margin_ok = best is not None and (best["sim"] - second >= 0.1 or hard == 1)
        ok = bool(best and best["pass"] and margin_ok)
        # 수동 짝(notice_pins.json): 일본 공지가 적어 둔 수정 시각 그대로일 때만 적용(고쳐지면 일본어 그대로).
        pin = pins.get(str(j["id"]))
        if pin and str(pin.get("jp_updated")) == str(j.get("lastUpdatedAt")):
            pc = next((c for c in cands if str(c["kr"]) == str(pin["kr"])), None)
            if pc is not None:
                best, ok = pc, True
        r = {"jp": j["id"], "jp_title": j["title"], "category": j.get("category"), "jp_dates": sorted(jd),
             "matched": ok, "best": {k: v for k, v in best.items() if k != "i"} if best else None, "second_sim": second}
        if best and not ok:
            r["why"] = [w for w, bad in (("날짜", not best["dates_ok"]), ("숫자", not best["nums_ok"]),
                                         ("이름", not best["names_ok"]), ("유사도", best["sim"] < best["need"]),
                                         ("차이", not margin_ok)) if bad]
        if ok and best:
            prev = used.get(best["kr"])
            if prev is not None and prev[0] >= best["sim"]:
                r["matched"] = False; r["why"] = ["한국 공지 중복"]
            else:
                if prev is not None:
                    prev[1]["matched"] = False; prev[1]["why"] = ["한국 공지 중복"]
                used[best["kr"]] = (best["sim"], r)
        if best:
            src[j["id"]] = (j, krf[best["i"]]["a"])
        report.append(r)
    json.dump(mt_cache, open(mt_path, "w", encoding="utf-8"), ensure_ascii=False)
    for r in report:
        if r["matched"]:
            j, k = src[r["jp"]]
            pairs.append((j["title"], k["title"]))
            if j.get("body") and k.get("body"):
                pairs.append((j["body"], k["body"]))
            for f in ("bannerUrl", "pickupBannerUrl"):
                if j.get(f) and k.get(f) and j[f] != k[f]:
                    pairs.append((j[f], k[f]))
    sha = write_map(a.map, pairs)
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "jp": len(jp), "kr": len(kr),
               "matched": sum(r["matched"] for r in report), "map": a.map, "map_sha256": sha, "pairs": len(pairs),
               "notices": report}, open(a.out + "/notice_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in report:
        b = r["best"] or {}
        print(f"{'O' if r['matched'] else '-'} jp{r['jp']:>4} sim={b.get('sim', 0):.2f} 2nd={r['second_sim']:.2f} "
              f"d={int(b.get('dates_ok', 0))} n={int(b.get('nums_ok', 0))} nm={b.get('names', '-')} "
              f"{r['jp_title'][:40]} -> {b.get('kr', '-')} {b.get('kr_title', '')[:30]} {r.get('why', '')}")
    matched = sum(r["matched"] for r in report)
    print(f"matched {matched}/{len(jp)} pairs={len(pairs)} map={a.map} sha={sha[:8]}")
    if a.update_json:
        upd = json.load(open(a.update_json, encoding="utf-8"))
        if (upd.get("noticeMap") or {}).get("sha256") != sha:
            name = os.path.basename(a.map)
            upd["noticeMap"] = {"sha256": sha, "size": os.path.getsize(a.map), "url": a.base_url + name,
                                "urls": [a.base_url + name], "count": matched, "minAppCode": a.min_app_code}
            upd["seq"] = int(time.time())
            with open(a.update_json, "w", encoding="utf-8", newline="\n") as f:
                json.dump(upd, f, ensure_ascii=False, indent=1)
            print("NOTICE_CHANGED", sha[:8], matched)


if __name__ == "__main__":
    sys.exit(main())
