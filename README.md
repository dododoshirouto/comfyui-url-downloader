# comfyui-url-downloader

ComfyUI の画面から URL を指定して、モデルファイルとカスタムノードをサーバにダウンロードする拡張。

RunPod Serverless 上の ComfyUI（[subski-runpod-comfyui-backend](https://github.com/torilab-ai/subski-runpod-comfyui-backend)）で、Network Volume にモデルやカスタムノードを置くために使う。S3 互換 API や Pod を使わずに、ブラウザから直接入れられるようにする。

## 1. できること

- サイドバーの「URL ダウンロード」パネルで、URL・種類・保存先を指定してダウンロードする
- 対応する URL は 3 種類
  - モデルファイルの直リンク（Hugging Face の `resolve` URL など）
  - GitHub のリンク
  - CivitAI のリンク
- モデルは `models/` 配下の種類別フォルダ（`checkpoints`、`loras`、`vae`、`diffusion_models`、`text_encoders` など）に保存する。その中のサブフォルダも指定できる（無ければ作る）
- カスタムノードは `git clone` する。保存先は `custom_nodes_dev` があればそこ、無ければ探索パスの先頭の `custom_nodes`（5 章）
- ダウンロードはサーバ側で裏で動かし、画面には進捗を表示する
- ダウンロード中は、拡張 idle_disconnect による自動切断を止める

## 2. 前提となる構成

- ComfyUI 0.42.0、フロントエンド 1.55 以降
- `extra_model_paths.yaml` で Network Volume（`/runpod-volume/ComfyUI/`）が `is_default: true` になっている。そのため、ComfyUI の `folder_paths.get_folder_paths(<種類>)` の先頭が Network Volume のフォルダになる
- カスタムノードの探索パスの先頭が Network Volume の `/runpod-volume/ComfyUI/custom_nodes/` になっている（`extra_model_paths.yaml` の `subski_nodes` で `is_default: true`）。`custom_nodes_dev` を探索パスに入れている環境では、そちらを優先する
- ブラウザ → Cloudflare Worker（認証）→ RunPod → ComfyUI の構成。Worker の認証を通った人だけが使える
- Cloudflare は約 100 秒で応答のないリクエストを切るため、ダウンロード自体はリクエストの中で行わない

## 3. 画面

ComfyUI のサイドバーに「URL ダウンロード」タブ（アイコン：ダウンロード）を追加する。

### 3.1 入力欄

| 項目 | 内容 |
|---|---|
| 区分 | 「モデル」か「カスタムノード」をユーザーが選ぶ（URL から自動で判別しない） |
| URL | ダウンロード元の URL |
| モデルの種類 | 区分が「モデル」のときだけ表示。サーバに登録されている種類の一覧から選ぶ |
| サブフォルダ | 区分が「モデル」のときだけ表示。任意。`sdxl/style` のように `/` 区切りで指定する |
| ファイル名 | 区分が「モデル」のときだけ表示。任意。空なら自動で決める（4.3） |
| 保存先の表示 | 実際に保存されるパスを入力に合わせて表示する。Network Volume（`/runpod-volume`）の外なら「ワーカーが入れ替わると消えます」と警告する |
| 「ダウンロード」ボタン | ジョブを登録する |

### 3.2 ジョブ一覧

- ジョブごとに、名前・状態・進捗（受信量 / 全体量、割合、速度）・保存先・メッセージを表示する
- 状態：`待機中` → `ダウンロード中` → `完了` / `失敗` / `中止`
- ダウンロード中のジョブには「中止」ボタンを出す
- 一覧はパネルを開いている間 1 秒ごと、閉じている間も実行中のジョブがあれば 5 秒ごとに更新する
- 完了・失敗・中止のジョブは「一覧から消す」で消せる
- ジョブはサーバのメモリ上にだけ持つ。ComfyUI が再起動すると消える

### 3.3 完了時の表示

- モデル：「完了しました。モデル一覧を更新するには、ノードのモデル選択欄を開き直すか、画面を再読み込みしてください」
- カスタムノード：「完了しました。ワーカーを再起動すると読み込まれます」
  - `requirements.txt` がある場合は、あわせて「requirements.txt があります。追加のパッケージは自動では入りません。必要ならイメージに焼き込んでください」と警告する

## 4. URL の扱い

### 4.1 モデル：直リンク

- `http://`・`https://` の URL をそのままダウンロードする
- ホストが `huggingface.co` で、設定に Hugging Face のトークンがあれば `Authorization: Bearer <トークン>` を付ける

### 4.2 モデル：GitHub のリンク

- `https://github.com/<owner>/<repo>/blob/<ref>/<path>` は `https://github.com/<owner>/<repo>/raw/<ref>/<path>` に変換する
- `https://github.com/<owner>/<repo>/raw/...`・`https://raw.githubusercontent.com/...`・`https://github.com/<owner>/<repo>/releases/download/...` はそのままダウンロードする
- それ以外の GitHub の URL（リポジトリのトップなど）は「ファイルの URL ではありません」としてエラーにする

### 4.3 モデル：CivitAI のリンク

次の形を受け付ける。

| 形 | 扱い |
|---|---|
| `https://civitai.com/models/<modelId>` | `GET https://civitai.com/api/v1/models/<modelId>` で最新バージョンを取得する |
| `https://civitai.com/models/<modelId>?modelVersionId=<versionId>` | `GET https://civitai.com/api/v1/model-versions/<versionId>` |
| `https://civitai.com/api/download/models/<versionId>` | そのままダウンロードする |

- バージョン情報の `files` のうち `primary: true` のもの（無ければ先頭）の `downloadUrl` と `name` を使う
- 設定に CivitAI のトークンがあれば、ダウンロード URL にクエリ `token=<トークン>` を付ける（CivitAI はダウンロード時に署名付き URL へリダイレクトするため、ヘッダではなくクエリで渡す）
- トークンなしでログインが必要なモデルを指定すると、CivitAI が 401 を返す。その場合は「CivitAI のトークンが必要です」と表示する

### 4.4 ファイル名の決め方（モデル）

優先順位は次のとおり。

1. 入力欄のファイル名
2. CivitAI のファイル情報の `name`
3. レスポンスの `Content-Disposition` の `filename*` / `filename`
4. リダイレクト後の URL のパスの最後の部分

ファイル名に `/`・`\`・`..`・制御文字が含まれる場合はエラーにする。

### 4.5 カスタムノード：GitHub のリンク

- 受け付ける形：`https://github.com/<owner>/<repo>`（末尾の `.git`・`/` は無視）、`https://github.com/<owner>/<repo>/tree/<branch>`
- `git clone --depth 1 [--branch <branch>] <URL> <カスタムノードの保存先>/<repo>` を実行する
- GitHub 以外の URL はエラーにする

## 5. 保存先と安全性

- モデルの保存先：`folder_paths.get_folder_paths(<種類>)[0]` / `<サブフォルダ>` / `<ファイル名>`
- モデルの種類の一覧：`folder_paths.folder_names_and_paths` のうち、`custom_nodes`・`configs` を除いたもの
- カスタムノードの保存先：`folder_paths.get_folder_paths("custom_nodes")` のうち、パスに `custom_nodes_dev` を含むもの。無ければ先頭のパス（ComfyUI-Manager のインストール先と同じ）。探索パスが空ならエラーにする
- 保存先が `/runpod-volume` の外になる場合（`is_default` が効いていない、ローカル環境など）は、画面の保存先表示とジョブに「ワーカーが入れ替わると消えます」と警告を出す。ダウンロード自体は止めない
- サブフォルダは `/` か `\` で区切り、各要素が空・`.`・`..`・絶対パス・制御文字を含む場合はエラーにする。組み立てたパスを解決し、保存先フォルダの外になる場合もエラーにする
- 同じ名前のファイル・フォルダが既にある場合は上書きせずエラーにする
- ダウンロード中は `<ファイル名>.part` に書き、完了してから本来の名前に変える。失敗・中止したら `.part` を消す
- `Content-Length` が分かる場合、保存先の空き容量が足りなければ始める前にエラーにする
- リダイレクトは自前で最大 10 回まで追う。認証情報（Hugging Face のトークン）は、最初のホストと同じホストへのリクエストにだけ付ける

## 6. サーバ API

すべて ComfyUI の `PromptServer.instance.routes` に登録する。

| メソッド・パス | 内容 |
|---|---|
| `GET /url_downloader/model_types` | モデルの種類と保存先パスの一覧、カスタムノードの保存先（無ければ `null`） |
| `GET /url_downloader/jobs` | ジョブの一覧 |
| `POST /url_downloader/jobs` | ジョブを登録する。本文：`{ kind: "model" \| "custom_node", url, model_type?, subfolder?, filename?, civitai_token?, hf_token? }`。入力が不正なら 400 と `{ error }` |
| `POST /url_downloader/jobs/{id}/cancel` | ダウンロード中・待機中のジョブを中止する |
| `DELETE /url_downloader/jobs/{id}` | 完了・失敗・中止のジョブを一覧から消す |

- トークンは画面の設定から、ジョブ登録の本文に入れて送る。サーバはジョブの一覧にトークンを含めない
- ジョブの一覧で返すもの：`id`、`kind`、`url`、`status`、`dest`、`received`、`total`、`speed`、`message`、`warning`、`created_at`

## 7. 設定（ComfyUI の設定画面に追加）

| id | 表示名 | 型 | 既定値 |
|---|---|---|---|
| `UrlDownloader.CivitaiToken` | CivitAI の API トークン | password | 空 |
| `UrlDownloader.HuggingFaceToken` | Hugging Face のトークン | password | 空 |

設定は ComfyUI の `user/` 配下（Network Volume）に**平文で**保存される。Worker の認証を通れる人なら誰でも読める。

## 8. idle_disconnect との連携

ダウンロードはサーバの裏で動くため、RunPod からは「処理中」に見えない。画面が idle_disconnect で `/__idle` に移動すると通信が止まり、ワーカーがアイドルタイムアウトで止まって、ダウンロードが途中で切れる。これを防ぐため、2 つの拡張を連携させる。

- 本拡張：待機中・ダウンロード中のジョブがある間、`window.__torilabBusy` に `{ urlDownloader: true }` を立てる（無くなったら `false`）
- idle_disconnect：`window.__torilabBusy` のいずれかの値が `true` の間は「キューに処理が残っている」と同じ扱いにする（警告も切断もせず、無くなった時点から計測し直す）

画面を開いていればジョブ一覧の問い合わせが続くので、その間ワーカーは止まらない。ダウンロード中に画面を閉じると、ワーカーが止まってダウンロードが切れることがある。

## 9. ファイル構成

```
comfyui-url-downloader/
├── __init__.py          # ルート登録・WEB_DIRECTORY
├── downloader.py        # ジョブ管理・ダウンロード・git clone
├── url_resolver.py      # URL の判定と変換（直リンク・GitHub・CivitAI）
├── paths.py             # 保存先の決定と検証
└── js/
    └── url_downloader.js  # サイドバーのパネル・設定
```

追加の pip 依存はない（ComfyUI が使っている `aiohttp` を使う）。

## 10. 導入

Network Volume の `ComfyUI/custom_nodes/` に、このリポジトリを `comfyui-url-downloader/` として置き、ワーカーを再起動する。

## 11. 受け入れ確認

| # | 操作 | 期待する結果 |
|---|---|---|
| 1 | サイドバーに「URL ダウンロード」が出る | パネルが開き、モデルの種類一覧に `checkpoints`・`loras` などが出る |
| 2 | Hugging Face の直リンク（小さい VAE など）を `vae` に入れる | 進捗が出て完了し、`/runpod-volume/ComfyUI/models/vae/` に保存される。ノードの VAE 選択欄に出る |
| 3 | サブフォルダ `test/a` を指定して入れる | `models/<種類>/test/a/` が作られ、その中に保存される |
| 4 | サブフォルダに `../x` を入れる | 「保存先が不正です」のエラーになり、ジョブは作られない |
| 5 | 同じファイルをもう一度入れる | 「既に存在します」で失敗し、元のファイルは残る |
| 6 | GitHub の `blob` のファイル URL をモデルとして入れる | `raw` に変換されて保存される |
| 7 | CivitAI のモデルページの URL を `loras` に入れる | 最新バージョンの主ファイルが、CivitAI 上のファイル名で保存される |
| 8 | ログインが必要な CivitAI のモデルをトークンなしで入れる | 「CivitAI のトークンが必要です」で失敗する。トークンを設定すると成功する |
| 9 | 大きいファイルの途中で「中止」を押す | 中止になり、`.part` が残らない |
| 10 | GitHub のリポジトリ URL をカスタムノードとして入れる | `/runpod-volume/ComfyUI/custom_nodes/<repo>` に clone され、再起動の案内が出る。`requirements.txt` があれば警告も出る |
| 11 | ダウンロード中に 5 分以上操作しない（idle_disconnect 有効） | 警告も切断もされず、完了から計測が始まる |

## 12. 既知の制約

- ジョブは ComfyUI のメモリ上にだけあり、再起動で消える。途中から再開する仕組みはない
- ダウンロード中に画面を閉じると、ワーカーが止まって途中で切れることがある（8 章）
- カスタムノードの追加の pip 依存は入らない。依存のあるノードはイメージに焼き込む
- トークンは平文で保存される（7 章）
