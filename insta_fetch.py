"""
Instagram投稿データ取得スクリプト（Instagram Graph API / Facebookログイン版）

設定は同じフォルダの .env に書く（.env.example をコピーして作る）。トークンはチャットに貼らないこと。

使い方（Git Bash でこのフォルダに cd してから）
  python insta_fetch.py whoami                 # トークンの確認と、IG_USER_ID の取得
  python insta_fetch.py extend                 # 短期トークンを長期トークン（約60日）に交換して .env を更新
  python insta_fetch.py own --limit 50         # 自分のアカウントの投稿 + インサイト → data/own_posts.csv
  python insta_fetch.py others ユーザー名1 ユーザー名2 --limit 50
                                               # 他のビジネス/クリエイターアカウント → data/others_posts.csv
  python insta_fetch.py reclassify             # classify.py を直したあと、取得済みCSVの分類だけ付け直す

期間・形式の指定（own / others 共通）
  --since 2026-07-01 --until 2026-09-30        # 期間内の投稿を全部（--limit を付けなければ件数の上限なし）
  --only reel                                  # リールだけ（reel carousel image video から複数指定可）
  例: python insta_fetch.py others --file accounts.txt --since 2026-07-01 --until 2026-09-30 --only reel

出力CSVの列（インスタ空きテーマ分析ツールと同じ並び + 追加列）
  account,date,genre,region,format,likes,comments,saves,followers,caption,reach,shares,views,permalink
  genre / region は classify.py のルールで自動付与
"""
import argparse
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests が入っていません。先に  pip install requests  を実行してください。")

from classify import genre_of, is_pr, region_of

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
DATA = HERE / "data"
API_VERSION = "v26.0"  # Graph APIエクスプローラの表示に合わせた版（2026-10-20時点）
API = f"https://graph.facebook.com/{API_VERSION}"

COLUMNS = ["account", "date", "genre", "region", "format", "likes", "comments", "saves",
           "followers", "caption", "reach", "shares", "views", "pr", "posted_at", "weekday", "hour",
           "age_days", "caption_len", "hashtags", "permalink"]
JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"
FORMAT_KEYS = {"reel": "リール", "carousel": "カルーセル", "image": "画像", "video": "動画"}
INSIGHT_METRICS = ["reach", "saved", "shares", "views"]
FORMAT_JA = {"CAROUSEL_ALBUM": "カルーセル", "IMAGE": "画像", "VIDEO": "動画"}
MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink"


# ---------- .env ----------
def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("IG_", "FB_"))})
    return env


def save_env(updates):
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


ENV = load_env()


def need(*keys):
    missing = [k for k in keys if not ENV.get(k)]
    if missing:
        sys.exit(f".env に {', '.join(missing)} がありません。.env.example を参考に書いてください。")
    if "IG_USER_ID" in keys and not ENV["IG_USER_ID"].isdigit():
        sys.exit("IG_USER_ID の形式が違います（数字のみのIDです）。python insta_fetch.py whoami で取得してください。")


# ---------- API ----------
def get(path, **params):
    params["access_token"] = ENV["IG_TOKEN"]
    for attempt in range(3):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=30)
        except requests.RequestException as e:
            # URLにトークンが含まれるので、例外メッセージはそのまま出さない
            sys.exit(f"Instagram APIに接続できませんでした（{type(e).__name__}）。ネット接続を確認してください。")
        if r.status_code == 200:
            return r.json()
        err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
        code = err.get("code")
        if code in (4, 17, 32, 613) and attempt < 2:  # レート制限
            print("  呼び出し回数の上限。60秒待って再試行します…", file=sys.stderr)
            time.sleep(60)
            continue
        if code == 190:
            sys.exit("トークンが無効か期限切れです。Graph APIエクスプローラで発行し直して .env を更新してください。")
        raise RuntimeError(f"[{code}] {err.get('message', r.text[:200])}")


def fmt(m):
    if m.get("media_product_type") == "REELS":
        return "リール"
    return FORMAT_JA.get(m.get("media_type"), m.get("media_type", ""))


def posted(m):
    """APIの時刻（UTC）を日本時間に直す"""
    return datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(JST)


def row(account, followers, m, ins=None):
    caption = (m.get("caption") or "").replace("\r", " ").replace("\n", " ")
    ins = ins or {}
    t = posted(m)
    return {
        "account": account, "date": t.strftime("%Y-%m-%d"), "format": fmt(m),
        "posted_at": t.strftime("%Y-%m-%d %H:%M"), "weekday": WEEKDAYS[t.weekday()], "hour": t.hour,
        "age_days": (datetime.now(JST) - t).days,  # 取得時点での経過日数（新しい投稿はまだ伸びている途中）
        "caption_len": len(caption), "hashtags": caption.count("#") + caption.count("＃"),
        "genre": genre_of(caption), "region": region_of(caption), "pr": "PR" if is_pr(caption) else "",
        "likes": m.get("like_count", ""),  # いいね数を非表示にしている投稿は空
        "comments": m.get("comments_count", ""), "followers": followers,
        "saves": ins.get("saved", ""), "reach": ins.get("reach", ""),
        "shares": ins.get("shares", ""), "views": ins.get("views", ""),
        "caption": caption, "permalink": m.get("permalink", ""),
    }


def media_insights(media_id):
    """指標を1つずつ取る（投稿の種類や時期によって使えない指標があるため）"""
    out = {}
    for metric in INSIGHT_METRICS:
        try:
            data = get(f"{media_id}/insights", metric=metric).get("data", [])
            out[metric] = data[0]["values"][0]["value"] if data else ""
        except RuntimeError:
            out[metric] = ""
    return out


def write_csv(path, rows):
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"→ {path.name}（{len(rows)}件）")


# ---------- コマンド ----------
def cmd_whoami(_):
    need("IG_TOKEN")
    me = get("me", fields="id,name")
    print(f"Facebookユーザー: {me.get('name')}")
    pages = get("me/accounts", fields="name,instagram_business_account{id,username,media_count,followers_count}").get("data", [])
    found = [(p["name"], p["instagram_business_account"]) for p in pages if p.get("instagram_business_account")]
    if not pages:
        print("管理しているFacebookページが見つかりません。トークン発行時にページを選んだか確認してください。")
    for page, ig in found:
        print(f"ページ「{page}」→ Instagram @{ig.get('username')}  IG_USER_ID={ig['id']}"
              f"  投稿数={ig.get('media_count')}  フォロワー={ig.get('followers_count')}")
    if ENV.get("IG_USER_ID") and ENV["IG_USER_ID"] not in [ig["id"] for _, ig in found]:
        print(f"注意: .env の IG_USER_ID は上のどのアカウントとも一致しません。")
    if pages and not found:
        print("ページはありますが、Instagramプロアカウントが連携されていません。")
    if len(found) == 1 and not ENV.get("IG_USER_ID", "").isdigit():
        save_env({"IG_USER_ID": found[0][1]["id"]})
        print("IG_USER_ID を .env に保存しました。")


def cmd_extend(_):
    need("IG_TOKEN", "FB_APP_ID", "FB_APP_SECRET")
    r = requests.get(f"{API}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": ENV["FB_APP_ID"],
        "client_secret": ENV["FB_APP_SECRET"], "fb_exchange_token": ENV["IG_TOKEN"]}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        sys.exit(f"交換に失敗しました: {data.get('error', {}).get('message', data)}")
    save_env({"IG_TOKEN": data["access_token"]})
    days = int(data.get("expires_in", 0)) // 86400
    print(f"長期トークンに交換して .env を更新しました（有効期限 約{days}日）。")


def in_range(a, m):
    """期間・形式の条件に合うか。戻り値: True=採用 / False=対象外 / None=期間より古いので打ち切り"""
    t = posted(m).date()
    if a.since and t < a.since:
        return None
    if a.until and t > a.until:
        return False
    if a.only and fmt(m) not in a.only:
        return False
    return True


def collect(a, fetch_page, make_row, label):
    """新しい順に返ってくる投稿をページ送りしながら、条件に合うものを集める"""
    rows, after, scanned, older, prev_after = [], None, 0, 0, None
    while len(rows) < a.limit and scanned < a.max_scan:
        items, after = fetch_page(after)
        stop = False
        for m in items:
            scanned += 1
            ok = in_range(a, m)
            if ok is None:
                older += 1
                if older >= 5:  # 固定（ピン留め）投稿で順番が前後することがあるので、古い投稿が5件続いたら打ち切り
                    stop = True
                    break
                continue
            older = 0
            if ok:
                rows.append(make_row(m))
                print(f"  {label}: {len(rows)}件", end="\r", file=sys.stderr)
                if len(rows) >= a.limit:
                    break
        if stop or not after or after == prev_after:
            break
        prev_after = after
    if scanned >= a.max_scan:
        print(f"  {label}: {a.max_scan}件まで遡ったので打ち切りました（--max-scan で変更可）", file=sys.stderr)
    return rows


def cmd_own(a):
    need("IG_TOKEN", "IG_USER_ID")
    me = get(ENV["IG_USER_ID"], fields="username,followers_count,media_count")
    print(f"@{me['username']}（投稿数 {me.get('media_count')}）を取得します")

    def page(after):
        params = {"fields": MEDIA_FIELDS, "limit": 50}
        if after:
            params["after"] = after
        p = get(f"{ENV['IG_USER_ID']}/media", **params)
        data = p.get("data", [])
        nxt = p.get("paging", {}).get("cursors", {}).get("after") if data else None
        return data, nxt

    rows = collect(a, page, lambda m: row(me["username"], me["followers_count"], m,
                                          None if a.no_insights else media_insights(m["id"])), me["username"])
    write_csv(DATA / a.out, rows)


def fetch_other(username, a):
    info = {}

    def page(after):
        media = "media.limit(50)" + (f".after({after})" if after else "")
        fields = (f"business_discovery.username({username})"
                  f"{{username,followers_count,{media}{{{MEDIA_FIELDS}}}}}")
        bd = get(ENV["IG_USER_ID"], fields=fields)["business_discovery"]
        info.update(username=bd["username"], followers=bd["followers_count"])
        data = bd.get("media", {}).get("data", [])
        # business_discovery の入れ子では "next" が返らないことがあるので、after カーソルがあれば次へ進む
        nxt = bd.get("media", {}).get("paging", {}).get("cursors", {}).get("after") if data else None
        return data, nxt

    return collect(a, page, lambda m: row(info["username"], info["followers"], m), username)


def cmd_others(a):
    need("IG_TOKEN", "IG_USER_ID")
    names = list(a.usernames)
    if a.file:
        names += [l.strip().lstrip("@") for l in Path(a.file).read_text(encoding="utf-8-sig").splitlines()
                  if l.strip() and not l.startswith("#")]
    if not names:
        sys.exit("ユーザー名を指定するか、--file accounts.txt でリストを渡してください。")
    rows = []
    for u in dict.fromkeys(n.lstrip("@") for n in names):
        try:
            got = fetch_other(u, a)
            rows += got
            print(f"  @{u}: {len(got)}件" + " " * 10)
        except RuntimeError as e:
            print(f"  @{u}: 取得できませんでした（個人アカウント・非公開・ユーザー名違いの可能性） {e}")
    write_csv(DATA / a.out, rows)


def cmd_reclassify(a):
    """取得済みCSVのジャンル・地域・PRを、classify.py の最新ルールで付け直す（APIは呼ばない）"""
    for name in a.files:
        path = Path(name) if Path(name).exists() else DATA / name
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            c = r.get("caption", "")
            r["genre"], r["region"], r["pr"] = genre_of(c), region_of(c), "PR" if is_pr(c) else ""
        try:
            write_csv(path, [{k: r.get(k, "") for k in COLUMNS} for r in rows])
        except PermissionError:
            sys.exit(f"{path.name} が他のアプリ（Excelなど）で開かれていて上書きできません。閉じてから再実行してください。")


def ymd(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("日付は 2026-07-01 の形で指定してください")


def add_filters(p):
    p.add_argument("--since", type=ymd, help="この日以降の投稿だけ（例 2026-07-01）")
    p.add_argument("--until", type=ymd, help="この日以前の投稿だけ（例 2026-09-30）")
    p.add_argument("--only", nargs="+", choices=list(FORMAT_KEYS), help="形式で絞る（reel carousel image video）")
    p.add_argument("--limit", type=int, default=None, help="アカウントごとの最大件数（期間指定時の既定は上限なし）")
    p.add_argument("--max-scan", type=int, default=1000, help="遡る投稿数の上限（API呼び出しの節約）")


def finish_filters(a):
    if a.only:
        a.only = [FORMAT_KEYS[k] for k in a.only]
    if a.limit is None:
        a.limit = 10 ** 9 if a.since else 50


def main():
    ap = argparse.ArgumentParser(description="Instagram投稿データ取得")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami", help="トークン確認とIG_USER_IDの取得").set_defaults(fn=cmd_whoami)
    sub.add_parser("extend", help="長期トークンに交換").set_defaults(fn=cmd_extend)
    p = sub.add_parser("own", help="自分のアカウントの投稿とインサイト")
    add_filters(p)
    p.add_argument("--no-insights", action="store_true", help="インサイトを取らずに速く取得")
    p.add_argument("--out", default="own_posts.csv")
    p.set_defaults(fn=cmd_own)
    p = sub.add_parser("others", help="他アカウントの公開投稿")
    p.add_argument("usernames", nargs="*")
    p.add_argument("--file", help="ユーザー名を1行1つで書いたテキストファイル")
    add_filters(p)
    p.add_argument("--out", default="others_posts.csv")
    p.set_defaults(fn=cmd_others)
    p = sub.add_parser("reclassify", help="取得済みCSVを分類し直す")
    p.add_argument("files", nargs="*", default=["own_posts.csv", "others_posts.csv"])
    p.set_defaults(fn=cmd_reclassify)
    a = ap.parse_args()
    if hasattr(a, "since"):
        finish_filters(a)
    a.fn(a)


if __name__ == "__main__":
    main()
