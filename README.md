# analyze_insta

兵庫の地域メディアのInstagram運用のために作った、投稿データの取得・分類・分析ツールです。
Instagram公式APIで自分たちと他アカウントの投稿を集め、ジャンル・地域・形式・キャプションの1行目・投稿曜日ごとに「どんな投稿が伸びるか」を比べます。

## できること

- **取得**：Instagram Graph API（Facebookログイン版）で投稿データをCSVに保存
  - 自分のアカウント：いいね・コメント・保存・リーチ・シェア・再生数
  - 他のビジネス/クリエイターアカウント：いいね・コメント・フォロワー数・キャプション（`business_discovery`）
  - 期間（`--since` / `--until`）と形式（`--only reel` など）で絞り込み
- **分類**：キャプションから自動で付与
  - ジャンル（開店情報 / イベント / カフェ・スイーツ / グルメ / 体験 / おでかけスポット ほか）
  - 地域（兵庫県41市町＋駅名・地名の辞書。複数地域の「〇選」は県全体、県外は「県外」）
  - PR投稿かどうか（`#PR`・提供・タイアップなど。「PR・お仕事のご依頼」の定型文は除外）
- **可視化**：`tool/insta-gap.html` にCSVを貼ると、「他アカウントで反応が良いのに、自分たちはまだ投稿していないジャンル×地域」を一覧表示

## 使い方

```bash
pip install -r requirements.txt
cp .env.example .env          # IG_TOKEN、FB_APP_ID、FB_APP_SECRET を記入

python insta_fetch.py whoami  # トークン確認。IG_USER_ID を自動で .env に保存
python insta_fetch.py extend  # 長期トークン（約60日）に交換

python insta_fetch.py own --limit 50                           # 自分の投稿＋インサイト
python insta_fetch.py others --file accounts.txt --limit 50    # 他アカウント
python insta_fetch.py others --file accounts.txt --since 2026-01-01 --only reel
python insta_fetch.py reclassify                               # classify.py を直した後に分類だけやり直す
```

`accounts.txt` には比較したいアカウントのユーザー名を1行に1つ書きます（`#` で始まる行はコメント）。

### 必要な準備
1. 自分のInstagramをプロアカウント（クリエイター/ビジネス）にして、Facebookページと連携
2. [Meta for Developers](https://developers.facebook.com/) でアプリを作り、ユースケース「Instagramでメッセージとコンテンツを管理」→「FacebookログインによるAPI設定」を選ぶ
3. 「アクセス許可と機能」で `instagram_manage_insights` を追加
4. Graph APIエクスプローラで `instagram_basic`、`instagram_manage_insights`、`pages_show_list`、`pages_read_engagement`、`business_management` を付けてトークンを発行

### 出力CSVの列
`account, date, genre, region, format, likes, comments, saves, followers, caption, reach, shares, views, pr, posted_at, weekday, hour, age_days, caption_len, hashtags, permalink`

## 分析の設計

- **反応率** ＝（いいね＋コメント）÷ フォロワー数
- **倍率** ＝ 同じアカウント・同じ月の反応率の中央値を1倍とした値。フォロワー数の差と時期の差を消して比べるため
- 全体の集計は、アカウントごとの中央値を平均（投稿の多いアカウントに引っ張られないため）
- 投稿から3日未満の投稿は、まだ伸びている途中なので除外

## 分析でわかったこと（6アカウント・約1,200投稿、2026年1〜10月）

- 伸び悩みの原因はジャンルより**見せ方**。同じグルメのリールでも、他アカウントは平均1.3倍、伸び悩んでいたアカウントは0.5倍
- キャプション1行目が**問いかけ**（「〜知ってた？」）の投稿は、そうでない投稿より大きく伸びる傾向
- キャプション200文字以下、ハッシュタグ0個の投稿が伸びやすい
- 曜日は日曜が強く（1.29倍）、金曜が弱い（0.84倍）。時間帯は19〜20時台が強い
- PR投稿は全アカウントで伸びにくい（0.75〜0.85倍）
- リール動画10本の比較では、冒頭が「なぜ今見るべきか」（新店・話題・期間限定・場所）で始まるものが伸び、料理の説明から始まるものは伸びにくかった。店内・メニューの静止カットが長いほど伸びにくい傾向

※ 相関の分析なので、施策はテスト運用で確かめる前提です。

## 限界

- 他アカウントの保存数・リーチ・シェアはAPIで取得できない
- 動画の中身（映像・音声）はAPIでは取れず、別途確認が必要
- ジャンル・地域・PRはキーワードによる自動分類なので、誤分類がある
- フォロワー数は取得時点の値（投稿当時の値は取れない）

## 注意

- `.env`（トークン）と `data/`（取得データ）はリポジトリに含めていません
- 他アカウントのデータは、Instagramの利用規約とAPIの利用条件の範囲で扱ってください

## 使用技術

Python（requests）、Instagram Graph API v26.0、HTML/JavaScript
