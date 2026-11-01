# F2競輪データ（2025年以降）

F2開催の **A級1・2班** と **A級3班（チャレンジ）** を、原本を変更せずに年別SQLiteへ統合・検索する専用リポジトリです。**S級・L級（ガールズ）・F1・G3などは取り込みません。**

## 連携した原本

元リポジトリ: [yuhevita-wq/keirin-market-analysis-](https://github.com/yuhevita-wq/keirin-market-analysis-)。ファイルの参照先を `data/source_manifest.json` に固定Gitコミットとともに保存しています。

| クラス | 期間 | 原本 | フィルタ |
| --- | --- | --- | --- |
| A級1・2班（`A12`） | 2025年1月〜2026年10月2日 | [data/a_class_12](https://github.com/yuhevita-wq/keirin-market-analysis-/tree/main/data/a_class_12) | `meeting_grade=F2` だけ。F1・不明を除外 |
| A級3班（`A3`） | 2025年1月〜2026年10月7日 | [data/a3_challenge](https://github.com/yuhevita-wq/keirin-market-analysis-/tree/main/data/a3_challenge) | `meeting_grade=F2` だけ。混在するG3を除外 |

元データはそれぞれ2か月ごとに分散しています（末尾2026年は部分期間）。`data/all_classes/` は同じ原本の整理用コピーにつき、**二重に取り込みません**。取得レース数とファイル別SHA-256を監査レポートへ記録します。

### データ抽出条件

- F2以外を除外。A1/A2とA3チャレンジの級班を分けて保存する。
- A1/A2は `race_type` のA級表記、`class_scope=A1/A2` で確認。
- A3は `race_type` のチャレンジ表記、`class_scope=A3 challenge` で確認。
- **5・6・7車をすべて保持**。7車立てだけを見たいときは `entry_count='7'` を指定する。
- 原本の `race_id` と `race_id+car_no` の一意性、孤児レコード、不完全な結果・払戻を監査。欠損は推測補完しない。
- 中止と欠損は区別する。**結果がないことだけで中止とは断定しません**。中止の公式情報が確認された場合に別途状態を記録します。

## 使い方

GitHubの **Actions → Build F2 2025+ datasets** から初回または再構築を実行できます。必要な元CSVは固定コミットから取得し、標準Python（外部ライブラリ不要）で年別に統合します。

```bash
# 年別SQLiteを取得・生成（インターネット接続が必要）
python scripts/build_f2.py --year all

# 2025年の級班別F2レース数を取得
python scripts/query_f2.py --year 2025 --sql "SELECT f2_class,COUNT(*) FROM races GROUP BY f2_class"

# 2025年・A3チャレンジ・7車の3連単確定払戻をCSV出力
python scripts/query_f2.py --year 2025 --sql "SELECT r.race_date,r.track,r.race_no,p.combination,p.payout_yen FROM races r JOIN v_paid_trifecta p ON p.race_id=r.race_id WHERE r.f2_class='A3' AND r.entry_count='7'" --output challenge.csv

# 年をまたいで検索（SQLでは年別スキーマ名を指定）
python scripts/query_f2.py --year all --sql "SELECT f2_class,COUNT(*) FROM y2025.races GROUP BY f2_class"
```

- `data/ready/f2_2025.sqlite.gz`, `f2_2026.sqlite.gz`: ビルド結果の年別検索用SQLite圧縮データ
- `reports/ingestion/YYYY.json`: 取込件数、元CSV SHA-256、除外行数、結果・払戻の未収録件数
- `races`: 開催日・競輪場・レース番号・出走数・種別・ライン予想
- `entries`: 出走表・車番・選手属性
- `results`: 着順等（レース終了後の情報）
- `payouts`: 払戻（レース終了後の情報）
- `v_pre_race_races`, `v_pre_race_entries`, `v_paid_trifecta`: 検索用ビュー

**事後結果は事前予測の入力に使わないでください。** 過去に収集した出走表の内容が発走前時点に完全一致するかは別途検証が必要です。ラインは公開された「並び予想」であり、公式確定の隊列ではありません。払戻から発走前オッズを推測しません。

2026年10月3日以降のA1/A2、10月8日以降のA3は、上記の主な原本フォルダーでは未収録です。未収録期間に実際のF2開催がなかったとは判断しません。全国F2の完全網羅も別途開催カレンダーとの照合が必要です。
