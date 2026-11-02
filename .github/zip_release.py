"""patch-data 알림 워크플로(notify.yml)가 푸시 전에 돌린다: 이미지 묶음 onp_images.zip을 git 대신 Release 첨부로 둔다(2026-10-22).

공개 저장소에는 .github/zip_release.py로 들어간다(publish_public.py가 복사). 표준 라이브러리와 gh(Actions 기본 설치)만 쓴다.
1) images/(index.tsv + p/*.bin)로 묶음을 만든다. review_assemble.py와 같은 형식(index.tsv 먼저·이름순, 시각 고정, deflate 6)이다.
2) update.json images의 sha256·size·url·urls와 SHA256SUMS.txt가 이 묶음과 다르거나 저장소에 onp_images.zip이 남아 있으면
   고쳐 커밋한다(zip은 지운다). 묶음을 PC(Windows)에서 만들어 바이트가 달라도 여기서 맞춰진다.
3) 태그 images-<dataBuild>(main이 아니면 미리 보기 test-images-<dataBuild>) Release에 묶음을 올리고 같은 종류는 최근 3개만 남긴다.
   태그는 zip이 빠진 커밋에 붙인다(옛 zip이 든 커밋을 태그가 붙잡아 저장소가 줄지 않는 일을 막음).
사용: python .github/zip_release.py   (GITHUB_REPOSITORY·GITHUB_REF_NAME·GH_TOKEN 필요)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ZIP = "onp_images.zip"
KEEP = 3
AUTHOR = ["-c", "user.name=ournotes-ko", "-c", "user.email=noreply@ournotes-ko.invalid"]


def build_zip(img_dir, out):
    rels = sorted(os.path.relpath(os.path.join(d, n), img_dir).replace("\\", "/")
                  for d, _, fs in os.walk(img_dir) for n in fs)
    names = ["index.tsv"] + [r for r in rels if r != "index.tsv"]
    with zipfile.ZipFile(out, "w") as z:
        for n in names:
            zi = zipfile.ZipInfo(n, ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(os.path.join(img_dir, n), "rb") as f:
                z.writestr(zi, f.read(), compresslevel=6)
    with open(out, "rb") as f:
        data = f.read()
    return hashlib.sha256(data).hexdigest(), len(data), len(names) - 1


def tag_for(ref, build):
    return ("images-" if ref == "main" else "test-images-") + str(build)


def release_url(repo, tag):
    return f"https://github.com/{repo}/releases/download/{tag}/{ZIP}"


def fix_update(upd, sha, size, url):
    im, changed = upd["images"], False
    for k, v in (("sha256", sha), ("size", size), ("url", url), ("urls", [url])):
        if im.get(k) != v:
            im[k] = v
            changed = True
    return changed


def fix_sums(text, sha):
    lines = [l for l in text.splitlines() if l and not l.endswith("  " + ZIP)]
    return "".join(l + "\n" for l in lines + [f"{sha}  {ZIP}"])


def git(*a, check=True):
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)} 실패\n{r.stdout}{r.stderr}")
    return r


def gh(*a, check=True):
    r = subprocess.run(["gh", *a], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"gh {' '.join(a)} 실패\n{r.stdout}{r.stderr}")
    return r


def align(repo, ref, zpath):
    # 최신 커밋 기준으로 묶음을 만들고 update.json·SHA256SUMS를 맞춘다. push가 겹치면(공지 갱신 등) 받아서 다시 한다.
    for i in range(3):
        git("fetch", "-q", "--depth", "1", "origin", ref)
        git("reset", "-q", "--hard", "origin/" + ref)
        with open("update.json", encoding="utf-8") as f:
            upd = json.load(f)
        tag = tag_for(ref, upd["images"]["dataBuild"])
        sha, size, count = build_zip("images", zpath)
        changed = fix_update(upd, sha, size, release_url(repo, tag))
        with open("SHA256SUMS.txt", encoding="utf-8") as f:
            sums_old = f.read()
        sums = fix_sums(sums_old, sha)
        if not (changed or sums != sums_old or os.path.exists(ZIP)):
            return tag, sha, count, "그대로"
        with open("update.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump(upd, f, ensure_ascii=False, indent=1)
        with open("SHA256SUMS.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write(sums)
        git("rm", "-q", "--ignore-unmatch", ZIP)
        git("add", "update.json", "SHA256SUMS.txt")
        git(*AUTHOR, "commit", "-q", "-m", f"[images] {tag} 묶음을 Release로")
        if git("push", "-q", "origin", "HEAD:" + ref, check=False).returncode == 0:
            return tag, sha, count, "고쳐 커밋함"
        print(f"push 거절(다른 커밋과 겹침) -> 다시 {i + 1}/3", flush=True)
    sys.exit("update.json을 맞추지 못함(push 3번 거절)")


def publish(repo, ref, tag, zpath, count):
    if gh("release", "view", tag, "--repo", repo, check=False).returncode == 0:
        return "이미 있음"
    head = git("rev-parse", "HEAD").stdout.strip()
    extra = [] if ref == "main" else ["--prerelease"]
    gh("release", "create", tag, f"{zpath}#{ZIP}", "--repo", repo, "--target", head, "--latest=false", *extra,
       "--title", f"이미지 데이터 {tag}", "--notes", f"패치 앱이 받는 이미지 교체 데이터 묶음(그림 {count}개). 직접 받을 필요는 없습니다.")
    return "만듦"


def prune(repo, ref, keep_tag):
    prefix = tag_for(ref, "")
    rows = json.loads(gh("release", "list", "--repo", repo, "--limit", "100", "--json", "tagName").stdout or "[]")
    tags = sorted((t["tagName"] for t in rows if t["tagName"].startswith(prefix) and t["tagName"][len(prefix):].isdigit()),
                  key=lambda t: int(t[len(prefix):]), reverse=True)
    gone = [t for t in tags[KEEP:] if t != keep_tag]
    for t in gone:
        gh("release", "delete", t, "--repo", repo, "--yes", "--cleanup-tag")
    return gone


def main():
    repo, ref = os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_REF_NAME", "main")
    with tempfile.TemporaryDirectory() as d:
        zpath = os.path.join(d, ZIP)
        tag, sha, count, how = align(repo, ref, zpath)
        made = publish(repo, ref, tag, zpath, count)
    gone = prune(repo, ref, tag)
    print("ZIP_RELEASE_OK", tag, sha[:8], f"그림 {count}개", f"update.json {how}", f"Release {made}",
          f"지운 Release {len(gone)}" + (" " + ",".join(gone) if gone else ""))


if __name__ == "__main__":
    main()
