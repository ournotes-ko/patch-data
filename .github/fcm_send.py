"""업데이트 푸시를 보낸다(Firebase 클라우드 메시징, 'updates' 주제). patch-data 저장소의 GitHub Actions와 PC 시험이 같이 쓴다.

사용: python fcm_send.py <서비스 계정 json 경로 | -> <update.json> [--set 경로=값 ...] [--dry-run]
  기본 주제는 시험용 updates-test(개발 기기만 구독). 실제 사용자에게 보내려면 --topic updates(Actions가 씀).
  '-'이면 환경 변수 FCM_SA(서비스 계정 json 내용)를 쓴다(Actions의 저장소 비밀 값).
  --set은 시험용: 예) --set app.versionCode=999 --set urgent=true --set notice="'시험 문구'" (값은 JSON으로 읽고, 안 되면 문자열)
패치 앱(1.30 PushService)은 data의 json(=update.json)을 그대로 받아 받을 것이 있으면 기기 알림을 띄운다.
raw 주소는 몇 분간 캐시되므로 목록을 푸시에 실어 보낸다. 푸시 데이터 한도(4 KB) 안에 들어야 한다.
필요: pip install google-auth requests
"""
import argparse
import json
import os
import sys

LIMIT = 3800


def set_path(d, path, raw):
    try:
        v = json.loads(raw)
    except ValueError:
        v = raw
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sa")
    ap.add_argument("update_json")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--topic", default="updates-test", help="기본은 시험 주제. 실제 사용자 주제(updates)는 patch-data Actions만 쓴다")
    ap.add_argument("--dev", nargs=2, metavar=("TITLE", "BODY"),
                    help="개발용 알림(패치 앱 1.32 이상, 시험 주제 구독 기기). update_json 자리에는 '-'")
    ap.add_argument("--url", default="", help="--dev 알림을 누르면 열 GitHub 주소(패치 앱 1.34 이상)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dev:
        if a.topic == "updates":
            sys.exit("개발용 알림은 실제 사용자 주제로 보내지 않는다")
        return send(a, {"type": "dev", "title": a.dev[0], "body": a.dev[1][:3000], **({"url": a.url} if a.url else {})})
    upd = json.load(open(a.update_json, encoding="utf-8"))
    for s in a.set:
        k, _, v = s.partition("=")
        set_path(upd, k, v)
    payload = json.dumps(upd, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > LIMIT:
        sys.exit(f"update.json이 푸시 한도를 넘음: {len(payload.encode('utf-8'))} B")
    print("seq", upd.get("seq"), "app", (upd.get("app") or {}).get("versionCode"), "urgent", upd.get("urgent", False),
          "bytes", len(payload.encode("utf-8")))
    send(a, {"type": "update", "json": payload})


def send(a, data):
    body = {"message": {"topic": a.topic, "data": data, "android": {"priority": "HIGH", "ttl": "86400s"}}}
    if a.dry_run:
        print("DRY", json.dumps(body, ensure_ascii=False)[:300])
        return
    import google.auth.transport.requests
    import requests
    from google.oauth2 import service_account
    info = json.loads(os.environ["FCM_SA"]) if a.sa == "-" else json.load(open(a.sa, encoding="utf-8"))
    cred = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    cred.refresh(google.auth.transport.requests.Request())
    r = requests.post(f"https://fcm.googleapis.com/v1/projects/{info['project_id']}/messages:send",
                      headers={"Authorization": "Bearer " + cred.token}, json=body, timeout=30)
    print("FCM", r.status_code, r.text[:300])
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
